from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QComboBox, QDateEdit, QHBoxLayout, QLabel, QTabWidget, QVBoxLayout, QWidget)

from database.connection import session_scope
from services import payment_service as pay
from ui.common import confirm, money, notify, pretty_state, show_error
from ui.payments.payment_dialog import CollectPaymentDialog
from ui.receipts import open_receipt, open_whatsapp_chat
from ui.widgets.forms import Field, ask_form
from ui.widgets.page import Page, button_row, make_button
from ui.widgets.table import DataTable


class PaymentsPage(Page):
    def __init__(self, parent=None):
        super().__init__("Payments", "Collect, confirm, refund and review payments. Receipts are PDFs.", parent)
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        self._rows: dict = {}
        self._outstanding: dict = {}

        # -- all payments
        tab = QWidget()
        lay = QVBoxLayout(tab)
        bar = QHBoxLayout()
        self.status = QComboBox()
        for label, value in (("All", None), ("Confirmed", "confirmed"), ("Pending (not verified)", "pending"),
                             ("Voided", "voided")):
            self.status.addItem(label, value)
        bar.addWidget(QLabel("Show"))
        bar.addWidget(self.status)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.table = DataTable(["Receipt", "Date", "Member", "Type", "Method", "Reference", "Amount", "Status"], stretch=2)
        lay.addWidget(self.table, 1)
        lay.addLayout(button_row(
            make_button("Open receipt", self.receipt), make_button("Confirm pending", self.confirm_pending),
            make_button("Refund...", self.refund), make_button("Void...", self.void, "danger")))
        self.tabs.addTab(tab, "All payments")

        # -- outstanding
        tab2 = QWidget()
        lay2 = QVBoxLayout(tab2)
        self.out_label = QLabel("")
        self.out_label.setObjectName("muted")
        lay2.addWidget(self.out_label)
        self.out_table = DataTable(["Member ID", "Member", "Plan", "Expires", "Fee", "Paid", "Outstanding"], stretch=1)
        lay2.addWidget(self.out_table, 1)
        lay2.addLayout(button_row(make_button("Collect payment", self.collect, "primary")))
        self.tabs.addTab(tab2, "Outstanding")

        # -- collections
        tab3 = QWidget()
        lay3 = QVBoxLayout(tab3)
        bar3 = QHBoxLayout()
        today = QDate.currentDate()
        self.from_date, self.to_date = QDateEdit(QDate(today.year(), today.month(), 1)), QDateEdit(today)
        for e in (self.from_date, self.to_date):
            e.setCalendarPopup(True)
            e.setDisplayFormat("dd MMM yyyy")
        bar3.addWidget(QLabel("From"))
        bar3.addWidget(self.from_date)
        bar3.addWidget(QLabel("To"))
        bar3.addWidget(self.to_date)
        bar3.addStretch(1)
        lay3.addLayout(bar3)
        self.totals = QLabel("")
        self.totals.setStyleSheet("font-size: 13pt; font-weight: 600;")
        lay3.addWidget(self.totals)
        self.method_table = DataTable(["Method", "Net collected"], stretch=0)
        lay3.addWidget(self.method_table, 1)
        self.tabs.addTab(tab3, "Collections")

        self.status.currentIndexChanged.connect(lambda _: self.refresh())
        self.from_date.dateChanged.connect(lambda _: self.refresh())
        self.to_date.dateChanged.connect(lambda _: self.refresh())
        self.tabs.currentChanged.connect(lambda _: self.refresh())

    def refresh(self) -> None:
        try:
            with session_scope() as s:
                payments = pay.list_payments(s, self.status.currentData())
                outstanding = pay.outstanding_subscriptions(s)
                first, last = self.from_date.date().toPython(), self.to_date.date().toPython()
                summary = pay.collection_summary(s, first, last)
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        self._rows = {p.id: p for p in payments}
        self.table.set_rows([(p.id, [p.receipt_no, p.paid_at, f"{p.member_name} ({p.member_code})", p.kind, p.method,
                                     p.reference, p.amount if p.kind == "payment" else -p.amount,
                                     pretty_state(p.status)]) for p in payments])
        self._outstanding = {r.id: r for r in outstanding}
        self.out_table.set_rows([(r.id, [r.member_code, r.member_name, r.plan_name, r.end_date, r.amount_due, r.paid,
                                         r.outstanding]) for r in outstanding])
        self.out_label.setText(f"{len(outstanding)} membership(s) owe {money(sum(r.outstanding for r in outstanding))} in total.")
        self.totals.setText(f"Collected {money(summary.payments_total)}   Refunded {money(summary.refunds_total)}   "
                            f"Net {money(summary.net)}")
        self.method_table.set_rows([(m, [m, t]) for m, t in summary.by_method.items()])

    def _payment(self):
        row = self._rows.get(self.table.selected_id())
        if row is None:
            notify("Select a payment first.", "info")
        return row

    def receipt(self) -> None:
        if (p := self._payment()) is None:
            return
        if p.status != "confirmed":
            return notify("Receipts exist only for confirmed payments.", "info")
        open_receipt(self, p.id)

    def confirm_pending(self) -> None:
        if (p := self._payment()) is None:
            return
        if p.status != "pending":
            return notify("That payment is not pending.", "info")
        if not confirm(self, "Confirm payment",
                       f"Have you checked your bank / UPI app and seen {money(p.amount)} from {p.member_name} "
                       f"({p.method}{', ref ' + p.reference if p.reference else ''})?", "Yes, it arrived"):
            return
        try:
            with session_scope() as s:
                pay.confirm_payment(s, p.id)
                pay.queue_confirmation_message(s, pay.get_payment(s, p.id))
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        notify("Payment confirmed.")
        self.refresh()
        open_whatsapp_chat(self, p.id)

    def refund(self) -> None:
        if (p := self._payment()) is None:
            return
        with session_scope() as s:
            try:
                limit = pay.refundable_amount(s, p.id)
            except Exception as exc:  # noqa: BLE001
                return show_error(self, exc)

        def submit(v: dict) -> None:
            with session_scope() as s:
                pay.refund_payment(s, p.id, v["amount"], v["reason"])

        fields = [Field("amount", "Refund amount", required=True, value=f"{limit:.2f}"),
                  Field("reason", "Reason", "multiline", True)]
        ok, _ = ask_form(self, "Refund payment", fields, submit, "Refund",
                         f"Refundable: {money(limit)}. A refund is recorded as a separate entry; the original is kept.")
        if ok:
            notify("Refund recorded.")
            self.refresh()

    def void(self) -> None:
        if (p := self._payment()) is None:
            return

        def submit(v: dict) -> None:
            with session_scope() as s:
                pay.void_payment(s, p.id, v["reason"])

        ok, _ = ask_form(self, "Void payment", [Field("reason", "Reason", "multiline", True)], submit, "Void it",
                         "Use only for a payment entered by mistake. If money was really received, refund it instead.")
        if ok:
            notify("Payment voided.")
            self.refresh()

    def collect(self) -> None:
        sub_id = self.out_table.selected_id()
        if sub_id is None:
            return notify("Select a membership first.", "info")
        if CollectPaymentDialog(sub_id, self).exec():
            self.refresh()
