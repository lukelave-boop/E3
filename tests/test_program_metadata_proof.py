"""Cached syntax facts never cache current focus, material or execution authority."""
import copy
from dataclasses import replace

import pytest

from laser_aligner.air_assist import AirAssistMode, AirAssistSettings
from laser_aligner.errors import SafetyError
from laser_aligner.machine import material_surface
from tests.test_large_job_preparation import machine, program_text
from tests.test_material_surface import PROGRAM, selected
from tests.test_material_surface import focus as focus
from tests.test_material_surface import focus_machine as focus_machine
from tests.test_material_surface import machine_probe as machine_probe


def test_cached_start_facts_avoid_full_directive_scans_but_check_live_material(focus_machine, monkeypatch):
    service, _, _ = focus_machine
    snapshot, binding = selected(service)
    program = service.preflight_program(material_surface.attach(PROGRAM, binding))
    expected = service._selected_job_focus(program)
    monkeypatch.setattr("laser_aligner.machine.service.focus_program_metadata",
                        lambda _: pytest.fail("rescanned immutable focus metadata"))
    monkeypatch.setattr("laser_aligner.machine.service.material_surface_binding",
                        lambda _: pytest.fail("rescanned immutable surface metadata"))
    original = material_surface.program_binding
    inspected = []

    def inspect(lines):
        inspected.append(len(lines))
        assert len(lines) <= 2
        return original(lines)

    monkeypatch.setattr(material_surface, "program_binding", inspect)
    for _ in range(3):
        service._require_current_safety_profile(program)
        assert service._selected_job_focus(program) == expected
    assert inspected
    changed = copy.deepcopy(snapshot)
    changed["measurement_id"] = "different current measurement"
    monkeypatch.setattr(service, "material_surface_snapshot", lambda: changed)
    with pytest.raises(SafetyError, match="changed"):
        service._require_current_safety_profile(program)


@pytest.mark.parametrize("change", ["xy", "session", "surface", "calibration"])
def test_cached_focus_facts_still_require_current_selection(focus_machine, change):
    service, focus_state, _ = focus_machine
    selected(service)
    program = service.preflight_program(PROGRAM)
    service._selected_job_focus(program)
    if change == "xy":
        service._jog_position_mm = (1, 1)
    elif change == "session":
        focus_state.state.job_plan["session"] = (999, 999, 999)
    elif change == "surface":
        focus_state.state.clear_surface()
    else:
        focus_state.state.calibration["focus_offset_mm"] += 1
    with pytest.raises(SafetyError):
        service._selected_job_focus(program)


def test_replacing_even_equal_line_tuple_invalidates_identity_proof():
    service = machine()
    program = service.preflight_program(program_text())
    replacement = tuple(list(program.lines))
    assert replacement == program.lines and replacement is not program.lines
    object.__setattr__(program, "lines", replacement)
    with pytest.raises(SafetyError, match="changed after program preflight"):
        service._program_proof(program)


def test_cache_eviction_does_not_authorize_changed_or_misplaced_directives():
    service = machine()
    original = service.preflight_program(program_text())
    service.preflight_program("G21\nG90\nM5\nM5")
    forged = replace(original, lines=(*original.lines[:-1],
                                    "E3FOCUS 11111111-1111-1111-1111-111111111111", "M5"))
    with pytest.raises(SafetyError):
        service._program_proof(forged)
    assert service._program_proof(original).program is original


def test_cached_operation_targets_still_check_current_clearance(focus_machine):
    from tests.test_operation_focus import program_for
    from tests.test_thickness_focus import automatic

    service, focus_state, _ = focus_machine
    automatic(service, focus_state)
    program = service.preflight_program(program_for("CUT", "RASTER"))
    service._selected_job_focus(program)
    focus_state.state.job_plan["target_z_mm"] = 999
    with pytest.raises(SafetyError):
        service._selected_job_focus(program)


def test_policy_change_after_first_start_check_rejects_before_controller_work(monkeypatch):
    service = machine()
    service.settings.protocol = "marlin"
    service.settings.air_assist = AirAssistSettings(mode=AirAssistMode.MARLIN_FAN, fan_index=1)
    program = service.preflight_program(program_text())
    current = service._require_current_safety_profile

    def change_after_check(candidate):
        current(candidate)
        service.settings.air_assist.fan_index = 2

    monkeypatch.setattr(service, "_require_current_safety_profile", change_after_check)
    monkeypatch.setattr(service, "_require_session",
                        lambda: pytest.fail("Changed policy reached controller admission"))
    with pytest.raises(SafetyError, match="changed after program preflight"):
        service.start_validated_program(program)
    assert not service._job.running
    assert service._armed_program_digest is None


@pytest.mark.parametrize("field,value", [
    ("requires_motion", 1), ("requires_laser_authorization", 1),
    ("digest", "f" * 64), ("safety_profile", ()),
])
def test_metadata_cache_never_overrides_replaced_safety_fields(field, value):
    service = machine()
    program = service.preflight_program(program_text())
    service._program_proof(program)
    object.__setattr__(program, field, value)
    with pytest.raises(SafetyError):
        service._program_proof(program)


def test_cache_eviction_returns_the_proof_actually_checked(monkeypatch):
    service = machine()
    program = service.preflight_program(program_text())
    integrity = service._require_validated_program_integrity

    def evict_after_check(candidate):
        proof = integrity(candidate)
        service.preflight_program("G21\nG90\nM5\nM5")
        return proof

    monkeypatch.setattr(service, "_require_validated_program_integrity", evict_after_check)
    proof = service._program_proof(program)
    assert proof.program is program
    assert proof.snapshot.lines is program.lines
    assert service._validated_program_cache.program is not program


@pytest.mark.parametrize("field,value", [
    ("lines", ("G21", "G90", "M5", "G91", "M5")), ("requires_motion", 1),
])
def test_cache_miss_cannot_publish_mutation_during_full_validation(monkeypatch, field, value):
    service = machine()
    program = service.preflight_program(program_text())
    service.preflight_program("G21\nG90\nM5\nM5")
    analyze = service._analyze_program

    def change_after_analysis(*args):
        result = analyze(*args)
        object.__setattr__(program, field, value)
        return result

    monkeypatch.setattr(service, "_analyze_program", change_after_analysis)
    with pytest.raises(SafetyError, match="changed while recording"):
        service._program_proof(program)
    assert service._validated_program_cache.program is not program
