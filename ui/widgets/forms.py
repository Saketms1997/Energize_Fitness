"""A small declarative form used for most add/edit screens."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from services.errors import DomainError
from ui.common import show_error


@dataclass
class Field:
    key: str
    label: str
    kind: str = "text"        # text | multiline | date | choice | bool | file | password | readonly
    required: bool = False
    value: Any = None
    choices: list[str] | None = None
    placeholder: str = ""
    file_filter: str = "All files (*.*)"
    editable_choice: bool = False
    optional_date: bool = False   # date field with a "set" checkbox (value None when unchecked)


class _FileField(QWidget):
    def __init__(self, filt: str, value: str | None, parent=None, folder: bool = False):
        super().__init__(parent)
        self.folder = folder
        self.edit = QLineEdit(value or "")
        self.edit.setReadOnly(True)
        button = QPushButton("Browse...")
        button.setObjectName("secondary")
        button.clicked.connect(self._pick)
        clear = QPushButton("Clear")
        clear.setObjectName("secondary")
        clear.clicked.connect(lambda: self.edit.setText(""))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for w in (self.edit, button, clear):
            layout.addWidget(w)
        self._filter = filt

    def _pick(self) -> None:
        if self.folder:
            path = QFileDialog.getExistingDirectory(self, "Choose folder", self.edit.text())
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choose file", "", self._filter)
        if path:
            self.edit.setText(path)

    def value(self) -> str | None:
        return self.edit.text() or None


class _OptionalDate(QWidget):
    def __init__(self, value: date | None, parent=None):
        super().__init__(parent)
        self.check = QCheckBox("Set")
        self.edit = QDateEdit(QDate.currentDate())
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("dd MMM yyyy")
        if value:
            self.check.setChecked(True)
            self.edit.setDate(QDate(value.year, value.month, value.day))
        self.edit.setEnabled(self.check.isChecked())
        self.check.toggled.connect(self.edit.setEnabled)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.check)
        layout.addWidget(self.edit, 1)

    def value(self) -> date | None:
        return self.edit.date().toPython() if self.check.isChecked() else None


class FormPanel(QWidget):
    """The fields of a form, usable on their own (wizard pages) or inside FormDialog."""

    def __init__(self, fields: list[Field], parent: QWidget | None = None):
        super().__init__(parent)
        self.fields = fields
        self._widgets: dict[str, QWidget] = {}
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setVerticalSpacing(12)
        form.setHorizontalSpacing(24)
        for f in fields:
            widget = self._make(f)
            self._widgets[f.key] = widget
            form.addRow(f"{f.label}{' *' if f.required else ''}", widget)

    def _make(self, f: Field) -> QWidget:
        if f.kind == "multiline":
            w = QPlainTextEdit(str(f.value or ""))
            w.setFixedHeight(84)
            w.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # short notes; avoids a stray stub
            return w
        if f.kind == "date":
            if f.optional_date:
                return _OptionalDate(f.value)
            w = QDateEdit(QDate(f.value.year, f.value.month, f.value.day) if f.value else QDate.currentDate())
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd MMM yyyy")
            return w
        if f.kind == "choice":
            w = QComboBox()
            w.setEditable(f.editable_choice)
            w.addItems(f.choices or [])
            if f.value is not None:
                idx = w.findText(str(f.value))
                if idx >= 0:
                    w.setCurrentIndex(idx)
                elif f.editable_choice:
                    w.setCurrentText(str(f.value))
            return w
        if f.kind == "bool":
            w = QCheckBox()
            w.setChecked(bool(f.value))
            return w
        if f.kind in ("file", "folder"):
            return _FileField(f.file_filter, f.value, folder=f.kind == "folder")
        if f.kind == "readonly":
            label = QLabel(str(f.value or ""))
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            return label
        w = QLineEdit(str(f.value or ""))
        w.setPlaceholderText(f.placeholder)
        if f.kind == "password":
            w.setEchoMode(QLineEdit.EchoMode.Password)
        return w

    def widget(self, key: str) -> QWidget:
        return self._widgets[key]

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in self.fields:
            w = self._widgets[f.key]
            if f.kind == "multiline":
                out[f.key] = w.toPlainText()
            elif f.kind == "date":
                out[f.key] = w.value() if f.optional_date else w.date().toPython()
            elif f.kind == "choice":
                out[f.key] = w.currentText()
            elif f.kind == "bool":
                out[f.key] = w.isChecked()
            elif f.kind in ("file", "folder"):
                out[f.key] = w.value()
            elif f.kind == "readonly":
                out[f.key] = f.value
            else:
                out[f.key] = w.text()
        return out

    def missing_required(self) -> str | None:
        values = self.values()
        for f in self.fields:
            if f.required and f.kind not in ("bool", "readonly") and not str(values[f.key] or "").strip():
                return f"{f.label} is required."
        return None


class FormDialog(QDialog):
    """Shows ``fields``; on OK validates required fields, then calls ``submit(values)``.

    ``submit`` may raise a DomainError: it is shown inside the dialog and the dialog stays open.
    """

    def __init__(self, title: str, fields: list[Field], submit: Callable[[dict[str, Any]], Any],
                 parent: QWidget | None = None, ok_text: str = "Save", intro: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self.submit, self.result_value = submit, None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        heading.setStyleSheet("font-size: 15pt;")
        layout.addWidget(heading)
        if intro:
            note = QLabel(intro)
            note.setObjectName("muted")
            note.setWordWrap(True)
            layout.addWidget(note)
        self.panel = FormPanel(fields)
        from PySide6.QtGui import QGuiApplication

        from ui.widgets.page import scrollable

        scroll = scrollable(self.panel)
        screen = QGuiApplication.primaryScreen()
        limit = int(screen.availableGeometry().height() * 0.62) if screen else 560
        scroll.setMinimumHeight(min(self.panel.sizeHint().height() + 6, limit))  # tall forms scroll, never overlap
        layout.addWidget(scroll, 1)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #ff5470;")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox()
        self.ok_button = buttons.addButton(ok_text, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def widget(self, key: str) -> QWidget:
        return self.panel.widget(key)

    def values(self) -> dict[str, Any]:
        return self.panel.values()

    def _on_ok(self) -> None:
        problem = self.panel.missing_required()
        if problem:
            self.error.setText(problem)
            return
        try:
            self.result_value = self.submit(self.values())
        except DomainError as exc:
            self.error.setText(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            show_error(self, exc)
            return
        self.accept()


def ask_form(parent: QWidget | None, title: str, fields: list[Field], submit: Callable[[dict[str, Any]], Any],
             ok_text: str = "Save", intro: str = "") -> tuple[bool, Any]:
    dialog = FormDialog(title, fields, submit, parent, ok_text, intro)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return accepted, dialog.result_value
