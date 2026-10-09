# Xbox MIDI Bridge

**English** · [Български](README.bg.md)

## ⬇️ [Download for Mac](https://github.com/YoanPetroshan/xbox-midi-bridge/releases/latest/download/Xbox.MIDI.Bridge.zip)

Apple Silicon (M1 or newer) · macOS 15 or later · free · [all versions](../../releases)

Unzip, move **Xbox MIDI Bridge.app** to Applications, and open it. The first time, macOS asks
for confirmation: see [Download](#download) below.

Turn an Xbox Series or PlayStation (DualSense / DualShock 4) controller, over Bluetooth or USB,
into a MIDI controller for
[Lightkey](https://lightkeyapp.com) on macOS. Every button and axis is programmable from a
graphical interface, and the two sticks drive the Pan/Tilt of moving heads in **rate mode**:
the stick sets the speed, and a released stick leaves the head where it is.

> **Made for Lightkey, works with anything that takes MIDI.** The app was created to control
> Lightkey, but it only sends standard MIDI (Note, CC, Program Change) to a virtual MIDI port.
> Any macOS program that accepts MIDI input can use it: other lighting software, DAWs,
> VJ and video software, and so on. The Lightkey-specific parts of this README are just
> the setup steps for Lightkey.

> **Built with [Claude Code](https://claude.com/claude-code).** The code, tests and
> documentation of this project were written with Anthropic's Claude Code.

![Xbox MIDI Bridge](docs/screenshot.png)

## Features

- **Virtual MIDI port** “Xbox MIDI Bridge”: Lightkey sees it directly, no IAC Driver needed.
- **Works in the background** while Lightkey is the frontmost app.
- **Rate mode for Pan/Tilt** with deadzone, expo curve, sensitivity and invert. No drift at center.
- **Absolute mode** where the stick position is the head position, with sensitivity and an optional
  **hold on release** (the spring back to center is ignored; pick-up without jumps).
- **PlayStation controllers:** touchpad as an XY pad or trackpad, gyro aiming, light bar shows the layer.
- **L3 / R3 glide Pan/Tilt back to center** smoothly, with adjustable glide time.
- **Modifiers and layers** (key combos): LB + X, D-pad ↑ + X and LB + D-pad ↑ + X are separate buttons.
- **14-bit CC** (MSB on CC n, LSB on CC n+32) for finer movement.
- Note / CC / Program Change, momentary / toggle / fixed value, any MIDI channel.
- Live controller diagram, MIDI monitor, MIDI Learn, profiles, and a **test mode** for MIDI Learn in Lightkey.
- No jumps on Bluetooth disconnect/reconnect; reconnects automatically.
- **Updates itself** from GitHub releases and shows what's new.
- Interface in **English and Bulgarian**; dark theme for dim venues.

## Download

Get [`Xbox MIDI Bridge.zip`](https://github.com/YoanPetroshan/xbox-midi-bridge/releases/latest/download/Xbox.MIDI.Bridge.zip) (always the latest version), or browse the
[releases page](../../releases).
Requires an **Apple Silicon Mac (M1 or newer) with macOS 15 Sequoia or later**.

1. Unzip it and move `Xbox MIDI Bridge.app` to **Applications**.
2. The app isn't signed with an Apple Developer certificate, so macOS blocks it on first
   launch. Open it once, then go to **System Settings → Privacy & Security** →
   “Xbox MIDI Bridge was blocked…” → **Open Anyway**. Or in Terminal:
   ```bash
   xattr -dr com.apple.quarantine "/Applications/Xbox MIDI Bridge.app"
   ```
3. Pair the controller over Bluetooth (see below).

Settings and profiles live in `~/Library/Application Support/XboxMidiBridge/`
(`profiles/*.json` and `settings.json`). Copy that folder to move your setup to another Mac.

## Updates

From version 1.3.0 the app keeps itself up to date:

- At startup it asks GitHub for the latest release (Settings → “Check for updates at startup”;
  or click **Check for updates** any time). Nothing about you is sent besides the usual web request.
- If there is a newer version it shows **what's new** and offers **Update now**, **Later** or
  **Skip this version**.
- **Update now** downloads the release, checks that it is this app (bundle id, version, intact
  signature), closes the app, swaps it in place and starts the new version. If the swap fails,
  the old version is put back. Updates installed this way don't trigger the “Open Anyway” prompt.
- After an update, **What's new** lists the changes since the version you had (also available in
  Settings → What's new).
- From the command line: `--check-update` prints the installed and the latest version.

Versions before 1.3.0 can't update themselves: download 1.3.0 once by hand.

## Connecting the controller

- **Bluetooth:** hold the pair button (top, next to the USB-C port) until the Xbox button
  blinks fast → System Settings → Bluetooth → “Xbox Wireless Controller” → Connect.
- **PlayStation (DualSense / DualShock 4) over Bluetooth:** hold **PS + Create** (DualShock 4:
  PS + Share) until the light bar flashes → System Settings → Bluetooth → Connect.
- **USB:** plug it in with a USB-C cable.
- If the controller falls asleep, press the Xbox button. The app finds it again without a
  restart and **sends no jumps**: it reads the current position first, then continues.

> **Xbox and Share buttons:** over Bluetooth macOS often intercepts the Xbox button, and
> Share may not be reported at all. They are marked “not detected” until they send an event.
> Don't use them for important functions.

## The interface

- **Controller diagram.** Every input has a leader line to a label with its current mapping
  (e.g. “Note 36 · Ch. 1 · Momentary” or “CC 10 · Rate · Pan 1”). Pressed buttons light up,
  sticks show a dot at their real position, triggers fill up. Axes also show the value sent.
- **Click a label** (or the part on the diagram) to open its editor. Changes apply
  immediately and are saved automatically.
- **Learn:** click “Learn”, then press a controller button to open its editor.
- **L3 / R3:** glide Pan/Tilt of that stick back to the middle (64, or 8192 in 14-bit).
  The glide time (0–10 s, default 1 s, 0 = instant) is set in Settings → “Glide to center”
  or in the L3/R3 editor. It eases in and out; a clear stick movement (over 50%) cancels
  it, a slight touch while clicking the stick does not.
- **Center Pan/Tilt** (top right): the same glide for both heads at once. Each of the three
  center functions can be assigned to any button (Function → “Center …”).
- **Fine:** assign to a button (e.g. RB), Function → “Fine”. While held, the sticks move
  ×0.25 (adjustable).
- **MIDI monitor:** the messages actually sent, with pause.
- **Lightkey test:** a slider per axis, no controller needed.
- **Profiles:** new / duplicate / rename / delete / reset to default.
- **Status bar:** controller, MIDI port, and whether data arrives in the background
  (green once the app receives data while another window is in front).
- **Language:** Settings → Language (Auto follows the macOS language). Applied on restart.

### Axis parameters

| Parameter | Meaning |
|---|---|
| Mode | **Rate**: deflection is speed; **Absolute**: position is the value (stick: center = 64) |
| Deadzone | area around the center with no reaction (default 0.08), so there is no drift |
| Curve | Linear or Exponential (amount 0–1: softer around the center for fine moves) |
| Sensitivity (rate, gyro) | at full deflection: fraction of the range per second (0.50 = end to end in 2 s) |
| Sensitivity (absolute) | fraction of the range full deflection covers, around the middle (0.50 = the middle half, for precise work) |
| Sensitivity (touch drag) | how far one full swipe moves the value (0.50 = half the range) |
| Hold position when released | absolute sticks only: the spring back to center is ignored, the head stays put. The stick takes over again when it reaches the kept position, so there is no jump |
| 14-bit | MSB on CC n + LSB on CC n+32 (16384 steps instead of 128). CC 0–31 only |
| Invert | reverses the direction |

Pan/Tilt values are remembered between sessions (Settings → “Remember Pan/Tilt after
restart”). With “Send them on startup” they are re-sent 1.5 s after launch.

## Modifiers and layers

Any button can be a **modifier** (like Shift). While it is held, the other buttons are in a
new **layer** with their own values, so one button gives many commands:

| You press | Layer | Example |
|---|---|---|
| X | Base | Note 38 |
| LB + X | LB | CC 53 |
| D-pad ↑ + X | D-pad ↑ | Note 70 |
| LB + D-pad ↑ + X | LB + D-pad ↑ | Note 71 |

**Rules**
- A layer is the **combination** of held modifiers. The order doesn't change the layer:
  LB then D-pad ↑ equals D-pad ↑ then LB (if both are modifiers).
- A button's role **depends on the layer**. Example: in the base layer the D-pad sends CCs,
  in the LB layer the D-pad is a modifier. Hold LB, then D-pad ↑, and you are in the
  “LB + D-pad ↑” layer. Press the D-pad first and it sends its base-layer value.
- A modifier from a lower layer **stays a modifier** in deeper layers (inherited). To make it
  a normal button in some layer, open it there, tick “Enabled” and assign MIDI.
- A modifier alone sends no MIDI.
- A button with no value in a layer **does nothing** there (label “not set in this layer”).
- Impossible D-pad combos (↑+↓, ←+→) don't create layers.
- Sticks and LT/RT are shared by all layers.
- Note Off is always correct: a button released after its modifier uses the layer it was
  pressed in.

**In the interface**
1. Click LB → Function → **Modifier**. The **Layer** bar appears on top.
2. Hold LB on the controller: the app switches to the “LB” layer and stays there
   (or pick it from the drop-down).
3. Click a button → set its value for this layer. Or **Fill empty**: every unassigned button
   gets a free Note number (channel 1). Numbers used in any layer are skipped.
4. For D-pad modifiers inside the LB layer: in the LB layer open D-pad ↑ → Function → Modifier.
5. **Learn** works with layers: hold the modifiers and press the button. A modifier pressed
   and released on its own opens the modifier itself.
- Modifiers have a purple outline. If the same value (type, channel, number) is assigned
  to two buttons, the label shows **“duplicate”**.

## Default layout (channel 1)

| Input | Type | Number |
|---|---|---|
| A / B / X / Y | Note momentary | 36 / 37 / 38 / 39 |
| LB / RB | Note momentary | 40 / 41 |
| View / Menu | Note momentary | 42 / 43 |
| L3 / R3 | Center left / right stick (glide) | (no MIDI) |
| D-pad ↑ ↓ ← → | Note momentary | 46 / 47 / 48 / 49 |
| Xbox / Share | Note momentary | 50 / 51 |
| Left stick X / Y | CC rate (Pan / Tilt 1) | CC 10 / 11 |
| Right stick X / Y | CC rate (Pan / Tilt 2) | CC 12 / 13 |
| LT / RT | CC absolute | CC 20 / 21 |
| Touchpad click | Note momentary | 52 |
| Back paddles 1–4 (Elite / Edge) | Note momentary | 53 / 54 / 55 / 56 |
| Touchpad X / Y | CC drag (Pan / Tilt 1) | CC 10 / 11 |
| Gyro turn / tilt | CC rate (Pan / Tilt 1), needs a Gyro button | CC 10 / 11 |

Stick up = value increases (Y is flipped relative to SDL). If a head moves the wrong way,
tick “Invert direction” for that axis.

Axes that send the **same CC** share **one value** (left stick X, touchpad X and gyro turn all
drive Pan 1): you can switch between them at any time without the head jumping.

## PlayStation controllers (DualSense / DualShock 4)

The app recognises PlayStation controllers and switches the diagram and names automatically.
Settings → **Diagram** lets you pick Xbox or PlayStation by hand, e.g. to prepare a profile for
a controller you don't have at hand. Profiles work with both: the buttons are the same SDL inputs.

| Xbox | PlayStation |
|---|---|
| A / B / X / Y | ✕ Cross / ○ Circle / □ Square / △ Triangle |
| LB / RB, LT / RT | L1 / R1, L2 / R2 |
| View / Menu / Xbox | Create / Options / PS |
| Share | Mic (mute button) |

![PlayStation layout](docs/screenshot-playstation.png)

**Extras on PlayStation**
- **Touchpad click** is its own button.
- **Touchpad surface** (finger 1) as two axes, Touchpad X / Y, in one of two modes:
  - **Drag (like a trackpad)** (default): moving the finger moves the value; lifting it keeps the
    value, and putting it down elsewhere does not jump. Great for fine Pan/Tilt adjustments.
  - **Absolute (XY pad)**: the finger position is the value (with sensitivity); held after lifting.
- **Gyro** (turn = yaw, tilt = pitch) moves Pan/Tilt like a stick in rate mode, but **only while a
  button with the “Gyro” function is held** (or toggled on with Mode → Toggle), so the head never
  moves by accident. Assign it in a button's editor: Function → Gyro. The status bar shows “GYRO”.
- **Light bar** shows the active layer: blue for the base layer, then a colour per layer (the same
  colour is drawn next to the touchpad). Settings → “Light bar colour per layer” turns it off.
- **DualSense Edge** back buttons and Fn buttons (and the Xbox Elite paddles) appear as
  Paddle 1–4 when the controller reports them.

> Works over USB and Bluetooth. Over Bluetooth the controller is switched to its full report mode
> (since 1.3.1); without it macOS gets no Mic button, touchpad position or gyro.
> If something doesn't work with your controller, run `--diag` and please open an issue with the output.

## Connecting to Lightkey

The app creates a **virtual MIDI port “Xbox MIDI Bridge”**. It exists while the app is
running; no IAC Driver is needed.

1. Start Xbox MIDI Bridge (the port appears immediately).
2. In Lightkey: **Lightkey → Settings… → External Control** and make sure the
   “Xbox MIDI Bridge” input is enabled.
3. Open the **External Control** window and click **MIDI**. Choose “Xbox MIDI Bridge” in the
   Input menu. When you move a control, Lightkey creates a binding (e.g. “CC 10”) that you
   assign an action to.

### MIDI Learn for Pan/Tilt

Lightkey learns the first message it receives. Move a stick diagonally and both CC 10 and
CC 11 arrive. So:

1. In Xbox MIDI Bridge open the **“Lightkey test”** tab.
2. In Lightkey start creating a binding (External Control → MIDI).
3. Move **only** the “Pan 1” slider: it sends CC 10 alone. Assign the head's Pan action.
4. Repeat for Tilt 1 (CC 11), Pan 2 (CC 12) and Tilt 2 (CC 13).
5. Only then use the sticks.

> **Check on your rig:** whether Lightkey offers Pan/Tilt of your particular heads as a MIDI
> binding action. The test mode exists for exactly this check.

### 14-bit mode

Lightkey's documentation describes support for 14-bit faders (16384 steps), i.e. the
standard MSB/LSB pair. Enable “14-bit” on the axis **before** MIDI Learn in Lightkey and
check with the test slider that the head moves smoothly. If Lightkey creates two separate
bindings (CC 10 and CC 42) instead of one 14-bit binding, turn 14-bit off for that axis.

### If Lightkey doesn't see the virtual port: IAC Driver

1. Open **Audio MIDI Setup** → Window → **Show MIDI Studio**.
2. Double-click **IAC Driver** → tick **Device is online** → Apply.
3. In Xbox MIDI Bridge → Settings → MIDI output choose **“IAC Driver Bus 1”**.
4. In Lightkey use the “IAC Driver Bus 1” input.

## Running from source

Requires Python 3.11 or 3.12 (Homebrew: `brew install python@3.12`).

```bash
git clone https://github.com/YoanPetroshan/xbox-midi-bridge.git
cd xbox-midi-bridge
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python app.py                        # graphical interface
.venv/bin/python app.py --diag                 # console diagnostics for the controller
.venv/bin/python app.py --test-midi            # test CCs/notes to the virtual port
.venv/bin/python -m unittest discover tests    # logic tests
```

### First check: `--diag`

Run `python app.py --diag`, press every button and move the sticks and triggers. It prints
the standard SDL names (`a`, `leftx`, `righttrigger`…). Then **click Lightkey (or any other
app)** and keep pressing: each event shows which app is in front. Ctrl+C prints a summary of
the detected inputs and whether background mode works.

Test an IAC port from the console: `python app.py --test-midi --port "IAC Driver Bus 1"`.

### Building the .app

```bash
./build_app.sh
```

Produces `dist/Xbox MIDI Bridge.app` and `dist/Xbox MIDI Bridge.zip` for **Apple Silicon,
macOS 15+**. It uses the python.org Python 3.14 (`/Library/Frameworks/Python.framework`),
because a Homebrew Python only runs on the macOS version it was built on, and it runs the
tests first. The bundle contains pygame-ce (a full pygame fork with SDL 2.32), because pygame
has no wheels for Python 3.14; python-rtmidi is compiled for macOS 12+.

## How it works

- `input_reader.py`: pygame / SDL2 Game Controller API in a **separate windowless process**
  at 250 Hz, with `SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1`. The process is never frontmost,
  so if it receives data at all, it also receives it while Lightkey is in front. The separate
  process also avoids the SDL ↔ Qt conflict over the main Cocoa thread.
- `engine.py`: a 250 Hz thread. Mapping → MIDI, rate-mode integration, layers, a limit of
  N messages/s per axis (default 120), sends only on change.
- `midi_out.py`: mido + python-rtmidi, virtual CoreMIDI port.
- `mapping.py`: models, default layout, layers, JSON profiles.
- `updater.py`, `ui/update_dialog.py`, `changelog.py`, `version.py`: update check, self-update and what's new.
- `i18n.py`: English/Bulgarian strings (`tr("English", "Български")`).
- `ui/`: PySide6 interface (diagram, editor popover, panels).
- `XboxMidiBridge.spec`, `build_app.sh`: PyInstaller build. `assets/make_icon.py` draws the icon.

Why not a web page with the Gamepad API: browsers stop delivering gamepad data to pages that
aren't visible or focused, and Lightkey will be in front.

## License

[MIT](LICENSE) © YoanPetroshan

Xbox is a trademark of Microsoft Corporation. Lightkey is a trademark of its respective owner.
This project is not affiliated with or endorsed by either.
