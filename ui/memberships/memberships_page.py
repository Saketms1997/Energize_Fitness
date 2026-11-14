from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel

from database.connection import session_scope
from services import membership_service
from ui.common import confirm, notify, pretty_state, show_error
from ui.memberships.reminder_dialog import BulkReminderDialog, send_reminder
from ui.memberships.renew import renew_member
from ui.payments.payment_dialog import CollectPaymentDialog
from ui.widgets.page import Page, button_row, make_button
from ui.widgets.table import DataTable

HEADERS = ["Member ID", "Member", "Plan", "Start", "Expires", "Fee", "Paid", "Outstanding", "State", "Renewed"]
STATES = [("All", "all"), ("Active", "active"), ("Expiring soon", "expiring"), ("Expired", "expired"),
          ("Waiting for payment", "pending"), ("Upcoming", "upcoming"), ("Cancelled", "cancelled")]


class MembershipsPage(Page):
    def __init__(self, parent=None):
        super().__init__("Memberships", "Plans, expiry dates, renewals and renewal history.", parent)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Show"))
        self.state = QComboBox()
        for label, value in STATES:
            self.state.addItem(label, value)
        bar.addWidget(self.state)
        bar.addStretch(1)
        self.root.addLayout(bar)
        self.alert = QLabel("")
        self.alert.setStyleSheet("color:#f5b83d; font-weight:600;")
        self.root.addWidget(self.alert)
        self.table = DataTable(HEADERS, stretch=1)
        self.root.addWidget(self.table, 1)
        self.root.addLayout(button_row(
            make_button("Renew", self.renew), make_button("Collect payment", self.collect),
            make_button("Send reminder (WhatsApp)", self.remind), make_button("Remind many...", self.remind_many),
            make_button("Cancel membership", self.cancel, "danger")))
        self.state.currentIndexChanged.connect(lambda _: self.refresh())
        self._rows: dict = {}

    def refresh(self) -> None:
        try:
            with session_scope() as s:
                rows = membership_service.list_subscriptions(s, self.state.currentData())
                expiring = len(membership_service.list_subscriptions(s, "expiring"))
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        self._rows = {r.id: r for r in rows}
        self.table.set_rows([(r.id, [r.member_code, r.member_name, r.plan_name, r.start_date, r.end_date, r.amount_due,
                                     r.paid, r.outstanding, pretty_state(r.state), r.renewed]) for r in rows])
        self.alert.setText(f"{expiring} membership(s) expiring soon - consider sending renewal reminders."
                           if expiring else "")

    def _row(self):
        row = self._rows.get(self.table.selected_id())
        if row is None:
            notify("Select a membership first.", "info")
        return row

    def renew(self) -> None:
        if (row := self._row()) and renew_member(self, row.member_id):
            self.refresh()

    def collect(self) -> None:
        if (row := self._row()) is None:
            return
        if row.outstanding <= 0:
            return notify("Nothing outstanding on this membership.", "info")
        if CollectPaymentDialog(row.id, self).exec():
            self.refresh()

    def remind(self) -> None:
        if (row := self._row()) is not None:
            send_reminder(self, row.id)

    def remind_many(self) -> None:
        BulkReminderDialog(self, "expiring").exec()

    def cancel(self) -> None:
        if (row := self._row()) is None:
            return
        if not confirm(self, "Cancel membership", f"Cancel {row.member_name}'s {row.plan_name} membership?", "Cancel it"):
            return
        try:
            with session_scope() as s:
                membership_service.cancel_subscription(s, row.id)
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        notify("Membership cancelled.")
        self.refresh()
