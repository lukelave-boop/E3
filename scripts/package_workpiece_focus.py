"""Build the hash-checked Pi companion for automatic, reusable workpiece focus."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

if __package__:
    from .package_laser_focus import PREVIOUS as INSTALLED_FOCUS
    from .package_laser_focus import PREVIOUS_REVISION as INSTALLED_FOCUS_REVISION
else:
    from package_laser_focus import PREVIOUS as INSTALLED_FOCUS
    from package_laser_focus import PREVIOUS_REVISION as INSTALLED_FOCUS_REVISION

ROOT = Path(__file__).resolve().parents[1]
SAVED_Z_REVISION = "c638b3683faf83b10bb8483dd53fa8a4726b25c8"
# Exact authoritative saved-Z sources; the earlier installed focus correction
# is independently pinned by package_laser_focus. Never accept arbitrary edits.
SAVED_Z = {
    "laser_aligner/config.py": "f12b0135c68ee8e787cf316294c15a51451f9ec64338b85f2db9f0c2929f9f58",
    "laser_aligner/machine/mainboard.py": "0388228c3ded64c5b424f6da163d6260bbc90a0f1161b19a8a9c88062cea4eba",
    "laser_aligner/machine/service.py": "79a25bbdc25bc6f16e14ca47ab9f8b425bad67db02b8a51fb73df7e8f0e11b72",
    "laser_aligner/machine/remote_service.py": "86d07386235f676467ed710b3901828a796ec45ae278285cde5384457aebb3a3",
    "laser_aligner/machine/pi_job_service.py": "f36f0841b67c48074dacefad05a1de242325675d398ce7eed12301f651d07da3",
    "laser_aligner/machine/pi_machine_server.py": "b37be2d367a1fd19e30abdc977c6db44d269939b1ea70b362ddd3aabd8d9be10",
    "laser_aligner/remote_node.py": "474325070988bf6e0323a4c8874e843438166ad81e569d9d862de580e7cf7088",
    "laser_aligner/machine/z_limits.py": "c1ccb0ec6f82d7489d49dd50bdaf295a75a20b22117d3cad0ee56298c3b00310",
    "laser_aligner/machine/z_probe.py": "393a2d2c5e791754fed2a14fd706548df4b57bb96cc7fc46ba71d31101c96ece",
    "laser_aligner/machine/secondary_controller.py": "788420240b542f1280343fc0677590510d2b13c395988871d2b75e67d88b08f2",
    "laser_aligner/machine/secondary_startup.py": "d7c6bc19862d8f3dd5fd638581860057189956e4314157efb92f317298eb258b",
    "laser_aligner/machine/laser_focus.py": "c248c4dfaca19be02dd7028097a617aaf2cdd76d1cea1940fc4bf8aa88d3abd9",
    "laser_aligner/machine/focus_bounds.py": "9d2f2403c38f50bf20b35bf364de54747128c131bb816ae46534ce6bceb93d5c",
    "laser_aligner/machine/job_focus.py": "cb1d8d1e3d918f66d943b5b8930d09bb482af1ccec2f43047584cbb6bc1a4397",
    "laser_aligner/machine/z_retention.py": "f6f5ac717b12272365b8635b4c4874824e644d42594a99e0e7c94b1dd88ee812",
}
# The Pi received e57adbb5, then only the four 12dbb3d files and two 59c31d7
# files. Those recorded installations did not replace the desktop remote
# client. Pin the whole installed combination rather than allow arbitrary mixing.
# The remote client bytes match Git 050498c0e31d7778c161688c9c232224aceb2717.
RECORDED_INSTALLED_FOCUS_NAME = "installed-e57adbb5-12dbb3d-59c31d7"
RECORDED_INSTALLED_FOCUS = {
    **INSTALLED_FOCUS,
    "laser_aligner/machine/remote_service.py": "d9a8165484133bd720dbb03515590b5de28890ff8f32cf02baa73d7052bf21aa",
}
PREDECESSORS = {
    INSTALLED_FOCUS_REVISION: INSTALLED_FOCUS,
    SAVED_Z_REVISION: SAVED_Z,
    RECORDED_INSTALLED_FOCUS_NAME: RECORDED_INSTALLED_FOCUS,
}


def installation_guide(folder: Path) -> str:
    # These are the existing operator-documented Pi login and project location.
    # Generate the package path from the actual artifact, never a placeholder.
    windows_source = str(folder.resolve()).replace("'", "''")
    return (
        "# Install the matching workpiece-focus companion\n\n"
        "This package updates application sources only. It does not flash firmware; "
        "automatic workpiece focus works with existing surface-height V2 firmware. "
        "Saved-Z restoration still requires its separate firmware capability.\n\n"
        "Copy the exact folder from Windows PowerShell:\n\n"
        "```powershell\n"
        f"scp -r '{windows_source}' greenhouse-climate@192.168.5.18:/home/greenhouse-climate/\n"
        "```\n\n"
        "In Pi Bash, inspect the planned source changes first. This command is read-only "
        "and does not send controller commands:\n\n"
        "```sh\n"
        "e3_project=/home/greenhouse-climate/Projects/laser-camera-aligner\n"
        f"e3_workpiece=/home/greenhouse-climate/{folder.name}\n"
        '"$e3_project/.venv/bin/python" "$e3_workpiece/install_workpiece_focus.py" '
        '--project "$e3_project"\n'
        "```\n\n"
        "The installer must accept every source file before applying. Unknown local changes "
        "or incompatible mixtures of predecessor revisions require review; do not force replacement. "
        "Existing configuration, calibration, maximum, cooling and retained-Z data remain unchanged. "
        "Replaced source files receive byte-for-byte backups.\n\n"
        "When the machine is idle and E3 is disconnected, use the same Pi Bash session to "
        "stop the service and apply. The installer checks that the service is inactive; "
        "it never stops or starts the service itself. Restart only after installation succeeds:\n\n"
        "```sh\n"
        "sudo systemctl stop e3-hardware-node.service &&\n"
        "sudo systemctl reset-failed e3-hardware-node.service &&\n"
        '"$e3_project/.venv/bin/python" "$e3_workpiece/install_workpiece_focus.py" '
        '--project "$e3_project" --apply &&\n'
        "sudo systemctl start e3-hardware-node.service\n"
        "```\n\n"
        "Open the matching E3 DEV TEST build after the service starts. Establish the required "
        "reference and measure the workpiece in the Machine tab; a service restart does not "
        "restore the previous workpiece focus. Check the calculated Workpiece focus status "
        "before an operator-controlled job test. See [the focus guide](LASER_FOCUS.md) "
        "for the workflow and physical verification limits.\n"
    )


def package(destination: Path) -> Path:
    if any(previous.keys() != SAVED_Z.keys() for previous in PREDECESSORS.values()):
        raise ValueError("Each predecessor must describe the complete companion payload")
    sources = {name: (ROOT / name).read_bytes().replace(b"\r\n", b"\n") for name in SAVED_Z}
    manifest = json.dumps({
        "predecessors": [{"revision": revision, "files": previous}
                         for revision, previous in PREDECESSORS.items()],
        "files": [{"path": name, "sha256_lf": hashlib.sha256(content).hexdigest()}
                  for name, content in sources.items()],
    }, indent=2).encode()
    digest = hashlib.sha256(manifest).hexdigest()
    folder = destination / f"e3-pi-workpiece-focus-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in sources.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (folder / "manifest.json").write_bytes(manifest)
    template = (ROOT / "scripts/install_workpiece_focus.py").read_text(encoding="utf-8")
    installer = template.replace("BUILD_REPLACES_MANIFEST_SHA256", digest)
    (folder / "install_workpiece_focus.py").write_text(installer, encoding="utf-8", newline="\n")
    (folder / "INSTALL.md").write_text(installation_guide(folder), encoding="utf-8", newline="\n")
    for name in ("LASER_FOCUS.md", "ENDER_RECOVERY.md", "Z_RETENTION.md"):
        guide = (ROOT / "docs" / name).read_text(encoding="utf-8")
        guide = guide.replace("__PI_PACKAGE__", folder.name).replace("install_laser_focus.py", "install_workpiece_focus.py")
        (folder / name).write_text(guide, encoding="utf-8", newline="\n")
    (folder / "README.md").write_text(
        "# Automatic workpiece focus Pi companion\n\n"
        "Install this matching companion before using automatic workpiece focus in E3 DEV TEST. "
        "It updates application sources only and does not flash firmware. "
        "Existing surface-height V2 firmware remains required for measurement; saved-Z restoration "
        "still requires its separate restore capability.\n\n"
        "The installer accepts the exact recorded installed Pi combination, the pinned main focus "
        "or saved-Z predecessor, or already "
        "current files. It rejects unknown changes and incompatible mixtures before replacement, "
        "preserves configuration/calibration and backs up replaced source bytes. It neither stops "
        "nor starts the service. First run without --apply to inspect the planned changes. "
        "Apply only with the machine idle, E3 disconnected and e3-hardware-node.service inactive.\n\n"
        "Use [INSTALL.md](INSTALL.md) for the exact copy, dry-run and apply commands. "
        "See [the focus guide](LASER_FOCUS.md) for the workflow and physical verification limits.\n",
        encoding="utf-8", newline="\n",
    )
    return folder


if __name__ == "__main__":
    print(package(ROOT / "dist"))
