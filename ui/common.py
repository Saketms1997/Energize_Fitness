"""Shared UI helpers: formatting, message boxes, toast notifications."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

from services.errors import DomainError
from ui.theme import COLORS

CURRENCY = "₹"  # INR


def money(value: Decimal | float | int | None) -> str:
    return f"{CURRENCY}{Decimal(value or 0):,.2f}"


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    if isinstance(value, datetime):
        return value.astimezone().strftime("%d %b %Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    return str(value)


def pretty_state(state: str) -> str:
    return {"none": "No membership", "inactive": "Inactive"}.get(state, state.capitalize())


def show_error(parent: QWidget | None, exc: BaseException, title: str = "Something went wrong") -> None:
    """DomainErrors are for the owner; anything else is logged and shown in plain words."""
    if isinstance(exc, DomainError):
        QMessageBox.warning(parent, title, str(exc))
        return
    import logging

    logging.getLogger("gym.ui").exception("Unexpected error", exc_info=exc)
    QMessageBox.critical(parent, title, f"An unexpected error occurred:\n\n{type(exc).__name__}: {exc}\n\n"
                                        "Details were written to the log file.")


def confirm(parent: QWidget | None, title: str, text: str, yes: str = "Yes") -> bool:
    box = QMessageBox(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.NoButton, parent)
    accept = box.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is accept


class Toast(QLabel):
    """Non-blocking success/error notification shown at the bottom right of the main window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setMaximumWidth(420)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, kind: str = "ok", msec: int = 4000) -> None:
        color = {"ok": COLORS["ok"], "error": COLORS["error"], "info": COLORS["accent"]}[kind]
        self.setStyleSheet(f"background:{COLORS['surface_alt']}; color:{COLORS['text']}; border:1px solid {color};"
                           f"border-left:5px solid {color}; border-radius:8px; padding:12px 16px; font-weight:600;")
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        self.move(parent.width() - self.width() - 24, parent.height() - self.height() - 48)
        self.raise_()
        self.show()
        self._timer.start(msec)


_toast: Toast | None = None


def install_toast(window: QWidget) -> None:
    global _toast
    _toast = Toast(window)


def notify(text: str, kind: str = "ok") -> None:
    if _toast is not None:
        _toast.show_message(text, kind)
