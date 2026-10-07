"""Engine tests without a controller or MIDI hardware: python -m unittest discover tests"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import Engine, quantize, shape_stick  # noqa: E402
from mapping import AXIS_IDS, BUTTON_IDS, AxisMapping, ProfileStore, default_profile  # noqa: E402


class FakeMidi:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)

    def cc(self, control):
        return [m.value for m in self.sent if m.type == "control_change" and m.control == control]


def state(buttons=(), **axes):
    b = tuple(x in buttons for x in BUTTON_IDS)
    a = tuple(axes.get(x, 0.0) for x in AXIS_IDS)
    return ("state", b, a)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.midi = FakeMidi()
        self.eng = Engine(default_profile(), self.midi)
        self.t = 0.0
        self.eng.feed(("connected", "Test Pad", {}), self.t)
        self.eng.feed(state(), self.t)
        self.run_for(0.3)  # settle + baseline
        self.midi.sent.clear()

    def run_for(self, seconds, dt=0.004):
        end = self.t + seconds
        while self.t < end:
            self.t += dt
            self.eng.step(self.t)

    def test_nothing_sent_at_rest(self):
        self.run_for(1.0)
        self.assertEqual(self.midi.sent, [])

    def test_note_momentary(self):
        self.eng.feed(state(buttons=("a",)), self.t)
        self.run_for(0.01)
        self.eng.feed(state(), self.t)
        self.run_for(0.01)
        types = [(m.type, m.note) for m in self.midi.sent]
        self.assertEqual(types, [("note_on", 36), ("note_off", 36)])

    def test_note_toggle(self):
        self.eng.profile.buttons["b"].mode = "toggle"
        for _ in range(2):
            self.eng.feed(state(buttons=("b",)), self.t)
            self.run_for(0.01)
            self.eng.feed(state(), self.t)
            self.run_for(0.01)
        self.assertEqual([m.type for m in self.midi.sent], ["note_on", "note_off"])

    def test_cc_value_and_pc(self):
        m = self.eng.profile.buttons["x"]
        m.type, m.mode, m.number, m.value = "cc", "value", 70, 99
        p = self.eng.profile.buttons["y"]
        p.type, p.number = "pc", 5
        self.eng.feed(state(buttons=("x", "y")), self.t)
        self.run_for(0.01)
        self.eng.feed(state(), self.t)
        self.run_for(0.01)
        self.assertEqual([(s.type, getattr(s, "value", None)) for s in self.midi.sent],
                         [("control_change", 99), ("program_change", None)])

    def test_rate_mode_moves_and_holds(self):
        self.eng.feed(state(leftx=1.0), self.t)
        self.run_for(0.5)
        self.eng.feed(state(), self.t)
        self.run_for(0.5)
        vals = self.midi.cc(10)
        self.assertTrue(vals, "Pan must move")
        self.assertEqual(vals, sorted(vals), "the value must only increase")
        self.assertGreater(vals[-1], 80)
        n = len(self.midi.sent)
        self.run_for(1.0)
        self.assertEqual(len(self.midi.sent), n, "released stick: no drift, no return to center")

    def test_deadzone_no_drift(self):
        self.eng.feed(state(leftx=0.05, lefty=-0.07), self.t)
        self.run_for(2.0)
        self.assertEqual(self.midi.sent, [])

    def test_rate_limit(self):
        self.eng.profile.axes["leftx"].hires = True
        self.eng.profile.options.max_rate_hz = 50
        self.eng.feed(state(leftx=1.0), self.t)
        self.run_for(1.0)
        msb = self.midi.cc(10)
        self.assertLessEqual(len(msb), 52)
        self.assertEqual(len(self.midi.cc(42)), len(msb), "14-bit: an LSB on CC+32 for every MSB")

    def test_fine_slows_down(self):
        self.eng.profile.buttons["rightshoulder"].action = "fine"
        self.eng.feed(state(buttons=("rightshoulder",), leftx=1.0), self.t)
        self.run_for(0.5)
        slow = self.eng.rate_values["leftx"] - 0.5
        self.eng.rate_values["leftx"] = 0.5
        self.eng.feed(state(leftx=1.0), self.t)
        self.run_for(0.5)
        fast = self.eng.rate_values["leftx"] - 0.5
        self.assertAlmostEqual(slow / fast, 0.25, places=2)

    def test_center_button_all(self):
        self.eng.profile.buttons["start"].action = "center"
        self.eng.profile.options.center_time = 0
        self.eng.rate_values["leftx"] = 0.9
        self.eng.feed(state(buttons=("start",)), self.t)
        self.run_for(0.01)
        self.assertEqual(self.midi.cc(10)[-1], 64)
        self.assertEqual(self.midi.cc(13)[-1], 64)

    def test_l3_glides_left_stick_to_center(self):
        self.eng.profile.options.center_time = 1.0
        for a, v in (("leftx", 0.9), ("lefty", 0.1), ("rightx", 0.8)):
            self.eng.rate_values[a] = v
            self.eng.mapping_changed(a)  # sync without sending
        # L3 clicked with a slight stick touch (below threshold): must not cancel the glide
        self.eng.feed(state(buttons=("leftstick",), leftx=0.2), self.t)
        self.run_for(0.5)
        mid = self.eng.rate_values["leftx"]
        self.assertTrue(0.55 < mid < 0.85, mid)
        self.eng.feed(state(), self.t)
        self.run_for(0.6)
        pan, tilt = self.midi.cc(10), self.midi.cc(11)
        self.assertEqual(pan[-1], 64)
        self.assertEqual(tilt[-1], 64)
        self.assertEqual(pan, sorted(pan, reverse=True), "smooth, no bounces")
        self.assertGreater(len(pan), 10, "many intermediate steps, not a jump")
        self.assertEqual(self.midi.cc(12), [], "the right stick is untouched")
        self.assertAlmostEqual(self.eng.rate_values["rightx"], 0.8)

    def test_r3_glide_cancelled_by_stick(self):
        self.eng.rate_values["rightx"] = 0.2
        self.eng.feed(state(buttons=("rightstick",)), self.t)
        self.run_for(0.2)
        self.eng.feed(state(rightx=-1.0), self.t)
        self.run_for(0.3)
        self.assertNotIn("rightx", self.eng._glides)
        self.assertLess(self.eng.rate_values["rightx"], 0.45)

    def test_old_profile_migrates_l3_r3(self):
        from mapping import Profile
        old = default_profile().to_json()
        old["version"] = 1
        for b, n in (("leftstick", 44), ("rightstick", 45)):
            old["buttons"][b].update(action="midi", number=n)
        old["buttons"]["rightstick"]["number"] = 70  # user customisation: left alone
        p = Profile.from_json(old)
        self.assertEqual(p.buttons["leftstick"].action, "center_left")
        self.assertEqual(p.buttons["rightstick"].action, "midi")

    def test_trigger_absolute(self):
        self.eng.feed(state(righttrigger=1.0), self.t)
        self.run_for(0.05)
        self.eng.feed(state(righttrigger=0.0), self.t)
        self.run_for(0.05)
        self.assertEqual(self.midi.cc(21), [127, 0])

    def test_reconnect_no_jumps(self):
        self.eng.feed(state(leftx=1.0, righttrigger=0.5), self.t)
        self.run_for(0.3)
        pan = self.eng.outputs["leftx"]
        trig = self.eng.outputs["righttrigger"]
        self.eng.feed(("disconnected",), self.t)
        self.run_for(1.0)
        self.midi.sent.clear()
        # On reconnect SDL sometimes reports zeros for the first frames.
        self.eng.feed(("connected", "Test Pad", {}), self.t)
        self.eng.feed(state(), self.t)
        self.run_for(0.05)
        self.eng.feed(state(righttrigger=0.5, buttons=("a",)), self.t)
        self.run_for(0.5)
        self.assertEqual(self.eng.outputs["leftx"], pan)
        self.assertEqual(self.eng.outputs["righttrigger"], trig)
        self.assertEqual(self.midi.sent, [], "a button held while connecting is not a press")

    def test_disconnect_releases_held_note(self):
        self.eng.feed(state(buttons=("a",)), self.t)
        self.run_for(0.01)
        self.eng.feed(("disconnected",), self.t)
        self.assertEqual([m.type for m in self.midi.sent], ["note_on", "note_off"])

    def test_learn(self):
        self.eng.learn = True
        self.eng.feed(state(buttons=("dpleft",)), self.t)
        self.run_for(0.01)
        self.assertEqual(self.eng.learned, ("dpleft", ""))
        self.assertFalse(self.eng.learn)
        self.assertEqual(self.midi.sent, [])

    def test_test_slider_sends_only_that_cc(self):
        self.eng.set_axis_value("righty", 0.75)
        self.assertEqual([(m.control, m.value) for m in self.midi.sent], [(13, 95)])
        self.run_for(0.1)
        self.assertEqual(len(self.midi.sent), 1)


class LayerTest(unittest.TestCase):
    """Modifiers: X, LB+X, D-pad↑+X, LB+D-pad↑+X are different buttons."""

    def setUp(self):
        from mapping import ButtonMapping
        self.BM = ButtonMapping
        p = default_profile()
        p.buttons["leftshoulder"].action = "modifier"
        p.buttons["dpup"].action = "modifier"
        # D-pad ← is a CC in the base layer but a modifier in the LB layer
        p.buttons["dpleft"] = ButtonMapping(type="cc", number=60)
        p.layers["leftshoulder"] = {
            "x": ButtonMapping(type="cc", number=53),
            "dpleft": ButtonMapping(action="modifier"),
        }
        p.layers["dpup"] = {"x": ButtonMapping(number=70)}
        p.layers["leftshoulder+dpup"] = {"x": ButtonMapping(number=71)}
        p.layers["leftshoulder+dpleft"] = {"x": ButtonMapping(number=72)}
        self.midi = FakeMidi()
        self.eng = Engine(p, self.midi)
        self.t = 0.0
        self.eng.feed(("connected", "Pad", {}), self.t)
        self.eng.feed(state(), self.t)
        self.run_for(0.3)

    def run_for(self, seconds, dt=0.004):
        end = self.t + seconds
        while self.t < end:
            self.t += dt
            self.eng.step(self.t)

    def press(self, *held):
        self.eng.feed(state(buttons=held), self.t)
        self.run_for(0.01)

    def sent(self):
        out = [(m.type, getattr(m, "note", getattr(m, "control", None))) for m in self.midi.sent]
        self.midi.sent.clear()
        return out

    def test_plain_and_modified(self):
        self.press("x")
        self.press()
        self.assertEqual(self.sent(), [("note_on", 38), ("note_off", 38)])
        self.press("leftshoulder")
        self.assertEqual(self.sent(), [], "a modifier alone sends nothing")
        self.press("leftshoulder", "x")
        self.press("leftshoulder")
        self.press()
        self.assertEqual(self.sent(), [("control_change", 53), ("control_change", 53)])

    def test_combination_is_own_layer(self):
        self.press("dpup")
        self.press("dpup", "x")
        self.press()
        self.assertEqual(self.sent(), [("note_on", 70), ("note_off", 70)])
        self.press("leftshoulder")
        self.press("leftshoulder", "dpup")
        self.press("leftshoulder", "dpup", "x")
        self.assertEqual(self.eng.current_layer, "leftshoulder+dpup")
        self.press()
        self.assertEqual(self.sent(), [("note_on", 71), ("note_off", 71)])

    def test_modifier_order_gives_same_layer(self):
        self.press("dpup")
        self.press("dpup", "leftshoulder")
        self.press("dpup", "leftshoulder", "x")
        self.press()
        self.assertEqual(self.sent(), [("note_on", 71), ("note_off", 71)])

    def test_explicit_override_cancels_inherited_modifier(self):
        # In the LB layer D-pad ↑ is explicitly CC 80, not a modifier
        self.eng.profile.layers["leftshoulder"]["dpup"] = self.BM(type="cc", number=80)
        self.press("leftshoulder")
        self.press("leftshoulder", "dpup")
        self.press("leftshoulder", "dpup", "x")
        self.press()
        self.assertEqual(self.sent(), [("control_change", 80), ("control_change", 53),
                                       ("control_change", 53), ("control_change", 80)])

    def test_button_becomes_modifier_only_in_layer(self):
        self.press("dpleft")
        self.press()
        self.assertEqual(self.sent(), [("control_change", 60), ("control_change", 60)])
        self.press("leftshoulder")
        self.press("leftshoulder", "dpleft")
        self.press("leftshoulder", "dpleft", "x")
        self.press()
        self.assertEqual(self.sent(), [("note_on", 72), ("note_off", 72)])

    def test_order_matters(self):
        # D-pad ← pressed before LB → sends its base-layer CC, not a modifier
        self.press("dpleft")
        self.press("dpleft", "leftshoulder")
        self.press("dpleft", "leftshoulder", "x")
        self.assertEqual(self.sent(), [("control_change", 60), ("control_change", 53)])

    def test_release_uses_press_layer(self):
        self.eng.profile.layers["leftshoulder"]["y"] = self.BM(number=90)
        self.press("leftshoulder")
        self.press("leftshoulder", "y")
        self.press("y")  # LB released first
        self.press()
        self.assertEqual(self.sent(), [("note_on", 90), ("note_off", 90)])

    def test_unassigned_in_layer_does_nothing(self):
        self.press("leftshoulder")
        self.press("leftshoulder", "a")
        self.press()
        self.assertEqual(self.sent(), [])

    def test_learn_in_layer(self):
        self.eng.learn = True
        self.press("leftshoulder")
        self.press("leftshoulder", "x")
        self.assertEqual(self.eng.learned, ("x", "leftshoulder"))
        self.press()
        self.eng.learn = True
        self.press("leftshoulder")
        self.press()
        self.assertEqual(self.eng.learned, ("leftshoulder", ""), "modifier pressed on its own")
        self.assertEqual(self.sent(), [])

    def test_reachable_layers_and_roundtrip(self):
        from mapping import Profile
        layers = self.eng.profile.reachable_layers()
        self.assertEqual(layers[0], "")
        for k in ("leftshoulder", "dpup", "leftshoulder+dpup", "leftshoulder+dpleft"):
            self.assertIn(k, layers)
        q = Profile.from_json(self.eng.profile.to_json())
        self.assertEqual(q.layers["leftshoulder+dpleft"]["x"].number, 72)
        self.assertEqual(q.reachable_layers(), layers)

    def test_no_impossible_dpad_layers(self):
        p = self.eng.profile
        for d in ("dpdown", "dpright"):
            p.buttons[d].action = "modifier"
        layers = p.reachable_layers()
        self.assertIn("dpup+dpright", layers)
        self.assertNotIn("dpup+dpdown", layers)
        self.assertFalse(any("dpleft" in k and "dpright" in k for k in layers))

    def test_free_note_skips_used(self):
        n = self.eng.profile.free_note()
        self.assertNotIn(n, (36, 37, 38, 39, 40, 41, 42, 43, 46, 47, 48, 49, 50, 51, 70, 71, 72))


def pad_state(buttons=(), touch=None, **axes):
    """State message with the touchpad: touch=(x, y) while a finger is down."""
    msg = state(buttons, **axes)
    vals = dict(zip(AXIS_IDS, msg[2]))
    if touch:
        vals["touchx"], vals["touchy"] = touch
    return ("state", msg[1], tuple(vals[a] for a in AXIS_IDS), bool(touch))


class PlayStationTest(unittest.TestCase):
    """DualSense extras: touchpad, gyro with its button, light bar, shared CCs."""

    def setUp(self):
        self.midi = FakeMidi()
        self.eng = Engine(default_profile(), self.midi)
        self.t = 0.0
        info = {"family": "playstation", "touchpad": True, "gyro": True, "led": True}
        self.eng.feed(("connected", "PS5 Controller", {}, info), self.t)
        self.eng.feed(pad_state(), self.t)
        self.run_for(0.3)
        self.midi.sent.clear()

    def run_for(self, seconds, dt=0.004):
        end = self.t + seconds
        while self.t < end:
            self.t += dt
            self.eng.step(self.t)

    def test_touch_drag_moves_like_a_trackpad(self):
        # default: touch X is "drag" on CC 10 with sensitivity 0.5 per full swipe
        self.eng.feed(pad_state(touch=(0.2, 0.5)), self.t)
        self.run_for(0.05)
        self.assertEqual(self.midi.cc(10), [], "putting a finger down is not a move")
        for i in range(1, 11):
            self.eng.feed(pad_state(touch=(0.2 + 0.06 * i, 0.5)), self.t)
            self.run_for(0.02)
        self.assertAlmostEqual(self.eng.rate_values["touchx"], 0.5 + 0.6 * 0.5, places=2)
        self.eng.feed(pad_state(), self.t)  # lift
        self.run_for(0.1)
        self.eng.feed(pad_state(touch=(0.9, 0.5)), self.t)  # new touch elsewhere: no jump
        self.run_for(0.1)
        self.assertAlmostEqual(self.eng.rate_values["touchx"], 0.8, places=2)

    def test_touch_and_stick_share_cc_10(self):
        self.eng.feed(pad_state(leftx=1.0), self.t)
        self.run_for(0.4)
        self.eng.feed(pad_state(), self.t)
        self.run_for(0.05)
        stick = self.eng.rate_values["leftx"]
        self.assertAlmostEqual(self.eng.rate_values["touchx"], stick)
        self.assertAlmostEqual(self.eng.rate_values["gyroyaw"], stick)
        vals = self.midi.cc(10)
        self.assertEqual(len(vals), len(set(vals)), "one message per value, not one per axis")
        self.assertEqual(vals, sorted(vals))

    def test_touch_absolute_with_sensitivity(self):
        m = self.eng.profile.axes["touchx"]
        m.mode, m.span = "absolute", 0.5
        self.eng.mapping_changed("touchx")
        self.eng.feed(pad_state(touch=(1.0, 0.5)), self.t)
        self.run_for(0.05)
        self.eng.feed(pad_state(), self.t)
        self.run_for(0.2)
        self.assertEqual(self.midi.cc(10)[-1], 95, "right edge with span 0.5 = 0.75 → 95, held after lift")

    def test_gyro_needs_its_button(self):
        self.eng.feed(pad_state(gyroyaw=-0.5), self.t)
        self.run_for(0.3)
        self.assertEqual(self.midi.cc(10), [], "gyro without its button does nothing")
        self.eng.profile.buttons["rightshoulder"].action = "gyro"
        self.eng.feed(pad_state(buttons=("rightshoulder",), gyroyaw=-0.5), self.t)
        self.run_for(0.3)
        self.assertGreater(self.eng.rate_values["leftx"], 0.6, "gyro turns head 1 (inverted yaw)")
        self.eng.feed(pad_state(gyroyaw=-0.5), self.t)
        n = len(self.midi.sent)
        self.run_for(0.3)
        self.assertEqual(len(self.midi.sent), n, "button released → gyro stops")

    def test_gyro_toggle(self):
        b = self.eng.profile.buttons["touchpad"]
        b.action, b.mode = "gyro", "toggle"
        self.eng.feed(pad_state(buttons=("touchpad",)), self.t)
        self.run_for(0.01)
        self.eng.feed(pad_state(gyropitch=0.5), self.t)
        self.run_for(0.2)
        self.assertTrue(self.eng.gyro_active)
        self.assertGreater(self.eng.rate_values["lefty"], 0.55)

    def test_light_bar_follows_layer(self):
        from engine import LAYER_COLORS
        self.eng.profile.buttons["leftshoulder"].action = "modifier"
        self.eng.feed(pad_state(), self.t)
        self.run_for(0.01)
        self.assertEqual(self.eng.led_color, LAYER_COLORS[0])
        self.eng.feed(pad_state(buttons=("leftshoulder",)), self.t)
        self.run_for(0.01)
        self.assertEqual(self.eng.led_color, LAYER_COLORS[1])
        self.eng.profile.options.led_layers = False
        self.eng.feed(pad_state(), self.t)
        self.run_for(0.01)
        self.assertEqual(self.eng.led_color, LAYER_COLORS[1], "switched off: colour left alone")


class AbsoluteStickTest(unittest.TestCase):
    """Absolute sticks: sensitivity (span) and hold-on-release with pick-up."""

    def setUp(self):
        self.midi = FakeMidi()
        p = default_profile()
        m = p.axes["leftx"]
        m.mode, m.hold, m.deadzone, m.curve = "absolute", True, 0.05, "linear"
        self.eng = Engine(p, self.midi)
        self.t = 0.0
        self.eng.feed(("connected", "Pad", {}), self.t)
        self.eng.feed(state(), self.t)
        self.run_for(0.3)
        self.midi.sent.clear()

    def run_for(self, seconds, dt=0.004):
        end = self.t + seconds
        while self.t < end:
            self.t += dt
            self.eng.step(self.t)

    def move(self, x, seconds=0.2, steps=20):
        """Move the stick smoothly from where it is to x."""
        start = self.eng.axes["leftx"]
        for i in range(1, steps + 1):
            self.eng.feed(state(leftx=start + (x - start) * i / steps), self.t)
            self.run_for(seconds / steps)

    def test_follows_then_holds_on_spring_back(self):
        self.move(0.8)
        held = self.midi.cc(10)[-1]
        self.assertGreater(held, 100)
        self.eng.feed(state(leftx=0.0), self.t)  # let go: the spring snaps it back
        self.run_for(0.3)
        self.assertEqual(self.midi.cc(10)[-1], held, "position kept")

    def test_pick_up_without_jump(self):
        self.move(0.8)
        held = self.midi.cc(10)[-1]
        self.eng.feed(state(leftx=0.0), self.t)
        self.run_for(0.1)
        n = len(self.midi.sent)
        self.move(0.3, seconds=0.5)  # below the kept value: nothing happens
        self.assertEqual(len(self.midi.sent), n)
        self.move(0.95, seconds=0.5)  # passes the kept value → takes over smoothly
        after = self.midi.cc(10)[n:]
        self.assertTrue(after and abs(after[0] - held) <= 3, after[:3])
        self.assertGreater(after[-1], held)

    def test_slow_return_is_followed(self):
        self.move(0.8)
        self.move(0.3, seconds=1.0)  # a hand moving back slowly
        self.assertLess(self.midi.cc(10)[-1], 90)

    def test_span(self):
        m = self.eng.profile.axes["leftx"]
        m.hold, m.span = False, 0.5
        self.eng.mapping_changed("leftx")
        self.eng.feed(state(leftx=1.0), self.t)
        self.run_for(0.05)
        self.assertEqual(self.midi.cc(10)[-1], 95, "full deflection with span 0.5 = 0.75")

    def test_l3_centers_held_absolute(self):
        self.eng.profile.options.center_time = 0.5
        self.move(0.9)
        self.eng.feed(state(leftx=0.0), self.t)
        self.run_for(0.1)
        self.eng.feed(state(buttons=("leftstick",)), self.t)
        self.run_for(0.7)
        self.assertEqual(self.midi.cc(10)[-1], 64)


class MigrationAndLabelsTest(unittest.TestCase):
    def test_v3_profile_gets_free_notes_for_new_buttons(self):
        from mapping import Profile
        old = default_profile().to_json()
        old["version"] = 3
        for b in ("touchpad", "paddle1", "paddle2", "paddle3", "paddle4"):
            del old["buttons"][b]
        old["buttons"]["a"]["number"] = 52  # the user already uses note 52
        p = Profile.from_json(old)
        nums = [p.buttons[b].number for b in ("touchpad", "paddle1", "paddle2", "paddle3", "paddle4")]
        self.assertNotIn(52, nums)
        self.assertEqual(len(set(nums)), 5)

    def test_family_labels(self):
        import mapping
        try:
            mapping.set_family("playstation")
            self.assertEqual(mapping.INPUT_LABELS["a"], "✕ Cross")
            self.assertEqual(mapping.layer_label("leftshoulder"), "L1")
            self.assertEqual(mapping.unreliable_inputs(), ("guide",))
        finally:
            mapping.set_family("xbox")
        self.assertEqual(mapping.INPUT_LABELS["a"], "A")


class ShapeTest(unittest.TestCase):
    def test_center_values(self):
        self.assertEqual(quantize(0.5, False), 64)
        self.assertEqual(quantize(0.5, True), 8192)

    def test_shape(self):
        m = AxisMapping(deadzone=0.1, curve="linear")
        self.assertEqual(shape_stick(0.09, m), 0.0)
        self.assertAlmostEqual(shape_stick(1.0, m), 1.0)
        self.assertAlmostEqual(shape_stick(-0.55, m), -0.5)
        m.invert = True
        self.assertAlmostEqual(shape_stick(1.0, m), -1.0)


class StoreTest(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            s = ProfileStore(Path(d))
            p = default_profile("Сцена 1")  # non-ASCII name on purpose (file name handling)
            p.buttons["a"].number = 60
            p.axes["leftx"].hires = True
            s.save_profile(p)
            q = ProfileStore(Path(d)).load_profile("Сцена 1")
            self.assertEqual(q.buttons["a"].number, 60)
            self.assertTrue(q.axes["leftx"].hires)
            self.assertIn("Сцена 1", s.list_profiles())


if __name__ == "__main__":
    unittest.main()
