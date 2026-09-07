"""One explicit mainboard operation through the authenticated Pi MachineService."""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Sequence
from pathlib import Path

from .deployment import read_bridge_token
from .errors import MachineError
from .machine.mainboard import validate_control
from .machine.pi_job_protocol import MIN_TOKEN_LENGTH, validate_boot_id, validate_session_generation
from .probe_diagnostic import _exchange


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("control", choices=("status", "fan1", "fan2", "z"))
    parser.add_argument("value", nargs="?", help="Fan percent (0-100) or absolute Z (20-80 mm; max 5 mm per move)")
    parser.add_argument("--confirm", action="store_true", help="Confirm output readiness / stowed probe and Z clearance")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token-file", type=Path)
    args = parser.parse_args(argv)
    token = None
    try:
        value = (int(args.value) if args.control.startswith("fan") else float(args.value)) if args.value is not None else None
        validate_control(args.control, value, args.confirm)
        if not args.host.strip() or not 1 <= args.port <= 65535:
            raise ValueError("Invalid host or port")
        token = args.token_file.read_text().strip() if args.token_file else read_bridge_token()
        if not isinstance(token, str) or len(token) < MIN_TOKEN_LENGTH:
            raise ValueError("A saved Pi authentication token is required")
        caps = _exchange(args.host, args.port, token, "service.capabilities")
        if not caps.get("ok") or "machine.mainboard" not in caps.get("actions", {}):
            raise MachineError("Install the accompanying Pi software before using mainboard controls")
        response = _exchange(args.host, args.port, token, "machine.status")
        status = response.get("status", {})
        if not response.get("ok") or status.get("connected") is not True:
            raise MachineError("Connect the machine in E3 first")
        result = _exchange(
            args.host, args.port, token, "machine.mainboard", control=args.control, value=value,
            confirmed=args.confirm, client_id=str(uuid.uuid4()),
            expected_boot_id=validate_boot_id(status.get("boot_id")),
            expected_session_generation=validate_session_generation(status.get("controller_session_generation")),
        )
    except (ValueError, OSError, MachineError) as exc:
        result = {"ok": False, "error": str(exc)}
    text = json.dumps(result, ensure_ascii=True, allow_nan=False)
    if token:
        text = text.replace(json.dumps(token, ensure_ascii=True)[1:-1], "[REDACTED]")
    print(text)
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
