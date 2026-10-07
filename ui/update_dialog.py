"""Update check UI: background check, "new version" dialog, download progress, install."""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressDialog,
                               QPushButton, QTextBrowser, QVBoxLayout)

import updater
from i18n import tr
from version import APP_VERSION


class _Worker(QObject):
    """Runs a function in a thread and reports back on the GUI thread via signals."""
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def run(self, fn, *args):
        def target():
            try:
                self.done.emit(fn(*args))
            except Exception as e:  # reported to the user, never crashes the app
                self.failed.emit(str(e))
        threading.Thread(target=target, daemon=True).start()


class UpdateDialog(QDialog):
    """Shows a newer release. Result: "update", "later", "skip" or "page"."""

    def __init__(self, parent, release: updater.Release, can_install: bool):
        super().__init__(parent)
        self.setWindowTitle(tr("Update available", "Има нова версия"))
        self.choice = "later"
        lay = QVBoxLayout(self)
        head = QLabel(tr("<b>Xbox MIDI Bridge {}</b> is available, you have {}.",
                         "<b>Xbox MIDI Bridge {}</b> е налична, при теб е {}.").format(release.version, APP_VERSION))
        head.setStyleSheet("font-size: 15px;")
        lay.addWidget(head)
        what = QLabel(tr("What's new in {}:", "Какво е новото в {}:").format(release.version))
        what.setObjectName("h")
        lay.addWidget(what)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(release.notes or tr("(no release notes)", "(няма бележки към версията)"))
        notes.setMinimumSize(520, 260)
        lay.addWidget(notes, 1)
        if not can_install:
            hint = QLabel(tr("This copy can't update itself (it runs from source, or its folder isn't "
                             "writable). The download page will open instead.",
                             "Това копие не може да се обнови само (пуснато е от изходния код или папката му "
                             "не позволява запис). Вместо това ще се отвори страницата за изтегляне."))
            hint.setWordWrap(True)
            hint.setObjectName("muted")
            lay.addWidget(hint)
        row = QHBoxLayout()
        skip = QPushButton(tr("Skip this version", "Пропусни тази версия"))
        later = QPushButton(tr("Later", "По-късно"))
        go = QPushButton(tr("Update now", "Обнови сега") if can_install
                         else tr("Open download page", "Отвори страницата"))
        go.setDefault(True)
        row.addWidget(skip)
        row.addStretch()
        row.addWidget(later)
        row.addWidget(go)
        lay.addLayout(row)
        skip.clicked.connect(lambda: self._pick("skip"))
        later.clicked.connect(lambda: self._pick("later"))
        go.clicked.connect(lambda: self._pick("update" if can_install else "page"))

    def _pick(self, choice: str):
        self.choice = choice
        self.accept()


class WhatsNewDialog(QDialog):
    def __init__(self, parent, markdown: str):
        super().__init__(parent)
        self.setWindowTitle(tr("What's new", "Какво е новото"))
        lay = QVBoxLayout(self)
        head = QLabel(tr("<b>Xbox MIDI Bridge {}</b> · what's new", "<b>Xbox MIDI Bridge {}</b> · какво е новото")
                      .format(APP_VERSION))
        head.setStyleSheet("font-size: 15px;")
        lay.addWidget(head)
        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        text.setMarkdown(markdown)
        text.setMinimumSize(520, 300)
        lay.addWidget(text, 1)
        row = QHBoxLayout()
        row.addStretch()
        ok = QPushButton("OK")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)


def show_whats_new(parent, previous: str | None) -> None:
    import changelog
    md = changelog.markdown_since(previous, APP_VERSION)
    if md:
        WhatsNewDialog(parent, md).exec()


class UpdateController(QObject):
    """Owns the whole flow; the main window only calls check() and provides quit_app."""

    def __init__(self, window, settings: dict, save_settings, quit_app):
        super().__init__(window)
        self.window = window
        self.settings = settings
        self.save_settings = save_settings
        self.quit_app = quit_app
        self._busy = False
        self._progress: QProgressDialog | None = None
        self._cancel = False

    # ---------------------------------------------------------------- check
    def check(self, manual: bool = False):
        if self._busy:
            return
        self._busy = True
        w = _Worker(self)
        w.done.connect(lambda rel: self._checked(rel, manual))
        w.failed.connect(lambda err: self._check_failed(err, manual))
        w.run(updater.fetch_latest)

    def _check_failed(self, err: str, manual: bool):
        self._busy = False
        if manual:  # an automatic check stays silent when offline
            QMessageBox.warning(self.window, tr("Update check", "Проверка за обновления"),
                                tr("Could not reach GitHub:\n{}", "Няма връзка с GitHub:\n{}").format(err))

    def _checked(self, rel: updater.Release, manual: bool):
        self._busy = False
        if not rel.version or not updater.is_newer(rel.version):
            if manual:
                QMessageBox.information(self.window, tr("Update check", "Проверка за обновления"),
                                        tr("You have the latest version ({}).", "Имаш последната версия ({}).")
                                        .format(APP_VERSION))
            return
        if not manual and self.settings.get("skipped_version") == rel.version:
            return
        bundle = updater.running_bundle()
        can_install = bool(rel.zip_url) and updater.can_self_update(bundle)
        dlg = UpdateDialog(self.window, rel, can_install)
        dlg.exec()
        if dlg.choice == "skip":
            self.settings["skipped_version"] = rel.version
            self.save_settings()
        elif dlg.choice == "page":
            updater.open_release_page(rel.page)
        elif dlg.choice == "update":
            self._download(rel, bundle)

    # ---------------------------------------------------------------- download & install
    def _download(self, rel: updater.Release, bundle):
        zip_path, unpack_dir = updater.work_paths(rel.version)
        self._cancel = False
        prog = QProgressDialog(tr("Downloading {}…", "Изтегляне на {}…").format(rel.version),
                               tr("Cancel", "Откажи"), 0, 100, self.window)
        prog.setWindowTitle(tr("Update", "Обновяване"))
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        prog.canceled.connect(lambda: setattr(self, "_cancel", True))
        prog.setValue(0)
        self._progress = prog

        def job():
            updater.download(rel.zip_url, zip_path,
                             progress=lambda d, t: w.progress.emit(d, t),
                             cancelled=lambda: self._cancel)
            return updater.prepare(zip_path, rel.version, unpack_dir)

        w = _Worker(self)
        w.progress.connect(self._on_progress)
        w.done.connect(lambda app: self._ready(app, bundle, rel))
        w.failed.connect(self._failed)
        w.run(job)

    def _on_progress(self, done: int, total: int):
        if self._progress is None:
            return
        if total:
            self._progress.setValue(int(done * 100 / total))
            self._progress.setLabelText(tr("Downloading… {:.1f} / {:.1f} MB", "Изтегляне… {:.1f} / {:.1f} MB")
                                        .format(done / 1e6, total / 1e6))
        else:
            self._progress.setRange(0, 0)

    def _close_progress(self):
        if self._progress is not None:
            self._progress.close()
            self._progress = None

    def _failed(self, err: str):
        self._close_progress()
        if err == "cancelled":
            return
        QMessageBox.warning(self.window, tr("Update", "Обновяване"),
                            tr("The update failed and nothing was changed:\n{}",
                               "Обновяването не успя и нищо не е променено:\n{}").format(err))

    def _ready(self, new_app, bundle, rel: updater.Release):
        self._close_progress()
        ok = QMessageBox.question(
            self.window, tr("Update", "Обновяване"),
            tr("Version {} is downloaded and checked.\nThe app will close, update and start again. "
               "Continue?",
               "Версия {} е изтеглена и проверена.\nПриложението ще се затвори, обнови и пусне отново. "
               "Продължаваш ли?").format(rel.version))
        if ok != QMessageBox.Yes:
            return
        updater.start_install(new_app, bundle, os.getpid())
        self.quit_app()
