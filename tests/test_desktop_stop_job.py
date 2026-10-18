"""The actual desktop Stop job handler interrupts a fake controller queue."""
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from test_job_pause import pause, paused_machine  # noqa: F401
from test_machine_streaming import _finish, _program, _start

from laser_aligner.desktop.controller import DesktopController
from laser_aligner.errors import MachineError, SafetyError


@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("powered", [False, True])
def test_desktop_stop_aborts_pending_ack_and_revokes_restart(paused_machine, paused, powered):  # noqa: F811
    machine, transport = paused_machine()
    _, worker = _start(machine, _program(count=4, powered=powered))
    transport.wait_for_motion(4)  # The whole short path fits in the controller queue.
    if paused:
        pause(machine)
    notices, errors = [], []
    controller = SimpleNamespace(
        runtime=SimpleNamespace(context=SimpleNamespace(machine=machine)),
        stopInitiated=SimpleNamespace(emit=lambda: None),
        errorOccurred=SimpleNamespace(emit=errors.append),
        _machine_changed=notices.append,
    )
    DesktopController.cancel_job(controller)
    # No release of withheld ACKs or completion of queued work is necessary.
    result = _finish(machine, worker)
    assert result["phase"] == "failed" and result["error"]
    assert not errors and notices
    assert ("raw", b"!\x18") in transport.writes
    assert ("raw", b"~") not in transport.writes
    assert any(kind == "line" and value == "M5" for kind, value in transport.writes)
    assert not machine.armed and not machine._job_laser_authorized
    assert not machine._coordinate_reference_ready
    assert machine._last_successful_job is None
    with pytest.raises((MachineError, SafetyError)):
        machine.start_job("G21\nG90\nM5\nG1 X3 Y3 F300\nM5")
