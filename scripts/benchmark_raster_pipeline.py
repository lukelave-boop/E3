"""Offline raster phase measurements; never connect, arm, send, or start a job.

Run from a checkout using its Python environment. The optional historical
comparison loads only static validator methods from a local Git revision.
Synthetic assets and prepared jobs live in a temporary directory.
"""
from __future__ import annotations

import argparse
import ast
import cProfile
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from types import MethodType
from unittest.mock import patch

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
bootstrap = argparse.ArgumentParser(add_help=False)
bootstrap.add_argument("--source-root", type=Path, default=SCRIPT_ROOT)
bootstrap.add_argument("--verified-envelope", action="store_true")
BOOTSTRAP = bootstrap.parse_known_args()[0]
ROOT = BOOTSTRAP.source_root.resolve()
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

from laser_aligner.config import LaserSettings, MachineSettings, WorkArea  # noqa: E402
from laser_aligner.desktop.job_preview import JobPreviewCanvas, prepare_job_preview  # noqa: E402
from laser_aligner.desktop.workspace import ObjectGraphicsItem  # noqa: E402
from laser_aligner.machine import service as service_module  # noqa: E402
from laser_aligner.machine.pi_job_protocol import MAX_UPLOAD_CHUNK_BYTES  # noqa: E402
from laser_aligner.machine.pi_job_service import (  # noqa: E402
    PiJobService,
    canonical_program_bytes,
    execution_policy_digest,
)
from laser_aligner.machine.pi_job_store import PiJobStore  # noqa: E402
from laser_aligner.machine.remote_service import RemoteMachineService  # noqa: E402
from laser_aligner.machine.service import MachineService  # noqa: E402
from laser_aligner.planning.cache import PlanningCache  # noqa: E402
from laser_aligner.project import Bounds, LayerMode, ProjectDocument, SceneObject  # noqa: E402
from laser_aligner.project.job_preflight import JobPreflightContext, build_job_preflight_report  # noqa: E402
from laser_aligner.project.raster_asset import scan_raster_file  # noqa: E402
from laser_aligner.project.toolpath import generate_project_gcode, verify_project_job_assets  # noqa: E402
from laser_aligner.storage import atomic_write_text  # noqa: E402


def forbidden(*args, **kwargs):
    raise AssertionError("Offline benchmark must never create/connect/start hardware or RPC")


def machine():
    settings = MachineSettings(allow_motion=True, work_area=WorkArea(0, 220, 0, 220))
    if BOOTSTRAP.verified_envelope:
        settings.raster_travel_envelope = synthetic_envelope()
    return MachineService(
        settings, LaserSettings(), hardware_enabled=True,
    )


def synthetic_envelope():
    from laser_aligner.raster_safety import COORDINATE_REFERENCE, RasterTravelEnvelope

    return RasterTravelEnvelope(
        0, 220, 0, 220, True, "synthetic offline controller", "synthetic firmware",
        "benchmark only; no physical qualification", COORDINATE_REFERENCE, "2026-10-15",
    )


def legacy_machine(reference):
    """Restore only pre-fix static methods; no imported historical side effects."""
    result = machine()
    source = subprocess.check_output(
        ["git", "show", f"{reference}:laser_aligner/machine/service.py"],
        cwd=ROOT, text=True, encoding="utf-8",
    )
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MachineService")
    names = {"_analyze_program", "_validate_stream_line", "_air_assist_command_kind", "preflight_program"}
    namespace = dict(vars(service_module))
    selected = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == names
    exec(compile(ast.Module(body=selected, type_ignores=[]), "historical-static-validators", "exec"), namespace)
    for name in names:
        setattr(result, name, MethodType(namespace[name], result))
    return result


def timed(values, name, operation):
    begin = time.perf_counter()
    result = operation()
    values[name] = round((time.perf_counter() - begin) * 1000, 3)
    return result


def executable(text):
    return tuple(line.partition(";")[0].strip() for line in text.splitlines() if line.partition(";")[0].strip())


def document(directory, name):
    image = np.full((364, 362), 255, dtype=np.uint8)
    if name == "white_border":
        image[70:294, 70:292] = 0
    elif name == "gray_dither":
        image[:] = 128
    elif name == "near_white_border":
        image[:] = 250
        image[70:294, 70:292] = 0
    else:
        raise ValueError(name)
    path = directory / f"{name}.png"
    assert cv2.imwrite(str(path), image)
    doc = ProjectDocument.new(name, Bounds(0, 0, 220, 220))
    layer = doc.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 0.5
    layer.speed_mm_min = 1200
    layer.overscan_percent = 2.5
    doc.add_object(SceneObject(
        name=name, kind="image", layer_id=layer.id, geometry={"asset": str(path)},
        transform={"x_mm": 110, "y_mm": 110, "width_mm": 180.752, "height_mm": 182.367},
    ))
    return doc, path


def run(directory, name, legacy_reference=None):
    timings = {}
    doc, source = document(directory, name)
    laser = LaserSettings()
    generation_options = (
        {"raster_travel_envelope": synthetic_envelope(), "require_verified_raster_travel_limits": True}
        if BOOTSTRAP.verified_envelope else {}
    )
    area = WorkArea(0, 220, 0, 220)
    cache = PlanningCache()
    timed(timings, "input_manifest", lambda: scan_raster_file(source))
    timed(timings, "canvas_decode", lambda: ObjectGraphicsItem._load_raster_snapshot(source, use_cache=False))
    snapshot = timed(timings, "document_snapshot", doc.clone)
    timed(timings, "structured_preflight", lambda: build_job_preflight_report(snapshot, JobPreflightContext(machine_work_area=area)))
    job = timed(timings, "generate_cold", lambda: generate_project_gcode(snapshot, laser, planning_cache=cache, **generation_options))
    warm = timed(timings, "generate_warm", lambda: generate_project_gcode(snapshot, laser, planning_cache=cache, **generation_options))
    # Generation-time comments may differ; all executable bytes must agree.
    assert executable(warm.text) == executable(job.text)
    preview = timed(timings, "preview_index", lambda: prepare_job_preview(job.plan))
    canvas = timed(timings, "preview_geometry", lambda: JobPreviewCanvas(job.plan, (0, 220, 0, 220), move_ends=preview.move_ends))
    canvas.close()
    canvas.deleteLater()
    QtWidgets.QApplication.processEvents()
    timed(timings, "asset_verify", lambda: verify_project_job_assets(job))
    timed(timings, "export_write", lambda: atomic_write_text(directory / "export.gcode", job.text))
    policy = machine()
    program = timed(timings, "send_preflight_once", lambda: policy.preflight_program(job.text))
    timed(timings, "proof_integrity", lambda: policy._require_validated_program_integrity(program))
    if legacy_reference:
        old = legacy_machine(legacy_reference)
        previous = timed(timings, "send_preflight_before_oct15", lambda: old.preflight_program(job.text))
        assert previous.lines == program.lines and previous.digest == program.digest
        assert previous.requires_laser_authorization == program.requires_laser_authorization
    # This is the same local policy preflight done by RemoteMachineService;
    # its wrapper currently does it twice. No remote object or RPC is needed.
    timed(timings, "send_preflight_twice", lambda: (policy.preflight_program(job.text), policy.preflight_program(job.text)))
    owner = PiJobService(machine(), PiJobStore(directory / "jobs"))
    raw = canonical_program_bytes(program)
    identifier = str(uuid.uuid4())

    def upload():
        owner.begin_upload(identifier, "benchmark.gcode", len(raw), program.digest)
        for offset in range(0, len(raw), MAX_UPLOAD_CHUNK_BYTES):
            owner.append_upload_chunk(identifier, offset, raw[offset:offset + MAX_UPLOAD_CHUNK_BYTES])

    timed(timings, "offline_durable_upload", upload)
    record = timed(timings, "offline_finalize", lambda: owner.finalize_upload(
        identifier, program_digest=program.digest,
        requires_laser_authorization=program.requires_laser_authorization,
        requires_motion=program.requires_motion, guarded_output_polygon_mm=None,
        policy_digest=execution_policy_digest(program),
    ))
    # Deliberately stop before START; exercise the read/hash/policy check only.
    checked = timed(timings, "offline_committed_recheck", lambda: owner._preflight_committed_program(record))
    assert checked.digest == program.digest
    return {
        "timings_ms": timings, "commands": len(program.lines), "bytes": len(raw),
        "preview_moves": len(job.plan.moves), "source_pixels": 364 * 362,
        "cut_mm": round(job.plan.cut_distance_mm, 3),
        "travel_mm": round(job.plan.travel_distance_mm, 3),
        "powered_geometry_sha256": hashlib.sha256(json.dumps([
            (move.start_x, move.start_y, move.end_x, move.end_y, move.power, move.feed_mm_min)
            for move in job.plan.moves if move.laser_on
        ], separators=(",", ":")).encode("ascii")).hexdigest(),
    }


COUNT_NAMES = {
    "probe_raster_asset", "_read_stable_payload", "_read_bounded_stable_encoded",
    "decode_raster_payload_grayscale", "_image_raster_rows", "_ordered_dither_row",
    "build_job_plan", "prepare_job_preview", "_analyze_program", "parse_words",
    "_check_line_safety", "_resolved_air_assist_commands", "_file_size_and_sha",
    "_read_program_text_path", "read_program_bytes",
}


def profile_counts(operation):
    profile = cProfile.Profile()
    profile.runcall(operation)
    result = {}
    for entry in profile.getstats():
        code = entry.code
        if not isinstance(code, str) and code.co_name in COUNT_NAMES:
            key = f"{Path(code.co_filename).name}:{code.co_name}"
            result[key] = result.get(key, 0) + entry.callcount
    return dict(sorted(result.items()))


def source_identifier():
    """Include untracked application files, which git diff alone omits."""
    entries = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "laser_aligner").rglob("*.py"))
    }
    tracked_diff = subprocess.check_output(
        ["git", "diff", "--binary", "HEAD", "--", "laser_aligner"], cwd=ROOT,
    )
    return {
        "application_source_tree_sha256": hashlib.sha256(
            json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "application_python_file_count": len(entries),
        "tracked_application_diff_sha256": hashlib.sha256(tracked_diff).hexdigest(),
        "benchmark_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def validation_counts(reference):
    text = "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S0\n" + "G1 X20 Y20 F500 S10\n" * 128 + "M5"
    policy = machine()
    counts = {"current_134_commands": profile_counts(lambda: policy.preflight_program(text))}
    if reference:
        old = legacy_machine(reference)
        counts["before_oct15_134_commands"] = profile_counts(lambda: old.preflight_program(text))
    remote = RemoteMachineService(
        MachineSettings(port="e3bridge://127.0.0.1:9", allow_motion=True),
        LaserSettings(), hardware_enabled=True,
    )
    with patch.object(remote._policy, "preflight_program", wraps=remote._policy.preflight_program) as preflight:
        remote.preflight_program(text)
        counts["remote_wrapper_unchanged_text"] = {
            "local_policy_preflight_calls": preflight.call_count,
            "identical_text": preflight.call_args_list[0].args == preflight.call_args_list[1].args,
        }
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--verified-envelope", action="store_true", help="Exercise new limits using synthetic offline evidence only")
    parser.add_argument("--legacy-reference", default=None)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--jobs", nargs="+", default=["white_border", "near_white_border", "gray_dither"])
    parser.add_argument("--counts", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    if args.verified_envelope and args.legacy_reference:
        parser.error("historical validator cannot accept the new raster metadata; compare before/after parser without --verified-envelope")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    report = {
        "platform": platform.platform(), "python": platform.python_version(),
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "working_tree": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True).strip(),
        "historical_static_validators": args.legacy_reference,
        "synthetic_verified_envelope": args.verified_envelope,
        "repeats": args.repeats, "jobs": {},
        "scope": "Offline Windows CPU/filesystem/offscreen Qt only; no RPC/controller/START. Counts measured separately from timings.",
    }
    report["source_identifier"] = source_identifier()
    with ExitStack() as stack:
        for cls, method in ((MachineService, "connect"), (MachineService, "start_validated_program"), (MachineService, "send_command"), (RemoteMachineService, "_rpc")):
            stack.enter_context(patch.object(cls, method, forbidden))
        stack.enter_context(patch.object(service_module, "create_machine_transport", forbidden))
        report["validation_counts"] = validation_counts(args.legacy_reference)
        scratch = SCRIPT_ROOT / "build"
        scratch.mkdir(exist_ok=True)
        temp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="raster-benchmark-", dir=scratch)))
        for name in args.jobs:
            samples = []
            for index in range(args.repeats):
                directory = temp / name / str(index)
                directory.mkdir(parents=True)
                sample = run(directory, name, args.legacy_reference)
                samples.append(sample)
                print(f"{name} repeat {index + 1}: {sample['commands']:,} commands", flush=True)
            item = {key: value for key, value in samples[0].items() if key != "timings_ms"}
            item["median_ms"] = {
                key: round(statistics.median(sample["timings_ms"][key] for sample in samples), 3)
                for key in samples[0]["timings_ms"]
            }
            item["samples_ms"] = [sample["timings_ms"] for sample in samples]
            if args.counts:
                directory = temp / name / "counts"
                directory.mkdir()
                item["pipeline_counts"] = profile_counts(lambda directory=directory, name=name: run(directory, name))
            report["jobs"][name] = item
            app.processEvents()
    report["source_unchanged_during_measurement"] = source_identifier() == report["source_identifier"]
    if not report["source_unchanged_during_measurement"]:
        raise RuntimeError("Application or benchmark source changed during measurement; rerun against a stable source tree")
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
