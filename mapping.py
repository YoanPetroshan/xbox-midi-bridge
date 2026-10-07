"""Mapping models, the default layout, layers, and JSON profile storage."""
from __future__ import annotations

import copy
import itertools
import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from i18n import tr

APP_NAME = "XboxMidiBridge"
CONFIG_DIR = Path(os.path.expanduser(f"~/Library/Application Support/{APP_NAME}"))
PROFILES_DIR = CONFIG_DIR / "profiles"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
DEFAULT_PROFILE_NAME = tr("Default", "По подразбиране")
PROFILE_VERSION = 3

# --- Controller inputs (standard SDL Game Controller API names) ---

# (id, UI label)
BUTTONS: list[tuple[str, str]] = [
    ("a", "A"),
    ("b", "B"),
    ("x", "X"),
    ("y", "Y"),
    ("leftshoulder", "LB"),
    ("rightshoulder", "RB"),
    ("back", "View"),
    ("start", "Menu"),
    ("leftstick", "L3"),
    ("rightstick", "R3"),
    ("dpup", "D-pad ↑"),
    ("dpdown", "D-pad ↓"),
    ("dpleft", "D-pad ←"),
    ("dpright", "D-pad →"),
    ("guide", "Xbox"),
    ("misc1", "Share"),
]

AXES: list[tuple[str, str]] = [
    ("leftx", tr("Left stick X", "Ляв стик X")),
    ("lefty", tr("Left stick Y", "Ляв стик Y")),
    ("rightx", tr("Right stick X", "Десен стик X")),
    ("righty", tr("Right stick Y", "Десен стик Y")),
    ("lefttrigger", "LT"),
    ("righttrigger", "RT"),
]

BUTTON_IDS = [b for b, _ in BUTTONS]
AXIS_IDS = [a for a, _ in AXES]
TRIGGER_IDS = ("lefttrigger", "righttrigger")
INPUT_LABELS = dict(BUTTONS + AXES)
# Inputs macOS may not report over Bluetooth.
UNRELIABLE_INPUTS = ("guide", "misc1")

# Buttons that physically cannot be held together.
IMPOSSIBLE_PAIRS = (("dpup", "dpdown"), ("dpleft", "dpright"))

# --- Models ---

BUTTON_ACTIONS = ("midi", "modifier", "center", "center_left", "center_right", "fine")
# Axes each "center" action glides back to the middle (rate-mode axes only).
CENTER_TARGETS = {
    "center": ("leftx", "lefty", "rightx", "righty"),
    "center_left": ("leftx", "lefty"),
    "center_right": ("rightx", "righty"),
}
BUTTON_TYPES = ("note", "cc", "pc")
BUTTON_MODES = ("momentary", "toggle", "value")
AXIS_MODES = ("absolute", "rate")
CURVES = ("linear", "expo")


@dataclass
class ButtonMapping:
    enabled: bool = True
    action: str = "midi"  # midi | modifier | center | center_left | center_right | fine
    type: str = "note"  # note | cc | pc
    mode: str = "momentary"  # momentary | toggle | value (value: CC only)
    channel: int = 1  # 1..16
    number: int = 36  # note / CC / program, 0..127
    value: int = 127  # velocity / CC value, 0..127
    label: str = ""

    def normalized(self) -> "ButtonMapping":
        m = copy.copy(self)
        if m.action not in BUTTON_ACTIONS:
            m.action = "midi"
        if m.type not in BUTTON_TYPES:
            m.type = "note"
        if m.mode not in BUTTON_MODES:
            m.mode = "momentary"
        if m.type == "note" and m.mode == "value":
            m.mode = "momentary"
        m.channel = _clamp(int(m.channel), 1, 16)
        m.number = _clamp(int(m.number), 0, 127)
        m.value = _clamp(int(m.value), 0, 127)
        return m


@dataclass
class AxisMapping:
    enabled: bool = True
    mode: str = "absolute"  # absolute | rate
    channel: int = 1
    cc: int = 20
    hires: bool = False  # 14-bit CC: MSB on cc, LSB on cc+32
    deadzone: float = 0.08
    curve: str = "linear"  # linear | expo
    expo: float = 0.6  # 0..1, strength of the expo curve
    max_speed: float = 0.5  # rate mode: fraction of the full range per second at full deflection
    invert: bool = False
    label: str = ""

    def normalized(self) -> "AxisMapping":
        m = copy.copy(self)
        if m.mode not in AXIS_MODES:
            m.mode = "absolute"
        if m.curve not in CURVES:
            m.curve = "linear"
        m.channel = _clamp(int(m.channel), 1, 16)
        m.cc = _clamp(int(m.cc), 0, 127)
        if m.cc >= 32:
            m.hires = False  # LSB must be cc+32, so CC 0..31 only
        m.deadzone = _clampf(float(m.deadzone), 0.0, 0.9)
        m.expo = _clampf(float(m.expo), 0.0, 1.0)
        m.max_speed = _clampf(float(m.max_speed), 0.01, 10.0)
        return m


@dataclass
class ProfileOptions:
    fine_factor: float = 0.25  # stick speed multiplier while a "fine" button is held
    max_rate_hz: int = 120  # max CC messages per second per axis
    center_time: float = 1.0  # seconds to glide back to center (0 = instant)


@dataclass
class Profile:
    name: str = DEFAULT_PROFILE_NAME
    buttons: dict[str, ButtonMapping] = field(default_factory=dict)
    axes: dict[str, AxisMapping] = field(default_factory=dict)
    options: ProfileOptions = field(default_factory=ProfileOptions)
    # Layers: key = held modifiers (layer_key), value = button mappings in that layer.
    # The base layer is `buttons` (key ""). A button with no entry in a layer does nothing there.
    layers: dict[str, dict[str, ButtonMapping]] = field(default_factory=dict)

    def button_map(self, key: str, b: str) -> ButtonMapping | None:
        if not key:
            return self.buttons.get(b)
        return self.layers.get(key, {}).get(b)

    def effective_map(self, key: str, b: str) -> ButtonMapping | None:
        """The mapping that actually applies in a layer.

        An enabled entry in the layer wins. Otherwise, if the button is a modifier in a
        lower layer (a subset of the held modifiers), it stays a modifier here too:
        that is how LB and D-pad ↑ held together give their own "LB + D-pad ↑" layer.
        """
        m = self.button_map(key, b)
        if m is not None and m.enabled:
            return m
        mods = layer_mods(key)
        for size in range(len(mods) - 1, -1, -1):
            for sub in itertools.combinations(mods, size):
                sm = self.button_map(layer_key(sub), b)
                if sm is not None and sm.enabled and sm.action == "modifier":
                    return sm
        return m

    def inherited_modifier(self, key: str, b: str) -> bool:
        """True if the button is a modifier here only by inheritance from a lower layer."""
        m = self.button_map(key, b)
        e = self.effective_map(key, b)
        return e is not None and e is not m

    def ensure_button_map(self, key: str, b: str) -> ButtonMapping:
        """The button's mapping in a layer; creates a disabled entry with a free note if missing."""
        m = self.button_map(key, b)
        if m is None:
            m = ButtonMapping(enabled=False, number=self.free_note())
            self.layers.setdefault(key, {})[b] = m
        return m

    def all_button_maps(self):
        """(layer key, button, mapping) across all layers, base included."""
        for b, m in self.buttons.items():
            yield "", b, m
        for key, maps in self.layers.items():
            for b, m in maps.items():
                yield key, b, m

    def used_signatures(self) -> dict[tuple, int]:
        """How many times each (type, channel, number) is used by enabled MIDI buttons."""
        out: dict[tuple, int] = {}
        for _, _, m in self.all_button_maps():
            sig = midi_signature(m)
            if sig:
                out[sig] = out.get(sig, 0) + 1
        return out

    def free_note(self, start: int = 52, channel: int = 1) -> int:
        used = {sig[2] for sig in self.used_signatures() if sig[0] == "note" and sig[1] == channel}
        for n in list(range(start, 128)) + list(range(0, start)):
            if n not in used:
                return n
        return start

    def reachable_layers(self, max_depth: int = 4, limit: int = 128) -> list[str]:
        """Layers reachable through modifiers, in discovery order (base first)."""
        found = [""]
        queue = [()]
        seen = {""}
        while queue and len(found) < limit:
            mods = queue.pop(0)
            if len(mods) >= max_depth:
                continue
            key = layer_key(mods)
            for b in BUTTON_IDS:
                if b in mods:
                    continue
                m = self.effective_map(key, b)
                if m and m.enabled and m.action == "modifier":
                    nxt = tuple(mods) + (b,)
                    if any(x in nxt and y in nxt for x, y in IMPOSSIBLE_PAIRS):
                        continue  # a D-pad cannot press ↑+↓ or ←+→
                    k = layer_key(nxt)
                    if k not in seen:
                        seen.add(k)
                        found.append(k)
                        queue.append(nxt)
        return found

    def to_json(self) -> dict:
        return {
            "version": PROFILE_VERSION,
            "name": self.name,
            "layers": {key: {b: asdict(m) for b, m in maps.items()}
                       for key, maps in self.layers.items() if maps},
            "buttons": {k: asdict(v) for k, v in self.buttons.items()},
            "axes": {k: asdict(v) for k, v in self.axes.items()},
            "options": asdict(self.options),
        }

    @classmethod
    def from_json(cls, data: dict) -> "Profile":
        p = default_profile(data.get("name") or DEFAULT_PROFILE_NAME)
        for k, v in (data.get("buttons") or {}).items():
            if k in p.buttons and isinstance(v, dict):
                p.buttons[k] = _build(ButtonMapping, v).normalized()
        for k, v in (data.get("axes") or {}).items():
            if k in p.axes and isinstance(v, dict):
                p.axes[k] = _build(AxisMapping, v).normalized()
        if isinstance(data.get("options"), dict):
            p.options = _build(ProfileOptions, data["options"])
        for key, maps in (data.get("layers") or {}).items():
            mods = layer_mods(key)
            if not mods or not isinstance(maps, dict):
                continue
            key = layer_key(mods)
            for b, v in maps.items():
                if b in p.buttons and b not in mods and isinstance(v, dict):
                    p.layers.setdefault(key, {})[b] = _build(ButtonMapping, v).normalized()
        if int(data.get("version", 1)) < 2:
            # v2: L3/R3 glide Pan/Tilt back to center. Only touched if they still match
            # the old default layout (Note 44/45).
            for bid, action, note in (("leftstick", "center_left", 44), ("rightstick", "center_right", 45)):
                b = p.buttons[bid]
                if (b.action, b.type, b.mode, b.channel, b.number) == ("midi", "note", "momentary", 1, note):
                    b.action = action
        return p

    def clone(self, name: str | None = None) -> "Profile":
        p = copy.deepcopy(self)
        if name:
            p.name = name
        return p


def layer_key(mods) -> str:
    """Canonical layer key: modifiers in BUTTON_IDS order, joined with '+'."""
    ms = set(mods)
    return "+".join(b for b in BUTTON_IDS if b in ms)


def layer_mods(key: str) -> list[str]:
    return [b for b in key.split("+") if b in BUTTON_IDS] if key else []


def layer_label(key: str) -> str:
    mods = layer_mods(key)
    return " + ".join(INPUT_LABELS[b] for b in mods) if mods else tr("Base", "Основен")


def midi_signature(m: ButtonMapping) -> tuple | None:
    if not m.enabled or m.action != "midi":
        return None
    return (m.type, m.channel, m.number)


def default_profile(name: str = DEFAULT_PROFILE_NAME) -> Profile:
    """The default layout. Channel 1 for everything."""
    notes = {
        "a": 36, "b": 37, "x": 38, "y": 39,
        "leftshoulder": 40, "rightshoulder": 41,
        "back": 42, "start": 43,
        "leftstick": 44, "rightstick": 45,
        "dpup": 46, "dpdown": 47, "dpleft": 48, "dpright": 49,
        "guide": 50, "misc1": 51,
    }
    buttons = {bid: ButtonMapping(number=notes[bid]) for bid in BUTTON_IDS}
    buttons["leftstick"].action = "center_left"
    buttons["rightstick"].action = "center_right"
    rate = dict(mode="rate", curve="expo", expo=0.6, max_speed=0.5)
    axes = {
        "leftx": AxisMapping(cc=10, label="Pan 1", **rate),
        "lefty": AxisMapping(cc=11, label="Tilt 1", **rate),
        "rightx": AxisMapping(cc=12, label="Pan 2", **rate),
        "righty": AxisMapping(cc=13, label="Tilt 2", **rate),
        "lefttrigger": AxisMapping(cc=20, mode="absolute", deadzone=0.03),
        "righttrigger": AxisMapping(cc=21, mode="absolute", deadzone=0.03),
    }
    return Profile(name=name, buttons=buttons, axes=axes)


# --- Label descriptions ---

MODE_NAMES = {"momentary": "Momentary", "toggle": "Toggle", "value": tr("Value", "Стойност")}


def describe_button(m: ButtonMapping) -> str:
    if not m.enabled:
        return tr("Disabled", "Изключен")
    if m.action == "modifier":
        return tr("Modifier (hold for layer)", "Модификатор (задръж за слой)")
    if m.action == "center":
        return tr("Center all Pan/Tilt (smooth)", "Център всички Pan/Tilt (плавно)")
    if m.action == "center_left":
        return tr("Center left stick · Pan/Tilt 1 (smooth)", "Център ляв стик · Pan/Tilt 1 (плавно)")
    if m.action == "center_right":
        return tr("Center right stick · Pan/Tilt 2 (smooth)", "Център десен стик · Pan/Tilt 2 (плавно)")
    if m.action == "fine":
        return tr("Fine (slow sticks)", "Фино (бавни стикове)")
    ch = tr("Ch. {}", "Кан. {}").format(m.channel)
    if m.type == "note":
        return f"Note {m.number} · {ch} · {MODE_NAMES[m.mode]}"
    if m.type == "cc":
        if m.mode == "value":
            return f"CC {m.number} = {m.value} · {ch}"
        return f"CC {m.number} · {ch} · {MODE_NAMES[m.mode]}"
    return f"Program {m.number} · {ch}"


def describe_axis(m: AxisMapping) -> str:
    if not m.enabled:
        return tr("Disabled", "Изключен")
    mode = tr("Rate", "Скоростен") if m.mode == "rate" else tr("Absolute", "Абсолютен")
    parts = [f"CC {m.cc}" + ("/" + str(m.cc + 32) if m.hires else ""), mode]
    if m.label:
        parts.append(m.label)
    if m.channel != 1:
        parts.append(tr("Ch. {}", "Кан. {}").format(m.channel))
    return " · ".join(parts)


# --- Storage ---


def _safe_filename(name: str) -> str:
    s = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", name).strip().strip(".")
    return s or "profile"


class ProfileStore:
    """Profiles as separate JSON files + settings.json for global settings."""

    def __init__(self, base: Path | None = None):
        self.base = Path(base) if base else CONFIG_DIR
        self.profiles_dir = self.base / "profiles"
        self.settings_file = self.base / "settings.json"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.settings = self._load_settings()

    # settings
    def _load_settings(self) -> dict:
        defaults = {
            "active_profile": DEFAULT_PROFILE_NAME,
            "language": "auto",  # auto | en | bg (applied on restart, see i18n.py)
            "midi_port": None,  # None = virtual port "Xbox MIDI Bridge"
            "controller": None,  # preferred controller name
            "persist_rate_values": True,
            "resend_on_start": False,
            "rate_values": {},  # {axis: 0..1}, shared by all profiles (physical head position)
            "seen_inputs": [],
        }
        try:
            data = json.loads(self.settings_file.read_text("utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
        except (OSError, ValueError):
            pass
        return defaults

    def save_settings(self) -> None:
        _atomic_write(self.settings_file, self.settings)

    # profiles
    def path_for(self, name: str) -> Path:
        return self.profiles_dir / (_safe_filename(name) + ".json")

    def list_profiles(self) -> list[str]:
        names = []
        for f in sorted(self.profiles_dir.glob("*.json")):
            try:
                names.append(json.loads(f.read_text("utf-8")).get("name") or f.stem)
            except (OSError, ValueError):
                continue
        if not names:
            self.save_profile(default_profile())
            names = [DEFAULT_PROFILE_NAME]
        return sorted(names, key=lambda n: (n != DEFAULT_PROFILE_NAME, n.lower()))

    def load_profile(self, name: str) -> Profile:
        try:
            data = json.loads(self.path_for(name).read_text("utf-8"))
            return Profile.from_json(data)
        except (OSError, ValueError):
            p = default_profile(name)
            self.save_profile(p)
            return p

    def load_active(self) -> Profile:
        names = self.list_profiles()
        name = self.settings.get("active_profile")
        if name not in names:
            name = names[0]
        return self.load_profile(name)

    def save_profile(self, p: Profile) -> None:
        _atomic_write(self.path_for(p.name), p.to_json())

    def delete_profile(self, name: str) -> None:
        try:
            self.path_for(name).unlink()
        except OSError:
            pass

    def rename_profile(self, p: Profile, new_name: str) -> None:
        self.delete_profile(p.name)
        p.name = new_name
        self.save_profile(p)

    def exists(self, name: str) -> bool:
        return self.path_for(name).exists()


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)


def _build(cls, data: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _clampf(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
