"""BENCH console tests use fake serial only; physical outputs are never accessed."""

from types import SimpleNamespace

import pytest

from firmware.ender_aux import bench, host

BENCH = "E3AUX1 APP 0.2.0 BOARD=0401E013 MODE=BENCH OUTPUTS=DISABLED"


def status(**changes):
    fields = {
        "FAN1": 0, "FAN2": 0, "PROBE": "STOWED", "TRIGGER": 0, "SOURCE": "SIM",
        "Z_UM": 10000, "TARGET_UM": 10000, "MOTION": "IDLE", "RESULT": "NONE", "OUTPUTS": "DISABLED",
    }
    fields.update(changes)
    return "BENCH " + " ".join(f"{key}={value}" for key, value in fields.items())


def handshake():
    return [("INFO", BENCH), ("STATUS", status())]


def cleanup():
    return [("STOP", "OK STOP"), ("STATUS", status(RESULT="STOPPED"))]


class FakePort:
    def __init__(self, exchanges):
        self.exchanges = list(exchanges)
        self.incoming = bytearray()
        self.commands = []
        self.closed = False

    def write(self, data):
        command = data.decode("ascii").removesuffix("\n")
        self.commands.append(command)
        assert self.exchanges, f"Unexpected command or retry: {command}"
        expected, response = self.exchanges.pop(0)
        assert command == expected
        if response is not None:
            self.incoming.extend((response + "\n").encode("ascii"))
        return len(data)

    def read(self, size):
        assert size == 1
        result = bytes(self.incoming[:1])
        del self.incoming[:1]
        return result

    def flush(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True


@pytest.fixture(autouse=True)
def no_real_hardware(monkeypatch):
    clock = SimpleNamespace(now=0.0)

    def tick():
        clock.now += 0.001
        return clock.now

    def forbidden(_name):
        pytest.fail("A bench test attempted to open hardware")

    monkeypatch.setattr(host, "time", SimpleNamespace(monotonic=tick))
    monkeypatch.setattr(host, "open_port", forbidden)


def install_port(monkeypatch, exchanges):
    port = FakePort(exchanges)
    opened = []
    monkeypatch.setattr(host, "open_port", lambda name: opened.append(name) or port)
    return port, opened


def install_input(monkeypatch, commands):
    pending = iter(commands)

    def enter(_prompt):
        try:
            value = next(pending)
        except StopIteration as exc:
            raise EOFError from exc
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr("builtins.input", enter)


@pytest.mark.parametrize("entered, expected", [
    ("status", "STATUS"), ("inputs", "INPUTS"), ("quit", "QUIT"), ("help", "HELP"),
    ("fan1 0", "SIM FAN 1 0"), ("fan1 100", "SIM FAN 1 100"), ("fan2 35", "SIM FAN 2 35"),
    ("deploy", "SIM PROBE DEPLOY"), ("stow", "SIM PROBE STOW"),
    ("trigger on", "SIM TRIGGER 1"), ("trigger off", "SIM TRIGGER 0"),
    ("source sim", "SIM SOURCE SIM"), ("source switch", "SIM SOURCE SWITCH"),
    ("position 0", "SIM Z SET 0"), ("position 200", "SIM Z SET 200000"),
    ("position 10.001", "SIM Z SET 10001"),
    ("move +10", "SIM Z MOVE 10000 1000"), ("move -0.001 0.1", "SIM Z MOVE -1 100"),
    ("move 3 0.1", "SIM Z MOVE 3000 100"), ("move -10 10", "SIM Z MOVE -10000 10000"),
    ("probe 10", "SIM Z PROBE 10000 1000"), ("probe .001 .1", "SIM Z PROBE 1 100"),
    ("reset", "SIM RESET"), ("stop", "STOP"),
])
def test_friendly_commands_translate_exactly(entered, expected):
    assert bench.translate(entered) == expected


@pytest.mark.parametrize("entered", [
    "", "M3 S100", "SIM FAN 1 50", "UPDATE", "BOOT", "BEGIN 0401E013 00000010 00000000",
    "fan1 -1", "fan2 101", "fan1 2.5", "fan1 1 extra", "fan3 5", "deploy extra",
    "trigger 1", "source gpio", "position -0.001", "position 200.001", "position nan",
    "position inf", "position 1e2", "position 0.0001", "position 1_000", "position 2 3",
    "move 0", "move 10.001", "move -10.001", "move 1 0.099", "move 1 10.001",
    "move 3.001 0.1", "move 1 nan", "move 1 1 extra", "probe 0", "probe -1",
    "probe 10.001", "probe 4 .1", "probe 1 0.0001", "x" * 161,
])
def test_invalid_or_raw_commands_are_rejected_locally(entered):
    with pytest.raises(host.FirmwareError):
        bench.translate(entered)


def test_status_parsing_and_readable_display():
    state = bench.parse_status(status(FAN1=30, FAN2=100, Z_UM=10501, TARGET_UM=15000, MOTION="MOVE"))
    assert state.fan1 == 30 and state.fan2 == 100
    assert state.z_um == 10501 and state.target_um == 15000
    assert "10.501 mm -> 15.000 mm" in state.describe()
    assert "Outputs DISABLED" in state.describe()


@pytest.mark.parametrize("response", [
    status(OUTPUTS="ENABLED"), status(FAN1=101), status(FAN2=-1), status(Z_UM=200001),
    status(TARGET_UM=200001), status(PROBE="UNKNOWN"), status(TRIGGER=2), status(SOURCE="AUTO"),
    status(MOTION="HOME"), status(RESULT="UNKNOWN"), status() + " extra", "OK STATUS", "",
])
def test_status_requires_exact_schema_bounds_and_disabled_outputs(response):
    with pytest.raises(host.FirmwareError):
        bench.parse_status(response)


def test_cli_requires_hardware_flag_before_open(capsys):
    assert bench.main(["--port", "COM6", "--status"]) == 1
    assert "--hardware-enabled" in capsys.readouterr().err


def test_cli_requires_explicit_port():
    with pytest.raises(SystemExit) as exc:
        bench.main(["--hardware-enabled", "--status"])
    assert exc.value.code == 2


@pytest.mark.parametrize("identity", [
    "E3AUX1 APP 0.1.0 BOARD=0401E013", "E3AUX1 UPDATER 0.1.0 BOARD=0401E013",
    "FIRMWARE_NAME:Marlin", BENCH.replace("DISABLED", "ENABLED"), BENCH.replace("0.2.0", "0.2.1"),
])
def test_refuse_nonbench_identity_without_stop_reset_update_or_status(identity, monkeypatch, capsys):
    port, opened = install_port(monkeypatch, [("INFO", identity)])
    assert bench.main(["--port", "COM6", "--hardware-enabled", "--status"]) == 1
    assert opened == ["COM6"] and port.closed
    assert port.commands == ["INFO"]
    assert "Stopped:" in capsys.readouterr().err


def test_status_acceptance_reads_physical_input_and_stops_before_close(monkeypatch, capsys):
    exchanges = handshake() + [("INPUTS", "INPUTS PROBE_PC14=1"), ("STATUS", status())] + cleanup()
    port, opened = install_port(monkeypatch, exchanges)
    assert bench.main(["--port", "COM6", "--hardware-enabled", "--status"]) == 0
    assert port.commands == [command for command, _ in exchanges]
    assert port.closed and opened == ["COM6"] and not port.exchanges
    output = capsys.readouterr()
    assert "Physical probe input PC14: 1" in output.out
    assert "Outputs DISABLED" in output.out and not output.err


@pytest.mark.parametrize("entered, wire, after", [
    ("fan1 25", "SIM FAN 1 25", status(FAN1=25)),
    ("fan2 50", "SIM FAN 2 50", status(FAN2=50)),
    ("deploy", "SIM PROBE DEPLOY", status(PROBE="DEPLOYED")),
    ("stow", "SIM PROBE STOW", status()),
    ("trigger on", "SIM TRIGGER 1", status(TRIGGER=1)),
    ("trigger off", "SIM TRIGGER 0", status()),
    ("source switch", "SIM SOURCE SWITCH", status(SOURCE="SWITCH")),
    ("position 50", "SIM Z SET 50000", status(Z_UM=50000, TARGET_UM=50000)),
    ("move -1 .5", "SIM Z MOVE -1000 500", status(TARGET_UM=9000, MOTION="MOVE")),
    ("probe 2", "SIM Z PROBE 2000 1000", status(PROBE="DEPLOYED", TARGET_UM=8000, MOTION="PROBE")),
    ("reset", "SIM RESET", status()),
])
def test_interactive_control_ack_then_status_and_one_cleanup_stop(entered, wire, after, monkeypatch):
    exchanges = handshake() + [(wire, "OK " + wire), ("STATUS", after)] + cleanup()
    port, opened = install_port(monkeypatch, exchanges)
    install_input(monkeypatch, [entered, "quit"])
    assert bench.main(["--port", "COM6", "--hardware-enabled"]) == 0
    assert port.commands == [command for command, _ in exchanges]
    assert port.commands.count("STOP") == 1
    assert opened == ["COM6"] and port.closed


@pytest.mark.parametrize("bad_ack", [None, "OK", "OK SIM FAN 1 25 extra", "ERR BUSY"])
def test_bad_command_ack_attempts_stop_once_and_closes_without_retry(bad_ack, monkeypatch, capsys):
    exchanges = handshake() + [("SIM FAN 1 25", bad_ack)] + cleanup()
    port, opened = install_port(monkeypatch, exchanges)
    install_input(monkeypatch, ["fan1 25", "fan2 50"])
    assert bench.main(["--port", "COM6", "--hardware-enabled"]) == 1
    assert port.commands == [command for command, _ in exchanges]
    assert opened == ["COM6"] and port.closed
    assert "no automatic retry" in capsys.readouterr().err


@pytest.mark.parametrize("after", [status(OUTPUTS="ENABLED"), status(MOTION="UNKNOWN"), None])
def test_bad_post_ack_status_stops_instead_of_continuing(after, monkeypatch):
    exchanges = handshake() + [("SIM FAN 1 25", "OK SIM FAN 1 25"), ("STATUS", after)] + cleanup()
    port, _ = install_port(monkeypatch, exchanges)
    install_input(monkeypatch, ["fan1 25", "fan2 50"])
    assert bench.main(["--port", "COM6", "--hardware-enabled"]) == 1
    assert port.commands == [command for command, _ in exchanges] and port.closed


def test_initial_bad_status_stops_only_after_exact_bench_identity(monkeypatch):
    exchanges = [("INFO", BENCH), ("STATUS", status(OUTPUTS="ENABLED"))] + cleanup()
    port, _ = install_port(monkeypatch, exchanges)
    assert bench.main(["--port", "COM6", "--hardware-enabled", "--status"]) == 1
    assert port.commands == [command for command, _ in exchanges] and port.closed


@pytest.mark.parametrize("input_reply", ["INPUTS PROBE_PC14=2", "INPUTS PROBE_PC14=0 extra", None])
def test_bad_input_response_stops_and_closes(input_reply, monkeypatch):
    exchanges = handshake() + [("INPUTS", input_reply)] + cleanup()
    port, _ = install_port(monkeypatch, exchanges)
    assert bench.main(["--port", "COM6", "--hardware-enabled", "--status"]) == 1
    assert port.commands == [command for command, _ in exchanges] and port.closed


@pytest.mark.parametrize("exit_action, expected_code", [("quit", 0), (EOFError(), 0), (KeyboardInterrupt(), 130)])
def test_exit_paths_attempt_stop_once_and_close(exit_action, expected_code, monkeypatch):
    port, _ = install_port(monkeypatch, handshake() + cleanup())
    install_input(monkeypatch, [exit_action])
    assert bench.main(["--port", "COM6", "--hardware-enabled"]) == expected_code
    assert port.commands.count("STOP") == 1 and port.closed


@pytest.mark.parametrize("stop_ack", [None, "ERR STOP"])
def test_failed_explicit_stop_is_not_retried_during_cleanup(stop_ack, monkeypatch, capsys):
    port, _ = install_port(monkeypatch, handshake() + [("STOP", stop_ack)])
    install_input(monkeypatch, ["stop", "quit"])
    assert bench.main(["--port", "COM6", "--hardware-enabled"]) == 1
    assert port.commands.count("STOP") == 1 and port.closed
    assert "Stopped:" in capsys.readouterr().err


def test_cleanup_stop_failure_is_reported_without_retry(monkeypatch, capsys):
    port, _ = install_port(monkeypatch, handshake() + [("STOP", None)])
    install_input(monkeypatch, ["quit"])
    assert bench.main(["--port", "COM6", "--hardware-enabled"]) == 1
    assert port.commands.count("STOP") == 1 and port.closed
    assert "STOP could not be confirmed" in capsys.readouterr().err


def test_unknown_interactive_command_never_reaches_serial(monkeypatch):
    port, _ = install_port(monkeypatch, handshake() + cleanup())
    install_input(monkeypatch, ["M3 S100"])
    assert bench.main(["--port", "COM6", "--hardware-enabled"]) == 1
    assert port.commands == ["INFO", "STATUS", "STOP", "STATUS"] and port.closed


def test_session_has_no_raw_command_passthrough():
    port = FakePort(handshake())
    session = bench.Session(port)
    session.connect()
    with pytest.raises(host.FirmwareError):
        session.command("SIM Z MOVE 999999 1000")
    assert port.commands == ["INFO", "STATUS"]
