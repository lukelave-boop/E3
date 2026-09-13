from __future__ import annotations

import threading
import time
import uuid
from typing import Any

import pytest

from laser_aligner.errors import MachineError
from laser_aligner.machine.pi_job_protocol import CAPABILITY_PI_CONTROL_OWNER
from laser_aligner.machine.pi_machine_server import (
    ACTION_MACHINE_CONNECT,
    ACTION_MACHINE_CONTROL_RELEASE,
    ACTION_MACHINE_DISCONNECT,
    ACTION_MACHINE_STATUS,
)
from tests.test_remote_machine_service import FakePi, _install_fake, _service


class OwnershipPi(FakePi):
    def __init__(self) -> None:
        super().__init__()
        self.capabilities.append(CAPABILITY_PI_CONTROL_OWNER)
        self.owner: str | None = None
        self.owner_revision = 0

    def _response(self, request: dict[str, Any], **body: Any) -> dict[str, Any]:
        return {
            **super()._response(request, **body),
            "control_owner_client_id": self.owner,
            "control_owner_revision": self.owner_revision,
            "control_owner_leased": self.owner is not None,
            "control_owner_lease_seconds": 30.0,
        }

    def __call__(self, host, port, token, request, **kwargs):
        action = request["action"]
        if action == ACTION_MACHINE_CONTROL_RELEASE:
            self.requests.append(dict(request))
            assert request["client_id"] == self.owner
            assert request["expected_control_owner_revision"] == self.owner_revision
            self.owner = None
            self.owner_revision += 1
            return self._response(request, released=True)
        if action == ACTION_MACHINE_CONNECT:
            if self.owner is not None and self.owner != request["client_id"]:
                self.requests.append(dict(request))
                return self._response(
                    request, ok=False, error="Pi is in use by another E3 app. Disconnect that app first.",
                    error_code="controller.in_use", retryable=False, action_required=None,
                )
            self.owner = request["client_id"]
            self.owner_revision += 1
        if action == ACTION_MACHINE_DISCONNECT:
            assert self.owner == request["client_id"]
            assert request["expected_control_owner_revision"] == self.owner_revision
            self.owner = None
            self.owner_revision += 1
        return super().__call__(host, port, token, request, **kwargs)


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("E3_BRIDGE_TOKEN", "remote-machine-test-token-value")


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("cleanup", ["disconnect", "shutdown", "detach"])
def test_observation_never_authorizes_controller_cleanup(monkeypatch, modern, cleanup):
    pi = OwnershipPi() if modern else FakePi()
    if modern:
        pi.owner = str(uuid.uuid4())
    _install_fake(monkeypatch, pi)
    observer = _service()
    observer.refresh_status()
    assert observer.connected
    if modern:
        assert observer.status()["control_in_use"]
        assert not observer.status()["control_owned"]
    pi.requests.clear()
    if cleanup == "shutdown":
        observer.detach(remember_idle_for_shutdown=True)
        observer.shutdown(deadline=time.monotonic() + 1.0)
    else:
        getattr(observer, cleanup)()
    assert pi.requests == []
    assert pi.connected


def test_negotiated_owner_renews_on_status_and_releases_without_disconnect(monkeypatch):
    pi = OwnershipPi()
    _install_fake(monkeypatch, pi)
    owner = _service()
    owner.connect()
    assert pi.requests[-1]["control_lease"] is True
    assert owner.status()["control_owned"]
    owner.refresh_status()
    assert pi.requests[-1]["action"] == ACTION_MACHINE_STATUS
    assert pi.requests[-1]["client_id"] == owner._client_id
    owner.detach()
    assert pi.requests[-1]["action"] == ACTION_MACHINE_CONTROL_RELEASE
    assert pi.owner is None
    assert pi.connected
    assert not owner.status()["control_owned"]


def test_second_app_rejection_preserves_first_owner_and_cleanup_is_inert(monkeypatch):
    pi = OwnershipPi()
    _install_fake(monkeypatch, pi)
    first, second = _service(), _service()
    first.connect()
    with pytest.raises(MachineError, match="Disconnect that app first"):
        second.connect()
    assert second.status()["control_in_use"]
    assert not second.status()["control_owned"]
    pi.requests.clear()
    second.shutdown(deadline=time.monotonic() + 1.0)
    assert pi.requests == []
    assert pi.owner == first._client_id
    assert pi.connected


def test_delayed_control_metadata_cannot_restore_released_ownership(monkeypatch):
    pi = OwnershipPi()
    _install_fake(monkeypatch, pi)
    owner = _service()
    owner.connect()
    old = pi._response({"request_id": "unused"})
    owner.detach()
    owner._accept_control_metadata(old)
    assert owner._control_owner_id is None
    assert not owner.status()["control_owned"]


def test_owner_shutdown_disconnects_once_after_observers_stop(monkeypatch):
    pi = OwnershipPi()
    _install_fake(monkeypatch, pi)
    owner = _service()
    owner.connect()
    owner.detach(remember_idle_for_shutdown=True)
    pi.requests.clear()
    owner.shutdown(deadline=time.monotonic() + 1.0)
    assert [request["action"] for request in pi.requests] == [ACTION_MACHINE_DISCONNECT]
    assert not pi.connected
    assert pi.owner is None


def _wait_until(predicate):
    end = time.monotonic() + 3.0
    while not predicate():
        assert time.monotonic() < end, "Timed out waiting for observer"
        time.sleep(.005)


def test_direct_connect_does_not_create_an_unrequested_monitor(monkeypatch):
    pi = OwnershipPi()
    _install_fake(monkeypatch, pi)
    owner = _service()
    owner.connect()
    assert owner._monitor_thread is None
    owner.detach()


def test_connect_after_detach_restores_app_monitoring_and_owner_renewal(monkeypatch):
    pi = OwnershipPi()
    _install_fake(monkeypatch, pi)
    owner = _service()
    owner.connect()
    owner.start_monitoring()
    try:
        _wait_until(lambda: any(r["action"] == ACTION_MACHINE_STATUS for r in pi.requests))
        owner.detach()
        assert owner._monitor_thread is None
        before = len(pi.requests)
        owner.connect()
        _wait_until(lambda: any(r["action"] == ACTION_MACHINE_STATUS for r in pi.requests[before:]))
        renewed = [r for r in pi.requests[before:] if r["action"] == ACTION_MACHINE_STATUS]
        assert all(r["client_id"] == owner._client_id for r in renewed)
        assert owner.status()["control_owned"]
        assert owner._monitor_thread.is_alive()
    finally:
        owner.detach()


def test_reattach_reuses_a_monitor_still_unwinding_after_bounded_detach(monkeypatch):
    pi = OwnershipPi()
    entered, release = threading.Event(), threading.Event()
    polling = [0, 0, 0]
    poll_lock = threading.Lock()

    def request(*args, **kwargs):
        if args[3]["action"] != ACTION_MACHINE_STATUS:
            return pi(*args, **kwargs)
        with poll_lock:
            polling[0] += 1
            polling[1] = max(polling[1], polling[0])
            polling[2] += 1
            first = polling[2] == 1
        try:
            if first:
                entered.set()
                assert release.wait(3.0)
            return pi(*args, **kwargs)
        finally:
            with poll_lock:
                polling[0] -= 1

    _install_fake(monkeypatch, request)
    owner = _service()
    owner.connect()
    owner.start_monitoring()
    try:
        assert entered.wait(2.0)
        original = owner._monitor_thread
        owner.detach(deadline=time.monotonic())
        assert owner._monitor_thread is original and original.is_alive()
        owner.connect()
        assert owner._monitor_thread is original
        release.set()
        _wait_until(lambda: polling[2] >= 2 and not owner.status()["status_stale"])
        assert polling[1] == 1
        assert owner.status()["control_owned"]
    finally:
        release.set()
        owner.detach()


def test_late_disconnect_reply_cannot_clear_a_new_control_claim(monkeypatch):
    pi = OwnershipPi()
    entered, release = threading.Event(), threading.Event()

    def request(*args, **kwargs):
        response = pi(*args, **kwargs)
        if args[3]["action"] == ACTION_MACHINE_DISCONNECT:
            entered.set()
            assert release.wait(3.0)
        return response

    _install_fake(monkeypatch, request)
    owner = _service()
    owner.connect()
    errors = []

    def disconnect():
        try:
            owner.disconnect()
        except MachineError as exc:
            errors.append(exc)

    worker = threading.Thread(target=disconnect)
    worker.start()
    try:
        assert entered.wait(2.0)
        owner.connect()
        assert owner.status()["control_owned"]
        release.set()
        worker.join(3.0)
        assert not worker.is_alive()
        assert owner.status()["control_owned"]
        assert owner._control_claim_attempted
    finally:
        release.set()
        worker.join(3.0)
        owner.detach()


def test_disconnect_binds_the_exact_negotiated_ownership_revision(monkeypatch):
    pi = OwnershipPi()
    _install_fake(monkeypatch, pi)
    owner = _service()
    owner.connect()
    revision = pi.owner_revision
    owner.disconnect()
    request = pi.requests[-1]
    assert request["action"] == ACTION_MACHINE_DISCONNECT
    assert request["expected_control_owner_revision"] == revision
    assert request["control_lease"] is True
    assert not pi.connected
