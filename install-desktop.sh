#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "The desktop installer currently targets Linux Mint, Ubuntu, and Debian systems with apt." >&2
  exit 1
fi

# Qt's Linux wheels rely on the system EGL/OpenGL loader. Install the same
# minimal runtime used by desktop CI so a missing shared library fails here
# rather than at first launch.
sudo apt-get install -y libegl1 libgl1

.venv/bin/python -m pip install -e '.[desktop]'
.venv/bin/python -m pip check

QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'PYQT'
from PySide6 import QtCore, QtGui, QtWidgets

print(f"PySide6 desktop runtime is importable (Qt {QtCore.qVersion()}).")
PYQT

APPLICATIONS="$HOME/.local/share/applications"
mkdir -p "$APPLICATIONS"

for template in system/e3-positioning-system.desktop.in
do
  output="$APPLICATIONS/$(basename "${template%.in}")"
  sed "s|@ROOT@|$ROOT|g" "$template" > "$output"
  chmod +x "$output"
done

rm -f "$APPLICATIONS/e3-positioning-system-safe.desktop"

if [[ -d "$HOME/Desktop" ]]; then
  cp "$APPLICATIONS/e3-positioning-system.desktop" "$HOME/Desktop/"
  chmod +x "$HOME/Desktop/e3-positioning-system.desktop"
fi

update-desktop-database "$APPLICATIONS" >/dev/null 2>&1 || true

echo
echo "Desktop installation complete."
echo "Launch 'E3 Positioning System' from the application menu."
