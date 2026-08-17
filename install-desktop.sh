#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi

.venv/bin/python -m pip install -e '.[desktop]'

APPLICATIONS="$HOME/.local/share/applications"
mkdir -p "$APPLICATIONS"

for template in \
  system/e3-positioning-system.desktop.in \
  system/e3-positioning-system-safe.desktop.in
do
  output="$APPLICATIONS/$(basename "${template%.in}")"
  sed "s|@ROOT@|$ROOT|g" "$template" > "$output"
  chmod +x "$output"
done

if [[ -d "$HOME/Desktop" ]]; then
  cp "$APPLICATIONS/e3-positioning-system.desktop" "$HOME/Desktop/"
  chmod +x "$HOME/Desktop/e3-positioning-system.desktop"
fi

update-desktop-database "$APPLICATIONS" >/dev/null 2>&1 || true

echo
echo "Desktop installation complete."
echo "Launch 'E3 Positioning System' from the application menu."
echo "Use 'E3 Positioning System (Safe)' when serial hardware must remain locked."
