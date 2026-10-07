"""Main window."""
from __future__ import annotations

import sys
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QInputDialog, QLabel,
                               QMainWindow, QMessageBox, QPushButton, QSizePolicy, QSplitter,
                               QTabWidget, QToolBar, QVBoxLayout, QWidget)

from engine import Engine
from input_reader import ReaderProcess
from mapping import (AXIS_IDS, BUTTON_IDS, DEFAULT_PROFILE_NAME, INPUT_LABELS, UNRELIABLE_INPUTS,
                     ProfileStore, default_profile, layer_label, layer_mods)
from i18n import tr
from midi_out import MidiOut
from ui import theme
from ui.controller_view import LEFT_ORDER, ControllerView
from ui.panels import MonitorPanel, SettingsPanel, TestPanel
from ui.popover import MappingPopover

UI_HZ = 30


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Xbox MIDI Bridge")
        self.resize(1500, 860)

        self.store = ProfileStore()
        st = self.store.settings
        self.profile = self.store.load_active()
        self.midi = MidiOut()
        self.midi.open(st.get("midi_port"))
        self.reader = ReaderProcess()
        self.reader.start(st.get("controller"))
        rate_values = st.get("rate_values") if st.get("persist_rate_values") else None
        self.engine = Engine(self.profile, self.midi, self.reader, rate_values=rate_values)
        self.engine.reader_prefer = st.get("controller")
        self.engine.seen.update(i for i in st.get("seen_inputs", []) if i in UNRELIABLE_INPUTS)
        self.engine.start()
        self._popover: MappingPopover | None = None
        self._last_rate_save = time.time()
        self._devices_shown: list[str] | None = None
        self.edit_layer = ""  # the layer being shown and edited
        self._live_layer = ""

        self._build_toolbar()
        self.view = ControllerView()
        self.view.set_profile(self.profile)
        self.view.seen_ever = set(self.engine.seen)
        self.view.inputClicked.connect(self.open_editor)

        self.tabs = QTabWidget()
        self.monitor = MonitorPanel(self.midi)
        self.test = TestPanel(self.engine, lambda: self.profile)
        self.settings_panel = SettingsPanel()
        self.tabs.addTab(self.monitor, tr("MIDI monitor", "MIDI монитор"))
        self.tabs.addTab(self.test, tr("Lightkey test", "Тест за Lightkey"))
        self.tabs.addTab(self.settings_panel, tr("Settings", "Настройки"))
        self.tabs.setMinimumWidth(340)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)
        lv.addWidget(self._build_layer_bar())
        lv.addWidget(self.view, 1)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([1120, 380])
        split.setContentsMargins(8, 8, 8, 8)
        self.setCentralWidget(split)

        self._build_statusbar()
        self._init_settings_panel()
        self._reload_profiles()
        self._refresh_layers()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(lambda: self.store.save_profile(self.profile))

        self.timer = QTimer(self)
        self.timer.setInterval(int(1000 / UI_HZ))
        self.timer.timeout.connect(self.tick)
        self.timer.start()

        if st.get("persist_rate_values") and st.get("resend_on_start"):
            # Give Lightkey time to see the virtual port.
            QTimer.singleShot(1500, self.engine.resend_rate_values)

    # ---------------------------------------------------------------- UI
    def _build_toolbar(self):
        tb = QToolBar(tr("Profiles", "Профили"))
        tb.setMovable(False)
        self.addToolBar(tb)
        lbl = QLabel(tr("  Profile ", "  Профил "))
        lbl.setStyleSheet("background: transparent;")
        tb.addWidget(lbl)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(200)
        self.profile_combo.activated.connect(lambda _: self.switch_profile(self.profile_combo.currentText()))
        tb.addWidget(self.profile_combo)
        for text, slot in ((tr("New", "Нов"), self.new_profile),
                           (tr("Duplicate", "Дублирай"), self.duplicate_profile),
                           (tr("Rename", "Преименувай"), self.rename_profile),
                           (tr("Delete", "Изтрий"), self.delete_profile),
                           (tr("Reset to default", "Върни по подразбиране"), self.reset_profile)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            tb.addWidget(b)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        spacer.setStyleSheet("background: transparent;")
        tb.addWidget(spacer)
        self.center_btn = QPushButton(tr("Center Pan/Tilt", "Център Pan/Tilt"))
        self.center_btn.setToolTip(tr("Smoothly glides all rate-mode axes back to 64 (or 8192).\n"
                                      "The time is set in Settings → Glide to center.",
                                      "Плавно връща всички оси в скоростен режим към 64 (или 8192).\n"
                                      "Времето се задава в Настройки → Към центъра."))
        self.center_btn.clicked.connect(self.engine.center_all)
        tb.addWidget(self.center_btn)
        self.learn_btn = QPushButton(tr("Learn", "Научи"))
        self.learn_btn.setCheckable(True)
        self.learn_btn.setToolTip(tr("Click, then press a controller button to edit it",
                                     "Натисни, после натисни бутон на контролера, за да го редактираш"))
        self.learn_btn.toggled.connect(self._learn_toggled)
        tb.addWidget(self.learn_btn)
        quit_act = QAction(tr("Quit", "Изход"), self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        self.addAction(quit_act)

    def _build_statusbar(self):
        sb = self.statusBar()
        self.st_ctrl = QLabel()
        self.st_midi = QLabel()
        self.st_bg = QLabel()
        self.st_fine = QLabel()
        for w in (self.st_ctrl, self.st_midi, self.st_bg):
            sb.addWidget(w)
        sb.addPermanentWidget(self.st_fine)

    def _init_settings_panel(self):
        sp = self.settings_panel
        st = self.store.settings
        sp.refresh_ports(st.get("midi_port"))
        sp.set_devices([], st.get("controller"))
        sp.fine.setValue(self.profile.options.fine_factor)
        sp.rate.setValue(self.profile.options.max_rate_hz)
        sp.glide.setValue(self.profile.options.center_time)
        sp.persist.setChecked(bool(st.get("persist_rate_values")))
        sp.resend.setChecked(bool(st.get("resend_on_start")))
        sp.changed.connect(self._settings_changed)
        sp.midiPortChosen.connect(self._choose_port)
        sp.controllerChosen.connect(self._choose_controller)
        sp.resendRequested.connect(self.engine.resend_rate_values)
        sp.language.setCurrentIndex(max(0, sp.language.findData(st.get("language", "auto"))))
        sp.languageChosen.connect(self._choose_language)

    def _choose_language(self, code: str):
        self.store.settings["language"] = code
        self.store.save_settings()
        QMessageBox.information(self, "Language / Език",
                                "Restart the app to apply the new language.\n"
                                "Рестартирай приложението, за да смениш езика.")

    def _settings_changed(self):
        sp = self.settings_panel
        with self.engine.lock:
            self.profile.options.fine_factor = sp.fine.value()
            self.profile.options.max_rate_hz = sp.rate.value()
            self.profile.options.center_time = sp.glide.value()
        self.store.settings["persist_rate_values"] = sp.persist.isChecked()
        self.store.settings["resend_on_start"] = sp.resend.isChecked()
        sp.resend.setEnabled(sp.persist.isChecked())
        self.store.save_settings()
        self._save_timer.start()

    def _choose_port(self, name):
        with self.engine.lock:
            ok = self.midi.open(name)
        if not ok:
            QMessageBox.warning(self, "MIDI", tr("Could not open the port:\n{}", "Портът не се отвори:\n{}").format(self.midi.error))
            with self.engine.lock:
                self.midi.open(None)
            name = None
            self.settings_panel.refresh_ports(None)
        self.store.settings["midi_port"] = name
        self.store.save_settings()

    def _choose_controller(self, name):
        self.store.settings["controller"] = name
        self.store.save_settings()
        self.engine.reader_prefer = name
        self.reader.prefer(name)

    # ---------------------------------------------------------------- layers
    def _build_layer_bar(self) -> QWidget:
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(4, 0, 4, 0)
        lbl = QLabel(tr("Layer", "Слой"))
        lbl.setStyleSheet(f"color: {theme.MOD}; font-weight: 600;")
        h.addWidget(lbl)
        self.layer_combo = QComboBox()
        self.layer_combo.setMinimumWidth(240)
        self.layer_combo.setToolTip(tr("Which layer is shown and edited. Hold a modifier on the "
                                       "controller and the app switches to its layer.",
                                       "Кой слой се показва и редактира. Задръж модификатор на "
                                       "контролера и приложението превключва на неговия слой."))
        self.layer_combo.activated.connect(lambda _: self._select_layer(self.layer_combo.currentData()))
        h.addWidget(self.layer_combo)
        self.fill_btn = QPushButton(tr("Fill empty", "Попълни свободните"))
        self.fill_btn.setToolTip(tr("Gives free Note numbers (channel 1, momentary) to every\n"
                                    "unassigned button in this layer.",
                                    "Дава свободни Note номера (канал 1, momentary) на всички\n"
                                    "незададени бутони в този слой."))
        self.fill_btn.clicked.connect(self.fill_layer)
        h.addWidget(self.fill_btn)
        self.clear_btn = QPushButton(tr("Clear layer", "Изчисти слоя"))
        self.clear_btn.clicked.connect(self.clear_layer)
        h.addWidget(self.clear_btn)
        self.layer_hint = QLabel()
        self.layer_hint.setObjectName("muted")
        h.addWidget(self.layer_hint, 1)
        return bar

    def _refresh_layers(self):
        layers = self.profile.reachable_layers()
        if self.edit_layer not in layers:
            self.edit_layer = ""
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        for key in layers:
            n = sum(1 for b in BUTTON_IDS
                    if b not in layer_mods(key) and (m := self.profile.effective_map(key, b))
                    and m.enabled and m.action != "modifier")
            self.layer_combo.addItem(tr("{}   ({} buttons)", "{}   ({} бутона)").format(layer_label(key), n), key)
        self.layer_combo.setCurrentIndex(max(0, self.layer_combo.findData(self.edit_layer)))
        self.layer_combo.blockSignals(False)
        self.clear_btn.setEnabled(bool(self.edit_layer))
        if len(layers) == 1:
            self.layer_hint.setText(tr("Set a button as “Modifier” to get new layers.",
                                       "Задай бутон като „Модификатор“, за да получиш нови слоеве."))
        else:
            self.layer_hint.setText(tr("{} layers · hold a modifier on the controller to see its layer",
                                       "{} слоя · задръж модификатор на контролера, за да видиш слоя му").format(len(layers)))
        self.view.layer = self.edit_layer
        self.view.update()

    def _select_layer(self, key: str):
        self.edit_layer = key or ""
        if self._popover is not None:
            try:
                self._popover.close()
            except RuntimeError:
                pass
        self._refresh_layers()

    def fill_layer(self):
        key = self.edit_layer
        mods = layer_mods(key)
        filled = []
        with self.engine.lock:
            for b in BUTTON_IDS:
                if b in mods or (b in UNRELIABLE_INPUTS and b not in self.engine.seen):
                    continue
                e = self.profile.effective_map(key, b)
                if e is not None and e.enabled:
                    continue  # assigned, or an inherited modifier
                m = self.profile.buttons[b] if not key else self.profile.ensure_button_map(key, b)
                m.enabled = False
                n = self.profile.free_note()
                m.enabled, m.action, m.type, m.mode, m.channel, m.number = True, "midi", "note", "momentary", 1, n
                filled.append(f"{INPUT_LABELS[b]}={n}")
        self._save_timer.start()
        self._refresh_layers()
        self.statusBar().showMessage(
            (tr("Filled: ", "Попълнени: ") + ", ".join(filled)) if filled
            else tr("No unassigned buttons in this layer.", "Няма незададени бутони в този слой."), 8000)

    def clear_layer(self):
        key = self.edit_layer
        if not key:
            return
        if QMessageBox.question(self, tr("Clear layer", "Изчисти слоя"),
                                tr("Delete all values in the layer “{}”?", "Да изтрия ли всички стойности в слоя „{}“?")
                                .format(layer_label(key))) != QMessageBox.Yes:
            return
        with self.engine.lock:
            self.profile.layers.pop(key, None)
        self._save_timer.start()
        self._refresh_layers()

    # ---------------------------------------------------------------- editing
    def open_editor(self, input_id: str, anchor=None):
        if self._popover is not None:
            try:
                self._popover.close()
            except RuntimeError:
                pass
        self.view.selected = input_id
        if anchor is None or anchor.isNull():
            anchor = self.view.label_global_rect(input_id)
        is_axis = input_id in AXIS_IDS
        key = self.edit_layer
        if not is_axis and input_id in layer_mods(key):
            self.view.selected = None
            self.statusBar().showMessage(
                tr("{} is the held modifier of the layer “{}”. Edit it in the lower layer.",
                   "{} е задържаният модификатор на слоя „{}“. Редактирай го в по-ниския слой.")
                .format(INPUT_LABELS[input_id], layer_label(key)), 6000)
            return
        if is_axis:
            m = self.profile.axes[input_id]
        elif key:
            with self.engine.lock:
                m = self.profile.ensure_button_map(key, input_id)
        else:
            m = self.profile.buttons[input_id]
        extra = {"seen": input_id in self.engine.seen,
                 "layer": key,
                 "inherited": not is_axis and self.profile.inherited_modifier(key, input_id),
                 "options": self.profile.options,
                 "options_changed": self._options_changed_in_popover,
                 "center_axis": lambda a: self.engine.set_axis_value(a, 0.5)}
        pop = MappingPopover(self, input_id, m, self._mapping_changed, extra)
        pop.destroyed.connect(lambda *_, p=pop: self._popover_closed(p))
        self._popover = pop
        pop.show_near(anchor, prefer_left=input_id in LEFT_ORDER)

    def _options_changed_in_popover(self):
        g = self.settings_panel.glide
        g.blockSignals(True)
        g.setValue(self.profile.options.center_time)
        g.blockSignals(False)
        self._save_timer.start()

    def _popover_closed(self, pop):
        if pop is not self._popover:
            return  # an old panel closed after a new one opened
        self._popover = None
        self.view.selected = None
        self.view.update()

    def _mapping_changed(self, input_id: str):
        # The edit is already in the profile object; sync the engine without jumps.
        self.engine.mapping_changed(input_id)
        self._refresh_layers()
        self._save_timer.start()

    def _learn_toggled(self, on: bool):
        with self.engine.lock:
            self.engine.learn = on
            self.engine.learned = None
        self.view.learn = on
        self.view.update()

    # ---------------------------------------------------------------- profiles
    def _reload_profiles(self):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(self.store.list_profiles())
        self.profile_combo.setCurrentText(self.profile.name)
        self.profile_combo.blockSignals(False)
        if hasattr(self, "layer_combo"):
            self._refresh_layers()

    def _activate(self, profile):
        self.edit_layer = ""
        self.store.save_profile(self.profile)
        self.profile = profile
        self.engine.set_profile(profile)
        self.view.set_profile(profile)
        self.store.settings["active_profile"] = profile.name
        self.store.save_settings()
        sp = self.settings_panel
        for w, v in ((sp.fine, profile.options.fine_factor), (sp.rate, profile.options.max_rate_hz),
                     (sp.glide, profile.options.center_time)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self._reload_profiles()

    def switch_profile(self, name: str):
        if name and name != self.profile.name:
            self._activate(self.store.load_profile(name))

    def _ask_name(self, title: str, default: str) -> str | None:
        name, ok = QInputDialog.getText(self, title, tr("Profile name:", "Име на профила:"), text=default)
        name = name.strip()
        if not ok or not name:
            return None
        if self.store.exists(name) and name != self.profile.name:
            QMessageBox.warning(self, title, tr("Profile “{}” already exists.", "Профил „{}“ вече съществува.").format(name))
            return None
        return name

    def new_profile(self):
        name = self._ask_name(tr("New profile", "Нов профил"), tr("New profile", "Нов профил"))
        if name:
            p = default_profile(name)
            self.store.save_profile(p)
            self._activate(p)

    def duplicate_profile(self):
        name = self._ask_name(tr("Duplicate profile", "Дублирай профил"),
                              tr("{} (copy)", "{} (копие)").format(self.profile.name))
        if name:
            p = self.profile.clone(name)
            self.store.save_profile(p)
            self._activate(p)

    def rename_profile(self):
        name = self._ask_name(tr("Rename profile", "Преименувай профил"), self.profile.name)
        if name and name != self.profile.name:
            self.store.rename_profile(self.profile, name)
            self.store.settings["active_profile"] = name
            self.store.save_settings()
            self._reload_profiles()

    def delete_profile(self):
        names = self.store.list_profiles()
        if len(names) <= 1:
            QMessageBox.information(self, tr("Delete profile", "Изтрий профил"),
                                    tr("This is the only profile.", "Това е единственият профил."))
            return
        if QMessageBox.question(self, tr("Delete profile", "Изтрий профил"),
                                tr("Delete profile “{}”?", "Да изтрия ли профил „{}“?")
                                .format(self.profile.name)) != QMessageBox.Yes:
            return
        self.store.delete_profile(self.profile.name)
        nxt = [n for n in self.store.list_profiles() if n != self.profile.name][0]
        p = self.store.load_profile(nxt)
        self.profile = p  # so the deleted one isn't written back
        self.engine.set_profile(p)
        self.view.set_profile(p)
        self.store.settings["active_profile"] = p.name
        self.store.save_settings()
        self._reload_profiles()

    def reset_profile(self):
        if QMessageBox.question(
                self, tr("Reset to default", "Върни по подразбиране"),
                tr("Reset all buttons and axes in “{}” to the default layout?",
                   "Да върна ли всички бутони и оси в „{}“ към разпределението по подразбиране?")
                .format(self.profile.name)) != QMessageBox.Yes:
            return
        p = default_profile(self.profile.name)
        self.store.save_profile(p)
        self.profile = p
        self._activate(p)

    # ---------------------------------------------------------------- loop
    def tick(self):
        eng = self.engine
        eng.app_active = QGuiApplication.applicationState() == Qt.ApplicationActive
        snap = eng.snapshot()
        with eng.lock:
            values = {a: eng.axis_value_01(a) for a in AXIS_IDS}
        self.view.learn = snap["learn"]
        newly_seen = (snap["seen"] & set(UNRELIABLE_INPUTS)) - self.view.seen_ever
        if newly_seen:
            self.view.seen_ever |= newly_seen
            self.store.settings["seen_inputs"] = sorted(self.view.seen_ever)
            self.store.save_settings()
        live = snap["layer"]
        if live != self._live_layer:
            self._live_layer = live
            if live:  # held modifier → show and edit its layer
                self.edit_layer = live
            self._refresh_layers()
        self.view.layer_live = bool(live) and live == self.edit_layer
        self.view.set_state(snap, values, snap["outputs"])

        if snap["learned"]:
            with eng.lock:
                eng.learned = None
            self.learn_btn.blockSignals(True)
            self.learn_btn.setChecked(False)
            self.learn_btn.blockSignals(False)
            self.view.learn = False
            self.raise_()
            self.activateWindow()
            iid, key = snap["learned"]
            if iid not in AXIS_IDS:
                self.edit_layer = key
                self._refresh_layers()
            self.open_editor(iid)

        if snap["devices"] != self._devices_shown:
            self._devices_shown = snap["devices"]
            self.settings_panel.set_devices(snap["devices"], self.store.settings.get("controller"))

        self._update_status(snap)
        self.monitor.refresh()
        if self.tabs.currentWidget() is self.test:
            self.test.refresh()

        if eng.rate_dirty and time.time() - self._last_rate_save > 2.0:
            self._save_rate_values()

    def _update_status(self, snap):
        if snap["connected"]:
            self.st_ctrl.setText(f"<span style='color:{theme.OK}'>●</span> " +
                                 tr("Controller: {}", "Контролер: {}").format(snap["name"]))
        elif snap["error"]:
            self.st_ctrl.setText(f"<span style='color:{theme.ERR}'>●</span> {snap['error']}")
        else:
            self.st_ctrl.setText(f"<span style='color:{theme.ERR}'>●</span> " +
                                 tr("Controller: not connected", "Контролер: несвързан"))
        if self.midi.is_open:
            self.st_midi.setText(f"<span style='color:{theme.OK}'>●</span> MIDI: {self.midi.port_name}")
        else:
            self.st_midi.setText(f"<span style='color:{theme.ERR}'>●</span> MIDI: " + tr("closed", "затворен") +
                                 f"{' (' + self.midi.error + ')' if self.midi.error else ''}")
        if snap["background_ok"]:
            self.st_bg.setText(f"<span style='color:{theme.OK}'>●</span> " +
                              tr("Background mode: working", "Фонов режим: работи"))
        else:
            self.st_bg.setText(f"<span style='color:{theme.MUTED}'>●</span> " +
                               tr("Background mode: not verified yet (switch to Lightkey and move a stick)",
                                  "Фонов режим: още не е проверен (премини към Lightkey и мръдни стик)"))
        self.st_fine.setText(tr("FINE ×{:.2f}", "ФИНО ×{:.2f}").format(self.profile.options.fine_factor)
                             if snap["fine"] else "")

    def _save_rate_values(self):
        with self.engine.lock:
            self.engine.rate_dirty = False
            values = dict(self.engine.rate_values)
        self._last_rate_save = time.time()
        if self.store.settings.get("persist_rate_values"):
            self.store.settings["rate_values"] = values
            self.store.save_settings()

    def closeEvent(self, e):
        self.timer.stop()
        self.engine.stop()
        self._save_rate_values()
        self.store.save_profile(self.profile)
        self.store.save_settings()
        self.reader.stop()
        self.midi.close()
        super().closeEvent(e)


def run_gui() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Xbox MIDI Bridge")
    theme.apply(app)
    w = MainWindow()
    w.show()
    return app.exec()
