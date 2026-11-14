from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLineEdit, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from config.settings import get_settings
from database.connection import session_scope
from services import auth_service, settings_service
from services.errors import DomainError
from ui.async_utils import run_async
from ui.common import show_error
from ui.widgets.forms import Field, FormDialog, FormPanel


class PasswordResetDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, username: str = "", fixed_username: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Reset owner password")
        self.setMinimumWidth(540)
        self._busy = False
        self.username = ""
        s = get_settings()
        layout = QVBoxLayout(self)
        note = QLabel(
            "Enter the PostgreSQL 'postgres' administrator password chosen when PostgreSQL was installed "
            "(not your gym login password). It is checked against the configured server and is not saved. "
            "Your gym records will not be deleted."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        server = QLabel(f"Server: {s.db_host}:{s.db_port}   Database: {s.db_name}")
        server.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(server)
        self.panel = FormPanel([
            Field("username", "Owner username", required=True,
                  value=fixed_username if fixed_username is not None else username),
            Field("admin_password", "PostgreSQL administrator password", "password", True),
            Field("new", "New login password", "password", True,
                  placeholder="At least 8 characters, letters and numbers"),
            Field("confirm", "Repeat new password", "password", True),
        ])
        self.panel.widget("username").setEnabled(fixed_username is None)
        layout.addWidget(self.panel)
        self.error = QLabel("")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color:#ff5470;")
        layout.addWidget(self.error)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.buttons = QDialogButtonBox()
        self.reset_button = self.buttons.addButton("Reset password", QDialogButtonBox.ButtonRole.AcceptRole)
        self.reset_button.setDefault(True)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._submit)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _clear_secrets(self) -> None:
        for key in ("admin_password", "new", "confirm"):
            self.panel.widget(key).clear()

    def _submit(self) -> None:
        if self._busy:
            return
        problem = self.panel.missing_required()
        if problem:
            self.error.setText(problem)
            return
        values = self.panel.values()
        if values["new"] != values["confirm"]:
            self.error.setText("The new passwords do not match.")
            return
        self.username = values["username"].strip().lower()
        try:
            auth_service.check_password_strength(values["new"], self.username)
        except DomainError as exc:
            self.error.setText(str(exc))
            return
        self.error.setText("")
        self._busy = True
        self.panel.setEnabled(False)
        self.buttons.setEnabled(False)
        self.progress.show()
        self._clear_secrets()

        def reset() -> None:
            with session_scope() as session:
                auth_service.reset_password(session, self.username, values["admin_password"], values["new"])

        run_async(reset, self._done, self._failed)

    def _done(self, _: object) -> None:
        self._busy = False
        self.progress.hide()
        self.accept()

    def _failed(self, exc: BaseException) -> None:
        self._busy = False
        self.panel.setEnabled(True)
        self.buttons.setEnabled(True)
        self.progress.hide()
        self._clear_secrets()
        if isinstance(exc, DomainError):
            self.error.setText(str(exc))
        else:
            show_error(self, exc, "Password reset failed")
            self.error.setText("Password reset failed. See the log file for details.")

    def reject(self) -> None:
        if not self._busy:
            self._clear_secrets()
            super().reject()


class LoginDialog(QDialog):
    """Sign-in. With ``fixed_username`` it is the idle-lock screen for the current owner."""

    def __init__(self, parent: QWidget | None = None, fixed_username: str | None = None, locked: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Sign in")
        self.setMinimumWidth(380)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        with session_scope() as s:
            gym = settings_service.get(s, "gym.name")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(10)
        brand = QLabel(gym)
        brand.setObjectName("pageTitle")
        brand.setStyleSheet("font-size: 18pt; color: #ff6a3d;")
        layout.addWidget(brand)
        sub = QLabel("Locked after inactivity. Enter your password to continue." if locked else "Sign in to continue")
        sub.setObjectName("pageSubtitle")
        layout.addWidget(sub)
        self.username = QLineEdit(fixed_username or "")
        self.username.setPlaceholderText("Username")
        self.username.setEnabled(fixed_username is None)
        self.password = QLineEdit()
        self.password.setPlaceholderText("Password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.username)
        layout.addWidget(self.password)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color:#ff5470;")
        layout.addWidget(self.message)
        self.button = QPushButton("Sign in")
        self.button.setDefault(True)
        self.button.clicked.connect(self._attempt)
        layout.addWidget(self.button)
        self.forgot_button = QPushButton("Forgot password?")
        self.forgot_button.setObjectName("secondary")
        self.forgot_button.setAutoDefault(False)
        self.forgot_button.clicked.connect(self._forgot_password)
        layout.addWidget(self.forgot_button)
        self.password.returnPressed.connect(self._attempt)
        self.username.returnPressed.connect(self.password.setFocus)
        (self.password if fixed_username else self.username).setFocus()
        self.result = None

    def _attempt(self) -> None:
        with session_scope() as s:  # commits the failed-attempt counter too
            result = auth_service.authenticate(s, self.username.text(), self.password.text())
        if result.ok:
            self.result = result
            self.accept()
            return
        self.message.setText(result.message)
        self.password.clear()
        self.password.setFocus()

    def _forgot_password(self) -> None:
        fixed_username = self.username.text() if not self.username.isEnabled() else None
        dialog = PasswordResetDialog(self, self.username.text(), fixed_username)
        if dialog.exec():
            self.username.setText(dialog.username)
            self.password.clear()
            self.message.setText("Password reset. Sign in with your new password.")
            self.password.setFocus()


def run_owner_setup(parent: QWidget | None = None) -> bool:
    """First run: create the owner account and name the gym."""
    def submit(v: dict) -> None:
        if v["password"] != v["confirm"]:
            raise DomainError("The passwords do not match.")
        with session_scope() as s:
            auth_service.create_owner(s, v["username"], v["password"], v["display_name"])
            settings_service.set_value(s, "gym.name", v["gym_name"].strip())

    fields = [Field("gym_name", "Gym name", required=True, value="My Gym"),
              Field("display_name", "Your name", required=True),
              Field("username", "Username", required=True),
              Field("password", "Password", "password", True, placeholder="At least 8 characters, letters and numbers"),
              Field("confirm", "Repeat password", "password", True)]
    dialog = FormDialog("Welcome - create the owner account", fields, submit, parent, "Create account",
                        "This account protects your member and payment data. Keep the password safe.")
    return bool(dialog.exec())
