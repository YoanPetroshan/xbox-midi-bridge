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
from mapping import (AXIS_IDS, BUTTON_IDS, CENTER_TARGETS, TRIGGER_IDS, AxisMapping,
                     ButtonMapping, Profile, layer_key)

TICK_HZ = 250
SETTLE_S = 0.15  # after connecting: wait for real values before reacting
MAX_DT = 0.05  # guard against jumps if the thread stalls
LEARN_AXIS_THRESHOLD = 0.6
# While gliding to center, small stick touches (e.g. while clicking L3) are ignored;
# only a clear deflection above this threshold cancels the glide.
GLIDE_CANCEL = 0.5


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

    reader_prefer: str | None = None

    # ------------------------------------------------------------ input
    def feed(self, msg: tuple, now: float) -> None:
        with self.lock:
            kind = msg[0]
            if kind == "state":
                _, buttons, axes = msg
                self.buttons = dict(zip(BUTTON_IDS, buttons))
                self.axes = dict(zip(AXIS_IDS, axes))
                self.last_input_time = now
                self.input_count += 1
                if not self.app_active and self.connected:
                    self.background_ok = True
                for b, p in self.buttons.items():
                    if p:
                        self.seen.add(b)
                for a, v in self.axes.items():
                    if abs(v) > 0.3:
                        self.seen.add(a)
            elif kind == "connected":
                self.connected = True
                self.controller_name = msg[1]
                self.sdl_mapping = msg[2] if len(msg) > 2 else {}
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
                    if m.mode == "absolute":
                        self._target[a] = self._compute_absolute(a, m)
                        self._pending.discard(a)
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
            for b, key in self._held.items():
                m = self.profile.effective_map(key, b)
                if m and m.enabled and m.action == "fine":
                    self.fine_active = True

            for a in AXIS_IDS:
                m = self.profile.axes.get(a)
                if not m or not m.enabled:
                    continue
                if m.mode == "rate" and a in self._glides:
                    target = quantize(self.rate_values[a], m.hires)
                elif m.mode == "rate":
                    x = self.axes.get(a, 0.0)
                    if a in TRIGGER_IDS:
                        x = shape_trigger(x, m)
                    else:
                        x = shape_stick(x, m)
                    if x != 0.0 and dt > 0:
                        speed = m.max_speed * (self.profile.options.fine_factor if self.fine_active else 1.0)
                        v = self.rate_values[a] + x * speed * dt
                        self.rate_values[a] = max(0.0, min(1.0, v))
                        self.rate_dirty = True
                    target = quantize(self.rate_values[a], m.hires)
                else:
                    target = self._compute_absolute(a, m)
                if target != self._target.get(a):
                    self._target[a] = target
                    self._pending.add(a)
            self._flush_pending(now)

    def _run_glides(self, now: float) -> None:
        for a, (start, t0, dur) in list(self._glides.items()):
            m = self.profile.axes.get(a)
            if not m or not m.enabled or m.mode != "rate" or (
                    self.connected and abs(self.axes.get(a, 0.0)) > GLIDE_CANCEL):
                del self._glides[a]  # the user took over
                continue
            p = (now - t0) / dur
            if p >= 1.0:
                v = 0.5
                del self._glides[a]
            else:
                v = start + (0.5 - start) * smoothstep(p)
            self.rate_values[a] = v
            self.rate_dirty = True
            target = quantize(v, m.hires)
            if target != self._target.get(a):
                self._target[a] = target
                self._pending.add(a)

    def _compute_absolute(self, a: str, m: AxisMapping) -> int:
        x = self.axes.get(a, 0.0)
        if a in TRIGGER_IDS:
            v = shape_trigger(x, m)
        else:
            v = (shape_stick(x, m) + 1.0) / 2.0  # center = 64 / 8192
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
        for a in AXIS_IDS:
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
            for a, m in profile.axes.items():
                if m.mode == "rate":
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
            if m.mode == "rate":
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
                if not m or not m.enabled or m.mode != "rate":
                    continue
                if dur <= 0:
                    self._glides.pop(a, None)
                    self.rate_values[a] = 0.5
                    self.rate_dirty = True
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
            if m.mode == "rate":
                self.rate_values[a] = v
                self.rate_dirty = True
            self._force_axis(a, m, quantize(v, m.hires))

    def _force_axis(self, a: str, m: AxisMapping, value: int) -> None:
        if m.mode == "rate":
            self._target[a] = value
        self._pending.discard(a)
        self.outputs.pop(a, None)
        self._send_axis(a, m, value, self._last_step or time.perf_counter())

    def resend_rate_values(self) -> None:
        with self.lock:
            for a, m in self.profile.axes.items():
                if m.enabled and m.mode == "rate":
                    self._force_axis(a, m, quantize(self.rate_values[a], m.hires))

    def axis_value_01(self, a: str) -> float:
        """Current output value of the axis 0..1 (for display)."""
        m = self.profile.axes.get(a)
        if not m:
            return 0.0
        out = self.outputs.get(a)
        if out is None:
            return self.rate_values[a] if m.mode == "rate" else 0.0
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
                "gliding": set(self._glides),
                "learn": self.learn,
                "learned": self.learned,
                "seen": set(self.seen),
                "background_ok": self.background_ok,
                "last_input_time": self.last_input_time,
            }
