#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi
REVISION="${GITHUB_SHA:-$(git rev-parse HEAD)}"
CHANNEL="${E3_UPDATE_CHANNEL:-development}"
REPOSITORY="${GITHUB_REPOSITORY:-lukelave-boop/E3}"
RELEASE_TAG="${E3_RELEASE_TAG:-e3-development}"

"$PYTHON" -m pip install --upgrade pyinstaller
"$PYTHON" packaging/write_build_info.py \
  --output build-info.json \
  --revision "$REVISION" \
  --channel "$CHANNEL" \
  --repository "$REPOSITORY" \
  --release-tag "$RELEASE_TAG" \
  --platform-key linux-x86_64

rm -rf build/E3 dist/E3 AppDir E3-x86_64.AppImage
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onedir \
  --name E3 \
  --collect-all laser_aligner \
  --collect-all cv2 \
  --collect-all PySide6 \
  packaging/e3_entry.py

mkdir -p \
  AppDir/usr/bin \
  AppDir/usr/share/applications \
  AppDir/usr/share/icons/hicolor/scalable/apps
cp -a dist/E3 AppDir/usr/bin/E3
cp build-info.json AppDir/usr/bin/E3/build-info.json
mkdir -p AppDir/usr/bin/E3/config
cp config/default.json AppDir/usr/bin/E3/config/default.json
cp packaging/AppRun AppDir/AppRun
chmod +x AppDir/AppRun
cp packaging/E3.desktop AppDir/E3.desktop
cp packaging/E3.desktop AppDir/usr/share/applications/E3.desktop
cp laser_aligner/desktop/assets/e3-positioning-system.svg \
  AppDir/e3-positioning-system.svg
cp laser_aligner/desktop/assets/e3-positioning-system.svg \
  AppDir/usr/share/icons/hicolor/scalable/apps/e3-positioning-system.svg

APPIMAGETOOL="${APPIMAGETOOL:-$ROOT/appimagetool-x86_64.AppImage}"
if [[ ! -x "$APPIMAGETOOL" ]]; then
  curl --fail --location --retry 3 \
    --output "$APPIMAGETOOL" \
    https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$APPIMAGETOOL"
fi
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 \
  "$APPIMAGETOOL" AppDir E3-x86_64.AppImage
printf '\nE3 Linux AppImage complete:\n%s\n' "$ROOT/E3-x86_64.AppImage"
