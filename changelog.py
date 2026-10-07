"""What's new in each version, shown after an update (and from Settings).

Newest first. Each entry: version → (English items, Bulgarian items).
Add an entry here with every release; the GitHub release notes can be copied from it.
"""
from __future__ import annotations

from i18n import LANG
from updater import parse_version

CHANGES: dict[str, tuple[list[str], list[str]]] = {
    "1.3.0": (
        [
            "**Automatic updates:** the app checks GitHub for a new version at startup and can "
            "download, verify and install it for you (Settings → Check for updates).",
            "**What's new** is shown after every update, and from Settings at any time.",
            "`--check-update` on the command line prints the installed and the latest version.",
        ],
        [
            "**Автоматични обновления:** при старт приложението проверява в GitHub за нова версия и "
            "може само да я изтегли, провери и инсталира (Настройки → Провери за обновления).",
            "**„Какво е новото“** се показва след всяко обновяване, а и по всяко време от Настройки.",
            "`--check-update` от командния ред показва инсталираната и последната версия.",
        ],
    ),
    "1.2.0": (
        [
            "**PlayStation controllers (DualSense / DualShock 4)** with their own diagram and names.",
            "Touchpad click, and the touchpad surface as a trackpad (drag) or an XY pad.",
            "Gyro aiming while a “Gyro” button is held; the light bar shows the active layer.",
            "**Sensitivity** for every stick, touchpad and gyro axis.",
            "Absolute sticks can **hold their position** when released, with pick-up (no jumps).",
            "Axes on the same CC share one value: stick, touchpad and gyro switch without jumps.",
        ],
        [
            "**PlayStation контролери (DualSense / DualShock 4)** със собствена схема и имена.",
            "Тъчпад клик, а повърхността на тъчпада като тракпад (плъзгане) или XY пад.",
            "Насочване с жироскоп, докато е задържан бутон „Жиро“; лентата показва активния слой.",
            "**Чувствителност** за всеки стик, тъчпад и жироскоп.",
            "Абсолютните стикове могат да **задържат позицията** при пускане, с поемане (без скок).",
            "Осите на един и същ CC имат обща стойност: стик, тъчпад и жироскоп се сменят без скок.",
        ],
    ),
    "1.1.0": (
        [
            "Modifiers and layers (key combinations), L3/R3 glide Pan/Tilt back to center.",
            "English and Bulgarian interface.",
        ],
        [
            "Модификатори и слоеве (клавишни комбинации), L3/R3 връщат Pan/Tilt плавно в центъра.",
            "Интерфейс на английски и български.",
        ],
    ),
}


def items(version: str) -> list[str]:
    en, bg = CHANGES.get(version, ([], []))
    return bg if LANG == "bg" else en


def markdown_since(previous: str | None, current: str) -> str:
    """Markdown for every version after `previous` up to and including `current`."""
    out = []
    for v in sorted(CHANGES, key=parse_version, reverse=True):
        if parse_version(v) > parse_version(current):
            continue
        if previous and parse_version(v) <= parse_version(previous):
            continue
        out.append(f"### {v}\n\n" + "\n".join(f"- {i}" for i in items(v)))
        if not previous:
            break  # only the current version
    return "\n\n".join(out)
