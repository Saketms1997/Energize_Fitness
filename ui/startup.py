"""Start-up sequence: database -> schema upgrade -> sign-in -> main window."""
from __future__ import annotations

import logging

from PySide6.QtWidgets import QApplication, QMessageBox

from database.connection import dispose_engine, session_scope
from services import attendance_service, auth_service, db_setup_service, plan_service
from services.session_context import set_actor
from ui.login import LoginDialog, run_owner_setup
from ui.main_window import MainWindow
from ui.setup_wizard import SetupWizard

log = logging.getLogger("gym")


def ensure_database() -> bool:
    """Make sure PostgreSQL is configured, reachable and migrated. False = the owner gave up."""
    state = db_setup_service.inspect_state()
    while not state.ready:
        if not SetupWizard(None, state.reason).exec():
            return False
        dispose_engine()
        state = db_setup_service.inspect_state()
    if state.needs_migration:
        log.info("Upgrading the database schema")
        db_setup_service.upgrade_schema()  # additive Alembic migrations; fast
        dispose_engine()
    return True


def sign_in():
    with session_scope() as s:
        has_owner = auth_service.has_owner(s)
    if not has_owner:
        if not run_owner_setup():
            return None
    dialog = LoginDialog()
    return dialog.result if dialog.exec() else None


def prepare() -> MainWindow | None:
    if not ensure_database():
        return None
    result = sign_in()
    if result is None:
        return None
    set_actor(result.username, result.owner_id)
    with session_scope() as s:
        plan_service.ensure_default_plans(s)
        closed = attendance_service.auto_close_stale(s)
    if closed:
        log.info("Closed %s forgotten check-in(s)", closed)
    return MainWindow(background=True, username=result.username, display_name=result.display_name)
