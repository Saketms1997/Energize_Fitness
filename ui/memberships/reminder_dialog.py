from __future__ import annotations

import uuid

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from database.connection import session_scope
from services import membership_service, whatsapp_service
from ui.common import fmt, notify, show_error

FILTERS = [("Expiring soon", "expiring"), ("Expired", "expired")]


def open_chat(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


def send_reminder(parent: QWidget | None, subscription_id: uuid.UUID) -> None:
    """Open WhatsApp with the reminder for one membership; the owner presses Send."""
    try:
        with session_scope() as s:
            link = whatsapp_service.build_reminder_links(s, [subscription_id])[0]
    except Exception as exc:  # noqa: BLE001
        return show_error(parent, exc, "Cannot open WhatsApp")
    if link.url is None:
        return notify(f"{link.member_name} has no phone number saved.", "error")
    open_chat(link.url)


class BulkReminderDialog(QDialog):
    """Pick members whose membership is ending (or ended) and step through their WhatsApp chats.

    WhatsApp cannot be sent from a link without a person pressing Send, so each click opens the next member's
    chat with the message filled in. Send it, then click again for the next one.
    """

    def __init__(self, parent: QWidget | None = None, state: str = "expiring"):
        super().__init__(parent)
        self.setWindowTitle("Send membership reminders on WhatsApp")
        self.setMinimumSize(520, 460)
        self._queue: list[tuple[str, str]] = []   # (member name, url) still to open
        self._total = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        title = QLabel("Membership reminders")
        title.setObjectName("pageTitle")
        title.setStyleSheet("font-size: 15pt;")
        layout.addWidget(title)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Members whose membership is"))
        self.filter = QComboBox()
        for label, value in FILTERS:
            self.filter.addItem(label, value)
        self.filter.setCurrentIndex(max(0, self.filter.findData(state)))
        bar.addWidget(self.filter)
        bar.addStretch(1)
        select_all = QPushButton("Select all")
        select_all.setObjectName("secondary")
        select_none = QPushButton("Clear")
        select_none.setObjectName("secondary")
        bar.addWidget(select_all)
        bar.addWidget(select_none)
        layout.addLayout(bar)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        row = QHBoxLayout()
        close = QPushButton("Close")
        close.setObjectName("secondary")
        self.go = QPushButton("Open WhatsApp")
        row.addStretch(1)
        row.addWidget(close)
        row.addWidget(self.go)
        layout.addLayout(row)

        self.filter.currentIndexChanged.connect(lambda _: self._load())
        select_all.clicked.connect(lambda: self._check_all(Qt.CheckState.Checked))
        select_none.clicked.connect(lambda: self._check_all(Qt.CheckState.Unchecked))
        self.list.itemChanged.connect(lambda _: self._reset_queue())
        self.go.clicked.connect(self._open_next)
        close.clicked.connect(self.accept)
        self._load()

    def _load(self) -> None:
        try:
            with session_scope() as s:
                rows = membership_service.list_subscriptions(s, self.filter.currentData())
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        rows = [r for r in rows if not r.renewed]  # a member who already renewed does not need a reminder
        self.list.blockSignals(True)
        self.list.clear()
        for r in rows:
            item = QListWidgetItem(f"{r.member_name}  ({r.member_code})  -  {r.plan_name}, ends {fmt(r.end_date)}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, r.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._reset_queue()

    def _check_all(self, state: Qt.CheckState) -> None:
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def _checked_ids(self) -> list[uuid.UUID]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def _reset_queue(self) -> None:
        self._queue = []
        n = len(self._checked_ids())
        self.go.setEnabled(n > 0)
        self.go.setText(f"Open WhatsApp for {n} member(s)" if n else "Open WhatsApp")
        self.info.setText("Each click opens the next member's chat with the message ready. Press Send in WhatsApp, "
                          "then come back and click again." if n else "Tick the members to remind, or none are due.")

    def _open_next(self) -> None:
        if not self._queue:
            try:
                with session_scope() as s:
                    links = whatsapp_service.build_reminder_links(s, self._checked_ids())
            except Exception as exc:  # noqa: BLE001
                return show_error(self, exc)
            missing = [link.member_name for link in links if link.url is None]
            self._queue = [(link.member_name, link.url) for link in links if link.url]
            self._total = len(self._queue)
            if missing:
                notify("No phone number saved for: " + ", ".join(missing), "error")
            if not self._queue:
                return
        name, url = self._queue.pop(0)
        open_chat(url)
        done = self._total - len(self._queue)
        if self._queue:
            self.go.setText(f"Next member ({done} of {self._total} opened)")
            self.info.setText(f"Opened {name}. Press Send in WhatsApp, then click Next. Next up: {self._queue[0][0]}.")
        else:
            self.info.setText(f"Opened {name} - that was the last one ({self._total} of {self._total}).")
            self.go.setText("Done - open again")
