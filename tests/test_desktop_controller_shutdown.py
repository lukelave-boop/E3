from __future__ import annotations

from types import SimpleNamespace

import pytest

from laser_aligner.desktop.controller import DesktopController


class _Timer:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


class _Signal:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def emit(self, message: str) -> None:
        self.messages.append(message)


class _Machine:
    def __init__(self, *, pi_owned_execution: bool) -> None:
        self.pi_owned_execution = pi_owned_execution
        # Model a fresh remote client whose first status refresh has not run.
        self.pi_owned_job_active = False
        self.detach_calls = 0
        self.stop_calls: list[bool] = []

    def detach(self) -> None:
        self.detach_calls += 1

    def request_stop(self, *, emergency: bool = False) -> None:
        self.stop_calls.append(emergency)


def _controller(machine: _Machine) -> SimpleNamespace:
    return SimpleNamespace(
        _shutdown_started=False,
        _poll_timer=_Timer(),
        _camera_live_timer=_Timer(),
        _trace_request_id=0,
        _trace_review_active=True,
        _trace_sample_image=object(),
        _trace_sample_area=object(),
        _trace_sample_signature=object(),
        _template_match_request_id=0,
        _template_review_active=True,
        _template_review_signature=object(),
        runtime=SimpleNamespace(context=SimpleNamespace(machine=machine)),
        errorOccurred=_Signal(),
    )


@pytest.mark.parametrize(
    ("pi_owned_execution", "expected_detaches", "expected_stops"),
    [(True, 1, []), (False, 0, [False])],
)
def test_begin_shutdown_never_stops_remote_execution_from_an_empty_cache(
    pi_owned_execution: bool,
    expected_detaches: int,
    expected_stops: list[bool],
) -> None:
    machine = _Machine(pi_owned_execution=pi_owned_execution)
    controller = _controller(machine)

    DesktopController.begin_shutdown(controller)
    DesktopController.begin_shutdown(controller)

    assert controller._shutdown_started is True
    assert controller._poll_timer.stop_calls == 1
    assert controller._camera_live_timer.stop_calls == 1
    assert machine.detach_calls == expected_detaches
    assert machine.stop_calls == expected_stops
    assert controller.errorOccurred.messages == []
