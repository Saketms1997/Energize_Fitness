from __future__ import annotations

import uuid

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from database.connection import session_scope
from services import payment_service
from services.errors import DomainError
from ui.common import money, notify, show_error
from ui.payments.payment_panel import PaymentPanel
from ui.receipts import open_receipt, open_whatsapp_chat


class CollectPaymentDialog(QDialog):
    """Collect (part of) the balance of one membership."""

    def __init__(self, subscription_id: uuid.UUID, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Collect payment")
        self.subscription_id = subscription_id
        with session_scope() as s:
            ctx = payment_service.collection_context(s, subscription_id)
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        title = QLabel(f"Collect payment - {ctx.member_name}")
        title.setObjectName("pageTitle")
        title.setStyleSheet("font-size: 15pt;")
        layout.addWidget(title)
        info = QLabel(f"{ctx.member_code}  |  {ctx.plan_name}  |  {ctx.period}\n"
                      f"Fee {money(ctx.amount_due)}   Paid {money(ctx.paid)}   Outstanding {money(ctx.outstanding)}")
        info.setObjectName("muted")
        layout.addWidget(info)
        self.panel = PaymentPanel(editable_amount=True)
        self.panel.load(ctx.outstanding, f"{ctx.plan_name} membership")
        layout.addWidget(self.panel)
        self.error = QLabel("")
        self.error.setStyleSheet("color:#ff5470;")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        self.pending_btn = QPushButton("Save as pending (not verified)")
        self.pending_btn.setObjectName("secondary")
        self.confirm_btn = QPushButton("Confirm payment received")
        cancel = QPushButton("Cancel")
        cancel.setObjectName("secondary")
        row.addWidget(self.pending_btn)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(self.confirm_btn)
        layout.addLayout(row)
        self.confirm_btn.setEnabled(False)
        self.panel.changed.connect(lambda: self.confirm_btn.setEnabled(self.panel.is_verified()))
        cancel.clicked.connect(self.reject)
        self.confirm_btn.clicked.connect(lambda: self._save(True))
        self.pending_btn.clicked.connect(lambda: self._save(False))
        self.payment_id: uuid.UUID | None = None

    def _save(self, confirmed: bool) -> None:
        v = self.panel.values()
        try:
            with session_scope() as s:
                if confirmed:
                    payment = payment_service.record_confirmed_payment(
                        s, self.subscription_id, v["amount"], v["method"], v["reference"], notify=True)
                else:
                    payment = payment_service.create_pending_payment(
                        s, self.subscription_id, v["amount"], v["method"], v["reference"])
                self.payment_id = payment.id
        except DomainError as exc:
            self.error.setText(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            show_error(self, exc)
            return
        notify("Payment confirmed." if confirmed else "Saved as pending - confirm it once the money arrives.")
        self.accept()
        if confirmed:
            open_receipt(self.parent(), self.payment_id)
            open_whatsapp_chat(self.parent(), self.payment_id)
