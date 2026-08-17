#!/usr/bin/env python3
"""Exercise named live desktop operations with process-wide laser lockout."""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Drive a small set of real desktop operations with positive laser "
            "output rejected by MachineService."
        )
    )
    parser.add_argument(
        "operation",
        choices=("status", "connect", "home-park", "camera-frame"),
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "local.json")
    parser.add_argument("--screenshot", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser


def _public_machine_status(status: dict[str, Any]) -> dict[str, Any]:
    return {
        key: status.get(key)
        for key in (
            "backend",
            "connected",
            "connecting",
            "protocol",
            "hardware_enabled",
            "laser_lockout",
            "allow_motion",
            "coordinate_reference_ready",
            "controller_reconnect_required",
            "jog_position_mm",
        )
    }


def _wait_for_controller(window: Any, application: Any, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    started = False
    while time.monotonic() < deadline:
        application.processEvents()
        busy = bool(window._controller_busy)
        started = started or busy
        if started and not busy:
            return
        time.sleep(0.01)
    raise TimeoutError("Desktop controller operation did not finish before timeout")


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    from PySide6 import QtWidgets

    from laser_aligner.core import CoreRuntime
    from laser_aligner.desktop.main import configure_application_identity
    from laser_aligner.desktop.main_window import E3MainWindow
    from laser_aligner.desktop.theme import apply_dark_theme

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication(
        [sys.argv[0]]
    )
    configure_application_identity(application)
    application.setOrganizationName("E3")
    application.setOrganizationDomain("local.e3-positioning-system")
    apply_dark_theme(application)

    runtime = CoreRuntime.from_config(
        arguments.config,
        hardware_enabled=True,
        laser_lockout=True,
    )
    window = E3MainWindow(runtime)
    window.show()
    application.processEvents()
    report: dict[str, Any] = {
        "operation": arguments.operation,
        "laser_lockout": True,
    }
    try:
        if arguments.operation == "connect":
            window.machine_panel.connect_button.click()
            _wait_for_controller(window, application, arguments.timeout)
        elif arguments.operation == "home-park":
            window.machine_panel.park_button.click()
            _wait_for_controller(window, application, arguments.timeout)
        elif arguments.operation == "camera-frame":
            frame = runtime.context.camera_frame(undistort=False)
            report["camera_frame"] = {
                "width": int(frame.shape[1]),
                "height": int(frame.shape[0]),
                "channels": int(frame.shape[2]) if frame.ndim == 3 else 1,
            }
        if arguments.screenshot is not None:
            arguments.screenshot.parent.mkdir(parents=True, exist_ok=True)
            if not window.grab().save(str(arguments.screenshot)):
                raise RuntimeError(f"Could not save screenshot {arguments.screenshot}")
            report["screenshot"] = str(arguments.screenshot)
        report["machine"] = _public_machine_status(runtime.context.machine.status())
        report["ok"] = True
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        report["machine"] = _public_machine_status(runtime.context.machine.status())
        report["ok"] = False
        report["error"] = str(exc)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    finally:
        window.controller.stop()
        runtime.stop()
        window._closing = True
        window.close()
        application.processEvents()


if __name__ == "__main__":
    raise SystemExit(main())
