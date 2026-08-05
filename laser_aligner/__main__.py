from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from .app import AppContext
from .calibration.targets import write_default_targets
from .config import ConfigError, load_settings
from .logging_setup import configure_logging
from .server import AppHTTPServer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Camera-assisted laser positioning and SVG placement")
    parser.add_argument("--config", type=Path, help="Path to a JSON configuration file")
    parser.add_argument("--hardware", action="store_true", help="Permit opening the configured serial controller")
    parser.add_argument("--host", help="Override the configured HTTP bind address")
    parser.add_argument("--port", type=int, help="Override the configured HTTP port")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the local browser automatically")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--generate-targets", action="store_true", help="Generate calibration SVG files and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if args.host:
        settings.app.host = args.host
    if args.port:
        settings.app.port = args.port
    if args.no_browser:
        settings.app.open_browser = False

    configure_logging(settings.app.data_dir / "logs", verbose=args.verbose)
    logger = logging.getLogger(__name__)

    if args.generate_targets:
        paths = write_default_targets(settings.project_root / "targets")
        for path in paths:
            print(path)
        return 0

    if settings.machine.backend == "serial" and not args.hardware:
        logger.warning("Serial backend is configured, but --hardware was not supplied; connection attempts will be blocked")
    if settings.app.host not in {"127.0.0.1", "localhost", "::1"} and not settings.app.allow_remote_control:
        logger.warning("Server is reachable over the network, but remote machine-control endpoints remain blocked")

    context = AppContext(settings, hardware_enabled=args.hardware)
    context.start()
    server = AppHTTPServer((settings.app.host, settings.app.port), context)
    url = f"http://{settings.app.host if settings.app.host not in {'0.0.0.0', '::'} else '127.0.0.1'}:{settings.app.port}/"
    logger.info("Laser Camera Aligner running at %s", url)

    shutdown_started = threading.Event()

    def shutdown(_signum: int, _frame: object) -> None:
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        logger.info("Stopping server")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if settings.app.open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        context.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
