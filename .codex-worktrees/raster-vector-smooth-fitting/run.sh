#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi
exec "$PYTHON" -m laser_aligner --config config/local.json "$@"
