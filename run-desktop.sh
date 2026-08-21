#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi

if ! .venv/bin/python -c 'import PySide6' >/dev/null 2>&1; then
  echo "Desktop dependencies are not installed." >&2
  echo "Run: .venv/bin/pip install -e '.[desktop]'" >&2
  exit 1
fi

CONFIG="${LASER_ALIGNER_CONFIG:-$ROOT/config/local.json}"
if [[ ! -f "$CONFIG" ]]; then
  CONFIG="$ROOT/config/default.json"
fi

exec .venv/bin/python -m laser_aligner.desktop.main --config "$CONFIG" "$@"
