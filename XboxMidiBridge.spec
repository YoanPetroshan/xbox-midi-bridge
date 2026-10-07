# PyInstaller config for the .app. Build with: ./build_app.sh
import sys
sys.path.insert(0, ".")
from version import APP_VERSION

block_cipher = None

a = Analysis(
    ["app.py"],
    pathex=["."],
    hiddenimports=["mido.backends.rtmidi", "rtmidi", "ui.main_window", "pygame._sdl2.controller", "certifi"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtMultimedia", "PySide6.Qt3DCore", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Xbox MIDI Bridge",
    console=False,
    target_arch="arm64",
)
coll = COLLECT(exe, a.binaries, a.datas, name="Xbox MIDI Bridge")
app = BUNDLE(
    coll,
    name="Xbox MIDI Bridge.app",
    icon="assets/icon.icns",
    bundle_identifier="com.yoan.xboxmidibridge",
    info_plist={
        "CFBundleDisplayName": "Xbox MIDI Bridge",
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "15.0",
        "LSApplicationCategoryType": "public.app-category.music",
    },
)
