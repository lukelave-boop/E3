#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This installer currently targets Linux Mint, Ubuntu, and Debian systems with apt." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y \
  git \
  python3 \
  python3-pip \
  python3-venv \
  v4l-utils

python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(
        f"Python 3.10 or newer is required; this computer has {sys.version.split()[0]}. "
        "Use a newer Linux release or install a newer Python first."
    )
print(f"Using Python {sys.version.split()[0]}")
PY

if [[ -d .venv && ! -x .venv/bin/python ]]; then
  echo "Existing .venv is incomplete; remove it and run the installer again." >&2
  exit 1
fi

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install --editable .
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q laser_aligner

.venv/bin/python -m laser_aligner --config config/default.json --generate-targets

for group in video dialout; do
  if getent group "$group" >/dev/null 2>&1; then
    sudo usermod -aG "$group" "$USER" || true
  fi
done

if [[ "${SKIP_TESTS:-0}" != "1" ]]; then
  .venv/bin/python -m pytest -q
fi

cat <<'EOF'

Installation complete.

  Configure a real machine through the E3 first-run setup before normal use.
  Camera probe:    .venv/bin/python tools/camera_probe.py
  Controller probe (laser power physically disconnected where practical):
                   .venv/bin/python tools/controller_probe.py --port /dev/serial/by-id/YOUR_CONTROLLER

Log out and back in if your user was newly added to the video or dialout group.
Motion remains blocked until machine.allow_motion is deliberately enabled for the saved machine.
EOF
