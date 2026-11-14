from __future__ import annotations

import os
import uuid

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QWidget

from database.connection import session_scope
from services import receipt_service, whatsapp_service
from ui.common import notify, show_error


def open_receipt(parent: QWidget | None, payment_id: uuid.UUID) -> None:
    """Generate the receipt PDF and open it in the default viewer."""
    try:
        with session_scope() as s:
            path = receipt_service.generate_receipt_pdf(s, payment_id)
        os.startfile(path)  # noqa: S606 - opens the PDF we just wrote
        notify(f"Receipt saved: {path.name}")
    except Exception as exc:  # noqa: BLE001
        show_error(parent, exc, "Cannot open receipt")


def open_whatsapp_chat(parent: QWidget | None, payment_id: uuid.UUID, kind: str = "payment_confirmation") -> None:
    """Open WhatsApp with the member's chat and the payment message filled in and the receipt PDF copied
    for pasting; the owner attaches and sends it by hand."""
    try:
        with session_scope() as s:
            url = whatsapp_service.build_chat_link(s, payment_id, kind)
            path = receipt_service.generate_receipt_pdf(s, payment_id)
        # A chat link cannot attach files, so put the receipt on the clipboard: Ctrl+V in the chat attaches it.
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(path))])
        QGuiApplication.clipboard().setMimeData(data)
        QDesktopServices.openUrl(QUrl(url))
        notify("In WhatsApp, press Ctrl+V to attach the receipt, then Send.", "info")
    except Exception as exc:  # noqa: BLE001
        show_error(parent, exc, "Cannot open WhatsApp")
