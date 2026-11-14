from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTabWidget, QVBoxLayout,
                               QWidget)

from config.settings import get_settings
from database.connection import check_connection, dispose_engine, session_scope
from services import (auth_service, mongodb_service, plan_service, settings_admin_service as admin,
                      settings_service as ss, whatsapp_service)
from services.errors import DomainError, ValidationError
from services.session_context import get_owner_id
from ui.async_utils import run_async
from ui.common import money, notify, show_error
from ui.widgets.forms import Field, FormPanel, ask_form
from ui.widgets.page import Page, button_row, make_button, scrollable
from ui.widgets.table import DataTable

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp)"


class _FormTab(QWidget):
    """A saved form: ``load(session) -> fields`` builds it, ``save(session, values)`` stores it."""

    def __init__(self, load: Callable, save: Callable, intro: str = "", extra_buttons: list | None = None):
        super().__init__()
        self._load, self._save = load, save
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(20, 18, 20, 18)
        if intro:
            note = QLabel(intro)
            note.setObjectName("muted")
            note.setWordWrap(True)
            self.layout_.addWidget(note)
        self.holder = QVBoxLayout()
        self.layout_.addLayout(self.holder)
        self.row_holder = QHBoxLayout()
        self.row_holder.addStretch(1)
        self.holder.addLayout(self.row_holder)
        self.error = QLabel("")
        self.error.setStyleSheet("color:#ff5470;")
        self.error.setWordWrap(True)
        self.layout_.addWidget(self.error)
        self.save_btn = QPushButton("Save settings")
        self.save_btn.clicked.connect(self.save)
        self.row = QHBoxLayout()
        self.row.addWidget(self.save_btn)
        for b in extra_buttons or []:
            self.row.addWidget(b)
        self.row.addStretch(1)
        self.layout_.addLayout(self.row)
        self.layout_.addStretch(1)
        self.panel: FormPanel | None = None

    def refresh(self) -> None:
        with session_scope() as s:
            fields = self._load(s)
        if self.panel is not None:
            self.panel.setParent(None)
        self.panel = FormPanel(fields)
        self.panel.setMaximumWidth(820)  # a form stretched across a wide window is hard to read
        self.row_holder.insertWidget(0, self.panel, 100)  # fills up to 820px; the spacer takes the rest
        self.error.setText("")

    def save(self) -> bool:
        problem = self.panel.missing_required()
        if problem:
            self.error.setText(problem)
            return False
        try:
            with session_scope() as s:
                self._save(s, self.panel.values())
        except DomainError as exc:
            self.error.setText(str(exc))
            return False
        except Exception as exc:  # noqa: BLE001
            show_error(self, exc)
            return False
        self.error.setText("")
        notify("Settings saved.")
        self.refresh()
        return True


def gym_tab() -> _FormTab:
    def load(s):
        g = lambda k: ss.get(s, k)  # noqa: E731
        return [Field("name", "Gym name", required=True, value=g("gym.name")),
                Field("address", "Address", "multiline", value=g("gym.address")),
                Field("phone", "Contact number", value=g("gym.phone")),
                Field("email", "Email", value=g("gym.email")),
                Field("logo", "Gym logo", "file", file_filter=IMAGE_FILTER,
                      value=None),
                Field("currency", "Currency", required=True, value=g("gym.currency")),
                Field("member_code_prefix", "Member ID prefix", required=True, value=g("gym.member_code_prefix")),
                Field("country_code", "WhatsApp country code", required=True, value=g("gym.country_code")),
                Field("require_gender", "Require gender when registering", "bool", value=g("gym.require_gender")),
                Field("expiry_alert_days", "Expiry alert (days before)", required=True,
                      value=g("membership.expiry_alert_days"))]

    return _FormTab(load, admin.save_gym_profile, "Shown on receipts and reports.")


def payments_tab() -> _FormTab:
    def load(s):
        return [Field("methods", "Payment methods", required=True, value=", ".join(ss.get(s, "payment.methods")),
                      placeholder="Cash, UPI, Bank transfer"),
                Field("categories", "Expense categories", required=True,
                      value=", ".join(ss.get(s, "expense.categories"))),
                Field("upi_id", "UPI ID", value=ss.get(s, "payment.upi_id"), placeholder="gym@okbank"),
                Field("payee", "Payee name", value=ss.get(s, "payment.upi_payee_name")),
                Field("qr_image", "UPI QR image (optional)", "file", file_filter=IMAGE_FILTER)]

    return _FormTab(load, admin.save_payment_settings,
                    "At registration the member sees your QR. If you upload a QR image it is shown as is; otherwise a "
                    "QR is generated from your UPI ID with the amount filled in. Payments are only recorded when you "
                    "confirm them.")


def backup_tab() -> _FormTab:
    def load(s):
        formats = ss.get(s, "local.backup.formats")
        return [Field("folder", "Backup folder", "folder", value=ss.get(s, "local.backup.dir"),
                      placeholder="(default: app data folder)"),
                Field("schedule", "Automatic daily backup", "bool", value=ss.get(s, "local.backup.schedule_enabled")),
                Field("time", "Backup time (HH:MM)", required=True, value=ss.get(s, "local.backup.time")),
                Field("retention", "Keep last (per format)", required=True, value=ss.get(s, "local.backup.retention")),
                Field("fmt_backup", "Create .backup (restorable)", "bool", value="backup" in formats),
                Field("fmt_sql", "Create .sql (restorable)", "bool", value="sql" in formats),
                Field("fmt_mdb", "Create .mdb export (Access, not restorable)", "bool", value="mdb" in formats),
                Field("pg_bin", "PostgreSQL bin folder (optional)", "folder", value=ss.get(s, "local.pg_bin_dir"))]

    return _FormTab(load, admin.save_backup_settings,
                    "The app must be running at the scheduled time; a missed backup runs at the next start.")


class _SecretTab(_FormTab):
    """Settings plus one secret kept in Windows Credential Manager (never in the database)."""

    def __init__(self, load, save, secret_field: str, store_secret: Callable[[str], None], intro: str,
                 test: Callable | None = None):
        buttons = []
        self._test = test
        if test:
            self.test_btn = make_button("Test connection", self._run_test)
            buttons.append(self.test_btn)
        super().__init__(load, save, intro, buttons)
        self.secret_field, self.store_secret = secret_field, store_secret

    def save(self) -> bool:
        secret = self.panel.values().get(self.secret_field, "").strip()
        if secret:
            try:
                self.store_secret(secret)  # validates, then writes to Credential Manager
            except DomainError as exc:
                self.error.setText(str(exc))
                return False
        return super().save()

    def _run_test(self) -> None:
        self.test_btn.setEnabled(False)
        self.error.setText("")
        values = self.panel.values()

        def done(result):
            self.test_btn.setEnabled(True)
            ok, msg = result
            self.error.setText("" if ok else msg)
            notify(msg, "ok" if ok else "error")

        run_async(lambda: self._test(values), done,
                  lambda exc: (self.test_btn.setEnabled(True), notify(str(exc), "error")))


def cloud_tab() -> _SecretTab:
    def load(s):
        configured = bool(mongodb_service.get_uri())
        return [Field("uri", "Atlas connection string", "password",
                      placeholder="Saved - leave blank to keep" if configured else "mongodb+srv://user:password@cluster.../"),
                Field("database", "Database name", required=True, value=ss.get(s, "local.mongodb.database")),
                Field("auto_minutes", "Automatic sync every (minutes, 0 = off)",
                      value=ss.get(s, "local.sync.auto_minutes"))]

    def test(v):
        database = v["database"].strip()
        if not database:
            raise ValidationError("Database name is required.")
        result = mongodb_service.test_connection(uri=v["uri"].strip() or None, database=database)
        return result.ok, result.message

    def save(s, v):
        admin.save_cloud_settings(s, v)

    return _SecretTab(load, save, "uri", mongodb_service.set_uri,
                      "Your Atlas string is stored in Windows Credential Manager, not in the database or any file. "
                      "TLS is always used.", test)


def whatsapp_tab() -> _SecretTab:
    def load(s):
        configured = bool(whatsapp_service.get_access_token())
        return [Field("enabled", "Send WhatsApp messages", "bool", value=ss.get(s, "whatsapp.enabled")),
                Field("phone_number_id", "Phone number ID", value=ss.get(s, "whatsapp.phone_number_id")),
                Field("token", "Access token", "password",
                      placeholder="Saved - leave blank to keep" if configured else "Permanent token from Meta"),
                Field("api_version", "API version", required=True, value=ss.get(s, "whatsapp.api_version")),
                Field("language", "Default template language", required=True, value=ss.get(s, "whatsapp.language")),
                Field("reminder_days", "Expiry reminders (days before)", value=", ".join(map(str, ss.get(s, "whatsapp.reminder_days")))),
                Field("renewal_days", "Renewal reminders (days after expiry)",
                      value=", ".join(map(str, ss.get(s, "whatsapp.renewal_reminder_days_after"))))]

    def test(_v):
        with session_scope() as s:
            return True, "Connected to " + whatsapp_service.test_connection(s)

    return _SecretTab(load, admin.save_whatsapp_settings, "token", whatsapp_service.set_access_token,
                      "Uses Meta's official WhatsApp Cloud API with approved templates (edit them on the WhatsApp page). "
                      "The token is stored in Windows Credential Manager.", test)


class PlansTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        self.table = DataTable(["Plan", "Duration (days)", "Price", "Active"], stretch=0)
        layout.addWidget(self.table, 1)
        layout.addLayout(button_row(make_button("Add plan", self.add, "primary"), make_button("Edit", self.edit),
                                    make_button("Activate / deactivate", self.toggle)))
        self._rows: dict = {}

    def refresh(self) -> None:
        with session_scope() as s:
            self._rows = {p.id: (p.name, p.duration_days, p.price, p.is_active) for p in plan_service.list_plans(s)}
        self.table.set_rows([(pid, list(v)) for pid, v in self._rows.items()])

    def _fields(self, v=None) -> list[Field]:
        return [Field("name", "Plan name", required=True, value=v[0] if v else ""),
                Field("duration_days", "Duration (days)", required=True, value=v[1] if v else "30",
                      placeholder="30, 90, 180, 365 or custom"),
                Field("price", "Price", required=True, value=f"{v[2]:.2f}" if v else "")]

    def add(self) -> None:
        def submit(v):
            with session_scope() as s:
                plan_service.create_plan(s, v["name"], v["duration_days"], v["price"])

        if ask_form(self, "Add membership plan", self._fields(), submit)[0]:
            notify("Plan added.")
            self.refresh()

    def edit(self) -> None:
        pid = self.table.selected_id()
        if pid is None:
            return notify("Select a plan first.", "info")

        def submit(v):
            with session_scope() as s:
                plan_service.update_plan(s, pid, name=v["name"], duration_days=v["duration_days"], price=v["price"])

        if ask_form(self, "Edit plan", self._fields(self._rows[pid]), submit,
                    intro="Changes apply to new memberships only; existing ones keep their price and dates.")[0]:
            notify("Plan updated.")
            self.refresh()

    def toggle(self) -> None:
        pid = self.table.selected_id()
        if pid is None:
            return notify("Select a plan first.", "info")
        with session_scope() as s:
            plan_service.update_plan(s, pid, is_active=not self._rows[pid][3])
        self.refresh()


class DatabaseTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.info)
        note = QLabel("The database passwords are stored in Windows Credential Manager. To change the server or "
                      "port, edit GYM_DB_HOST / GYM_DB_PORT in the .env file in the app data folder and restart, "
                      "then use 'Set up / repair database'.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addLayout(button_row(make_button("Re-check", self.refresh),
                                    make_button("Set up / repair database...", self.repair),
                                    make_button("Open app data folder", lambda: os.startfile(get_settings().data_dir))))
        layout.addStretch(1)

    def refresh(self) -> None:
        s, st = get_settings(), check_connection()
        self.info.setText(
            f"<b>{'Connected' if st.ok else 'Not connected'}</b> - {st.message}<br>"
            f"Server: {s.db_host}:{s.db_port}   Database: {s.db_name}   App user: {s.db_app_user}<br>"
            f"PostgreSQL {st.server_version or '-'}   Schema revision: {st.schema_revision or '-'}   "
            f"Latency: {'-' if st.latency_ms is None else f'{st.latency_ms:.0f} ms'}<br>"
            f"Data folder: {s.data_dir}<br>Logs: {s.log_dir}")

    def repair(self) -> None:
        from ui.setup_wizard import SetupWizard

        if SetupWizard(self).exec():
            dispose_engine()
            notify("Database set up.")
        self.refresh()


class AccountTab(_FormTab):
    def __init__(self):
        def load(_s):
            return [Field("current", "Current password", "password", True),
                    Field("new", "New password", "password", True, placeholder="At least 8 characters, letters and numbers"),
                    Field("confirm", "Repeat new password", "password", True)]

        def save(s, v):
            if v["new"] != v["confirm"]:
                raise ValidationError("The new passwords do not match.", "Repeat new password")
            owner_id = get_owner_id()
            if owner_id is None:
                raise ValidationError("No signed-in owner.")
            auth_service.change_password(s, owner_id, v["current"], v["new"])

        super().__init__(load, save, "Passwords are stored as Argon2 hashes. After 5 wrong attempts the account locks for 5 minutes.")
        self.save_btn.setText("Change password")


class SettingsPage(Page):
    def __init__(self, parent=None):
        super().__init__("Settings", "Gym profile, plans, payments, backups, cloud sync and WhatsApp.", parent)
        self.tabs = QTabWidget()
        self.tab_defs = [("Gym profile", gym_tab()), ("Membership plans", PlansTab()),
                         ("Payments & UPI", payments_tab()), ("Database", DatabaseTab()),
                         ("Backup", backup_tab()), ("Cloud (MongoDB)", cloud_tab()),
                         ("WhatsApp", whatsapp_tab()), ("Account", AccountTab())]
        for title, tab in self.tab_defs:
            # '&&' = a literal ampersand (a single '&' would become a keyboard mnemonic and vanish);
            # each tab scrolls instead of squeezing its rows together on a short window.
            self.tabs.addTab(scrollable(tab), title.replace("&", "&&"))
        self.root.addWidget(self.tabs, 1)
        self.tabs.currentChanged.connect(lambda _: self.refresh())

    def refresh(self) -> None:
        self.tab_defs[self.tabs.currentIndex()][1].refresh()
