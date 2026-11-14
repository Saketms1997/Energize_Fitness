from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QProgressBar, QTabWidget, QVBoxLayout, QWidget)

from database.connection import session_scope
from services import sync_runner, sync_service as sy
from ui.async_utils import run_async
from ui.common import fmt, notify, show_error
from ui.widgets.cards import StatCard
from ui.widgets.page import Page, button_row, make_button
from ui.widgets.table import DataTable


class ConflictDialog(QDialog):
    """Per-field decision for one conflicting record."""

    def __init__(self, conflict_id, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Resolve sync conflict")
        self.setMinimumWidth(720)
        with session_scope() as s:
            c = s.get(sy.SyncConflict, conflict_id)
            self.conflict_id = c.id
            self.fields = list(c.conflicting_fields)
            local, remote = c.local_snapshot, c.remote_snapshot
            table, financial, auto = c.entity_table, c.is_financial, list(c.auto_merged_fields)
            label = remote.get("full_name") or remote.get("member_code") or remote.get("receipt_no") or c.entity_id
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        title = QLabel(f"{table.replace('_', ' ').title()}: {label}")
        title.setObjectName("pageTitle")
        title.setStyleSheet("font-size: 15pt;")
        layout.addWidget(title)
        intro = QLabel("This record was changed on this PC and in the cloud. Nothing has been overwritten. "
                       "Choose the value to keep for each field.")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)
        if financial:
            warn = QLabel("This is a financial record. Check the amounts carefully before choosing.")
            warn.setStyleSheet("color:#f5b83d; font-weight:600;")
            layout.addWidget(warn)
        grid = QGridLayout()
        for col, head in enumerate(("Field", "This PC", "Cloud", "Keep")):
            h = QLabel(head)
            h.setObjectName("cardTitle")
            grid.addWidget(h, 0, col)
        self.choices: dict[str, tuple[QComboBox, QLineEdit]] = {}
        for row, name in enumerate(self.fields, 1):
            grid.addWidget(QLabel(name), row, 0)
            for col, snap in ((1, local), (2, remote)):
                value = QLabel(str(snap.get(name)))
                value.setWordWrap(True)
                grid.addWidget(value, row, col)
            combo = QComboBox()
            combo.addItem("This PC's value", "local")
            combo.addItem("Cloud's value", "remote")
            combo.addItem("Type my own...", "manual")
            custom = QLineEdit()
            custom.setPlaceholderText("Your value")
            custom.setVisible(False)
            combo.currentIndexChanged.connect(lambda _i, c=combo, e=custom: e.setVisible(c.currentData() == "manual"))
            box = QVBoxLayout()
            box.addWidget(combo)
            box.addWidget(custom)
            grid.addLayout(box, row, 3)
            self.choices[name] = (combo, custom)
        layout.addLayout(grid)
        if auto:
            note = QLabel("Merged automatically (no decision needed): " + ", ".join(auto))
            note.setObjectName("muted")
            note.setWordWrap(True)
            layout.addWidget(note)
        self.error = QLabel("")
        self.error.setStyleSheet("color:#ff5470;")
        layout.addWidget(self.error)
        bb = QDialogButtonBox()
        ok = bb.addButton("Apply decision", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton("Decide later", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self._apply)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _apply(self) -> None:
        picks = {}
        for name, (combo, custom) in self.choices.items():
            kind = combo.currentData()
            picks[name] = {"value": custom.text()} if kind == "manual" else kind
        try:
            with session_scope() as s:
                sy.resolve_conflict(s, self.conflict_id, picks)
        except Exception as exc:  # noqa: BLE001
            self.error.setText(str(exc))
            return
        self.accept()


class SyncPage(Page):
    def __init__(self, parent=None):
        super().__init__("Cloud Sync", "Two-way synchronisation with MongoDB Atlas. The app works fully offline.", parent)
        self.sync_btn = self.add_action("Sync now", self.sync_now)
        self.test_btn = self.add_action("Test connection", self.test_connection, "secondary")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.root.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.hide()
        self.root.addWidget(self.progress)
        cards = QHBoxLayout()
        self.cards = {n: StatCard(n, "-") for n in ("Uploaded", "Downloaded", "Updated", "Failed", "Conflicts")}
        for c in self.cards.values():
            cards.addWidget(c)
        self.root.addLayout(cards)
        self.tabs = QTabWidget()
        self.history = DataTable(["Started", "Trigger", "Status", "Uploaded", "Downloaded", "Updated", "Failed",
                                  "Conflicts", "Error"], stretch=8)
        self.failed = DataTable(["Table", "Attempts", "Queued", "Last error"], stretch=3)
        self.conflicts = DataTable(["Record", "Table", "Fields in conflict", "Financial", "Detected"], stretch=2)
        self.tabs.addTab(self.history, "History")
        failed_tab = QWidget()
        fl = QVBoxLayout(failed_tab)
        fl.addWidget(self.failed)
        fl.addLayout(button_row(make_button("Retry failed uploads", self.retry)))
        self.tabs.addTab(failed_tab, "Failed records")
        conflict_tab = QWidget()
        cl = QVBoxLayout(conflict_tab)
        cl.addWidget(self.conflicts)
        cl.addLayout(button_row(make_button("Resolve...", self.resolve, "primary")))
        self.tabs.addTab(conflict_tab, "Conflicts")
        self.root.addWidget(self.tabs, 1)
        self.conflicts.row_activated.connect(lambda _: self.resolve())
        self._running = False

    def refresh(self) -> None:
        try:
            with session_scope() as s:
                pending, last = sy.pending_count(s), sy.last_success(s)
                history = sy.list_history(s)
                failed = sy.failed_queue(s)
                conflicts = sy.open_conflicts(s)
                hist_rows = [(h.id, [h.started_at, h.trigger, h.status, h.uploaded, h.downloaded, h.updated, h.failed,
                                     h.conflicts, h.error]) for h in history]
                failed_rows = [(f.id, [f.entity_table, f.attempts, f.created_at, f.last_error]) for f in failed]
                conflict_rows = [(c.id, [str(c.remote_snapshot.get("full_name") or c.remote_snapshot.get("receipt_no")
                                             or c.entity_id)[:40], c.entity_table, ", ".join(c.conflicting_fields),
                                         c.is_financial, c.created_at]) for c in conflicts]
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        configured = sync_runner.is_configured()
        self.status.setText(
            f"{'Cloud connection is configured.' if configured else 'Not configured: add your Atlas connection string in Settings > Cloud.'}"
            f"  Waiting to upload: <b>{pending}</b> record(s).  Last successful sync: <b>{fmt(last) or 'never'}</b>.")
        self.sync_btn.setEnabled(configured and not self._running)
        self.history.set_rows(hist_rows)
        self.failed.set_rows(failed_rows)
        self.conflicts.set_rows(conflict_rows)
        self.tabs.setTabText(1, f"Failed records ({len(failed_rows)})")
        self.tabs.setTabText(2, f"Conflicts ({len(conflict_rows)})")
        if history:
            h = history[0]
            for name, value in (("Uploaded", h.uploaded), ("Downloaded", h.downloaded), ("Updated", h.updated),
                                ("Failed", h.failed), ("Conflicts", h.conflicts)):
                self.cards[name].set_value(str(value), "last sync")

    def sync_now(self, trigger: str = "manual") -> None:
        if self._running:
            return
        self._running = True
        self.sync_btn.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        run_async(lambda progress: sync_runner.run_sync(progress, trigger), self._done,
                  self._failed, self._progress)

    def _progress(self, done: int, total: int, message: str) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.progress.setFormat(f"{message}  ({done}/{total})")

    def _done(self, report: sy.SyncReport) -> None:
        self._running = False
        self.progress.hide()
        if report.status == "failed":
            notify(report.error or "Sync failed.", "error")
        elif report.status == "partial":
            notify(f"Sync finished with {report.failed} failure(s) and {report.conflicts} conflict(s).", "info")
        else:
            notify(f"Sync complete: {report.uploaded} up, {report.downloaded} down, {report.updated} updated.")
        self.refresh()

    def _failed(self, exc: BaseException) -> None:
        self._running = False
        self.progress.hide()
        self.refresh()
        show_error(self, exc, "Sync failed")

    def test_connection(self) -> None:
        self.test_btn.setEnabled(False)

        def done(result) -> None:
            self.test_btn.setEnabled(True)
            notify(f"{result.message} (MongoDB {result.server_version})" if result.ok else result.message,
                   "ok" if result.ok else "error")

        run_async(sync_runner.test_connection, done, lambda exc: (self.test_btn.setEnabled(True), show_error(self, exc)))

    def retry(self) -> None:
        with session_scope() as s:
            count = sy.retry_failed(s)
        notify(f"{count} upload(s) queued for retry. Run Sync now.", "info")
        self.refresh()

    def resolve(self) -> None:
        cid = self.conflicts.selected_id()
        if cid is None:
            return notify("Select a conflict first.", "info")
        if ConflictDialog(cid, self).exec():
            notify("Decision saved. It will be uploaded on the next sync.")
            self.refresh()
