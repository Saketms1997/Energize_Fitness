from __future__ import annotations

import logging
import time
from datetime import date

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from database.connection import ConnectionStatus, session_scope
from services import backup_runner, settings_service, sync_runner, whatsapp_service
from ui.async_utils import run_async
from ui.attendance.attendance_page import AttendancePage
from ui.backup.backup_page import BackupPage
from ui.common import install_toast, notify
from ui.dashboard.dashboard_page import DashboardPage
from ui.expenses.expenses_page import ExpensesPage
from ui.members.members_page import MembersPage
from ui.memberships.memberships_page import MembershipsPage
from ui.payments.payments_page import PaymentsPage
from ui.reports.reports_page import ReportsPage
from ui.settings.settings_page import SettingsPage
from ui.sync.sync_page import SyncPage
from ui.trainers.trainers_page import TrainersPage
from ui.whatsapp.whatsapp_page import WhatsAppPage
from ui.widgets.cards import StatusDot
from ui.workers import DbStatusMonitor

log = logging.getLogger("gym")
APP_TITLE = "Gym Management System"
POLL_INTERVAL_MS = 15_000
IDLE_LOCK_SECONDS = 30 * 60

NAV_ITEMS = ["Dashboard", "Members", "Memberships", "Attendance", "Payments", "Trainers", "Expenses", "Reports",
             "Backup & Restore", "Cloud Sync", "WhatsApp", "Settings"]


class _ActivityFilter(QObject):
    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self.window = window

    def eventFilter(self, _obj, event) -> bool:  # noqa: N802
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.MouseMove):
            self.window.last_input = time.monotonic()
        return False


class MainWindow(QMainWindow):
    def __init__(self, monitor: DbStatusMonitor | None = None, background: bool = False,
                 username: str | None = None, display_name: str = ""):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1360, 860)
        self.setMinimumSize(1180, 680)
        self.username, self.display_name = username, display_name
        self.last_input = time.monotonic()
        self._locked = False

        central = QWidget()
        central.setObjectName("central")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        self.monitor = monitor or DbStatusMonitor(self)
        self.stack = QStackedWidget()
        self.stack.setObjectName("content")
        self.nav_buttons: dict[str, QPushButton] = {}
        root.addWidget(self._build_sidebar())
        root.addWidget(self.stack, 1)

        self.dashboard = DashboardPage(on_refresh=self.monitor.refresh)
        self.pages: dict[str, QWidget] = {
            "Dashboard": self.dashboard, "Members": MembersPage(), "Memberships": MembershipsPage(),
            "Attendance": AttendancePage(), "Payments": PaymentsPage(), "Trainers": TrainersPage(),
            "Expenses": ExpensesPage(), "Reports": ReportsPage(), "Backup & Restore": BackupPage(),
            "Cloud Sync": SyncPage(), "WhatsApp": WhatsAppPage(), "Settings": SettingsPage(),
        }
        for name in NAV_ITEMS:
            page = self.pages[name]
            page.setProperty("page_name", name)
            self.stack.addWidget(page)
        self.nav_buttons["Dashboard"].setChecked(True)

        self._build_status_bar()
        install_toast(self)
        self.monitor.status_changed.connect(self._on_status)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.monitor.refresh)
        self.timer.start(POLL_INTERVAL_MS)
        self.monitor.refresh()
        if background:
            self._start_background_jobs()

    # ---- layout --------------------------------------------------------------------------------
    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 22, 16, 16)
        layout.setSpacing(4)
        brand_row = QHBoxLayout()
        accent, name = QLabel("Energize"), QLabel(" Gym")
        accent.setObjectName("brandAccent")
        name.setObjectName("brand")
        brand_row.addWidget(accent)
        brand_row.addWidget(name)
        brand_row.addStretch(1)
        layout.addLayout(brand_row)
        sub = QLabel("MANAGEMENT SYSTEM")
        sub.setObjectName("brandSub")
        layout.addWidget(sub)
        layout.addSpacing(18)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for label in NAV_ITEMS:
            button = QPushButton(label.replace("&", "&&"))  # '&&' = literal ampersand in Qt
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, n=label: self.show_page(n))
            self._nav_group.addButton(button)
            self.nav_buttons[label] = button
            layout.addWidget(button)
        layout.addStretch(1)
        self.user_label = QLabel(self.display_name or "")
        self.user_label.setObjectName("muted")
        layout.addWidget(self.user_label)
        lock = QPushButton("Lock")
        lock.setObjectName("secondary")
        lock.clicked.connect(self.lock)
        layout.addWidget(lock)
        return sidebar

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self.sync_label = QLabel("")
        bar.addWidget(self.sync_label)
        self.db_dot = StatusDot()
        self.db_label = QLabel("Database: checking...")
        bar.addPermanentWidget(self.db_dot)
        bar.addPermanentWidget(self.db_label)

    def show_page(self, name: str) -> None:
        page = self.pages[name]
        self.stack.setCurrentWidget(page)
        self.nav_buttons[name].setChecked(True)
        try:
            page.refresh()
        except Exception:  # noqa: BLE001 - a page failing to load must not take the app down
            log.exception("Page %s failed to refresh", name)
            notify(f"Could not load {name}. See the log for details.", "error")

    def _on_status(self, status: ConnectionStatus) -> None:
        self.db_dot.set_state("ok" if status.ok else "error")
        self.db_label.setText(f"Database: {status.message}")
        self.dashboard.show_status(status)

    # ---- security ------------------------------------------------------------------------------
    def lock(self) -> None:
        """Hide the data behind the password prompt (manual, or after 30 idle minutes)."""
        if self._locked or not self.username:
            return
        from ui.login import LoginDialog

        self._locked = True
        stack_was = self.stack.isVisible()
        self.stack.setVisible(False)
        dialog = LoginDialog(self, fixed_username=self.username, locked=True)
        accepted = dialog.exec()
        self.stack.setVisible(stack_was)
        self._locked = False
        self.last_input = time.monotonic()
        if not accepted:
            QApplication.quit()

    def _check_idle(self) -> None:
        if time.monotonic() - self.last_input > IDLE_LOCK_SECONDS:
            self.lock()

    # ---- background jobs -----------------------------------------------------------------------
    def _start_background_jobs(self) -> None:
        QApplication.instance().installEventFilter(_ActivityFilter(self))
        self._reminder_day: date | None = None
        self._sync_running = False
        self._timers: list[QTimer] = []
        for interval, handler in ((30_000, self._check_idle), (60_000, self._tick_backup),
                                  (120_000, self._tick_whatsapp), (60_000, self._tick_sync)):
            t = QTimer(self)
            t.timeout.connect(handler)
            t.start(interval)
            self._timers.append(t)
        QTimer.singleShot(5_000, self._tick_whatsapp)

    def _tick_backup(self) -> None:
        def work():
            return backup_runner.run_scheduled() if backup_runner.scheduled_backup_due() else None

        def done(outcomes):
            if outcomes:
                bad = [o for o in outcomes if not o.ok]
                notify("Scheduled backup finished." if not bad else f"Scheduled backup failed: {bad[0].error}",
                       "ok" if not bad else "error")

        run_async(work, done)

    def _tick_whatsapp(self) -> None:
        def work():
            with session_scope() as s:
                if not whatsapp_service.is_enabled(s):
                    return None
                if self._reminder_day != date.today():
                    whatsapp_service.queue_reminders(s)
                    self._reminder_day = date.today()
            return whatsapp_service.process_queue()

        run_async(work, lambda r: r and r["failed"] and notify(f"{r['failed']} WhatsApp message(s) failed.", "error"))

    def _tick_sync(self) -> None:
        if self._sync_running or not sync_runner.is_configured():
            return
        with session_scope() as s:
            minutes = int(settings_service.get(s, "local.sync.auto_minutes"))
        if minutes <= 0 or time.monotonic() - getattr(self, "_last_auto_sync", 0) < minutes * 60:
            return
        self._last_auto_sync = time.monotonic()
        self._sync_running = True
        page: SyncPage = self.pages["Cloud Sync"]  # type: ignore[assignment]

        def finished(report):
            self._sync_running = False
            self.sync_label.setText(f"Cloud sync: {report.status}")

        run_async(lambda: sync_runner.run_sync(None, "auto"), finished, lambda exc: finished(type("R", (), {"status": "failed"})()))
