"""The frozen 0.7.132 Windows client delegates speed to the combined Pi backend."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tests import test_laser_focus as focus_helpers
from tests import test_pi_machine_server as server_helpers

focus = focus_helpers.focus
server_harness = server_helpers.server_harness
FROZEN_CLIENT = "92a7a6149713c7218fdc49de42336d660f24c48a"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def frozen_client(tmp_path_factory):
    if subprocess.run(["git", "cat-file", "-e", FROZEN_CLIENT], cwd=ROOT,
                      capture_output=True).returncode:
        pytest.skip("The exact frozen 0.7.132 source revision is needed for cross-version verification")
    destination = tmp_path_factory.mktemp("frozen-0132-client")
    payload = subprocess.check_output(
        ["git", "archive", "--format=zip", FROZEN_CLIENT, "laser_aligner"], cwd=ROOT,
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert all((destination / name).resolve().is_relative_to(destination) for name in archive.namelist())
        archive.extractall(destination)
    assert not (destination / "laser_aligner/machine/setup_motion.py").exists()
    return destination


CLIENT_SCRIPT = r'''
import json
import sys
import time
from laser_aligner.config import LaserSettings, MachineSettings, WorkArea
from laser_aligner.errors import MachineError
from laser_aligner.machine.remote_service import RemoteMachineService

settings = MachineSettings(backend="serial", protocol="grbl",
    port="e3bridge://127.0.0.1:" + sys.argv[1], allow_motion=True,
    work_area=WorkArea(0, 220, 0, 220), photo_x=110, photo_y=110,
    home_and_release_after_powered_job=True, controller_startup_delay=0.0)
client = RemoteMachineService(settings, LaserSettings(arm_timeout_seconds=60), hardware_enabled=True)
client.connect()
client.start_monitoring()
client.prepare_photo_position()

def focus(action, **values):
    return client.focus_control(action, confirmed=True, **values)

def refused(callback, text):
    try:
        callback()
    except MachineError as error:
        assert text.lower() in str(error).lower(), str(error)
        return str(error)
    raise AssertionError("A forbidden action was accepted")

if sys.argv[2] == "old_firmware":
    errors = [refused(lambda: focus("reference"), "setup speed"),
              refused(lambda: client.mainboard_control("z_jog", 1, confirmed=True), "before moving Z")]
    print(json.dumps({"errors": errors}))
    client.detach()
    raise SystemExit()

stages = {}
stages["reference"] = focus("reference")
stages["manual_up"] = client.mainboard_control("z_jog", 5, confirmed=True)
stages["manual_down"] = client.mainboard_control("z_jog", -5, confirmed=True)
refused(lambda: client.mainboard_control("z_jog", 6, confirmed=True), "5")
# A separate app can observe and STOP, but cannot acquire this app's controller.
observer = RemoteMachineService(settings, LaserSettings(), hardware_enabled=True)
observer.refresh_status()
refused(observer.connect, "Another E3 app")
refused(lambda: observer.mainboard_control("z_jog", 1, confirmed=True), "control")
observer.detach()
# Manual movement invalidates focus context, so establish the border again.
focus("reference")
focus("set_xy_offset", value=[3, -4])
stages["probe_xy"] = focus("position_probe", value=[80, 80])
measured = focus("measure")
token = measured["surface"]["id"]
stages["measure"] = measured
stages["laser_xy"] = focus("align_laser", measurement_id=token)
stages["coarse_down"] = focus("jog", value=-5, measurement_id=token)
stages["fine_down"] = focus("jog", value=-.1, measurement_id=token)
stages["fine_up"] = focus("jog", value=.1, measurement_id=token)
focus("teach", measurement_id=token)
preview = focus("preview", measurement_id=token, gap_mm=5)["preview"]
stages["preview_move"] = focus("move", preview_id=preview["id"], gap_mm=5)
stages["clearance"] = focus("clearance")
focus("align_probe")
measured = focus("measure")
assert measured["job_focus"]["target_z_mm"] == 28
program_text = "G21\nG90\nM5\nG0 X100 Y100 F1000\nM4 S100\nG1 X105 Y100 F500\nM5"
job_ids = []
for _ in range(2):
    client.refresh_status()
    program = client.preflight_program(program_text)
    assert program.lines[0] == "E3FOCUS " + measured["job_focus"]["id"]
    started = client.start_preflighted_program(program, authorization_phrase=client.ARM_PHRASE)
    assert started["accepted"]
    job_ids.append(started["job_id"])
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = client.refresh_status()
        job = status["job"]
        if job.get("job_id") == started["job_id"] and job.get("state") == "complete":
            break
        assert job.get("state") not in {"failed", "stopped", "interrupted"}, job
        time.sleep(.02)
    else:
        raise AssertionError("Remote job did not complete: " + repr(status))
assert client.focus_control("status")["current_readback"]["z_mm"] == 30
print(json.dumps({"stages": stages, "job_ids": job_ids}))
client.detach()
'''


def run_client(source, harness, mode):
    environment = os.environ.copy()
    environment.update(PYTHONPATH=str(source), E3_BRIDGE_TOKEN=server_helpers._TOKEN)
    completed = subprocess.run(
        [sys.executable, "-c", CLIENT_SCRIPT, str(harness.server.bound_port), mode],
        cwd=source, env=environment, text=True, capture_output=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout.splitlines()[-1])


def z_moves(result):
    return [entry["command"] for entry in result["transcript"] if entry["command"].startswith("G1 Z")]


def configure_backend(harness, focus):
    harness.machine._secondary_air_assist = focus.fan
    harness.machine._z_probe = focus.probe
    harness.machine._laser_focus = focus.state
    harness.machine.settings.home_and_release_after_powered_job = True
    focus.serial.contacts = [0, 0, 5, 8]


def test_frozen_0132_client_drives_fast_setup_and_repeated_pi_owned_jobs(
    frozen_client, server_harness, focus, monkeypatch,
):
    configure_backend(server_harness, focus)
    events = []
    for device, label in ((server_harness.transport, "xy"), (focus.serial, "z")):
        original = device.write_line

        def write(line, original=original, label=label):
            events.append((label, line))
            return original(line)

        monkeypatch.setattr(device, "write_line", write)
    result = run_client(frozen_client, server_harness, "accept")
    stages = result["stages"]
    assert z_moves(stages["reference"]) == [
        "G1 Z5.000 F1200", "G1 Z20.000 F1200", "G1 Z20.000 F1200", "G1 Z30.000 F1200",
    ]
    for stage, command in {
        "manual_up": "G1 Z35.000 F1200", "manual_down": "G1 Z30.000 F600",
        "measure": "G1 Z30.000 F1200", "coarse_down": "G1 Z25.000 F600",
        "fine_down": "G1 Z24.900 F120", "fine_up": "G1 Z25.000 F1200",
        "preview_move": "G1 Z23.000 F600", "clearance": "G1 Z30.000 F1200",
    }.items():
        assert z_moves(stages[stage]) == [command]
    for stage, command in (("probe_xy", "G1 X77.000 Y84.000 F3000.000"),
                           ("laser_xy", "G1 X80.000 Y80.000 F3000.000")):
        assert command in [entry["command"] for entry in stages[stage]["transcript"]]
    assert len(result["job_ids"]) == 2
    assert all(server_harness.service.get(identifier)["state"] == "complete" for identifier in result["job_ids"])
    powered = [index for index, event in enumerate(events) if event == ("xy", "M4 S100")]
    assert len(powered) == 2
    for index in powered:
        before = events[:index]
        approach = max(i for i, event in enumerate(before) if event == ("xy", "G0 X100 Y100 F1000"))
        assert ("z", "G1 Z28.000 F600") in before[approach + 1:]
        after = events[index + 1:]
        off = after.index(("xy", "M5"))
        lift = after.index(("z", "G1 Z30.000 F1200"))
        home = after.index(("xy", "$H"))
        assert off < lift < home
    assert focus.serial.z == 30 and not focus.state.requires_clearance


@pytest.mark.parametrize("capability", [None, "Cap:E3_Z_SETUP_SPEED_V1:0", "Cap:E3_Z_SETUP_SPEED_V1:1junk"])
def test_frozen_0132_client_rejects_unsupported_firmware_before_z_travel(
    frozen_client, server_harness, focus, capability,
):
    configure_backend(server_harness, focus)
    identity = [line for line in focus_helpers.IDENTITY if not line.startswith("Cap:E3_Z_SETUP_SPEED")]
    focus.serial.overrides["M115"] = identity[:-1] + ([capability] if capability else []) + ["ok"]
    result = run_client(frozen_client, server_harness, "old_firmware")
    assert len(result["errors"]) == 2
    assert not any(line.startswith(("G1 Z", "G28 ", "G39")) for line in focus.serial.writes)
