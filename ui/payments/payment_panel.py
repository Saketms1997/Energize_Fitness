from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout,
                               QWidget)

from database.connection import session_scope
from services import settings_service, upi_service
from services.errors import BusinessRuleError
from ui.common import money


class PaymentPanel(QWidget):
    """Shows the owner's UPI QR beside the payment details.

    Displaying the QR never records a payment. The owner must tick the verification box
    ("I have checked my bank/UPI app...") before a payment can be confirmed.
    """

    changed = Signal()

    def __init__(self, editable_amount: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setSpacing(24)
        left = QVBoxLayout()
        self.qr = QLabel("Loading QR...")
        self.qr.setFixedSize(240, 240)
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setStyleSheet("background:#ffffff; border-radius:12px; color:#333; padding:6px;")
        self.qr.setWordWrap(True)
        self.qr_caption = QLabel("")
        self.qr_caption.setObjectName("muted")
        self.qr_caption.setWordWrap(True)
        self.qr_caption.setMaximumWidth(240)
        left.addWidget(self.qr)
        left.addWidget(self.qr_caption)
        left.addStretch(1)
        outer.addLayout(left)

        right = QVBoxLayout()
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.amount = QLineEdit()
        self.amount.setReadOnly(not editable_amount)
        self.method = QComboBox()
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("UPI transaction ID / UTR / cheque no.")
        form.addRow("Amount to collect", self.amount)
        form.addRow("Payment method", self.method)
        form.addRow("Reference", self.reference)
        right.addLayout(form)
        self.verified = QCheckBox()
        self.verified_label = QLabel("")
        self.verified_label.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.verified, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.verified_label, 1)
        right.addLayout(row)
        self.hint = QLabel("The membership is activated only after you confirm the money has arrived.")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        right.addWidget(self.hint)
        right.addStretch(1)
        outer.addLayout(right, 1)
        self.verified.toggled.connect(lambda _: self.changed.emit())
        self.amount.textChanged.connect(lambda _: (self._relabel(), self.changed.emit()))

    def load(self, amount: Decimal, note: str = "") -> None:
        self.amount.setText(f"{amount:.2f}")
        with session_scope() as s:
            methods = settings_service.get(s, "payment.methods")
            try:
                qr = upi_service.get_payment_qr(s, amount, note)
            except BusinessRuleError as exc:
                qr, problem = None, str(exc)
        self.method.clear()
        self.method.addItems(methods)
        idx = self.method.findText("UPI")
        if idx >= 0:
            self.method.setCurrentIndex(idx)
        if qr is None:
            self.qr.setPixmap(QPixmap())
            self.qr.setText(problem)
            self.qr_caption.setText("")
        else:
            pixmap = QPixmap()
            pixmap.loadFromData(qr.png)
            self.qr.setPixmap(pixmap.scaled(228, 228, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
            self.qr_caption.setText(
                f"{qr.payee} - {qr.upi_id}\n" + ("Amount is pre-filled in the QR." if qr.dynamic else
                                                  f"Ask the member to pay {money(amount)}."))
        self._relabel()

    def _relabel(self) -> None:
        self.verified_label.setText(f"I have checked my bank / UPI app and confirm that {self.amount_text()} "
                                    "has been received.")

    def amount_text(self) -> str:
        try:
            return money(Decimal(self.amount.text() or "0"))
        except Exception:  # noqa: BLE001
            return "the amount"

    def values(self) -> dict:
        return {"amount": self.amount.text().strip(), "method": self.method.currentText(),
                "reference": self.reference.text().strip() or None}

    def is_verified(self) -> bool:
        return self.verified.isChecked()
