import hashlib
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

import laser_aligner.remote_node as remote_node
from laser_aligner.air_assist import AirAssistMode, AirAssistSettings
from laser_aligner.errors import MachineError
from laser_aligner.machine.controller_dialects import resolve_air_assist_commands
from laser_aligner.machine.pi_job_store import (
    JobValidation,
    PendingSecondaryRecovery,
    PiJobStore,
)


def _secondary_binding(port: str = "/dev/serial/by-id/old-fan"):
    binding = resolve_air_assist_commands(
        AirAssistSettings(
            mode=AirAssistMode.SECONDARY_MARLIN_FAN,
            port=port,
            baudrate=115200,
        ),
        protocol="grbl",
    )
    assert binding is not None
    return binding


def test_remote_node_requires_explicit_hardware_gate() -> None:
    with pytest.raises(SystemExit) as exc:
        remote_node.main(["--config", "unused.json"])
    assert exc.value.code == 2


def test_remote_node_hosts_one_local_machine_service_and_no_raw_bridge(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        machine=SimpleNamespace(
            backend="serial",
            port="/dev/ttyUSB0",
            protocol="grbl",
        ),
        laser=object(),
        camera=object(),
        app=SimpleNamespace(data_dir=tmp_path / "data"),
        ensure_directories=lambda: (tmp_path / "data").mkdir(
            parents=True,
            exist_ok=True,
        ),
    )
    monkeypatch.setattr(remote_node, "load_settings", lambda _path: settings)
    monkeypatch.setattr(
        remote_node,
        "camera_token_from_environment",
        lambda: "x" * 32,
    )
    constructed: list[tuple[object, ...]] = []
    machine = object()
    class FakeStore:
        def pending_secondary_recoveries(self) -> tuple[object, ...]:
            return ()

    store = FakeStore()

    def machine_service(*args, **kwargs):
        constructed.append(("machine", args, kwargs))
        return machine

    def job_store(root):
        constructed.append(("store", root))
        return store

    class FakeJobService:
        def __init__(self, received_machine, received_store) -> None:
            assert received_machine is machine
            assert received_store is store
            self.shutdown_calls: list[bool] = []
            constructed.append(("job-service", self))

        def shutdown(self, *, stop_machine: bool = True) -> None:
            self.shutdown_calls.append(stop_machine)

    class FakeServer:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs
            self.started = threading.Event()
            self.stopped = threading.Event()
            self.exit = threading.Event()
            self.client_detached = threading.Event()

        def serve_forever(self) -> None:
            self.started.set()
            self.exit.wait(timeout=5.0)

        def stop(self) -> None:
            self.stopped.set()
            self.exit.set()

        def simulate_client_detach(self) -> None:
            # Client-session loss is handled inside each bridge server and must
            # not be confused with the service thread itself exiting.
            self.client_detached.set()

    machine_servers: list[FakeServer] = []
    camera_servers: list[FakeServer] = []

    def machine_server(*args, **kwargs):
        server = FakeServer(*args, **kwargs)
        machine_servers.append(server)
        return server

    def camera_server(*args, **kwargs):
        server = FakeServer(*args, **kwargs)
        camera_servers.append(server)
        return server

    monkeypatch.setattr(remote_node, "MachineService", machine_service)
    monkeypatch.setattr(remote_node, "PiJobStore", job_store)
    monkeypatch.setattr(remote_node, "PiJobService", FakeJobService)
    monkeypatch.setattr(remote_node, "PiMachineServer", machine_server)
    monkeypatch.setattr(remote_node, "CameraService", lambda _settings: object())
    monkeypatch.setattr(remote_node, "CameraBridgeServer", camera_server)

    failures: list[BaseException] = []

    def run() -> None:
        try:
            remote_node.main(["--hardware", "--config", "pi.json"])
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 3.0
    while (
        (not machine_servers or not camera_servers)
        or not machine_servers[0].started.is_set()
        or not camera_servers[0].started.is_set()
    ) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(machine_servers) == 1
    assert len(camera_servers) == 1
    assert machine_servers[0].args[0].__class__ is FakeJobService
    assert machine_servers[0].kwargs["token"] == "x" * 32
    assert camera_servers[0].kwargs["token"] == "x" * 32

    camera_servers[0].simulate_client_detach()
    assert camera_servers[0].client_detached.wait(timeout=1.0)
    time.sleep(0.05)
    job_service = next(entry[1] for entry in constructed if entry[0] == "job-service")
    assert worker.is_alive()
    assert not machine_servers[0].stopped.is_set()
    assert job_service.shutdown_calls == []

    # Only loss of the camera service thread itself tears down the combined
    # node and invokes the Pi-local machine shutdown path.
    camera_servers[0].exit.set()
    worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert failures and isinstance(failures[0], RuntimeError)
    assert machine_servers[0].stopped.is_set()
    assert job_service.shutdown_calls == [True]
    assert [entry[0] for entry in constructed].count("machine") == 1
    machine_call = next(entry for entry in constructed if entry[0] == "machine")
    assert machine_call[2]["secondary_air_assist"] is None
    assert all("BridgeServer" not in str(entry) for entry in constructed)


def test_secondary_controller_binding_is_pi_local_and_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        machine=SimpleNamespace(
            port="/dev/ttyUSB0",
            air_assist=AirAssistSettings(
                mode=AirAssistMode.SECONDARY_MARLIN_FAN,
                port="/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
                baudrate=115200,
            ),
        )
    )
    constructed: list[tuple[object, ...]] = []

    class FakeOwner:
        def __init__(self, port: str, baudrate: int) -> None:
            constructed.append(("owner", port, baudrate))

    class FakeFan:
        def __init__(self, owner: object, binding: object) -> None:
            constructed.append(("fan", owner, binding))

    monkeypatch.setattr(remote_node, "CrealityControllerOwner", FakeOwner)
    monkeypatch.setattr(remote_node, "SecondaryMarlinFanController", FakeFan)

    result = remote_node._secondary_air_assist_for_settings(
        settings,
        protocol="grbl",
    )

    assert isinstance(result, FakeFan)
    assert constructed[0] == (
        "owner",
        "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
        115200,
    )
    binding = constructed[1][2]
    assert binding.on_commands == ("M106 S255",)
    assert binding.off_commands == ("M106 S0",)


def test_secondary_controller_rejects_primary_device_alias() -> None:
    settings = SimpleNamespace(
        machine=SimpleNamespace(
            port="/dev/ttyUSB0",
            air_assist=AirAssistSettings(
                mode=AirAssistMode.SECONDARY_MARLIN_FAN,
                port="/dev/ttyUSB0",
                baudrate=115200,
            ),
        )
    )

    with pytest.raises(MachineError, match="different serial devices"):
        remote_node._secondary_air_assist_for_settings(settings, protocol="grbl")


def test_restart_recovery_uses_persisted_binding_and_deduplicates_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _secondary_binding("/dev/serial/by-id/accepted-old-fan")
    events: list[object] = []

    class FakeStore:
        def pending_secondary_recoveries(self):
            return (
                PendingSecondaryRecovery("job-a", binding),
                PendingSecondaryRecovery("job-b", binding),
            )

        def clear_secondary_recovery(self, job_id: str, *, acknowledged_binding):
            events.append(("clear", job_id, acknowledged_binding))
            return {}

    class FakeOwner:
        def __init__(self, port: str, baudrate: int) -> None:
            events.append(("owner", port, baudrate))

        def close(self) -> None:
            events.append("owner-close")

    class FakeFan:
        def __init__(self, owner: object, received_binding: object) -> None:
            assert isinstance(owner, FakeOwner)
            assert received_binding == binding

        def initialize_off(self) -> None:
            events.append("off-ack")

    monkeypatch.setattr(remote_node, "CrealityControllerOwner", FakeOwner)
    monkeypatch.setattr(remote_node, "SecondaryMarlinFanController", FakeFan)

    # Recovery is independent of a changed or disabled current Air Assist config.
    remote_node._recover_pending_secondary_air_assist(
        FakeStore(),
        primary_port="/dev/serial/by-id/current-primary",
    )

    assert events.count("off-ack") == 1
    assert events[0] == ("owner", binding.port, binding.baudrate)
    assert ("clear", "job-a", binding) in events
    assert ("clear", "job-b", binding) in events


def test_disabled_current_config_restart_recovers_persisted_old_binding(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "jobs"
    store = PiJobStore(root)
    job_id = str(uuid.uuid4())
    payload = b"G21\nG90\nM5\n"
    digest = hashlib.sha256(payload).hexdigest()
    store.begin(
        job_id,
        name="accepted-old.gcode",
        expected_size=len(payload),
        expected_sha256=digest,
    )
    store.append_chunk(job_id, offset=0, data=payload)
    store.finalize(
        job_id,
        validator=lambda _text, _polygon: JobValidation(
            program_digest=digest,
            requires_laser_authorization=False,
            requires_motion=False,
            execution_policy_digest="e" * 64,
        ),
    )
    binding = _secondary_binding("/dev/serial/by-id/accepted-old-fan")
    store.begin_execution(job_id, secondary_recovery_binding=binding)
    store.update_state(job_id, "running", ownership_accepted=True)
    restarted = PiJobStore(root)
    events: list[object] = []

    class FakeOwner:
        def __init__(self, port: str, baudrate: int) -> None:
            events.append(("open", port, baudrate))

        def close(self) -> None:
            events.append("close")

    class FakeFan:
        def __init__(self, _owner: object, received_binding: object) -> None:
            assert received_binding == binding

        def initialize_off(self) -> None:
            events.append("off-ack")

    monkeypatch.setattr(remote_node, "CrealityControllerOwner", FakeOwner)
    monkeypatch.setattr(remote_node, "SecondaryMarlinFanController", FakeFan)

    # No current Air Assist controller is needed; recovery uses the old record.
    remote_node._recover_pending_secondary_air_assist(
        restarted,
        primary_port="/dev/serial/by-id/current-primary",
    )

    assert restarted.get(job_id)["state"] == "interrupted"
    assert restarted.pending_secondary_recoveries() == ()
    assert events == [
        ("open", binding.port, binding.baudrate),
        "off-ack",
        "close",
    ]


def test_failed_restart_recovery_remains_pending_and_retries_next_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _secondary_binding()
    attempts: list[str] = []

    class FakeStore:
        def pending_secondary_recoveries(self):
            return (PendingSecondaryRecovery("job-a", binding),)

        def clear_secondary_recovery(self, *_args: object, **_kwargs: object):
            pytest.fail("failed OFF must not clear recovery authority")

    class FakeOwner:
        def __init__(self, _port: str, _baudrate: int) -> None:
            attempts.append("open")

        def close(self) -> None:
            attempts.append("close")

    class FakeFan:
        def __init__(self, _owner: object, _binding: object) -> None:
            pass

        def initialize_off(self) -> None:
            attempts.append("off")
            raise MachineError("no acknowledgement")

    monkeypatch.setattr(remote_node, "CrealityControllerOwner", FakeOwner)
    monkeypatch.setattr(remote_node, "SecondaryMarlinFanController", FakeFan)
    store = FakeStore()

    remote_node._recover_pending_secondary_air_assist(
        store,
        primary_port="/dev/ttyPRIMARY",
    )
    remote_node._recover_pending_secondary_air_assist(
        store,
        primary_port="/dev/ttyPRIMARY",
    )

    assert attempts == ["open", "off", "close", "open", "off", "close"]


@pytest.mark.parametrize(
    "recovery_port",
    ["/dev/serial/by-id/current-primary", "e3bridge://127.0.0.1:8765"],
)
def test_restart_recovery_refuses_primary_alias_and_bridge_uri(
    recovery_port: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _secondary_binding(recovery_port)

    class FakeStore:
        def pending_secondary_recoveries(self):
            return (PendingSecondaryRecovery("job-a", binding),)

        def clear_secondary_recovery(self, *_args: object, **_kwargs: object):
            pytest.fail("refused recovery must remain pending")

    monkeypatch.setattr(
        remote_node,
        "CrealityControllerOwner",
        lambda *_args, **_kwargs: pytest.fail("refused recovery must not open serial"),
    )

    remote_node._recover_pending_secondary_air_assist(
        FakeStore(),
        primary_port="/dev/serial/by-id/current-primary",
    )


def test_startup_off_failure_is_degraded_and_secondary_closes_after_machine_shutdown(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    settings = SimpleNamespace(
        machine=SimpleNamespace(
            backend="serial",
            port="/dev/ttyUSB0",
            protocol="grbl",
        ),
        laser=object(),
        camera=object(),
        app=SimpleNamespace(data_dir=tmp_path / "data"),
        ensure_directories=lambda: (tmp_path / "data").mkdir(
            parents=True,
            exist_ok=True,
        ),
    )

    class FakeFan:
        def initialize_off(self) -> None:
            events.append("secondary-initialize")
            raise MachineError("no ack")

        def close(self) -> None:
            events.append("secondary-close")

    fan = FakeFan()

    def machine_service(*_args: object, **kwargs: object) -> object:
        assert kwargs["secondary_air_assist"] is fan
        events.append("machine-created")
        return object()

    class FakeStore:
        def __init__(self) -> None:
            events.append("store-reconcile")

        def pending_secondary_recoveries(self) -> tuple[object, ...]:
            return ()

        def reconcile_boot(self) -> list[object]:
            events.append("store-reconcile-repeat")
            return []

    class FakeJobService:
        def __init__(self, _machine: object, store: FakeStore) -> None:
            store.reconcile_boot()
            events.append("job-service-created")

        def shutdown(self, *, stop_machine: bool = True) -> None:
            assert stop_machine is True
            events.append("machine-shutdown")

    class FakeServer:
        def __init__(self, label: str, *, fail_stop: bool = False) -> None:
            self.label = label
            self.fail_stop = fail_stop
            events.append(f"{label}-server-created")

        def serve_forever(self) -> None:
            return

        def stop(self) -> None:
            events.append(f"{self.label}-server-stop")
            if self.fail_stop:
                raise MachineError("machine server stop failed")

    monkeypatch.setattr(remote_node, "load_settings", lambda _path: settings)
    monkeypatch.setattr(
        remote_node,
        "_secondary_air_assist_for_settings",
        lambda *_args, **_kwargs: fan,
    )
    monkeypatch.setattr(
        remote_node,
        "camera_token_from_environment",
        lambda: "x" * 32,
    )
    monkeypatch.setattr(remote_node, "MachineService", machine_service)
    monkeypatch.setattr(remote_node, "PiJobStore", lambda _root: FakeStore())
    monkeypatch.setattr(remote_node, "PiJobService", FakeJobService)
    monkeypatch.setattr(
        remote_node,
        "PiMachineServer",
        lambda *_a, **_k: FakeServer("machine", fail_stop=True),
    )
    monkeypatch.setattr(remote_node, "CameraService", lambda _settings: object())
    monkeypatch.setattr(
        remote_node,
        "CameraBridgeServer",
        lambda *_a, **_k: FakeServer("camera"),
    )

    with pytest.raises(MachineError, match="machine server stop failed"):
        remote_node.main(["--hardware", "--config", "pi.json"])

    assert events.index("store-reconcile") < events.index("machine-created")
    assert events.index("machine-created") < events.index("store-reconcile-repeat")
    assert events.index("store-reconcile-repeat") < events.index(
        "secondary-initialize"
    )
    assert events.index("secondary-initialize") < events.index(
        "machine-server-created"
    )
    assert events.index("machine-server-stop") < events.index("machine-shutdown")
    assert events.index("machine-shutdown") < events.index("camera-server-stop")
    assert events.index("machine-shutdown") < events.index("secondary-close")
