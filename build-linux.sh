#!/usr/bin/env bash
# Build on Ubuntu 22.04 x86-64 for a conservative glibc baseline.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv-linux
python=.venv-linux/bin/python
"$python" -m pip install --upgrade pip
"$python" -m pip install -r requirements.txt 'pyinstaller>=6,<7'
QT_QPA_PLATFORM=offscreen "$python" -m unittest discover -s tests -v
"$python" scripts/prepare_release.py
"$python" scripts/release_assets.py prepare
"$python" scripts/build_appimage.py prepare
"$python" -m PyInstaller --noconfirm --clean --workpath build packaging/Dovecot-Mailbox-Viewer-Linux.spec
"$python" scripts/build_appimage.py package

# Exercise the standalone artifact outside the source tree, with a real X server.
check_dir=$(mktemp -d)
trap 'rm -rf "$check_dir"' EXIT
cp dist/Dovecot-Mailbox-Viewer-Linux-x86_64.AppImage "$check_dir/viewer.AppImage"
chmod +x "$check_dir/viewer.AppImage"
(
  cd "$check_dir"
  export LOCALAPPDATA="$check_dir/cache"
  export APPIMAGE_EXTRACT_AND_RUN=1
  timeout 90s xvfb-run -a ./viewer.AppImage --smoke-test smoke-test.json
)
cp "$check_dir/smoke-test.json" build/smoke-test.json
"$python" scripts/release_assets.py finalise
echo "Linux AppImage is ready"
