"""Small panel for editing one input's mapping. Changes apply immediately."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from i18n import tr
from mapping import INPUT_LABELS, layer_label, TRIGGER_IDS, UNRELIABLE_INPUTS, AxisMapping, ButtonMapping

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(n: int) -> str:
    # Convention C3 = 60 (as in most DAWs and Lightkey/Logic)
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 2}"


def _combo(items: list[tuple[str, str]]) -> QComboBox:
    c = QComboBox()
    for value, text in items:
        c.addItem(text, value)
    return c


def _set_combo(c: QComboBox, value: str) -> None:
    i = c.findData(value)
    if i >= 0:
        c.setCurrentIndex(i)


class MappingPopover(QFrame):
    def __init__(self, parent: QWidget, input_id: str, mapping, on_change: Callable[[str], None],
                 extra: dict | None = None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("popover")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.input_id = input_id
        self.m = mapping
        self.on_change = on_change
        self.extra = extra or {}
        self._loading = True

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        head = QHBoxLayout()
        title = QLabel(INPUT_LABELS[input_id])
        title.setObjectName("h")
        sub = QLabel(input_id)
        sub.setObjectName("muted")
        head.addWidget(title)
        head.addWidget(sub)
        head.addStretch()
        layer = self.extra.get("layer", "")
        if isinstance(mapping, ButtonMapping):
            chip = QLabel(tr("Layer: {}", "Слой: {}").format(layer_label(layer)))
            chip.setStyleSheet("color: #b48ef0; border: 1px solid #b48ef0; border-radius: 9px; padding: 1px 8px;")
            head.addWidget(chip)
        lay.addLayout(head)
        if self.extra.get("inherited"):
            note = QLabel(tr("A modifier here by inheritance from a lower layer.\n"
                             "Tick “Enabled” and assign something else to override it in this layer.",
                             "Тук е модификатор по наследство от по-нисък слой.\n"
                             "Включи „Активен“ и задай друго, за да го отмениш в този слой."))
            note.setStyleSheet("color: #b48ef0;")
            lay.addWidget(note)
        if input_id in UNRELIABLE_INPUTS and not self.extra.get("seen"):
            warn = QLabel(tr("Not detected on this Mac. Over Bluetooth macOS may\n"
                             "intercept this button. Don't use it for important functions.",
                             "Не е открит на този Mac. По Bluetooth macOS може да\n"
                             "прихваща този бутон. Не го ползвай за важни функции."))
            warn.setStyleSheet("color: #f0a840;")
            lay.addWidget(warn)

        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignRight)
        self.form.setHorizontalSpacing(10)
        self.form.setVerticalSpacing(7)
        lay.addLayout(self.form)

        self.enabled = QCheckBox(tr("Enabled", "Активен"))
        self.form.addRow("", self.enabled)
        if isinstance(mapping, ButtonMapping):
            self._build_button()
        else:
            self._build_axis()
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText(tr("e.g. Pan 1, Strobe, Scene 2…", "напр. Pan 1, Strobe, Сцена 2…"))
        self.form.addRow(tr("Label", "Етикет"), self.label_edit)
        self._load()
        self._loading = False
        self._refresh_visibility()
        self.setMinimumWidth(330)

    # ---------------------------------------------------------------- button
    def _build_button(self):
        self.action = _combo([("midi", "MIDI"),
                              ("modifier", tr("Modifier (hold → new layer)", "Модификатор (задръж → нов слой)")),
                              ("center_left", tr("Center left stick (Pan/Tilt 1)", "Център ляв стик (Pan/Tilt 1)")),
                              ("center_right", tr("Center right stick (Pan/Tilt 2)", "Център десен стик (Pan/Tilt 2)")),
                              ("center", tr("Center all Pan/Tilt", "Център всички Pan/Tilt")),
                              ("fine", tr("Fine (while held)", "Фино (докато е натиснат)"))])
        self.glide = QDoubleSpinBox()
        self.glide.setRange(0.0, 10.0)
        self.glide.setSingleStep(0.1)
        self.glide.setDecimals(1)
        self.glide.setSuffix(tr(" s", " сек"))
        self.glide.setToolTip(tr("How smoothly Pan/Tilt glide back to the middle. 0 = instant.\n"
                                 "Shared by all “Center” buttons in the profile.",
                                 "Колко плавно се връщат Pan/Tilt към средата. 0 = веднага.\n"
                                 "Настройката е обща за всички бутони „Център“ в профила."))
        opts = self.extra.get("options")
        self.glide.setValue(opts.center_time if opts else 1.0)
        self.glide.valueChanged.connect(self._glide_changed)
        self.type = _combo([("note", "Note"), ("cc", "CC"), ("pc", "Program Change")])
        self.mode = QComboBox()
        self.channel = QSpinBox()
        self.channel.setRange(1, 16)
        self.number = QSpinBox()
        self.number.setRange(0, 127)
        self.note_lbl = QLabel()
        self.note_lbl.setObjectName("muted")
        num_row = QWidget()
        nl = QHBoxLayout(num_row)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.addWidget(self.number, 1)
        nl.addWidget(self.note_lbl)
        self.value = QSpinBox()
        self.value.setRange(0, 127)
        self.form.addRow(tr("Function", "Функция"), self.action)
        self.form.addRow(tr("Glide time", "Връщане за"), self.glide)
        self.form.addRow(tr("Type", "Тип"), self.type)
        self.form.addRow(tr("Mode", "Режим"), self.mode)
        self.form.addRow(tr("Channel", "Канал"), self.channel)
        self.form.addRow(tr("Number", "Номер"), num_row)
        self.form.addRow("Velocity", self.value)
        self.num_row = num_row
        for w in (self.action, self.type, self.mode):
            w.currentIndexChanged.connect(self._changed)
        for w in (self.channel, self.number, self.value):
            w.valueChanged.connect(self._changed)

    def _fill_modes(self):
        t = self.type.currentData()
        cur = self.mode.currentData() or self.m.mode
        self.mode.blockSignals(True)
        self.mode.clear()
        self.mode.addItem(tr("Momentary (press/release)", "Momentary (натиснат/пуснат)"), "momentary")
        self.mode.addItem(tr("Toggle (on/off)", "Toggle (вкл./изкл.)"), "toggle")
        if t == "cc":
            self.mode.addItem(tr("Fixed value", "Фиксирана стойност"), "value")
        _set_combo(self.mode, cur)
        if self.mode.currentIndex() < 0:
            self.mode.setCurrentIndex(0)
        self.mode.blockSignals(False)

    def _glide_changed(self, v):
        opts = self.extra.get("options")
        if opts is not None and not self._loading:
            opts.center_time = float(v)
            self.extra.get("options_changed", lambda: None)()

    # ---------------------------------------------------------------- axis
    def _build_axis(self):
        self.amode = _combo([("rate", tr("Rate (Pan/Tilt)", "Скоростен (Pan/Tilt)")),
                             ("absolute", tr("Absolute (position)", "Абсолютен (позиция)"))])
        self.channel = QSpinBox()
        self.channel.setRange(1, 16)
        self.cc = QSpinBox()
        self.cc.setRange(0, 127)
        self.hires = QCheckBox(tr("14-bit (MSB on CC n, LSB on CC n+32)", "14-бит (MSB на CC n, LSB на CC n+32)"))
        self.deadzone = QDoubleSpinBox()
        self.deadzone.setRange(0.0, 0.9)
        self.deadzone.setSingleStep(0.01)
        self.deadzone.setDecimals(2)
        self.curve = _combo([("linear", tr("Linear", "Линейна")), ("expo", tr("Exponential", "Експоненциална"))])
        self.expo = QDoubleSpinBox()
        self.expo.setRange(0.0, 1.0)
        self.expo.setSingleStep(0.05)
        self.expo.setDecimals(2)
        self.expo.setToolTip(tr("0 = linear, 1 = softest around the center", "0 = линейна, 1 = максимално мека около центъра"))
        curve_row = QWidget()
        cl = QHBoxLayout(curve_row)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.curve, 1)
        cl.addWidget(QLabel(tr("amount", "сила")))
        cl.addWidget(self.expo)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.05, 5.0)
        self.speed.setSingleStep(0.05)
        self.speed.setDecimals(2)
        self.speed.setSuffix(tr(" range/s", " обхв./сек"))
        self.speed.setToolTip(tr("At full deflection: what fraction of the full range is covered per second.\n"
                                 "0.50 = end to end in 2 seconds.",
                                 "При пълно изместване: каква част от целия обхват се изминава за секунда.\n"
                                 "0.50 = от край до край за 2 секунди."))
        self.invert = QCheckBox(tr("Invert direction", "Инвертирай посоката"))
        self.form.addRow(tr("Mode", "Режим"), self.amode)
        self.form.addRow(tr("Channel", "Канал"), self.channel)
        self.form.addRow("CC", self.cc)
        self.form.addRow("", self.hires)
        self.form.addRow("Deadzone", self.deadzone)
        self.form.addRow(tr("Curve", "Крива"), curve_row)
        self.form.addRow(tr("Max speed", "Макс. скорост"), self.speed)
        self.form.addRow("", self.invert)
        self.center_btn = QPushButton(tr("Center (64)", "Център (64)"))
        self.center_btn.clicked.connect(lambda: self.extra.get("center_axis", lambda _: None)(self.input_id))
        self.form.addRow("", self.center_btn)
        for w in (self.amode, self.curve):
            w.currentIndexChanged.connect(self._changed)
        for w in (self.channel, self.cc, self.deadzone, self.expo, self.speed):
            w.valueChanged.connect(self._changed)
        self.hires.toggled.connect(self._changed)
        self.invert.toggled.connect(self._changed)

    # ---------------------------------------------------------------- data
    def _load(self):
        m = self.m
        self.enabled.setChecked(m.enabled)
        self.enabled.toggled.connect(self._changed)
        self.label_edit.setText(m.label)
        self.label_edit.textChanged.connect(self._changed)
        if isinstance(m, ButtonMapping):
            _set_combo(self.action, m.action)
            _set_combo(self.type, m.type)
            self._fill_modes()
            _set_combo(self.mode, m.mode)
            self.channel.setValue(m.channel)
            self.number.setValue(m.number)
            self.value.setValue(m.value)
        else:
            _set_combo(self.amode, m.mode)
            self.channel.setValue(m.channel)
            self.cc.setValue(m.cc)
            self.hires.setChecked(m.hires)
            self.deadzone.setValue(m.deadzone)
            _set_combo(self.curve, m.curve)
            self.expo.setValue(m.expo)
            self.speed.setValue(m.max_speed)
            self.invert.setChecked(m.invert)

    def _changed(self, *_):
        if self._loading:
            return
        m = self.m
        m.enabled = self.enabled.isChecked()
        m.label = self.label_edit.text().strip()
        if isinstance(m, ButtonMapping):
            if self.sender() is self.type:
                self._fill_modes()
            m.action = self.action.currentData()
            m.type = self.type.currentData()
            m.mode = self.mode.currentData() or "momentary"
            m.channel = self.channel.value()
            m.number = self.number.value()
            m.value = self.value.value()
            n = m.normalized()
            m.mode = n.mode
        else:
            m.mode = self.amode.currentData()
            m.channel = self.channel.value()
            m.cc = self.cc.value()
            m.hires = self.hires.isChecked() and m.cc < 32
            m.deadzone = self.deadzone.value()
            m.curve = self.curve.currentData()
            m.expo = self.expo.value()
            m.max_speed = self.speed.value()
            m.invert = self.invert.isChecked()
        self._refresh_visibility()
        self.on_change(self.input_id)

    def _show_row(self, field: QWidget, visible: bool):
        self.form.setRowVisible(field, visible)

    def _refresh_visibility(self):
        m = self.m
        if isinstance(m, ButtonMapping):
            midi = m.action == "midi"
            if m.action == "modifier" and not self._loading:
                self.extra.get("layers_changed", lambda: None)()
            self._show_row(self.glide, m.action.startswith("center"))
            for w in (self.type, self.mode, self.channel, self.num_row, self.value):
                self._show_row(w, midi)
            if midi:
                self._show_row(self.mode, m.type != "pc")
                show_val = m.type == "note" or (m.type == "cc" and m.mode == "value")
                self._show_row(self.value, show_val)
                lbl = self.form.labelForField(self.value)
                if lbl:
                    lbl.setText("Velocity" if m.type == "note" else tr("Value", "Стойност"))
                nl = self.form.labelForField(self.num_row)
                if nl:
                    nl.setText({"note": tr("Note", "Нота"), "cc": "CC №", "pc": tr("Program", "Програма")}[m.type])
                self.note_lbl.setText(note_name(m.number) if m.type == "note" else "")
        else:
            rate = m.mode == "rate"
            self._show_row(self.speed, rate)
            self._show_row(self.center_btn, rate)
            self.expo.setEnabled(m.curve == "expo")
            self.hires.setEnabled(m.cc < 32)
            self.hires.setToolTip("" if m.cc < 32 else tr("14-bit is only possible for CC 0–31 (LSB = CC+32).",
                                                                   "14-бит е възможен само за CC 0–31 (LSB = CC+32)."))
            if self.input_id in TRIGGER_IDS:
                self.center_btn.setText(tr("Reset value (64)", "Нулирай стойността (64)"))
        self.adjustSize()

    # ---------------------------------------------------------------- position
    def show_near(self, anchor: QRectF, prefer_left: bool):
        self.adjustSize()
        screen = QGuiApplication.screenAt(anchor.center().toPoint()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        w, h = self.width(), self.height()
        if prefer_left:
            x = anchor.right() + 10
        else:
            x = anchor.left() - w - 10
        y = anchor.top() - 10
        x = max(avail.left() + 8, min(x, avail.right() - w - 8))
        y = max(avail.top() + 8, min(y, avail.bottom() - h - 8))
        self.move(int(x), int(y))
        self.show()
