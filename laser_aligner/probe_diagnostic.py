"""Operator-run probe diagnostic client for the authenticated Pi machine service.

Each invocation submits one explicit diagnostic to an already connected Pi-owned
session. The separately confirmed native-cycle test moves Z and homes it.
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .deployment import read_bridge_token
from .errors import MachineError
from .machine.pi_job_protocol import (
    MIN_TOKEN_LENGTH,
    request_response,
    validate_boot_id,
    validate_session_generation,
)

_PIN_ACTION = "machine.probe_pin"
_NATIVE_ACTION = "machine.probe_z"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Operator CR Touch diagnostics. Inspect/deploy/stow have NO AXIS MOTION. "
            "Deploy/stow move the PIN. "
            "Native-cycle MOVES Z: raise 5 mm, one native homing cycle, then Z 20 mm "
            "clearance. It does not measure material height. No automatic connection, "
            "primary XY motion, retry or laser enable."
        ),
        epilog=(
            "Connect the machine yourself in E3 first. Run one action, observe the "
            "physical pin, and review its result before choosing another action. "
            "No action is retried automatically."
        ),
    )
    parser.add_argument("pin_action", choices=("inspect", "deploy", "stow", "native-cycle"))
    parser.add_argument("--host", default="127.0.0.1", help="Pi service host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Pi service port (default: 8765)")
    parser.add_argument("--token-file", type=Path, help="Read the saved authentication token from this file")
    parser.add_argument(
        "--confirm-pin-clearance", action="store_true",
        help=(
            "Required for deploy/stow: I am operating the controls, the pin has "
            "at least 10 mm of free space, and the laser cannot emit"
        ),
    )
    parser.add_argument(
        "--confirm-native-cycle", action="store_true",
        help=(
            "Required for native-cycle: I will operate the test; the Ender is at its reset "
            "position or homed Z 20 mm clearance; its "
            "pin is retracted with steady normal light; the initial 5 mm lift and final "
            "Z 20 mm clearance are physically available; the probe is over the solid border after E3 Home/park; "
            "Creality XY motors are disconnected; the laser cannot emit"
        ),
    )
    return parser


def _exchange(host: str, port: int, token: str, action: str, **fields: Any) -> dict[str, Any]:
    response = request_response(
        host, port, token,
        {"action": action, "request_id": str(uuid.uuid4()), **fields},
        timeout=120.0 if action == _NATIVE_ACTION else 25.0 if action == _PIN_ACTION else 5.0,
    )
    if not isinstance(response, dict) or type(response.get("ok")) is not bool:
        raise MachineError("Pi service returned an invalid response; no automatic retry was made")
    return response


def _run(args: argparse.Namespace, token: str) -> dict[str, Any]:
    capabilities = _exchange(args.host, args.port, token, "service.capabilities")
    if not capabilities["ok"]:
        return capabilities
    actions = capabilities.get("actions")
    native = args.pin_action == "native-cycle"
    action = _NATIVE_ACTION if native else _PIN_ACTION
    if not isinstance(actions, dict) or action not in actions:
        raise MachineError("The Pi service does not support pin diagnostics; update the Pi service first")

    response = _exchange(args.host, args.port, token, "machine.status")
    if not response["ok"]:
        return response
    status = response.get("status")
    if not isinstance(status, dict) or status.get("connected") is not True:
        raise MachineError("Connect machine yourself in E3 first; this client does not connect or home it")
    boot_id = validate_boot_id(status.get("boot_id"))
    generation = validate_session_generation(status.get("controller_session_generation"))
    fields = (
        {"operation": "native_test", "confirmed": args.confirm_native_cycle,
         "clearance_z_mm": 20.0, "support_height_mm": -1.5}
        if native else {"pin_action": args.pin_action, "confirmed": args.confirm_pin_clearance}
    )
    return _exchange(
        args.host, args.port, token, action, **fields,
        client_id=str(uuid.uuid4()),
        expected_boot_id=boot_id,
        expected_session_generation=generation,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    token: str | None = None
    try:
        # Reject missing confirmation before reading credentials or making RPCs.
        if args.pin_action == "native-cycle" and not args.confirm_native_cycle:
            raise ValueError("Native Z motion requires the operator's --confirm-native-cycle flag")
        if args.pin_action in {"deploy", "stow"} and not args.confirm_pin_clearance:
            raise ValueError("Deploy/stow require the operator's --confirm-pin-clearance flag")
        if not args.host.strip() or not 1 <= args.port <= 65535:
            raise ValueError("Host must be nonempty and port must be from 1 through 65535")
        token = (
            args.token_file.expanduser().read_text(encoding="utf-8").strip()
            if args.token_file is not None else read_bridge_token()
        )
        if not isinstance(token, str) or len(token) < MIN_TOKEN_LENGTH:
            raise ValueError("A saved Pi authentication token is required; specify --token-file if necessary")
        result = _run(args, token)
    except (MachineError, OSError, UnicodeError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    output = json.dumps(result, ensure_ascii=True, allow_nan=False)
    # Even an unexpected server error that echoes input must not expose a token.
    if token:
        output = output.replace(json.dumps(token, ensure_ascii=True)[1:-1], "[REDACTED]")
    print(output)
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
