"""Offline Generate benchmark; never creates a machine or sends controller commands.

--project reads an existing project without saving it. Placement/travel authority
is synthetic and must never be used as a hardware export or physical evidence.
--source-root allows the same workload to measure an older source checkout.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import inspect
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--project', type=Path)
    parser.add_argument('--kind', choices=('raster', 'vector'), default='raster')
    parser.add_argument('--pitch', type=float, default=.2)
    parser.add_argument('--grid', type=int, default=32)
    parser.add_argument('--iterations', type=int, default=3)
    parser.add_argument('--bind-surface', action='store_true')
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.iterations < 1 or args.grid < 1 or args.pitch <= 0:
        parser.error('iterations, grid and pitch must be positive')
    sys.path.insert(0, str(args.source_root.resolve()))

    import cv2
    import numpy as np

    from laser_aligner.calibration.support import HoneycombCoordinateFrame
    from laser_aligner.config import LaserSettings, WorkArea
    from laser_aligner.desktop.job_preview import prepare_job_preview
    from laser_aligner.gcode.job_plan import build_job_plan
    from laser_aligner.machine.material_surface import attach
    from laser_aligner.project.io import load_project
    from laser_aligner.project.job_preflight import JobPreflightContext, build_job_preflight_report
    from laser_aligner.project.model import Bounds, CoordinateSpace, LayerMode, ProjectDocument, SceneObject
    from laser_aligner.project.toolpath import generate_project_gcode, verify_project_job_assets
    from laser_aligner.raster_safety import COORDINATE_REFERENCE, RasterTravelEnvelope

    with tempfile.TemporaryDirectory(prefix='e3-generation-benchmark-', dir=args.work_dir) as temporary:
        source_hash = None
        if args.project:
            source_hash = hashlib.sha256(args.project.read_bytes()).hexdigest()
            document = load_project(args.project)
        else:
            document = ProjectDocument.new('Generation benchmark', Bounds(0, 0, 220, 220))
            document.id = 'benchmark-project'
            layer = document.layers[0]
            layer.id = 'benchmark-layer'
            layer.power_percent = 10
            layer.speed_mm_min = 6000
            if args.kind == 'raster':
                path = Path(temporary) / 'dense.png'
                assert cv2.imwrite(str(path), np.full((1800, 1800), 128, dtype=np.uint8))
                layer.mode = LayerMode.RASTER
                layer.line_interval_mm = args.pitch
                layer.overscan_percent = 0
                document.add_object(SceneObject(
                    id='benchmark-image', name='Dense image', kind='image', layer_id=layer.id,
                    geometry={'asset': str(path)}, transform={
                        'x_mm': 110, 'y_mm': 110, 'width_mm': 180, 'height_mm': 180,
                    },
                ))
            else:
                pitch = 192 / args.grid
                for y in range(args.grid):
                    for x in range(args.grid):
                        item = SceneObject.ellipse(layer.id, name=f'Contour {x},{y}',
                                                   center=(14 + x * pitch, 14 + y * pitch),
                                                   width_mm=pitch * .65, height_mm=pitch * .65)
                        item.id = f'contour-{x}-{y}'
                        document.add_object(item)
        area = document.work_area
        machine_area = WorkArea(0, max(220., area.x_max), 0, max(220., area.y_max))
        frame = None
        if document.coordinate_space is CoordinateSpace.HONEYCOMB_LOCAL:
            frame = HoneycombCoordinateFrame(
                origin_machine_mm=(0., 0.), x_axis_machine=(1., 0.), y_axis_machine=(0., 1.),
                width_mm=area.width, height_mm=area.height, provenance_digest='a' * 64,
            )
        envelope = RasterTravelEnvelope(
            machine_area.x_min, machine_area.x_max, machine_area.y_min, machine_area.y_max,
            True, 'simulation', 'simulation', 'synthetic benchmark only',
            COORDINATE_REFERENCE, '2026-10-17',
        )
        binding = {
            'schema_version': 1, 'model_id': 'a' * 64, 'binding_id': 'b' * 64,
            'datum_id': 'synthetic', 'capture_pose': [110., 110., None],
            'plan_id': 'synthetic-plan', 'measurement_id': 'synthetic-measurement',
            'surface_elevation_mm': 2., 'honeycomb_height_mm': 0.,
            'reference': {'synthetic': True}, 'session': [1, 2, 3],
            'evidence_source_ids': [str(index) * 64 for index in range(3)],
        } if args.bind_surface else None
        native_binding = 'material_surface_metadata' in inspect.signature(generate_project_gcode).parameters
        laser = LaserSettings(power_max=1000)
        samples = []
        for _ in range(args.iterations):
            gc.collect()
            started = time.perf_counter()
            snapshot = document.clone()
            report = build_job_preflight_report(snapshot, JobPreflightContext(
                machine_work_area=machine_area, coordinate_frame=frame,
                honeycomb_execution_signature=None if frame is None else (*frame.provenance_signature, "b" * 64),
                machine_id="synthetic", machine_profile_id="synthetic",
                expected_calibration_profile_id="synthetic", active_calibration_profile_id="synthetic",
            ))
            if not report.ready:
                raise ValueError(f'Benchmark preflight blocked: {report}')
            preflight = time.perf_counter() - started
            kwargs = {'material_surface_metadata': binding} if native_binding else {}
            job = generate_project_gcode(snapshot, laser, coordinate_frame=frame,
                                         machine_work_area=machine_area, raster_travel_envelope=envelope,
                                         **kwargs)
            generation = time.perf_counter() - started - preflight
            if binding is not None and not native_binding:
                job.text = attach(job.text, binding)
                job.plan = build_job_plan(
                    job.text, power_max=1000, default_feed_mm_min=laser.travel_feed_mm_min,
                    acceleration_mm_s2=laser.preview_acceleration_mm_s2,
                    command_delay_ms=laser.preview_command_delay_ms,
                )
            prepare_job_preview(job.plan)
            verify_project_job_assets(job)
            total = time.perf_counter() - started
            canonical = '\n'.join(line for line in job.text.splitlines() if not line.startswith('; Generated:'))
            sample = dict(total_seconds=total, generation_seconds=generation,
                          snapshot_preflight_seconds=preflight, moves=len(job.plan.moves),
                          program_bytes=len(job.text.encode('utf8')),
                          program_sha256=hashlib.sha256(canonical.encode('utf8')).hexdigest())
            samples.append(sample)
            print(json.dumps(sample), flush=True)
            del job
        assert len({sample['program_sha256'] for sample in samples}) == 1
        if args.project:
            assert hashlib.sha256(args.project.read_bytes()).hexdigest() == source_hash
        result = dict(scope='Offline snapshot, advisory preflight, generation, exact plan, preview indexes and asset verification; excludes GUI painting, upload and Start. Synthetic placement/travel authority.',
                      python=sys.version, platform=platform.platform(), source_root=str(args.source_root.resolve()),
                      project_sha256=source_hash, bind_surface=args.bind_surface, samples=samples,
                      median_seconds=statistics.median(sample['total_seconds'] for sample in samples),
                      maximum_seconds=max(sample['total_seconds'] for sample in samples))
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
        print(json.dumps({key: result[key] for key in ('median_seconds', 'maximum_seconds')}))


if __name__ == '__main__':
    main()
