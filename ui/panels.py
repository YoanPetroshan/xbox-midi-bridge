"""Side panels: MIDI monitor, Lightkey test mode, settings."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout,
                               QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)

from i18n import LANGUAGES, tr
from midi_out import describe_message
from mapping import AXIS_IDS, INPUT_LABELS


class MonitorPanel(QWidget):
    def __init__(self, midi, parent=None):
        super().__init__(parent)
        self.midi = midi
        self.last_seq = 0
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.pause = QPushButton(tr("Pause", "Пауза"))
        self.pause.setCheckable(True)
        self.pause.toggled.connect(lambda on: self.pause.setText(tr("Resume", "Продължи") if on else tr("Pause", "Пауза")))
        clear = QPushButton(tr("Clear", "Изчисти"))
        clear.clicked.connect(lambda: self.text.clear())
        self.count = QLabel(tr("0 messages", "0 съобщения"))
        self.count.setObjectName("muted")
        top.addWidget(self.pause)
        top.addWidget(clear)
        top.addStretch()
        top.addWidget(self.count)
        lay.addLayout(top)
        hdr = QLabel(f"{tr('Time', 'Време'):<13}{tr('Type', 'Тип'):<10}{tr('Ch.', 'Кан.'):>4}"
                     f"{'№':>6}{tr('Value', 'Стойн.'):>8}")
        hdr.setStyleSheet("font-family: Menlo, monospace; font-size: 12px; color: #8a929d; padding-left: 6px;")
        lay.addWidget(hdr)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(800)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.text, 1)

    def refresh(self):
        new = self.midi.since(self.last_seq)
        if new:
            self.last_seq = new[-1][0]
        self.count.setText(tr("{} messages", "{} съобщения").format(self.midi.sent_count))
        if self.pause.isChecked() or not new:
            return
        lines = []
        for _, t, msg in new[-200:]:
            kind, ch, num, val = describe_message(msg)
            ts = time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"
            lines.append(f"{ts:<13}{kind:<10}{ch:>4}{num:>6}{val:>8}")
        self.text.appendPlainText("\n".join(lines))
        sb = self.text.verticalScrollBar()
        sb.setValue(sb.maximum())


class TestPanel(QWidget):
    """Sliders that send CCs without a controller: for MIDI Learn in Lightkey."""

    def __init__(self, engine, get_profile, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.get_profile = get_profile
        lay = QVBoxLayout(self)
        info = QLabel(
            tr("For MIDI Learn in Lightkey: select the head, start learning Pan (or Tilt) "
               "and move only the matching slider. Each slider sends <b>only its own CC</b>, "
               "so Lightkey won't learn the wrong one.",
               "За MIDI Learn в Lightkey: избери главата, включи Learn на Pan (или Tilt) "
               "и мръдни само съответния плъзгач. Всеки плъзгач праща <b>само своя CC</b>, "
               "така Lightkey няма да научи грешния."))
        info.setWordWrap(True)
        info.setObjectName("muted")
        lay.addWidget(info)
        grid = QGridLayout()
        grid.setVerticalSpacing(10)
        self.rows: dict[str, tuple[QLabel, QSlider, QLabel]] = {}
        for i, a in enumerate(AXIS_IDS):
            name = QLabel()
            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, 1000)
            sl.valueChanged.connect(lambda v, a=a, s=sl: self._moved(a, s))
            val = QLabel("—")
            val.setMinimumWidth(42)
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            c = QPushButton("64")
            c.setToolTip(tr("Send the middle (64 / 8192)", "Прати средата (64 / 8192)"))
            c.setFixedWidth(40)
            c.clicked.connect(lambda _=False, a=a: self.engine.set_axis_value(a, 0.5))
            grid.addWidget(name, i * 2, 0, 1, 3)
            grid.addWidget(sl, i * 2 + 1, 0)
            grid.addWidget(val, i * 2 + 1, 1)
            grid.addWidget(c, i * 2 + 1, 2)
            self.rows[a] = (name, sl, val)
        lay.addLayout(grid)
        center = QPushButton(tr("Center all Pan/Tilt", "Центрирай всички Pan/Tilt"))
        center.clicked.connect(self.engine.center_all)
        lay.addWidget(center)
        lay.addStretch()

    def _moved(self, a, slider):
        if slider.isSliderDown() or slider.hasFocus():
            if getattr(slider, "_syncing", False):
                return
            self.engine.set_axis_value(a, slider.value() / 1000.0)

    def refresh(self):
        prof = self.get_profile()
        for a, (name, sl, val) in self.rows.items():
            m = prof.axes[a]
            label = m.label or INPUT_LABELS[a]
            kind = tr("rate", "скоростен") if m.mode == "rate" else tr("absolute", "абсолютен")
            ch = tr("Ch. {}", "Кан. {}").format(m.channel)
            extra = (tr(" · 14-bit", " · 14-бит") if m.hires else "") + (
                "" if m.enabled else tr(" · disabled", " · изключен"))
            name.setText(f"<b>{label}</b> &nbsp;<span style='color:#8a929d'>CC {m.cc} · {ch} · "
                         f"{kind}{extra}</span>")
            sl.setEnabled(m.enabled)
            out = self.engine.outputs.get(a)
            val.setText("—" if out is None else str(out))
            if not sl.isSliderDown():
                v = self.engine.axis_value_01(a)
                sl._syncing = True
                sl.blockSignals(True)
                sl.setValue(int(v * 1000))
                sl.blockSignals(False)
                sl._syncing = False


class SettingsPanel(QWidget):
    changed = Signal()
    midiPortChosen = Signal(object)  # None = virtual
    controllerChosen = Signal(object)  # None = automatic
    resendRequested = Signal()
    languageChosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        form = QFormLayout(inner)
        form.setLabelAlignment(Qt.AlignRight)
        form.setVerticalSpacing(10)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        self.port = QComboBox()
        self.port.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.port.setMinimumContentsLength(12)
        refresh = QPushButton("↻")
        refresh.setFixedWidth(34)
        refresh.setToolTip(tr("Refresh the MIDI port list", "Обнови списъка с MIDI портове"))
        row = QHBoxLayout()
        row.addWidget(self.port, 1)
        row.addWidget(refresh)
        wrap = QWidget()
        wrap.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        form.addRow(tr("MIDI output", "MIDI изход"), wrap)
        self.port.activated.connect(lambda _: self.midiPortChosen.emit(self.port.currentData()))
        refresh.clicked.connect(lambda: self.refresh_ports(self.port.currentData()))

        self.controller = QComboBox()
        self.controller.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.controller.setMinimumContentsLength(12)
        self.controller.activated.connect(lambda _: self.controllerChosen.emit(self.controller.currentData()))
        form.addRow(tr("Controller", "Контролер"), self.controller)

        self.fine = QDoubleSpinBox()
        self.fine.setRange(0.05, 1.0)
        self.fine.setSingleStep(0.05)
        self.fine.setPrefix("×")
        self.fine.setToolTip(tr("Stick speed multiplier while the “Fine” button is held.",
                                "Множител на скоростта на стиковете, докато е натиснат бутонът „Фино“."))
        form.addRow(tr("Fine", "Фино"), self.fine)

        self.glide = QDoubleSpinBox()
        self.glide.setRange(0.0, 10.0)
        self.glide.setSingleStep(0.1)
        self.glide.setDecimals(1)
        self.glide.setSuffix(tr(" s", " сек"))
        self.glide.setToolTip(tr("How smoothly L3/R3 (and “Center” buttons) glide Pan/Tilt back to the middle.\n"
                                 "0 = instant. Moving the stick cancels the glide.",
                                 "Колко плавно L3/R3 (и бутоните „Център“) връщат Pan/Tilt към средата.\n"
                                 "0 = веднага. Мръдване на стика прекъсва връщането."))
        form.addRow(tr("Glide to center", "Към центъра"), self.glide)

        self.rate = QSpinBox()
        self.rate.setRange(20, 500)
        self.rate.setSuffix(tr(" msg/s", " съобщ./сек"))
        self.rate.setToolTip(tr("Upper limit of CC messages per axis, so Lightkey isn't flooded.",
                                "Горна граница на CC съобщенията на ос, за да не се претовари Lightkey."))
        form.addRow(tr("Max per axis", "Макс. на ос"), self.rate)

        self.persist = QCheckBox(tr("Remember Pan/Tilt after restart", "Запомни Pan/Tilt след рестарт"))
        self.resend = QCheckBox(tr("Send them on startup", "Прати ги при старт"))
        form.addRow("", self.persist)
        form.addRow("", self.resend)
        resend_now = QPushButton(tr("Send current Pan/Tilt", "Прати текущите Pan/Tilt"))
        resend_now.clicked.connect(self.resendRequested.emit)
        form.addRow("", resend_now)

        self.language = QComboBox()
        for code, name in LANGUAGES.items():
            self.language.addItem(name, code)
        self.language.setToolTip(tr("Applied after restarting the app.", "Прилага се след рестарт на приложението."))
        self.language.activated.connect(lambda _: self.languageChosen.emit(self.language.currentData()))
        form.addRow(tr("Language", "Език"), self.language)

        note = QLabel(tr("If Lightkey doesn't see “Xbox MIDI Bridge”, choose the IAC Driver as output "
                         "(see README). Pan/Tilt values are shared by all profiles.",
                         "Ако Lightkey не вижда „Xbox MIDI Bridge“, избери IAC Driver като изход "
                         "(виж README). Стойностите на Pan/Tilt са общи за всички профили."))
        note.setWordWrap(True)
        note.setObjectName("muted")
        form.addRow(note)

        for w in (self.fine, self.rate, self.glide):
            w.valueChanged.connect(lambda *_: self.changed.emit())
        for w in (self.persist, self.resend):
            w.toggled.connect(lambda *_: self.changed.emit())

    def refresh_ports(self, current):
        from midi_out import VIRTUAL_PORT_NAME, list_output_ports
        self.port.blockSignals(True)
        self.port.clear()
        self.port.addItem(tr("Virtual “{}” (recommended)", "Виртуален „{}“ (препоръчан)").format(VIRTUAL_PORT_NAME), None)
        for n in list_output_ports():
            self.port.addItem(n, n)
        i = self.port.findData(current)
        self.port.setCurrentIndex(max(0, i))
        self.port.blockSignals(False)

    def set_devices(self, devices: list[str], preferred):
        self.controller.blockSignals(True)
        self.controller.clear()
        self.controller.addItem(tr("Automatic (first found)", "Автоматично (първият открит)"), None)
        names = list(devices)
        if preferred and preferred not in names:
            names.append(preferred)
        for d in names:
            self.controller.addItem(d if d in devices else tr("{} (not connected)", "{} (не е свързан)").format(d), d)
        self.controller.setCurrentIndex(max(0, self.controller.findData(preferred)))
        self.controller.blockSignals(False)
