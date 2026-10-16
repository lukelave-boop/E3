"""Prepare a separate, reviewable surface-travel record; never apply settings.

The confirmation is supplied by the operator. This offline tool performs no
physical test and imports no application runtime or controller service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from laser_aligner.calibration.bed import BedCalibration  # noqa: E402
from laser_aligner.calibration.profiles import signature_from_values  # noqa: E402
from laser_aligner.calibration.support import HoneycombSupportReference  # noqa: E402
from laser_aligner.errors import SafetyError  # noqa: E402
from laser_aligner.machine.profiles import MachineInstance  # noqa: E402
from laser_aligner.raster_safety import (  # noqa: E402
    VerifiedSurfaceTravel,
    parse_raster_travel_envelope,
    require_verified_raster_travel,
)
from laser_aligner.storage import strict_json_loads  # noqa: E402


def _read_snapshot(path: Path) -> tuple[Any, dict[str, str]]:
    content = path.read_bytes()
    return strict_json_loads(content.decode("utf-8")), {
        "path": str(path),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _select_machine(raw: object, machine_id: str | None) -> MachineInstance:
    if type(raw) is not dict or set(raw) != {"schema_version", "active_machine_id", "machines"}:
        raise ValueError("Machine registry must contain schema_version, active_machine_id and machines")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError("Unsupported machine registry schema")
    if type(raw["machines"]) is not list or not raw["machines"]:
        raise ValueError("Machine registry must contain saved machines")
    machines = [MachineInstance.from_dict(item) for item in raw["machines"]]
    by_id = {machine.id: machine for machine in machines}
    if len(by_id) != len(machines):
        raise ValueError("Machine registry contains duplicate machine IDs")
    active = raw["active_machine_id"]
    if type(active) is not str or active not in by_id:
        raise ValueError("Machine registry active_machine_id is invalid")
    selected = active if machine_id is None else machine_id
    if selected not in by_id:
        raise ValueError(f"Unknown saved machine: {selected}")
    return by_id[selected]


def prepare_proposal(
    *,
    support: Path,
    machine_registry: Path,
    bed_calibration: Path,
    confirmed_at: str,
    evidence: str,
    machine_id: str | None = None,
) -> dict[str, Any]:
    """Read each explicit input once and return an unapplied proposal.

    Support and bed files must be in the selected calibration-profile directory.
    A staged copy may be used, provided that directory binding is preserved.
    """
    support = Path(support).resolve(strict=True)
    machine_registry = Path(machine_registry).resolve(strict=True)
    bed_calibration = Path(bed_calibration).resolve(strict=True)
    registry_raw, registry_source = _read_snapshot(machine_registry)
    machine = _select_machine(registry_raw, machine_id)
    profile_id = machine.calibration_profile_id
    if not profile_id or support.parent.name != profile_id:
        raise ValueError("Support directory must match the selected machine calibration_profile_id")
    if bed_calibration.parent != support.parent:
        raise ValueError("Bed calibration and support must belong to the same calibration-profile directory")

    support_raw, support_source = _read_snapshot(support)
    bed_raw, bed_source = _read_snapshot(bed_calibration)
    reference = HoneycombSupportReference.from_dict(support_raw)
    if not reference.is_execution_verifiable:
        raise ValueError("Surface travel requires execution-verifiable four-corner support evidence")
    bed = BedCalibration.from_dict(bed_raw)
    if reference.bed_calibration_created_at != bed.created_at:
        raise ValueError("Support is bound to a different bed calibration creation time")
    camera = (bed.provenance or {}).get("camera")
    if type(camera) is not dict or type(camera.get("controls")) is not dict:
        raise ValueError("Bed calibration requires camera-profile provenance")
    signature = signature_from_values(
        width=camera.get("width"), height=camera.get("height"), controls=camera["controls"],
    )
    if signature.key != profile_id or (bed.image_width, bed.image_height) != (signature.width, signature.height):
        raise ValueError("Bed calibration camera provenance does not match the selected calibration profile")

    old = require_verified_raster_travel(machine.machine.raster_travel_envelope)
    frame = reference.coordinate_frame
    surface = VerifiedSurfaceTravel(
        beam_polygon_mm=frame.corners_machine_mm,
        spot_offset_mm=(machine.laser.spot_offset_x_mm, machine.laser.spot_offset_y_mm),
        coordinate_frame_digest=frame.provenance_digest,
        verified=True,
        confirmed_at=confirmed_at,
        evidence=evidence,
    )
    proposed_raw = old.to_dict()
    proposed_raw["surface"] = surface.to_dict()
    proposed = parse_raster_travel_envelope(proposed_raw)
    assert proposed is not None
    return {
        "schema_version": 1,
        "kind": "e3-surface-travel-proposal",
        "status": "PROPOSAL_NOT_APPLIED",
        "machine_id": machine.id,
        "machine_name": machine.name,
        "calibration_profile_id": profile_id,
        "expected_raster_travel_envelope_digest": old.digest,
        "sources": {
            "machine_registry": registry_source,
            "support": support_source,
            "bed_calibration": bed_source,
        },
        "support_bed_calibration_created_at": reference.bed_calibration_created_at,
        "current_raster_travel_envelope": old.to_dict(),
        "proposed_raster_travel_envelope": proposed.to_dict(),
        "notice": (
            "Operator-supplied physical confirmation; no physical test was performed by this tool. "
            "No configuration was applied. Review source hashes and the prior envelope digest "
            "before a separately authorized matching Desktop/Pi configuration update."
        ),
    }


def write_proposal(proposal: dict[str, Any], output: Path) -> None:
    """Create a new file exclusively, refusing inputs and all existing targets."""
    output = Path(output).resolve()
    inputs = {Path(source["path"]).resolve() for source in proposal["sources"].values()}
    if output in inputs:
        raise ValueError("Proposal output must not overwrite an input file")
    encoded = json.dumps(proposal, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--support", type=Path, required=True)
    parser.add_argument("--machine-registry", type=Path, required=True)
    parser.add_argument("--bed-calibration", type=Path, required=True)
    parser.add_argument("--machine-id", help="Saved machine ID; defaults to the registry's active machine")
    parser.add_argument("--confirmed-at", required=True, help="Operator confirmation date, exactly YYYY-MM-DD")
    parser.add_argument("--evidence", required=True, help="Operator's physical surface/head travel confirmation")
    parser.add_argument("--output", type=Path, required=True, help="New proposal file; must not already exist")
    args = parser.parse_args(argv)
    try:
        proposal = prepare_proposal(
            support=args.support, machine_registry=args.machine_registry,
            bed_calibration=args.bed_calibration, machine_id=args.machine_id,
            confirmed_at=args.confirmed_at, evidence=args.evidence,
        )
        write_proposal(proposal, args.output)
    except (OSError, ValueError, TypeError, KeyError, SafetyError) as exc:
        parser.error(str(exc))
    print(f"Unapplied proposal written to {args.output.resolve()}; active settings are unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
