import threading
import time
from types import SimpleNamespace

import pytest

import laser_aligner.remote_node as remote_node


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
    store = object()

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
    assert all("BridgeServer" not in str(entry) for entry in constructed)
