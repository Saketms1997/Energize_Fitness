"""UI tests: real pages and dialogs against the test database (offscreen Qt)."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from PySide6.QtCore import QObject, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QMessageBox

from database.connection import ConnectionStatus, session_scope
from services import (attendance_service, auth_service, clock, expense_service, membership_service, payment_service,
                      plan_service, settings_service, trainer_service)
from ui.main_window import NAV_ITEMS, MainWindow
from ui.widgets.forms import Field, FormDialog
from ui.widgets.table import DataTable

pytestmark = pytest.mark.db


class _Emitter(QObject):
    status_changed = Signal(object)


class FakeMonitor:
    """Stands in for DbStatusMonitor so window tests control the connection state."""

    def __init__(self):
        self._emitter = _Emitter()
        self.status_changed = self._emitter.status_changed
        self.calls = 0

    def refresh(self):
        self.calls += 1


@pytest.fixture(autouse=True)
def quiet_boxes(monkeypatch):
    """A test must never block on a modal message box; record them instead."""
    shown = []
    monkeypatch.setattr("ui.common._toast", None)
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: shown.append((_n, a[1:3]))))
    monkeypatch.setattr("ui.payments.payment_dialog.open_receipt", lambda *a: shown.append(("receipt", a[1:])))
    monkeypatch.setattr("ui.members.registration_dialog.open_receipt", lambda *a: shown.append(("receipt", a[1:])))
    yield shown
    QThreadPool.globalInstance().waitForDone(5000)


@pytest.fixture
def populated(app_db):
    with session_scope() as s:
        plan_service.ensure_default_plans(s)
        plan = plan_service.list_plans(s)[0]
        from services import member_service

        m1 = member_service.register_member(s, full_name="Asha Rao", phone="9000000001")
        m2 = member_service.register_member(s, full_name="Ben Kumar", phone="9000000002")
        sub = membership_service.create_subscription(s, m1.id, plan.id)
        pay = payment_service.record_confirmed_payment(s, sub.id, sub.amount_due, "Cash")
        membership_service.create_subscription(s, m2.id, plan.id)
        attendance_service.check_in(s, m1.id)
        trainer_service.register_trainer(s, full_name="Coach Dan", phone="9333333333")
        expense_service.add_expense(s, "Rent", "500", date.today(), "Cash")
        return {"m1": m1.id, "m2": m2.id, "sub": sub.id, "pay": pay.id, "plan": plan.id}


def _window(qtbot):
    win = MainWindow(monitor=FakeMonitor())
    qtbot.addWidget(win)
    return win


# ---- window ------------------------------------------------------------------------------------

def test_every_nav_item_has_a_real_page_that_loads(qtbot, populated):
    win = _window(qtbot)
    assert set(win.nav_buttons) == set(NAV_ITEMS) and win.stack.count() == len(NAV_ITEMS)
    for name in NAV_ITEMS:
        win.show_page(name)
        assert win.stack.currentWidget().property("page_name") == name
        assert win.nav_buttons[name].isChecked()


def test_pages_load_on_an_empty_database_too(qtbot, app_db):
    win = _window(qtbot)
    for name in NAV_ITEMS:
        win.show_page(name)


def test_status_bar_reflects_connection(qtbot, app_db):
    monitor = FakeMonitor()
    win = MainWindow(monitor=monitor)
    qtbot.addWidget(win)
    monitor.status_changed.emit(ConnectionStatus(True, "Connected", "gym", "17.5", 4.0, "0003"))
    assert win.db_dot.state == "ok" and "Connected" in win.db_label.text()
    monitor.status_changed.emit(ConnectionStatus(False, "Cannot connect: refused"))
    assert win.db_dot.state == "error"


# ---- pages show real data ------------------------------------------------------------------------

def test_members_page_search_and_status_filter(qtbot, populated):
    page = _window(qtbot).pages["Members"]
    page.refresh()
    assert page.table.rowCount() == 2
    page.search.setText("ben")
    assert page.table.rowCount() == 1 and page.table.cell(0, 1) == "Ben Kumar"
    page.search.setText("")
    page.status.setCurrentIndex(page.status.findData("active"))
    assert page.table.rowCount() == 1 and page.table.cell(0, 1) == "Asha Rao"
    page.status.setCurrentIndex(page.status.findData("pending"))
    assert [page.table.cell(r, 1) for r in range(page.table.rowCount())] == ["Ben Kumar"]


def test_members_page_deactivate_toggle(qtbot, populated, monkeypatch):
    page = _window(qtbot).pages["Members"]
    page.refresh()
    monkeypatch.setattr("ui.members.members_page.confirm", lambda *a, **k: True)
    page.table.select_id(populated["m2"])
    page.toggle_active()
    assert page.table.cell(page.table.rowCount() - 1, 8) in ("No", "Yes")
    page.status.setCurrentIndex(page.status.findData("inactive"))
    assert page.table.rowCount() == 1


def test_payments_outstanding_and_collections(qtbot, populated):
    page = _window(qtbot).pages["Payments"]
    page.refresh()
    assert page.table.rowCount() == 1 and page.out_table.rowCount() == 1
    assert "1,000.00" in page.totals.text()
    assert "owe" in page.out_label.text()


def test_memberships_page_filters(qtbot, populated):
    page = _window(qtbot).pages["Memberships"]
    page.refresh()
    assert page.table.rowCount() == 2
    page.state.setCurrentIndex(page.state.findData("pending"))
    assert page.table.rowCount() == 1


def test_attendance_trainers_expenses_reports_pages(qtbot, populated):
    win = _window(qtbot)
    att = win.pages["Attendance"]
    att.refresh()
    assert att.members.table.rowCount() == 1 and "Inside now: 1" in att.members.inside.text()
    win.pages["Trainers"].refresh()
    assert win.pages["Trainers"].table.rowCount() == 1
    win.pages["Expenses"].refresh()
    assert win.pages["Expenses"].table.rowCount() == 1 and "500.00" in win.pages["Expenses"].total.text()
    reports = win.pages["Reports"]
    for i in range(reports.kind.count()):
        reports.kind.setCurrentIndex(i)  # every report builds without error
    reports.kind.setCurrentText("Monthly collection")
    assert reports.table.rowCount() == 1 and "Net collection" in reports.footer.text()


def test_attendance_check_in_out_from_the_page(qtbot, populated, monkeypatch, quiet_boxes):
    panel = _window(qtbot).pages["Attendance"].members
    panel.refresh()
    idx = panel.person.findText("Ben", Qt.MatchFlag.MatchContains)
    assert idx >= 0
    panel.person.setCurrentIndex(idx)
    monkeypatch.setattr("ui.attendance.attendance_page.confirm", lambda *a, **k: False)
    panel.check_in()   # Ben has an unpaid membership -> override prompt declined -> no visit
    assert panel.table.rowCount() == 1
    monkeypatch.setattr("ui.attendance.attendance_page.confirm", lambda *a, **k: True)
    panel.check_in()
    assert panel.table.rowCount() == 2, quiet_boxes
    panel.check_out()
    assert "inside" not in {panel.table.cell(r, 4) for r in range(panel.table.rowCount()) if panel.table.cell(r, 1) == "Ben Kumar"}


def test_dashboard_shows_numbers_and_charts(qtbot, populated):
    dash = _window(qtbot).dashboard
    dash.refresh()
    qtbot.waitUntil(lambda: dash.stats is not None, timeout=5000)
    assert dash.cards["Total members"]._value.text() == "2" and dash.cards["Monthly revenue"]._value.text().endswith("1,000.00")
    assert dash.revenue_chart._targets and dash.status_chart._targets and dash.attendance_chart._targets
    # hovering a bar must not raise
    from types import SimpleNamespace

    bar = dash.revenue_chart._targets[-1][0]
    ev = SimpleNamespace(inaxes=dash.revenue_chart.figure.axes[0], x=1, y=1, xdata=5, ydata=1,
                         )
    dash.revenue_chart._hover(ev)


# ---- dialogs -----------------------------------------------------------------------------------

def _fill_registration(dlg, name="Meera Nair", phone="9444444444"):
    dlg.details.widget("full_name").setText(name)
    dlg.details.widget("phone").setText(phone)


def test_registration_pay_later(qtbot, app_db):
    from ui.members.registration_dialog import RegistrationDialog

    with session_scope() as s:
        plan_service.ensure_default_plans(s)
    dlg = RegistrationDialog()
    qtbot.addWidget(dlg)
    _fill_registration(dlg)
    dlg._next()
    assert dlg.stack.currentIndex() == 1
    dlg._next()
    assert dlg.stack.currentIndex() == 2
    assert not dlg.confirm_btn.isEnabled()  # a displayed QR never counts as payment
    dlg._register(False)
    assert dlg.result[0] == "GYM-00001"
    with session_scope() as s:
        rows = membership_service.list_subscriptions(s)
        assert rows[0].state == "pending" and rows[0].paid == 0


def test_registration_requires_owner_verification_before_paid(qtbot, app_db):
    from ui.members.registration_dialog import RegistrationDialog

    with session_scope() as s:
        plan_service.ensure_default_plans(s)
        settings_service.set_value(s, "payment.upi_id", "gym@okbank")
    dlg = RegistrationDialog()
    qtbot.addWidget(dlg)
    _fill_registration(dlg)
    dlg._next(), dlg._next()
    assert dlg.payment.qr.pixmap() is not None and not dlg.payment.qr.pixmap().isNull()  # QR shown
    dlg.payment.reference.setText("UTR123456")
    assert not dlg.confirm_btn.isEnabled()
    dlg.payment.verified.setChecked(True)
    assert dlg.confirm_btn.isEnabled()
    dlg._register(True)
    with session_scope() as s:
        row = membership_service.list_subscriptions(s)[0]
        assert row.paid == Decimal("1000.00") and row.state in ("active", "expiring")
        assert [p.reference for p in payment_service.list_payments(s)] == ["UTR123456"]


def test_registration_without_qr_configured_says_so(qtbot, app_db):
    from ui.members.registration_dialog import RegistrationDialog

    with session_scope() as s:
        plan_service.ensure_default_plans(s)
    dlg = RegistrationDialog()
    qtbot.addWidget(dlg)
    _fill_registration(dlg)
    dlg._next(), dlg._next()
    assert "No UPI QR configured" in dlg.payment.qr.text()


def test_registration_validation_messages(qtbot, app_db):
    from ui.members.registration_dialog import RegistrationDialog

    with session_scope() as s:
        plan_service.ensure_default_plans(s)
    dlg = RegistrationDialog()
    qtbot.addWidget(dlg)
    dlg._next()
    assert "Full name is required" in dlg.error.text() and dlg.stack.currentIndex() == 0
    _fill_registration(dlg, phone="12")
    dlg._next()
    assert "7 to 15 digits" in dlg.error.text()


def test_registration_duplicate_shown_not_crashed(qtbot, populated):
    from ui.members.registration_dialog import RegistrationDialog

    dlg = RegistrationDialog()
    qtbot.addWidget(dlg)
    _fill_registration(dlg, "Asha Rao", "9000000001")
    dlg._next(), dlg._next()
    dlg._register(False)
    assert "already registered" in dlg.error.text()


def test_collect_payment_dialog_confirm_and_pending(qtbot, populated, quiet_boxes):
    from ui.payments.payment_dialog import CollectPaymentDialog

    dlg = CollectPaymentDialog(next(r.id for r in _subs() if r.member_name == "Ben Kumar"))
    qtbot.addWidget(dlg)
    assert dlg.panel.amount.text() == "1000.00" and not dlg.confirm_btn.isEnabled()
    dlg.panel.amount.setText("400")
    dlg.panel.method.setCurrentText("Cash")
    dlg.panel.verified.setChecked(True)
    dlg._save(True)
    with session_scope() as s:
        row = next(r for r in membership_service.list_subscriptions(s) if r.member_name == "Ben Kumar")
        assert row.paid == Decimal("400.00") and row.state == "pending"
    assert any(kind == "receipt" for kind, _ in quiet_boxes)


def _subs():
    with session_scope() as s:
        return membership_service.list_subscriptions(s)


def test_collect_dialog_shows_domain_errors_inline(qtbot, populated):
    from ui.payments.payment_dialog import CollectPaymentDialog

    dlg = CollectPaymentDialog(next(r.id for r in _subs() if r.member_name == "Ben Kumar"))
    qtbot.addWidget(dlg)
    dlg.panel.amount.setText("99999")
    dlg.panel.method.setCurrentText("Cash")
    dlg._save(False)
    assert "exceeds the outstanding balance" in dlg.error.text()


def test_form_dialog_required_and_domain_error(qtbot):
    calls = []

    def submit(values):
        calls.append(values)
        if values["name"] == "bad":
            from services.errors import ValidationError

            raise ValidationError("Name is not allowed.")

    dlg = FormDialog("T", [Field("name", "Name", required=True)], submit)
    qtbot.addWidget(dlg)
    dlg._on_ok()
    assert "Name is required" in dlg.error.text() and not calls
    dlg.panel.widget("name").setText("bad")
    dlg._on_ok()
    assert dlg.error.text() == "Name is not allowed." and dlg.result() == 0
    dlg.panel.widget("name").setText("good")
    dlg._on_ok()
    assert dlg.result() == 1 and calls[-1] == {"name": "good"}


# ---- login & settings -------------------------------------------------------------------------------

def test_login_dialog_accepts_good_and_locks_after_bad_attempts(qtbot, app_db):
    from ui.login import LoginDialog

    with session_scope() as s:
        auth_service.create_owner(s, "owner", "Secret123", "The Owner")
    dlg = LoginDialog()
    qtbot.addWidget(dlg)
    dlg.username.setText("owner")
    for _ in range(auth_service.MAX_FAILED_ATTEMPTS):
        dlg.password.setText("wrong")
        dlg._attempt()
    assert "locked" in dlg.message.text().lower() and dlg.result is None
    dlg.password.setText("Secret123")
    dlg._attempt()
    assert dlg.result is None and "try again" in dlg.message.text().lower()  # still locked: correct password refused


def test_login_success(qtbot, app_db):
    from ui.login import LoginDialog

    with session_scope() as s:
        auth_service.create_owner(s, "owner", "Secret123", "The Owner")
    dlg = LoginDialog()
    qtbot.addWidget(dlg)
    dlg.username.setText("owner")
    dlg.password.setText("Secret123")
    dlg._attempt()
    assert dlg.result and dlg.result.display_name == "The Owner"


def _fill_password_reset(dialog, admin_password="admin-secret", new="NewSecret123"):
    dialog.panel.widget("admin_password").setText(admin_password)
    dialog.panel.widget("new").setText(new)
    dialog.panel.widget("confirm").setText(new)


def test_forgot_password_button_opens_recovery_without_signing_in(qtbot, app_db, monkeypatch):
    from ui.login import LoginDialog, PasswordResetDialog

    dlg = LoginDialog()
    qtbot.addWidget(dlg)
    dlg.username.setText("owner")
    dlg.password.setText("old-password")

    def accept_reset(reset):
        assert reset.panel.widget("username").text() == "owner"
        reset.username = "owner"
        return 1

    monkeypatch.setattr(PasswordResetDialog, "exec", accept_reset)
    qtbot.mouseClick(dlg.forgot_button, Qt.MouseButton.LeftButton)
    assert dlg.result is None and not dlg.password.text()
    assert "Sign in with your new password" in dlg.message.text()


def test_password_recovery_runs_off_ui_thread_and_allows_login(qtbot, app_db, monkeypatch):
    from PySide6.QtCore import QThread
    from ui.login import LoginDialog, PasswordResetDialog

    with session_scope() as s:
        owner = auth_service.create_owner(s, "owner", "Secret123")
        owner.locked_until = clock.now() + auth_service.LOCKOUT
    checks = []

    def verify(password):
        checks.append((password, QThread.currentThread()))

    monkeypatch.setattr(auth_service.db_setup_service, "verify_admin_password", verify)
    reset = PasswordResetDialog(username="owner")
    qtbot.addWidget(reset)
    _fill_password_reset(reset)
    reset._submit()
    assert not reset.panel.widget("admin_password").text()
    qtbot.waitUntil(lambda: reset.result() == 1, timeout=10000)
    assert checks[0][0] == "admin-secret" and checks[0][1] != reset.thread()
    login = LoginDialog()
    qtbot.addWidget(login)
    login.username.setText("owner")
    login.password.setText("NewSecret123")
    login._attempt()
    assert login.result and login.result.ok


def test_password_recovery_bad_admin_is_visible_and_retryable(qtbot, app_db, monkeypatch):
    from ui.login import PasswordResetDialog

    with session_scope() as s:
        auth_service.create_owner(s, "owner", "Secret123")

    def reject(_password):
        raise auth_service.db_setup_service.SetupError("Administrator password is incorrect.")

    monkeypatch.setattr(auth_service.db_setup_service, "verify_admin_password", reject)
    reset = PasswordResetDialog(fixed_username="owner")
    qtbot.addWidget(reset)
    _fill_password_reset(reset)
    reset._submit()
    qtbot.waitUntil(lambda: bool(reset.error.text()), timeout=10000)
    assert "Administrator password is incorrect" in reset.error.text()
    assert reset.result() == 0 and reset.reset_button.isEnabled()
    assert not reset.panel.widget("username").isEnabled()
    assert all(not reset.panel.widget(k).text() for k in ("admin_password", "new", "confirm"))
    with session_scope() as s:
        assert auth_service.authenticate(s, "owner", "Secret123").ok


def test_password_recovery_validates_confirmation_and_cancel_clears_secrets(qtbot, app_db, monkeypatch):
    from ui.login import PasswordResetDialog

    reset = PasswordResetDialog(username="owner", fixed_username="owner")
    qtbot.addWidget(reset)
    assert not reset.panel.widget("username").isEnabled()
    _fill_password_reset(reset)
    reset.panel.widget("confirm").setText("Different123")
    reset._submit()
    assert "do not match" in reset.error.text()
    assert not reset._busy
    reset.reject()
    assert all(not reset.panel.widget(k).text() for k in ("admin_password", "new", "confirm"))


def test_password_recovery_cannot_be_cancelled_mid_reset(qtbot, app_db, monkeypatch):
    from ui import login

    pending = []
    monkeypatch.setattr(login, "run_async", lambda fn, done, failed: pending.append((fn, done, failed)))
    reset = login.PasswordResetDialog(username="owner")
    qtbot.addWidget(reset)
    reset.show()
    _fill_password_reset(reset)
    reset._submit()
    reset.reject()
    assert reset.isVisible() and reset._busy and not reset.reset_button.isEnabled()
    assert len(pending) == 1
    pending[0][2](auth_service.db_setup_service.SetupError("Not submitted"))
    assert not reset._busy
    reset.reject()
    assert not reset.isVisible()


@pytest.mark.parametrize("ok", [True, False])
def test_cloud_connection_test_uses_unsaved_form_values(qtbot, app_db, monkeypatch, ok):
    from services import mongodb_service
    from ui.settings.settings_page import cloud_tab

    calls = []

    def test_connection(uri=None, database="gym_management"):
        calls.append((uri, database))
        return mongodb_service.ConnectionTest(ok, "Connection result")

    monkeypatch.setattr(mongodb_service, "test_connection", test_connection)
    tab = cloud_tab()
    qtbot.addWidget(tab)
    tab.refresh()
    tab.panel.widget("uri").setText("mongodb://new.example/gym")
    tab.panel.widget("database").setText("unsaved_database")
    tab._run_test()
    qtbot.waitUntil(lambda: tab.test_btn.isEnabled(), timeout=5000)
    assert calls == [("mongodb://new.example/gym", "unsaved_database")]
    assert tab.error.text() == ("" if ok else "Connection result")


def test_settings_gym_profile_saves_and_validates(qtbot, app_db):
    from ui.settings.settings_page import SettingsPage

    page = SettingsPage()
    qtbot.addWidget(page)
    tab = page.tab_defs[0][1]
    tab.refresh()
    tab.panel.widget("name").setText("Iron House")
    tab.panel.widget("member_code_prefix").setText("ih")
    assert tab.save()
    with session_scope() as s:
        assert settings_service.get(s, "gym.name") == "Iron House"
        assert settings_service.get(s, "gym.member_code_prefix") == "IH"
    tab.panel.widget("member_code_prefix").setText("bad!")
    assert not tab.save() and "letters and digits" in tab.error.text()


def test_settings_every_tab_loads(qtbot, app_db):
    from ui.settings.settings_page import SettingsPage

    page = SettingsPage()
    qtbot.addWidget(page)
    for i in range(page.tabs.count()):
        page.tabs.setCurrentIndex(i)
        page.refresh()


def test_settings_plans_tab_lists_plans(qtbot, app_db):
    from ui.settings.settings_page import PlansTab

    with session_scope() as s:
        plan_service.ensure_default_plans(s)
    tab = PlansTab()
    qtbot.addWidget(tab)
    tab.refresh()
    assert tab.table.rowCount() == 4


# ---- widgets --------------------------------------------------------------------------------------

def test_data_table_sorts_numbers_and_keeps_ids(qtbot):
    table = DataTable(["Name", "Amount"])
    qtbot.addWidget(table)
    table.set_rows([("a", ["x", Decimal("9.00")]), ("b", ["y", Decimal("100.00")]), ("c", ["z", Decimal("25.50")])])
    table.sortItems(1)
    assert [table.cell(r, 1) for r in range(3)] == ["9.00", "25.50", "100.00"]
    table.selectRow(2)
    assert table.selected_id() == "b"
    table.set_rows([("a", ["x", Decimal("9.00")]), ("b", ["y", Decimal("1.00")])])
    assert table.selected_id() == "b"  # selection survives a refresh
