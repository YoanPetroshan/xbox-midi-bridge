#!/bin/zsh
# Builds "dist/Xbox MIDI Bridge.app" for Apple Silicon, macOS 15+.
# Uses the python.org Python (not Homebrew: Homebrew Python only runs on the macOS version it was built on).
set -e
cd "$(dirname "$0")"
PY=${PY:-/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14}
SDK=/Library/Developer/CommandLineTools/SDKs/MacOSX15.sdk

if [ ! -x .venv-build/bin/python ]; then
  "$PY" -m venv .venv-build
  .venv-build/bin/pip install -q --upgrade pip
  # python-rtmidi has no wheel for 3.14 → compile it for macOS 12+.
  SDKROOT=$SDK MACOSX_DEPLOYMENT_TARGET=12.0 .venv-build/bin/pip install -q python-rtmidi
  .venv-build/bin/pip install -q --only-binary=:all: pygame-ce mido PySide6-Essentials pyinstaller
fi

.venv-build/bin/python -m unittest discover tests
rm -rf build "dist/Xbox MIDI Bridge" "dist/Xbox MIDI Bridge.app"
.venv-build/bin/pyinstaller --noconfirm --clean XboxMidiBridge.spec
# Ad-hoc sign the whole bundle (no Apple Developer account).
codesign --force --deep --sign - "dist/Xbox MIDI Bridge.app"
ditto -c -k --keepParent "dist/Xbox MIDI Bridge.app" "dist/Xbox MIDI Bridge.zip"
echo "Done: dist/Xbox MIDI Bridge.app and dist/Xbox MIDI Bridge.zip"
