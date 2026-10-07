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
