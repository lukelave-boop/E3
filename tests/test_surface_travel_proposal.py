from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from laser_aligner.calibration.bed import BedCalibration
from laser_aligner.calibration.support import HoneycombSupportReference
from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import SafetyError
from laser_aligner.machine.profiles import MachineInstance
from laser_aligner.raster_safety import (
    COORDINATE_REFERENCE,
    RasterTravelEnvelope,
    parse_raster_travel_envelope,
)
from scripts.prepare_surface_travel_record import main, prepare_proposal, write_proposal

PROFILE_ID = "1920x1080-manual-focus-010"
CONFIRMATION = "The operator confirms carriage clearance over the entire calibrated 191 x 191 mm surface."


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _inputs(tmp_path: Path, *, offset: tuple[float, float] = (0.0, 0.0)) -> dict:
    profile = tmp_path / PROFILE_ID
    profile.mkdir()
    angle = math.radians(0.37945093)
    origin = (28.7714478853, 37.5035345157)
    c, s = math.cos(angle), math.sin(angle)
    points = tuple((origin[0] + c * x - s * y, origin[1] + s * x + c * y)
                   for x, y in ((0, 0), (191, 0), (191, 191), (0, 191)))
    support = HoneycombSupportReference.from_four_corner_observations(
        raw_corners_machine_mm=points, corner_topology=(0, 1, 2, 3),
        support_width_mm=191.0, support_height_mm=191.0,
        created_at=20.0, bed_calibration_created_at=12.5,
    )
    bed = BedCalibration(
        image_to_machine=np.eye(3), machine_to_image=np.eye(3),
        image_width=1920, image_height=1080, rms_error_mm=0.0, max_error_mm=0.0,
        inlier_count=4, point_count=4, created_at=12.5,
        provenance={"camera": {"width": 1920, "height": 1080,
                               "controls": {"focus_absolute": 10, "focus_auto": 0}}},
    )
    envelope = RasterTravelEnvelope(
        x_min=10, x_max=225.7672592962, y_min=10, y_max=210, verified=True,
        controller="Recorded controller", firmware="Recorded firmware",
        configuration="Existing independently verified carriage rectangle",
        reference=COORDINATE_REFERENCE, tested_at="2026-10-15",
    )
    machine = MachineInstance(
        id="ender", name="Ender", machine_profile_id="generic-grbl",
        tool_head_profile_id="generic-diode-10w",
        machine=MachineSettings(protocol="grbl", raster_travel_envelope=envelope),
        laser=LaserSettings(spot_offset_x_mm=offset[0], spot_offset_y_mm=offset[1]),
        calibration_profile_id=PROFILE_ID,
    )
    paths = {
        "support": profile / "honeycomb_support.json",
        "bed_calibration": profile / "bed_calibration.json",
        "machine_registry": tmp_path / "machines.json",
    }
    _write(paths["support"], support.to_dict())
    _write(paths["bed_calibration"], bed.to_dict())
    _write(paths["machine_registry"], {
        "schema_version": 1, "active_machine_id": machine.id, "machines": [machine.to_dict()],
    })
    return {**paths, "confirmed_at": "2026-10-16", "evidence": CONFIRMATION}


@pytest.mark.parametrize("offset", [(0.0, 0.0), (3.5, -4.25)])
def test_proposal_binds_exact_rigid_surface_and_offset_without_expanding_rectangle(tmp_path, offset):
    inputs = _inputs(tmp_path, offset=offset)
    source_bytes = {key: inputs[key].read_bytes() for key in ("support", "bed_calibration", "machine_registry")}
    proposal = prepare_proposal(**inputs)
    original = json.loads(source_bytes["machine_registry"])["machines"][0]["machine"]["raster_travel_envelope"]
    proposed = proposal["proposed_raster_travel_envelope"]
    assert {key: value for key, value in proposed.items() if key != "surface"} == original
    reference = HoneycombSupportReference.from_dict(json.loads(source_bytes["support"]))
    surface = proposed["surface"]
    assert surface["beam_polygon_mm"] == reference.coordinate_frame.corners_machine_mm
    assert surface["coordinate_frame_digest"] == reference.coordinate_frame_digest
    assert surface["spot_offset_mm"] == offset
    assert surface["evidence"] == CONFIRMATION
    assert surface["confirmed_at"] == "2026-10-16"
    assert surface["verified"] is True
    envelope = parse_raster_travel_envelope(proposed)
    assert envelope is not None and envelope.surface is not None
    assert np.asarray(envelope.surface.carriage_polygon_mm) == pytest.approx(
        np.asarray(reference.coordinate_frame.corners_machine_mm) - np.asarray(offset)
    )
    assert max(y for _x, y in surface["beam_polygon_mm"]) > 229
    assert proposed["y_max"] == 210
    assert proposal["expected_raster_travel_envelope_digest"] == parse_raster_travel_envelope(original).digest
    assert proposal["machine_id"] == "ender"
    assert proposal["calibration_profile_id"] == PROFILE_ID
    assert proposal["status"] == "PROPOSAL_NOT_APPLIED"
    for key, data in source_bytes.items():
        assert proposal["sources"][key]["sha256"] == hashlib.sha256(data).hexdigest()
        assert inputs[key].read_bytes() == data


def test_proposal_selects_explicit_saved_machine(tmp_path):
    inputs = _inputs(tmp_path)
    registry = json.loads(inputs["machine_registry"].read_text())
    second = dict(registry["machines"][0], id="second", name="Second")
    second["laser"] = dict(second["laser"], spot_offset_x_mm=2.0)
    registry["machines"].append(second)
    _write(inputs["machine_registry"], registry)
    proposal = prepare_proposal(**inputs, machine_id="second")
    assert proposal["machine_id"] == "second"
    assert proposal["proposed_raster_travel_envelope"]["surface"]["spot_offset_mm"] == (2.0, 0.0)
    with pytest.raises(ValueError, match="Unknown saved machine"):
        prepare_proposal(**inputs, machine_id="missing")


@pytest.mark.parametrize(("key", "value", "message"), [
    ("confirmed_at", "", "confirmed_at"),
    ("confirmed_at", "20261016", "confirmed_at"),
    ("confirmed_at", "2026-02-30", "confirmed_at"),
    ("evidence", "  ", "evidence"),
])
def test_proposal_rejects_missing_or_invalid_operator_confirmation(tmp_path, key, value, message):
    inputs = _inputs(tmp_path)
    inputs[key] = value
    with pytest.raises(ValueError, match=message):
        prepare_proposal(**inputs)


@pytest.mark.parametrize("missing", ["--confirmed-at", "--evidence", "--bed-calibration"])
def test_cli_requires_explicit_confirmation_and_current_bed_file(tmp_path, missing):
    inputs = _inputs(tmp_path)
    args = []
    for key, value in {**inputs, "output": tmp_path / "proposal.json"}.items():
        flag = "--" + key.replace("_", "-")
        if flag != missing:
            args.extend([flag, str(value)])
    with pytest.raises(SystemExit) as raised:
        main(args)
    assert raised.value.code == 2
    assert not (tmp_path / "proposal.json").exists()


@pytest.mark.parametrize("change", ["profile", "bed_time", "camera_provenance", "legacy_support", "unverified_rectangle"])
def test_proposal_rejects_stale_or_missing_binding(tmp_path, change):
    inputs = _inputs(tmp_path)
    key = "machine_registry" if change in {"profile", "unverified_rectangle"} else "support" if change == "legacy_support" else "bed_calibration"
    raw = json.loads(inputs[key].read_text())
    if change == "profile":
        raw["machines"][0]["calibration_profile_id"] = "wrong-profile"
    elif change == "bed_time":
        raw["created_at"] += 1
    elif change == "camera_provenance":
        raw["provenance"]["camera"]["controls"]["focus_absolute"] = 11
    elif change == "legacy_support":
        raw["schema_version"] = 1
    else:
        raw["machines"][0]["machine"]["raster_travel_envelope"]["verified"] = False
    _write(inputs[key], raw)
    with pytest.raises((ValueError, SafetyError)):
        prepare_proposal(**inputs)


@pytest.mark.parametrize("source", ["support", "machine_registry", "bed_calibration"])
def test_write_proposal_refuses_every_input_target(tmp_path, source):
    inputs = _inputs(tmp_path)
    proposal = prepare_proposal(**inputs)
    old_bytes = inputs[source].read_bytes()
    with pytest.raises(ValueError, match="must not overwrite an input"):
        write_proposal(proposal, inputs[source])
    assert inputs[source].read_bytes() == old_bytes


def test_cli_creates_only_separate_proposal_and_never_replaces_it(tmp_path):
    inputs = _inputs(tmp_path)
    output = tmp_path / "proposal.json"
    args = [part for key, value in {**inputs, "output": output}.items()
            for part in ("--" + key.replace("_", "-"), str(value))]
    assert main(args) == 0
    original = output.read_bytes()
    assert json.loads(original)["status"] == "PROPOSAL_NOT_APPLIED"
    with pytest.raises(SystemExit) as raised:
        main(args)
    assert raised.value.code == 2
    assert output.read_bytes() == original
