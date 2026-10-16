"""No raster run-up may be consumed before the measured focus descent."""
import pytest

from laser_aligner.operation_focus import target_z
from tests import test_laser_focus as helpers
from tests.test_job_focus import stop_async, wait
from tests.test_thickness_focus import automatic

focus = helpers.focus
focus_machine = helpers.focus_machine
machine_probe = helpers.machine_probe


@pytest.mark.parametrize("stop_descent", [False, True])
@pytest.mark.parametrize("routed_approach", [False, True])
def test_measured_raster_focus_finishes_before_zero_power_runup(focus_machine, monkeypatch, stop_descent, routed_approach):
    machine, focus, primary = focus_machine
    plan = automatic(machine, focus)["job_focus"]
    descent = f"G1 Z{target_z(plan, 'RASTER'):.3f} F600"
    events = []
    for device, label in ((primary, "xy"), (focus.serial, "z")):
        original = device.write_line

        def write(line, original=original, label=label):
            events.append((label, line))
            if stop_descent and label == "z" and line == descent:
                stop_async(machine)
            return original(line)

        monkeypatch.setattr(device, "write_line", write)
    text = "\n".join((
        "G21", "G90", "M5", "E3OPFOCUS RASTER",
        *(("G0 X90 Y95 F1000",) if routed_approach else ()),
        "G0 X95 Y100 F1000", "M4 S0", "G1 X100 Y100 F500 S0",
        "G1 X105 Y100 F500 S100", "G1 X110 Y100 F500 S0", "M5",
    ))
    program = machine.preflight_program(text)
    machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    if stop_descent:
        assert machine._job.error is not None
        assert ("xy", "M4 S0") not in events
        assert ("xy", "G1X100Y100F500S0") not in events
        assert ("xy", "X105S100") not in events
        return
    assert machine._job.error is None
    approach = events.index(("xy", "G0 X95 Y100 F1000"))
    lowered = events.index(("z", descent))
    mode = events.index(("xy", "M4 S0"))
    runup = events.index(("xy", "G1X100Y100F500S0"))
    powered = events.index(("xy", "X105S100"))
    assert approach < lowered < mode < runup < powered
    if routed_approach:
        assert events.index(("xy", "G0 X90 Y95 F1000")) < approach
    assert ("xy", machine._session.dialect.motion_barrier_command) not in events[mode:powered]
    assert all(label != "z" for label, _line in events[mode:powered])
