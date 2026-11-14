from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QProgressBar, QVBoxLayout)

from database.connection import session_scope
from services import backup_runner, backup_service as bs, clock, settings_service
from ui.async_utils import run_async
from ui.common import confirm, fmt, notify, show_error
from ui.widgets.page import Page, button_row, make_button
from ui.widgets.table import DataTable

LABELS = {"backup": "PostgreSQL .backup (recommended - restorable)", "sql": "PostgreSQL .sql text dump (restorable)",
          "mdb": "Microsoft Access .mdb export (for viewing in Access - NOT restorable)"}


def _size(n: int | None) -> str:
    return "" if n is None else (f"{n / 1_048_576:.1f} MB" if n >= 1_048_576 else f"{n / 1024:.0f} KB")


class BackupPage(Page):
    def __init__(self, parent=None):
        super().__init__("Backup & Restore", "PostgreSQL backups are the authoritative way to restore your data.", parent)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.root.addWidget(self.info)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.root.addWidget(self.progress)
        self.buttons = [make_button("Back up now...", self.backup_now, "primary"),
                        make_button("Restore from file...", self.restore, "danger"),
                        make_button("Verify a backup file...", self.verify),
                        make_button("Open backup folder", self.open_folder)]
        self.root.addLayout(button_row(*self.buttons))
        self.table = DataTable(["When", "Format", "Trigger", "Status", "Size", "Checked", "File / error"], stretch=6)
        self.root.addWidget(self.table, 1)
        note = QLabel("An .mdb file is an export for Microsoft Access. Only .backup and .sql files can be restored.")
        note.setObjectName("muted")
        self.root.addWidget(note)

    def refresh(self) -> None:
        try:
            with session_scope() as s:
                history = bs.list_history(s)
                folder = bs.default_backup_dir(s)
                scheduled = settings_service.get(s, "local.backup.schedule_enabled")
                at = settings_service.get(s, "local.backup.time")
                last = bs.last_scheduled_success(s)
                rows = [(h.id, [h.created_at, h.format, h.trigger, h.status, _size(h.size_bytes),
                                "yes" if h.validated else "no", h.error or h.path]) for h in history]
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        nxt = f"Next automatic backup: {fmt(bs.next_run(clock.now(), at))}" if scheduled else "Automatic backups are off (Settings > Backup)."
        self.info.setText(f"Backup folder: <b>{folder}</b><br>{nxt}"
                          f"{'' if last is None else '<br>Last automatic backup: ' + fmt(last)}")
        self.table.set_rows(rows)

    def _busy(self, on: bool) -> None:
        self.progress.setVisible(on)
        for b in self.buttons:
            b.setEnabled(not on)

    def open_folder(self) -> None:
        import os

        with session_scope() as s:
            folder = bs.default_backup_dir(s)
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def backup_now(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Back up now")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Choose what to create:"))
        with session_scope() as s:
            chosen = backup_runner.configured_formats(s)
        boxes = {}
        for key, label in LABELS.items():
            boxes[key] = QCheckBox(label)
            boxes[key].setChecked(key in chosen)
            layout.addWidget(boxes[key])
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dialog.accept)
        bb.rejected.connect(dialog.reject)
        layout.addWidget(bb)
        if not dialog.exec():
            return
        formats = [k for k, b in boxes.items() if b.isChecked()]
        if not formats:
            return notify("Choose at least one format.", "info")
        self._busy(True)
        run_async(lambda: backup_runner.run_backups(formats, "manual"), self._backup_done,
                  lambda exc: (self._busy(False), show_error(self, exc)))

    def _backup_done(self, outcomes) -> None:
        self._busy(False)
        failed = [o for o in outcomes if not o.ok]
        if failed:
            QMessageBox.warning(self, "Backup finished with problems",
                                "\n\n".join(f"{o.format}: {o.error}" for o in failed))
        if len(failed) < len(outcomes):
            notify(f"{len(outcomes) - len(failed)} backup file(s) created and verified.")
        self.refresh()

    def restore(self) -> None:
        with session_scope() as s:
            start = str(bs.default_backup_dir(s))
        path, _ = QFileDialog.getOpenFileName(self, "Choose a backup to restore", start,
                                              "PostgreSQL backups (*.backup *.sql)")
        if not path:
            return
        if not confirm(self, "Restore backup",
                       f"This REPLACES all current data with the contents of:\n{Path(path).name}\n\n"
                       "A safety copy of the current data is saved first, and nothing changes if the restore fails.\n\n"
                       "Continue?", "Restore"):
            return
        self._busy(True)
        run_async(lambda: backup_runner.restore_from_file(Path(path)), self._restore_done,
                  lambda exc: (self._busy(False), show_error(self, exc, "Restore failed")))

    def _restore_done(self, safety: Path) -> None:
        self._busy(False)
        QMessageBox.information(self, "Restore complete",
                                f"The backup was restored.\n\nYour previous data was saved to:\n{safety}")
        self.refresh()

    def verify(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a backup to verify", "", "PostgreSQL backups (*.backup *.sql)")
        if not path:
            return
        self._busy(True)
        run_async(lambda: backup_runner.verify_file(Path(path)), self._verify_done,
                  lambda exc: (self._busy(False), show_error(self, exc, "Verification failed")))

    def _verify_done(self, counts: dict) -> None:
        self._busy(False)
        lines = "\n".join(f"{t}: {n}" for t, n in counts.items() if n)
        QMessageBox.information(self, "Backup verified",
                                "The file restores cleanly into a scratch database. Rows found:\n\n" + (lines or "(no rows)"))
