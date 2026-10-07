"""Xbox MIDI Bridge: Xbox controller → MIDI for Lightkey.

  python app.py              graphical interface
  python app.py --diag       console diagnostics for the controller
  python app.py --test-midi  sends test CCs/notes to the virtual port
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import subprocess
import sys
import time

from i18n import tr


def frontmost_app() -> str:
    """Name of the frontmost app (macOS), used to verify background operation."""
    try:
        asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=1).stdout.strip()
        out = subprocess.run(["lsappinfo", "info", "-only", "name", asn],
                             capture_output=True, text=True, timeout=1).stdout
        # "LSDisplayName"="Lightkey"
        return out.split("=", 1)[1].strip().strip('"') if "=" in out else "?"
    except Exception:
        return "?"


def run_diag() -> int:
    from input_reader import ReaderProcess
    from mapping import AXIS_IDS, BUTTON_IDS, INPUT_LABELS, UNRELIABLE_INPUTS

    import signal
    sys.stdout.reconfigure(line_buffering=True)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    print(tr("Xbox MIDI Bridge · diagnostics (Ctrl+C to quit)\n", "Xbox MIDI Bridge · диагностика (Ctrl+C за край)\n"))
    print(tr("Connect the controller (USB or Bluetooth). Press every button, move the sticks and triggers.",
             "Свържи контролера (USB или Bluetooth). Натискай всички бутони, мърдай стиковете и тригерите."))
    print(tr("To check background mode: click another app (e.g. Lightkey) and keep pressing.\n",
             "За проверка на фоновия режим: кликни върху друго приложение (напр. Lightkey) и продължи да натискаш.\n"))
    reader = ReaderProcess()
    reader.start()
    prev_b = {b: False for b in BUTTON_IDS}
    prev_a = {a: 0.0 for a in AXIS_IDS}
    seen: set[str] = set()
    fronts: dict[str, int] = {}
    front, front_t = frontmost_app(), time.time()
    connected = False
    try:
        while True:
            if time.time() - front_t > 1.0:
                front, front_t = frontmost_app(), time.time()
            for msg in reader.poll():
                kind = msg[0]
                if kind == "devices":
                    print(tr("[devices] {}", "[устройства] {}").format(msg[1] or tr("no controller found", "няма открит контролер")))
                elif kind == "connected":
                    connected = True
                    print(tr("[connected] {}", "[свързан] {}").format(msg[1]))
                    if msg[2]:
                        print(tr("  SDL mapping:", "  SDL мапинг:"), ", ".join(f"{k}:{v}" for k, v in msg[2].items()))
                elif kind == "disconnected":
                    connected = False
                    print(tr("[disconnected] controller gone, waiting for it to reconnect...", "[несвързан] контролерът изчезна, чакам повторно свързване..."))
                elif kind == "error":
                    print(tr("[error] {}", "[грешка] {}").format(msg[1]))
                elif kind == "state":
                    _, buttons, axes = msg
                    events = []
                    for b, p in zip(BUTTON_IDS, buttons):
                        if p != prev_b[b]:
                            prev_b[b] = p
                            events.append(tr("button {:<13} ({}) {}", "бутон {:<13} ({}) {}").format(
                                b, INPUT_LABELS[b], tr("pressed", "натиснат") if p else tr("released", "пуснат")))
                            if p:
                                seen.add(b)
                    for a, v in zip(AXIS_IDS, axes):
                        if abs(v - prev_a[a]) >= 0.1 or (v == 0.0) != (prev_a[a] == 0.0):
                            prev_a[a] = v
                            events.append(tr("axis   {:<13} ({}) {:+.3f}", "ос     {:<13} ({}) {:+.3f}").format(a, INPUT_LABELS[a], v))
                            if abs(v) > 0.3:
                                seen.add(a)
                    if events:
                        fronts[front] = fronts.get(front, 0) + 1
                        for e in events:
                            print(tr("  {}   [front: {}]", "  {}   [отпред: {}]").format(e, front))
            if not reader.alive():
                print(tr("[error] the controller process stopped.", "[грешка] процесът за контролера спря."))
                break
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        reader.stop()

    print(tr("\n──────── Summary ────────", "\n──────── Обобщение ────────"))
    print(tr("Connected at the end:", "Свързан в края:"), tr("yes", "да") if connected else tr("no", "не"))
    all_ids = BUTTON_IDS + AXIS_IDS
    print(tr("Detected inputs:  ", "Открити входове:  "), ", ".join(i for i in all_ids if i in seen) or tr("none", "няма"))
    missing = [i for i in all_ids if i not in seen]
    print(tr("Missing inputs:   ", "Неоткрити входове:"), ", ".join(missing) or tr("none", "няма"))
    for i in UNRELIABLE_INPUTS:
        if i not in seen:
            print(tr("  · {} ({}) was not reported: normal on macOS over Bluetooth.",
                     "  · {} ({}) не е докладван: на macOS по Bluetooth е нормално.").format(INPUT_LABELS[i], i))
    if fronts:
        print(tr("Events by frontmost app:", "Събития според приложението отпред:"), ", ".join(f"{k}: {v}" for k, v in fronts.items()))
        others = [k for k in fronts if k not in ("Terminal", "iTerm2", "Code", "Visual Studio Code", "?")]
        if others:
            print(tr("Background mode: WORKS (data received while in front was {})",
                     "Фонов режим: РАБОТИ (получени данни, докато отпред беше {})").format(", ".join(others)))
        else:
            print(tr("Background mode: not verified. Click another app and press buttons.",
                     "Фонов режим: не е проверен. Кликни върху друго приложение и натискай бутони."))
    return 0


def main() -> int:
    mp.freeze_support()
    ap = argparse.ArgumentParser(description=tr("Xbox controller → MIDI bridge for Lightkey", "Xbox контролер → MIDI мост за Lightkey"))
    ap.add_argument("--diag", action="store_true", help=tr("console diagnostics for the controller", "конзолна диагностика на контролера"))
    ap.add_argument("--test-midi", action="store_true", help=tr("send test CCs/notes to the MIDI port", "тестови CC/ноти към MIDI порта"))
    ap.add_argument("--port", help=tr("existing MIDI output instead of the virtual one (e.g. 'IAC Driver Bus 1')",
                                    "съществуващ MIDI изход вместо виртуалния (напр. 'IAC Driver Bus 1')"))
    args = ap.parse_args()

    if args.diag:
        return run_diag()
    if args.test_midi:
        from midi_out import run_midi_test
        return run_midi_test(args.port)

    from ui.main_window import run_gui
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
