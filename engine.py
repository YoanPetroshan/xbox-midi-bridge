"""Engine: controller state + mapping → MIDI messages.

Runs in its own ~250 Hz thread (independent of the Qt loop) so the Pan/Tilt rate
mode stays smooth. `step()` is a pure function of time and is tested without a
thread or a controller.
"""
from __future__ import annotations

import threading
import time

import mido

from i18n import tr
from mapping import (AXIS_IDS, BUTTON_IDS, CENTER_TARGETS, GYRO_IDS, STICK_IDS, TOUCH_IDS,
                     TRIGGER_IDS, AxisMapping, ButtonMapping, Profile, layer_key)

TICK_HZ = 250
SETTLE_S = 0.15  # after connecting: wait for real values before reacting
MAX_DT = 0.05  # guard against jumps if the thread stalls
LEARN_AXIS_THRESHOLD = 0.6
# While gliding to center, small stick touches (e.g. while clicking L3) are ignored;
# only a clear deflection above this threshold cancels the glide.
GLIDE_CANCEL = 0.5


# Modes whose value is kept by the engine (not derived from the input position).
HELD_MODES = ("rate", "relative")
# Absolute sticks with "hold": a return to center faster than this (stick units per second)
# is the spring, not the hand, so the position is kept.
RELEASE_SPEED = 6.0
PICKUP = 0.02  # how close the stick must come to the held value to take it over


def is_held(a: str, m: AxisMapping) -> bool:
    """True if the engine keeps this axis' value (rate_values) instead of reading it live."""
    return (m.mode in HELD_MODES or a in GYRO_IDS or a in TOUCH_IDS
            or (m.mode == "absolute" and m.hold and a in STICK_IDS))

# Light bar colours: base layer, then each layer in discovery order.
LAYER_COLORS = [(0, 90, 255), (170, 80, 255), (255, 120, 0), (0, 200, 80), (255, 30, 30),
                (0, 220, 220), (255, 200, 0), (255, 60, 160), (255, 255, 255)]


def smoothstep(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return p * p * (3.0 - 2.0 * p)


def shape_stick(x: float, m: AxisMapping) -> float:
    """-1..1 → -1..1 with deadzone, curve and invert."""
    a = abs(x)
    if a <= m.deadzone:
        return 0.0
    a = (a - m.deadzone) / (1.0 - m.deadzone)
    if m.curve == "expo":
        a = (1.0 - m.expo) * a + m.expo * a ** 3
    a = min(1.0, a)
    y = a if x > 0 else -a
    return -y if m.invert else y


def shape_trigger(x: float, m: AxisMapping) -> float:
    """0..1 → 0..1 with a low-end deadzone, curve and invert."""
    if x <= m.deadzone:
        a = 0.0
    else:
        a = min(1.0, (x - m.deadzone) / (1.0 - m.deadzone))
    if m.curve == "expo":
        a = (1.0 - m.expo) * a + m.expo * a ** 3
    return 1.0 - a if m.invert else a


def quantize(v: float, hires: bool) -> int:
    v = max(0.0, min(1.0, v))
    return int(v * (16383 if hires else 127) + 0.5)


class Engine:
    def __init__(self, profile: Profile, midi, reader=None, rate_values: dict | None = None):
        self.lock = threading.RLock()
        self.profile = profile
        self.midi = midi
        self.reader = reader

        # controller
        self.connected = False
        self.controller_name: str | None = None
        self.sdl_mapping: dict = {}
        self.devices: list[str] = []
        self.error: str | None = None
        self.buttons = {b: False for b in BUTTON_IDS}
        self.axes = {a: 0.0 for a in AXIS_IDS}
        self._prev = dict(self.buttons)
        self._settle_until = 0.0
        self._baseline = False

        # layers (modifiers)
        self.active_mods: list[str] = []  # held modifiers, in press order
        self._held: dict[str, str] = {}  # pressed button → key of the layer it was pressed in
        self._learn_mod: tuple[str, str] | None = None  # modifier pressed during Learn

        # output
        self.toggles: dict[str, bool] = {}  # "layer|button" → on
        self.rate_values = {a: 0.5 for a in AXIS_IDS}
        if rate_values:
            for k, v in rate_values.items():
                if k in self.rate_values:
                    self.rate_values[k] = max(0.0, min(1.0, float(v)))
        self.outputs: dict[str, int] = {}  # last value sent per axis
        self._target: dict[str, int] = {}
        self._pending: set[str] = set()
        self._last_send: dict[str, float] = {}
        self.rate_dirty = False
        self.fine_active = False
        self.gyro_active = False
        self._gyro_toggle = False
        self.touch_down = False
        self._touch_was_down = False
        self._touch_last: dict[str, float] = {}
        self._hold_state: dict[str, dict] = {}  # absolute+hold sticks: pick-up state
        self._sent_cc: dict[tuple, int] = {}  # (channel, cc, hires) → last value sent
        self.info: dict = {}  # capabilities reported by the reader
        self.led_color: tuple | None = None
        self._led_layer: str | None = None
        self._led_out: tuple | None = None
        self._glides: dict[str, tuple[float, float, float]] = {}  # axis → (start, t0, duration)

        # UI helpers
        self.learn = False
        self.learned: tuple[str, str] | None = None  # (input, layer key)
        self.seen: set[str] = set()
        self.app_active = True  # set by the GUI
        self.background_ok = False
        self.last_input_time = 0.0
        self.input_count = 0

        self._last_step: float | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._reader_restart_at = 0.0
        # Sync targets with current values so nothing is sent at startup.
        self.set_profile(profile)

    # ------------------------------------------------------------ thread
    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, name="midi-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        period = 1.0 / TICK_HZ
        while self._running:
            t0 = time.perf_counter()
            try:
                self._service_reader(t0)
                self.step(t0)
            except Exception as e:  # one error must not kill the thread
                self.error = tr("Engine error: {}", "Грешка в двигателя: {}").format(e)
            dt = time.perf_counter() - t0
            time.sleep(max(0.0005, period - dt))

    def _service_reader(self, now: float) -> None:
        if self.reader is None:
            return
        if not self.reader.alive():
            if now >= self._reader_restart_at:
                self._reader_restart_at = now + 3.0
                with self.lock:
                    if self.connected:
                        self._on_disconnect()
                try:
                    self.reader.stop()
                    self.reader.start(self.reader_prefer)
                except Exception as e:
                    self.error = tr("Controller reader failed to start: {}", "Четецът на контролера не стартира: {}").format(e)
            return
        for msg in self.reader.poll():
            self.feed(msg, now)
        with self.lock:
            led, self._led_out = self._led_out, None
        if led is not None:
            self.reader.send(("led",) + led)

    reader_prefer: str | None = None

    # ------------------------------------------------------------ input
    def feed(self, msg: tuple, now: float) -> None:
        with self.lock:
            kind = msg[0]
            if kind == "state":
                buttons, axes = msg[1], msg[2]
                self.touch_down = bool(msg[3]) if len(msg) > 3 else False
                self.buttons = dict(zip(BUTTON_IDS, buttons))
                self.axes = dict(zip(AXIS_IDS, axes))
                if self.touch_down:
                    self.seen.update(TOUCH_IDS)
                self.last_input_time = now
                self.input_count += 1
                if not self.app_active and self.connected:
                    self.background_ok = True
                for b, p in self.buttons.items():
                    if p:
                        self.seen.add(b)
                for a, v in self.axes.items():
                    if abs(v) > 0.3 and a not in TOUCH_IDS:
                        self.seen.add(a)
            elif kind == "connected":
                self.connected = True
                self.controller_name = msg[1]
                self.sdl_mapping = msg[2] if len(msg) > 2 else {}
                self.info = dict(msg[3]) if len(msg) > 3 and msg[3] else {}
                self._led_layer = None  # repaint the light bar for the new connection
                self.error = None
                self._settle_until = now + SETTLE_S
                self._baseline = True
            elif kind == "disconnected":
                self._on_disconnect()
            elif kind == "devices":
                self.devices = list(msg[1])
            elif kind == "error":
                self.error = msg[1]

    def _on_disconnect(self) -> None:
        """Release held momentary buttons; axis values are kept."""
        self.connected = False
        self._release_all_held()
        self.buttons = {b: False for b in BUTTON_IDS}
        self._prev = dict(self.buttons)
        # Sticks stop (rate values stay); triggers send nothing until reconnect.
        self.axes = {a: 0.0 for a in AXIS_IDS}
        self.fine_active = False
        self.gyro_active = False
        self._gyro_toggle = False
        self.touch_down = self._touch_was_down = False

    def _release_all_held(self) -> None:
        """Release all held buttons (using the mapping of the layer they were pressed in)."""
        for b, key in list(self._held.items()):
            m = self.profile.effective_map(key, b)
            if m and m.enabled:
                self._button_release(b, m)
        self._held.clear()
        self.active_mods.clear()
        self._learn_mod = None

    # ------------------------------------------------------------ step
    def step(self, now: float) -> None:
        with self.lock:
            dt = 0.0 if self._last_step is None else max(0.0, min(MAX_DT, now - self._last_step))
            self._last_step = now
            self._run_glides(now)
            if not self.connected or now < self._settle_until:
                self._flush_pending(now)
                return
            if self._baseline:
                # Read the current position first, then continue: no phantom presses.
                self._baseline = False
                self._prev = dict(self.buttons)
                for a, m in self.profile.axes.items():
                    if not is_held(a, m):
                        self._target[a] = self._compute_absolute(a, m)
                        self._pending.discard(a)
                self._hold_state.clear()  # held sticks re-pick-up from their kept value
                return

            if self.learn:
                self._detect_learn()
                self._prev = dict(self.buttons)
                return

            for b in BUTTON_IDS:
                pressed = self.buttons.get(b, False)
                if pressed != self._prev.get(b, False):
                    if pressed:
                        self._on_press(b)
                    else:
                        self._on_release(b)
            self._prev = dict(self.buttons)

            self.fine_active = False
            self.gyro_active = self._gyro_toggle
            for b, key in self._held.items():
                m = self.profile.effective_map(key, b)
                if m and m.enabled and m.action == "fine":
                    self.fine_active = True
                if m and m.enabled and m.action == "gyro" and m.mode != "toggle":
                    self.gyro_active = True
            self._update_led()

            for a in AXIS_IDS:
                m = self.profile.axes.get(a)
                if not m or not m.enabled:
                    continue
                fine = self.profile.options.fine_factor if self.fine_active else 1.0
                if a in TOUCH_IDS:
                    target = self._touch_target(a, m, fine)
                elif a in self._glides:
                    target = quantize(self.rate_values[a], m.hires)
                elif m.mode == "absolute" and m.hold and a in STICK_IDS:
                    target = self._hold_target(a, m, dt)
                elif m.mode in HELD_MODES or a in GYRO_IDS:
                    x = self.axes.get(a, 0.0)
                    if a in GYRO_IDS and not self.gyro_active:
                        x = 0.0  # the gyro only moves while its button is held / toggled on
                    elif a in TRIGGER_IDS:
                        x = shape_trigger(x, m)
                    else:
                        x = shape_stick(x, m)
                    if x != 0.0 and dt > 0:
                        self._move_held(a, x * m.max_speed * fine * dt)
                    target = quantize(self.rate_values[a], m.hires)
                else:
                    target = self._compute_absolute(a, m)
                if target != self._target.get(a):
                    self._target[a] = target
                    self._pending.add(a)
            self._touch_was_down = self.touch_down
            self._flush_pending(now)

    # ------------------------------------------------------------ shared values
    def _group(self, a: str) -> list[str]:
        """Held-value axes sending the same CC as `a` (e.g. left stick X, touch X and gyro
        turn all on CC 10): they share one value, so switching between them never jumps."""
        m = self.profile.axes.get(a)
        if not m:
            return [a]
        key = (m.channel, m.cc, m.hires)
        return [b for b, n in self.profile.axes.items()
                if n.enabled and (b == a or (is_held(b, n) and (n.channel, n.cc, n.hires) == key))]

    def _set_held(self, a: str, v: float) -> None:
        v = max(0.0, min(1.0, v))
        for b in self._group(a):
            self.rate_values[b] = v
        self.rate_dirty = True

    def _move_held(self, a: str, delta: float) -> None:
        for b in self._group(a):
            self._glides.pop(b, None)  # moving by hand cancels a glide to center
        self._set_held(a, self.rate_values[a] + delta)

    def _hold_target(self, a: str, m: AxisMapping, dt: float) -> int:
        """Absolute stick that keeps its position when released.

        Following: the head goes where the stick points. A fast return to center is the
        spring, so the value freezes. Frozen: the stick takes over again only when it
        reaches (or crosses) the kept value, so there is never a jump.
        """
        x = shape_stick(self.axes.get(a, 0.0), m)
        v = 0.5 + x * 0.5 * m.span
        st = self._hold_state.setdefault(a, {"held": True, "px": x, "pv": v})
        if not st["held"]:
            springing = dt > 0 and abs(x) < abs(st["px"]) and (abs(st["px"]) - abs(x)) / dt > RELEASE_SPEED
            if x == 0.0 or springing:
                st["held"] = True
            else:
                self._set_held(a, v)
        else:
            kept = self.rate_values[a]
            if x != 0.0 and ((st["pv"] - kept) * (v - kept) <= 0 or abs(v - kept) < PICKUP):
                st["held"] = False
                self._set_held(a, v)
        st["px"], st["pv"] = x, v
        return quantize(self.rate_values[a], m.hires)

    def _touch_target(self, a: str, m: AxisMapping, fine: float) -> int | None:
        pos = self.axes.get(a, 0.5)
        if m.invert:
            pos = 1.0 - pos
        if m.mode == "relative":
            if self.touch_down and self._touch_was_down:
                self._move_held(a, (pos - self._touch_last.get(a, pos)) * m.max_speed * fine)
            if self.touch_down:
                self._touch_last[a] = pos
            return quantize(self.rate_values[a], m.hires)
        if self.touch_down:  # absolute: the finger position; held after the finger lifts
            self._set_held(a, 0.5 + (pos - 0.5) * m.span)
        return quantize(self.rate_values[a], m.hires)

    # ------------------------------------------------------------ light bar
    def layer_color(self, key: str) -> tuple[int, int, int]:
        layers = self.profile.reachable_layers()
        i = layers.index(key) if key in layers else 0
        return LAYER_COLORS[i % len(LAYER_COLORS)]

    def _update_led(self) -> None:
        if not (self.info.get("led") and self.profile.options.led_layers):
            return
        key = self.current_layer
        if key != self._led_layer:
            self._led_layer = key
            self.led_color = self.layer_color(key)
            self._led_out = self.led_color

    def _run_glides(self, now: float) -> None:
        for a, (start, t0, dur) in list(self._glides.items()):
            m = self.profile.axes.get(a)
            if not m or not m.enabled or not is_held(a, m) or (
                    self.connected and abs(self.axes.get(a, 0.0)) > GLIDE_CANCEL):
                del self._glides[a]  # the user took over
                continue
            p = (now - t0) / dur
            if p >= 1.0:
                v = 0.5
                del self._glides[a]
            else:
                v = start + (0.5 - start) * smoothstep(p)
            self._set_held(a, v)
            target = quantize(v, m.hires)
            if target != self._target.get(a):
                self._target[a] = target
                self._pending.add(a)

    def _compute_absolute(self, a: str, m: AxisMapping) -> int:
        x = self.axes.get(a, 0.0)
        if a in TRIGGER_IDS:
            v = shape_trigger(x, m) * m.span
        else:
            v = 0.5 + shape_stick(x, m) * 0.5 * m.span  # center = 64 / 8192
        return quantize(v, m.hires)

    def _flush_pending(self, now: float) -> None:
        if not self._pending:
            return
        min_interval = 1.0 / max(1, self.profile.options.max_rate_hz)
        for a in list(self._pending):
            m = self.profile.axes.get(a)
            if not m or not m.enabled:
                self._pending.discard(a)
                continue
            if now - self._last_send.get(a, -1e9) < min_interval:
                continue  # stays pending, sent on a later tick
            self._pending.discard(a)
            self._send_axis(a, m, self._target[a], now)

    def _send_axis(self, a: str, m: AxisMapping, value: int, now: float) -> None:
        if self.outputs.get(a) == value:
            return
        self.outputs[a] = value
        self._last_send[a] = now
        key = (m.channel, m.cc, m.hires)
        if self._sent_cc.get(key) == value:
            return  # another axis on the same CC already sent this value
        self._sent_cc[key] = value
        ch = m.channel - 1
        if m.hires:
            self.midi.send(mido.Message("control_change", channel=ch, control=m.cc, value=value >> 7))
            self.midi.send(mido.Message("control_change", channel=ch, control=m.cc + 32, value=value & 127))
        else:
            self.midi.send(mido.Message("control_change", channel=ch, control=m.cc, value=value))

    # ------------------------------------------------------------ buttons
    @property
    def current_layer(self) -> str:
        return layer_key(self.active_mods)

    def _on_press(self, b: str) -> None:
        key = self.current_layer
        m = self.profile.effective_map(key, b)
        if m and m.enabled and m.action == "modifier":
            self.active_mods.append(b)  # following buttons are in a new layer
            return
        self._held[b] = key
        if m and m.enabled:
            self._button_press(b, m, key)

    def _on_release(self, b: str) -> None:
        if b in self.active_mods:
            self.active_mods.remove(b)
            return
        key = self._held.pop(b, None)
        if key is None:
            return  # pressed before connecting or during Learn
        m = self.profile.effective_map(key, b)
        if m and m.enabled:
            self._button_release(b, m)

    def _button_press(self, b: str, m: ButtonMapping, key: str = "") -> None:
        if m.action in CENTER_TARGETS:
            self.center_axes(CENTER_TARGETS[m.action])
            return
        if m.action == "fine":
            return
        if m.action == "gyro":
            if m.mode == "toggle":
                self._gyro_toggle = not self._gyro_toggle
            return
        ch = m.channel - 1
        if m.type == "pc":
            self.midi.send(mido.Message("program_change", channel=ch, program=m.number))
        elif m.type == "note":
            if m.mode == "toggle":
                tk = f"{key}|{b}"
                on = not self.toggles.get(tk, False)
                self.toggles[tk] = on
                self._note(ch, m, on)
            else:
                self._note(ch, m, True)
        elif m.type == "cc":
            if m.mode == "toggle":
                tk = f"{key}|{b}"
                on = not self.toggles.get(tk, False)
                self.toggles[tk] = on
                self.midi.send(mido.Message("control_change", channel=ch, control=m.number,
                                            value=127 if on else 0))
            elif m.mode == "value":
                self.midi.send(mido.Message("control_change", channel=ch, control=m.number, value=m.value))
            else:
                self.midi.send(mido.Message("control_change", channel=ch, control=m.number, value=127))

    def _button_release(self, b: str, m: ButtonMapping) -> None:
        if m.action != "midi" or m.mode != "momentary":
            return
        ch = m.channel - 1
        if m.type == "note":
            self._note(ch, m, False)
        elif m.type == "cc":
            self.midi.send(mido.Message("control_change", channel=ch, control=m.number, value=0))

    def _note(self, ch: int, m: ButtonMapping, on: bool) -> None:
        if on:
            self.midi.send(mido.Message("note_on", channel=ch, note=m.number, velocity=max(1, m.value)))
        else:
            self.midi.send(mido.Message("note_off", channel=ch, note=m.number, velocity=0))

    # ------------------------------------------------------------ learn
    def _detect_learn(self) -> None:
        """A pressed button → edit it in the current layer. Modifiers change the layer;
        a modifier pressed and released on its own → edit the modifier itself."""
        for b in BUTTON_IDS:
            pressed, before = self.buttons.get(b, False), self._prev.get(b, False)
            if pressed and not before:
                key = self.current_layer
                m = self.profile.effective_map(key, b)
                if m and m.enabled and m.action == "modifier":
                    self.active_mods.append(b)
                    self._learn_mod = (b, key)
                    continue
                self._finish_learn((b, key))
                return
            if before and not pressed and b in self.active_mods:
                self.active_mods.remove(b)
                if self._learn_mod and self._learn_mod[0] == b:
                    self._finish_learn(self._learn_mod)
                    return
        if self.touch_down:
            self._finish_learn(("touchx", ""))
            return
        for a in STICK_IDS + TRIGGER_IDS:  # not the gyro: the pad moves while you press buttons
            if abs(self.axes.get(a, 0.0)) > LEARN_AXIS_THRESHOLD:
                self._finish_learn((a, ""))
                return

    def _finish_learn(self, result: tuple[str, str]) -> None:
        self.learned = result
        self.learn = False
        self._learn_mod = None

    # ------------------------------------------------------------ GUI API
    def set_profile(self, profile: Profile, rate_values: dict | None = None) -> None:
        with self.lock:
            if getattr(self, "profile", None) is not None and self._held:
                self._release_all_held()  # note off with the old profile's mapping
            self.active_mods.clear()
            self._held.clear()
            self.profile = profile
            self.toggles.clear()
            self._pending.clear()
            self._glides.clear()
            if rate_values is not None:
                for a in AXIS_IDS:
                    self.rate_values[a] = max(0.0, min(1.0, float(rate_values.get(a, 0.5))))
            # New axes start from the current position without sending.
            self._hold_state.clear()
            for a, m in profile.axes.items():
                if is_held(a, m):
                    self._target[a] = self.outputs[a] = quantize(self.rate_values[a], m.hires)
                else:
                    self._target[a] = self._compute_absolute(a, m)

    def mapping_changed(self, input_id: str) -> None:
        """After editing an axis: send no jump, just sync the target."""
        with self.lock:
            m = self.profile.axes.get(input_id)
            if m is None:
                return
            self._pending.discard(input_id)
            self._hold_state.pop(input_id, None)
            if is_held(input_id, m):
                self._target[input_id] = self.outputs[input_id] = quantize(self.rate_values[input_id], m.hires)
            else:
                self._target[input_id] = self._compute_absolute(input_id, m)
                self.outputs[input_id] = self._target[input_id]

    def center_all(self) -> None:
        self.center_axes(CENTER_TARGETS["center"])

    def center_axes(self, axes, duration: float | None = None) -> None:
        """Smoothly glide rate-mode axes back to the middle: 64 / 8192."""
        with self.lock:
            dur = self.profile.options.center_time if duration is None else duration
            now = self._last_step if self._last_step is not None else time.perf_counter()
            for a in axes:
                m = self.profile.axes.get(a)
                if not m or not m.enabled or not is_held(a, m):
                    continue
                if dur <= 0:
                    self._glides.pop(a, None)
                    self._set_held(a, 0.5)
                    self._force_axis(a, m, quantize(0.5, m.hires))
                else:
                    self._glides[a] = (self.rate_values[a], now, dur)

    def set_axis_value(self, a: str, v: float) -> None:
        """Test mode: set an axis directly (0..1) and send only its CC."""
        with self.lock:
            m = self.profile.axes.get(a)
            if not m:
                return
            v = max(0.0, min(1.0, v))
            self._glides.pop(a, None)
            if is_held(a, m):
                self._set_held(a, v)
            self._force_axis(a, m, quantize(v, m.hires))

    def _force_axis(self, a: str, m: AxisMapping, value: int) -> None:
        """Send now, even if the same value went out before (center, test sliders, resend)."""
        if is_held(a, m):
            for b in self._group(a):
                self._target[b] = self.outputs[b] = value
                self._pending.discard(b)
        self._pending.discard(a)
        self.outputs.pop(a, None)
        self._sent_cc.pop((m.channel, m.cc, m.hires), None)
        self._send_axis(a, m, value, self._last_step or time.perf_counter())

    def resend_rate_values(self) -> None:
        with self.lock:
            done = set()
            for a, m in self.profile.axes.items():
                key = (m.channel, m.cc, m.hires)
                if m.enabled and is_held(a, m) and key not in done:
                    done.add(key)
                    self._force_axis(a, m, quantize(self.rate_values[a], m.hires))

    def axis_value_01(self, a: str) -> float:
        """Current output value of the axis 0..1 (for display)."""
        m = self.profile.axes.get(a)
        if not m:
            return 0.0
        out = self.outputs.get(a)
        if out is None:
            return self.rate_values[a] if is_held(a, m) else 0.0
        return out / (16383 if m.hires else 127)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "connected": self.connected,
                "name": self.controller_name,
                "devices": list(self.devices),
                "error": self.error,
                "buttons": dict(self.buttons),
                "axes": dict(self.axes),
                "outputs": dict(self.outputs),
                "rate_values": dict(self.rate_values),
                "toggles": dict(self.toggles),
                "layer": self.current_layer,
                "active_mods": list(self.active_mods),
                "fine": self.fine_active,
                "gyro": self.gyro_active,
                "touch_down": self.touch_down,
                "info": dict(self.info),
                "led": self.led_color,
                "gliding": set(self._glides),
                "learn": self.learn,
                "learned": self.learned,
                "seen": set(self.seen),
                "background_ok": self.background_ok,
                "last_input_time": self.last_input_time,
            }
