"""Offline evidence: exercise the historical downloader against a trickling server."""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from laser_aligner import updates


def main() -> None:
    source = subprocess.check_output(
        ["git", "show", "bc86c8912ef2:laser_aligner/updates.py"], text=True,
    )
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == "download_update")
    scope = dict(vars(updates))
    scope["_DOWNLOAD_CHUNK_BYTES"] = 1_048_576
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<old downloader>", "exec"), scope)
    payload = b"x" * 32

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            for byte in payload:
                try:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.05)
                except OSError:
                    break

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/setup.exe"
    scope["_request"] = lambda _: updates.urllib.request.Request(url)
    asset = updates.UpdateAsset(
        "windows-x86_64", "setup.exe", "https://example.com/setup.exe",
        hashlib.sha256(payload).hexdigest(), len(payload),
    )
    folder = Path("build/updater-stall-evidence")
    folder.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    events = []
    try:
        scope["download_update"](
            asset, destination_dir=folder, timeout=0.2,
            progress=lambda n, t: events.append({
                "bytes": n, "elapsed": round(time.monotonic() - started, 3),
            }),
        )
        report = {
            "socket_timeout_seconds": 0.2,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "progress_events": events,
            "result": "Old read exceeds socket timeout; trickle prevents timeout. "
                      "No progress is delivered until the entire response finishes.",
        }
        started = time.monotonic()
        corrected_events = []
        original_request = updates._request
        updates._request = scope["_request"]
        try:
            updates.download_update(
                asset, destination_dir=folder / "corrected", timeout=0.2, max_duration=0.3,
                progress=lambda n, t: corrected_events.append({
                    "bytes": n, "elapsed": round(time.monotonic() - started, 3),
                }),
            )
            raise AssertionError("Corrected downloader did not expire")
        except updates.UpdateError as exc:
            assert "time limit" in str(exc), str(exc)
            report["corrected"] = {
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": str(exc), "progress_events": corrected_events,
            }
        finally:
            updates._request = original_request
        (folder / "old-trickle-reproduction.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8",
        )
        print(json.dumps(report))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
