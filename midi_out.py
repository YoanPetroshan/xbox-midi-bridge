"""MIDI output: a virtual CoreMIDI port "Xbox MIDI Bridge" (or an existing port, e.g. IAC)."""
from __future__ import annotations

import threading
import time
from collections import deque

import mido

from i18n import tr

VIRTUAL_PORT_NAME = "Xbox MIDI Bridge"


def list_output_ports() -> list[str]:
    try:
        return [n for n in mido.get_output_names() if n != VIRTUAL_PORT_NAME]
    except Exception:
        return []


def describe_message(msg: mido.Message) -> tuple[str, int, str, str]:
    """(type, channel 1-16, number, value) for the MIDI monitor."""
    ch = getattr(msg, "channel", 0) + 1
    if msg.type == "note_on":
        return "Note On", ch, str(msg.note), str(msg.velocity)
    if msg.type == "note_off":
        return "Note Off", ch, str(msg.note), str(msg.velocity)
    if msg.type == "control_change":
        return "CC", ch, str(msg.control), str(msg.value)
    if msg.type == "program_change":
        return "Program", ch, str(msg.program), ""
    return msg.type, ch, "", ""


class MidiOut:
    def __init__(self, monitor_size: int = 500):
        self.port = None
        self.port_name: str | None = None
        self.error: str | None = None
        self._lock = threading.Lock()
        self.monitor: deque = deque(maxlen=monitor_size)  # (seq, time, msg)
        self.seq = 0
        self.sent_count = 0

    def open(self, name: str | None = None) -> bool:
        """name=None → virtual port. Otherwise opens an existing output (e.g. "IAC Driver Bus 1")."""
        self.close()
        try:
            if name is None:
                self.port = mido.open_output(VIRTUAL_PORT_NAME, virtual=True,
                                             client_name=VIRTUAL_PORT_NAME)
                self.port_name = VIRTUAL_PORT_NAME + tr(" (virtual)", " (виртуален)")
            else:
                self.port = mido.open_output(name)
                self.port_name = name
            self.error = None
            return True
        except Exception as e:
            self.port = None
            self.port_name = None
            self.error = str(e)
            return False

    @property
    def is_open(self) -> bool:
        return self.port is not None

    def close(self) -> None:
        with self._lock:
            if self.port is not None:
                try:
                    self.port.close()
                except Exception:
                    pass
            self.port = None
            self.port_name = None

    def send(self, msg: mido.Message) -> None:
        with self._lock:
            if self.port is not None:
                try:
                    self.port.send(msg)
                except Exception as e:
                    self.error = str(e)
            self.seq += 1
            self.sent_count += 1
            self.monitor.append((self.seq, time.time(), msg))

    def since(self, seq: int) -> list[tuple]:
        with self._lock:
            return [m for m in self.monitor if m[0] > seq]


def run_midi_test(port: str | None = None) -> int:
    """`python app.py --test-midi`: sends test CCs and notes to the port."""
    out = MidiOut()
    if not out.open(port):
        print(tr("Error: could not open the port: {}", "Грешка: портът не се отвори: {}").format(out.error))
        return 1
    print(tr("Opened MIDI port: {}", "Отворен MIDI порт: {}").format(out.port_name))
    print(tr("Waiting 2 seconds so Lightkey / MIDI Monitor can see it...", "Изчаквам 2 секунди, за да го види Lightkey / MIDI Monitor..."))
    time.sleep(2)
    print(tr("CC 10 (Pan 1), channel 1: 64 → 127 → 0 → 64", "CC 10 (Pan 1), канал 1: 64 → 127 → 0 → 64"))
    path = list(range(64, 128)) + list(range(127, -1, -1)) + list(range(0, 65))
    for v in path:
        out.send(mido.Message("control_change", channel=0, control=10, value=v))
        time.sleep(0.01)
    print(tr("Note 36, channel 1: On/Off ×3", "Note 36, канал 1: On/Off ×3"))
    for _ in range(3):
        out.send(mido.Message("note_on", channel=0, note=36, velocity=127))
        time.sleep(0.2)
        out.send(mido.Message("note_off", channel=0, note=36, velocity=0))
        time.sleep(0.2)
    print(tr("Done, sent {} messages.", "Готово, изпратени {} съобщения.").format(out.sent_count))
    out.close()
    return 0
