"""New-member wizard: details -> plan & amount -> UPI QR + owner verification -> save."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QComboBox, QDateEdit, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from database.connection import session_scope
from services import plan_service, registration_service, settings_service, validators
from services.errors import DomainError
from ui.common import money, notify, show_error
from ui.payments.payment_panel import PaymentPanel
from ui.receipts import open_receipt, open_whatsapp_chat
from ui.widgets.forms import Field, FormPanel
from ui.widgets.page import scrollable

GENDERS = ["", "Male", "Female", "Other"]
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"


def member_fields(require_gender: bool, values: dict | None = None) -> list[Field]:
    v = values or {}
    return [
        Field("full_name", "Full name", required=True, value=v.get("full_name")),
        Field("phone", "Phone number", required=True, value=v.get("phone"), placeholder="e.g. 9876543210"),
        Field("email", "Email", value=v.get("email")),
        Field("gender", "Gender", "choice", required=require_gender, choices=GENDERS,
              value=(v.get("gender") or "").capitalize()),
        Field("address", "Address", "multiline", value=v.get("address")),
        Field("date_of_joining", "Date of joining", "date", value=v.get("date_of_joining") or date.today()),
        Field("emergency_contact_name", "Emergency contact", value=v.get("emergency_contact_name")),
        Field("emergency_contact_phone", "Emergency phone", value=v.get("emergency_contact_phone")),
        Field("notes", "Notes", "multiline", value=v.get("notes")),
        Field("photo_source", "Photograph", "file", file_filter=IMAGE_FILTER),
    ]


class RegistrationDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Register new member")
        self.setMinimumSize(820, 600)
        self.result = None
        with session_scope() as s:
            require_gender = bool(settings_service.get(s, "gym.require_gender"))
            self.plans = [(p.id, p.name, p.duration_days, p.price) for p in plan_service.list_plans(s, True)]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        self.step_label = QLabel("")
        self.step_label.setObjectName("pageTitle")
        self.step_label.setStyleSheet("font-size: 15pt;")
        layout.addWidget(self.step_label)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.details = FormPanel(member_fields(require_gender))
        self.stack.addWidget(scrollable(self.details))  # ten fields: scroll on short screens instead of overlapping

        plan_page = QWidget()
        pf = QFormLayout(plan_page)
        pf.setVerticalSpacing(12)
        self.plan_combo = QComboBox()
        for pid, name, days, price in self.plans:
            self.plan_combo.addItem(f"{name}  -  {days} days  -  {money(price)}", pid)
        self.start_date = QDateEdit(QDate.currentDate())
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("dd MMM yyyy")
        self.amount = QLineEdit()
        self.summary = QLabel("")
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        pf.addRow("Membership plan", self.plan_combo)
        pf.addRow("Start date", self.start_date)
        pf.addRow("Amount to pay", self.amount)
        pf.addRow("", self.summary)
        self.stack.addWidget(plan_page)

        self.payment = PaymentPanel(editable_amount=False)
        self.stack.addWidget(self.payment)

        self.error = QLabel("")
        self.error.setStyleSheet("color:#ff5470;")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        self.back_btn = QPushButton("Back")
        self.back_btn.setObjectName("secondary")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("secondary")
        self.next_btn = QPushButton("Next")
        self.pending_btn = QPushButton("Register - payment pending")
        self.pending_btn.setObjectName("secondary")
        self.confirm_btn = QPushButton("Payment received - register")
        row.addWidget(self.back_btn)
        row.addStretch(1)
        for b in (self.cancel_btn, self.next_btn, self.pending_btn, self.confirm_btn):
            row.addWidget(b)
        layout.addLayout(row)

        self.back_btn.clicked.connect(lambda: self._go(self.stack.currentIndex() - 1))
        self.cancel_btn.clicked.connect(self.reject)
        self.next_btn.clicked.connect(self._next)
        self.pending_btn.clicked.connect(lambda: self._register(False))
        self.confirm_btn.clicked.connect(lambda: self._register(True))
        self.plan_combo.currentIndexChanged.connect(self._plan_changed)
        self.start_date.dateChanged.connect(lambda _: self._plan_changed())
        self.payment.changed.connect(self._sync_buttons)
        if not self.plans:
            self.error.setText("There are no active membership plans. Add one in Settings first.")
            self.next_btn.setEnabled(False)
        else:
            self._plan_changed(reset_amount=True)
        self._go(0)

    # ---- navigation ------------------------------------------------------------------------
    def _go(self, index: int) -> None:
        self.error.setText("")
        self.stack.setCurrentIndex(max(0, index))
        i = self.stack.currentIndex()
        self.step_label.setText(["Step 1 of 3 - Member details", "Step 2 of 3 - Membership plan",
                                 "Step 3 of 3 - Collect payment"][i])
        self.back_btn.setVisible(i > 0)
        self.next_btn.setVisible(i < 2)
        self.pending_btn.setVisible(i == 2)
        self.confirm_btn.setVisible(i == 2)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.confirm_btn.setEnabled(self.stack.currentIndex() == 2 and self.payment.is_verified())

    def _plan_changed(self, *_args, reset_amount: bool = True) -> None:
        idx = self.plan_combo.currentIndex()
        if idx < 0:
            return
        _, name, days, price = self.plans[idx]
        start = self.start_date.date().toPython()
        from services.membership_logic import end_date_for

        self.amount.setText(f"{price:.2f}")
        self.summary.setText(f"{name}: {start:%d %b %Y} to {end_date_for(start, days):%d %b %Y}. "
                             "Edit the amount to give a discount.")

    def _next(self) -> None:
        self.error.setText("")
        i = self.stack.currentIndex()
        try:
            if i == 0:
                problem = self.details.missing_required()
                if problem:
                    raise DomainError(problem)
                v = self.details.values()
                validators.required(v["full_name"], "Full name")
                validators.phone(v["phone"])
                validators.email(v["email"])
            elif i == 1:
                due = validators.money(self.amount.text(), "Amount to pay", allow_zero=True)
                if due == 0:
                    self._register(False)  # free membership: nothing to collect
                    return
                self.payment.load(due, f"{self.plan_combo.currentText().split('  -  ')[0]} membership")
        except DomainError as exc:
            self.error.setText(str(exc))
            return
        self._go(i + 1)

    # ---- save --------------------------------------------------------------------------------
    def _register(self, paid: bool) -> None:
        v = self.details.values()
        fields = {k: (val or None) if isinstance(val, str) else val for k, val in v.items()}
        fields["full_name"], fields["phone"] = v["full_name"], v["phone"]
        plan_id = self.plans[self.plan_combo.currentIndex()][0]
        payment = None
        if paid:
            pv = self.payment.values()
            payment = {"verified": self.payment.is_verified(), "method": pv["method"], "reference": pv["reference"]}
        try:
            with session_scope() as s:
                result = registration_service.register_with_membership(
                    s, fields, plan_id, self.start_date.date().toPython(), self.amount.text(), payment)
                self.result = (result.member.member_code, result.member.full_name,
                               result.payment.id if result.payment else None)
        except DomainError as exc:
            self.error.setText(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            show_error(self, exc)
            return
        code, name, payment_id = self.result
        notify(f"{name} registered as {code}." + ("" if paid else " Membership is waiting for payment."))
        self.accept()
        if payment_id:
            open_receipt(self.parent(), payment_id)
            open_whatsapp_chat(self.parent(), payment_id, "registration")
