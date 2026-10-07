"""Tiny i18n: every UI string is written as tr("English", "Български").

The language is resolved once at import time (before any UI module builds its
constants), from settings.json → "language": "auto" | "en" | "bg".
"auto" follows the macOS preferred language. Changing it requires a restart.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SETTINGS_FILE = Path(os.path.expanduser("~/Library/Application Support/XboxMidiBridge/settings.json"))
LANGUAGES = {"auto": "Auto / Автоматично", "en": "English", "bg": "Български"}


def _system_language() -> str:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var, "")
        if v.lower().startswith("bg"):
            return "bg"
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"],
                             capture_output=True, text=True, timeout=1).stdout
        first = out.replace("(", "").replace('"', "").split(",")[0].strip()
        if first.lower().startswith("bg"):
            return "bg"
    except Exception:
        pass
    return "en"


def configured_language() -> str:
    try:
        v = json.loads(SETTINGS_FILE.read_text("utf-8")).get("language", "auto")
        return v if v in LANGUAGES else "auto"
    except (OSError, ValueError):
        return "auto"


def resolve(setting: str) -> str:
    return _system_language() if setting == "auto" else setting


LANG = resolve(os.environ.get("XMB_LANG") or configured_language())


def tr(en: str, bg: str) -> str:
    return bg if LANG == "bg" else en
