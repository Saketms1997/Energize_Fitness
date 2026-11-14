from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from config.settings import get_settings
from services import db_setup_service
from ui.async_utils import run_async


class SetupWizard(QDialog):
    """First-run database setup: no terminal needed."""

    def __init__(self, parent: QWidget | None = None, reason: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Set up the database")
        self.setMinimumWidth(520)
        s = get_settings()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        title = QLabel("Set up the local database")
        title.setObjectName("pageTitle")
        title.setStyleSheet("font-size: 16pt;")
        layout.addWidget(title)
        text = QLabel(
            (f"<b>{reason}</b><br><br>" if reason else "") +
            "The gym data is kept in PostgreSQL on this PC. Enter the password of the PostgreSQL "
            "<b>postgres</b> administrator (chosen when PostgreSQL was installed). It is used once to create "
            "a limited database user and is <b>not saved</b>.")
        text.setWordWrap(True)
        layout.addWidget(text)
        form = QFormLayout()
        self.host = QLineEdit(s.db_host)
        self.port = QLineEdit(str(s.db_port))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("PostgreSQL 'postgres' password")
        form.addRow("Server", self.host)
        form.addRow("Port", self.port)
        form.addRow("Password", self.password)
        layout.addLayout(form)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color:#ff5470;")
        layout.addWidget(self.message)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        self.cancel = QPushButton("Quit" if reason else "Cancel")
        self.cancel.setObjectName("secondary")
        self.go = QPushButton("Set up database")
        self.go.setDefault(True)
        row.addStretch(1)
        row.addWidget(self.cancel)
        row.addWidget(self.go)
        layout.addLayout(row)
        self.cancel.clicked.connect(self.reject)
        self.go.clicked.connect(self._start)
        self.password.returnPressed.connect(self._start)

    def _start(self) -> None:
        try:
            port = int(self.port.text())
        except ValueError:
            self.message.setText("Port must be a number (PostgreSQL's default is 5432).")
            return
        self.message.setText("")
        self.go.setEnabled(False)
        self.progress.show()
        password, host = self.password.text(), self.host.text().strip() or "localhost"
        run_async(lambda: db_setup_service.provision(password, host, port), self._done, self._failed)

    def _done(self, _result) -> None:
        self.progress.hide()
        self.accept()

    def _failed(self, exc: BaseException) -> None:
        self.progress.hide()
        self.go.setEnabled(True)
        self.message.setText(str(exc) if isinstance(exc, db_setup_service.SetupError) else
                             f"Setup failed: {type(exc).__name__}: {exc}")
