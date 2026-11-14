from datetime import date
from decimal import Decimal

import pytest

from services import (attendance_service, clock, dashboard_service, expense_service, payment_service,
                      report_service, settings_service, trainer_service)
from services.errors import DuplicateError, NotFoundError, ValidationError
from tests.factories import make_member, make_paid_membership, make_plan, make_subscription

pytestmark = pytest.mark.db


# ---- trainers ----------------------------------------------------------------------------

def test_trainer_crud_and_duplicates(db):
    t = trainer_service.register_trainer(db, full_name="Dan", phone="9333333333", specialization="Yoga")
    with pytest.raises(DuplicateError):
        trainer_service.register_trainer(db, full_name="dan", phone="9333333333")
    trainer_service.update_trainer(db, t.id, specialization="CrossFit")
    trainer_service.set_trainer_active(db, t.id, False)
    assert trainer_service.list_trainers(db, active=True) == []
    assert trainer_service.list_trainers(db, "cross")[0].specialization == "CrossFit"


# ---- expenses ----------------------------------------------------------------------------

def test_expense_validation_and_monthly_report(db):
    expense_service.add_expense(db, "rent", "20000", date(2026, 3, 1), "Cash")
    expense_service.add_expense(db, "Electricity", "3500.50", date(2026, 3, 15), "UPI", "March bill")
    expense_service.add_expense(db, "Rent", "99", date(2026, 4, 1), "Cash")  # other month
    with pytest.raises(ValidationError):
        expense_service.add_expense(db, "Rent", "0", date(2026, 3, 1), "Cash")
    with pytest.raises(ValidationError):
        expense_service.add_expense(db, "Rent", "5", date(2026, 3, 1), "Gold")
    report = expense_service.monthly_report(db, 2026, 3)
    assert report.total == Decimal("23500.50")
    assert list(report.by_category) == ["Rent", "Electricity"] and len(report.expenses) == 2


def test_expense_edit_and_soft_delete(db):
    e = expense_service.add_expense(db, "Rent", "100", date(2026, 3, 1), "Cash")
    expense_service.update_expense(db, e.id, amount="150")
    assert e.amount == Decimal("150.00")
    expense_service.delete_expense(db, e.id)
    assert expense_service.monthly_report(db, 2026, 3).total == 0
    with pytest.raises(NotFoundError):
        expense_service.get_expense(db, e.id)


# ---- dashboard ---------------------------------------------------------------------------

def test_dashboard_numbers(db):
    today = clock.today()
    plan = make_plan(db)
    active = make_member(db, "Active One", "9000000001")
    expiring = make_member(db, "Expiring Two", "9000000002")
    expired = make_member(db, "Expired Three", "9000000003")
    make_member(db, "Nothing Four", "9000000004")
    owing = make_member(db, "Owing Five", "9000000005")
    make_paid_membership(db, active, plan, today)
    make_paid_membership(db, expiring, plan, today - __import__("datetime").timedelta(days=25))
    make_paid_membership(db, expired, plan, today - __import__("datetime").timedelta(days=60))
    sub = make_subscription(db, owing, plan, today)
    payment_service.record_confirmed_payment(db, sub.id, "300", "Cash")
    attendance_service.check_in(db, active.id)
    expense_service.add_expense(db, "Rent", "1200", today, "Cash")

    s = dashboard_service.get_stats(db, today)
    assert (s.total_members, s.active_members, s.expiring_soon, s.expired_memberships) == (5, 2, 1, 1)
    assert s.todays_attendance == 1
    assert s.outstanding_payments == Decimal("700.00")
    # revenue is month-to-date; payments for backdated memberships were still *received* today
    assert s.monthly_revenue == Decimal("3300.00") and s.monthly_expenses == Decimal("1200.00")
    assert s.net_revenue == Decimal("2100.00")
    assert s.status_distribution["none"] == 1 and s.status_distribution["pending"] == 1
    assert len(s.revenue_by_month) == 6 and s.revenue_by_month[-1][1] == Decimal("3300.00")
    assert len(s.attendance_by_day) == 14 and s.attendance_by_day[-1] == (today, 1)


def test_dashboard_refunds_reduce_revenue_and_renewal_rate(db):
    from datetime import timedelta

    today = clock.today()
    plan = make_plan(db)
    m = make_member(db)
    sub, pay = make_paid_membership(db, m, plan, today - timedelta(days=45))  # ended 16 days ago
    from services import membership_service

    renewal = membership_service.renew_subscription(db, m.id, today=today)
    payment_service.record_confirmed_payment(db, renewal.id, "1000", "Cash")
    payment_service.refund_payment(db, pay.id, "100", "goodwill")
    s = dashboard_service.get_stats(db, today)
    assert s.monthly_revenue == Decimal("1900.00")
    assert s.renewal_rate_90d == 1.0 and s.renewals_this_month == 1


def test_dashboard_empty_database(db):
    s = dashboard_service.get_stats(db)
    assert s.total_members == 0 and s.renewal_rate_90d is None and s.net_revenue == 0


# ---- reports -----------------------------------------------------------------------------

def test_reports_build_and_export(db, tmp_path):
    today = clock.today()
    m, plan = make_member(db), make_plan(db)
    make_paid_membership(db, m, plan, today)
    attendance_service.check_in(db, m.id)
    expense_service.add_expense(db, "Rent", "500", today, "Cash")
    tables = [
        report_service.daily_collection(db, today),
        report_service.monthly_collection(db, today.year, today.month),
        report_service.attendance_daily(db, today),
        report_service.attendance_monthly(db, today.year, today.month),
        report_service.expenses_monthly(db, today.year, today.month),
        report_service.members_list(db),
        report_service.outstanding_payments(db),
        report_service.expiring_memberships(db),
        report_service.trainer_attendance_monthly(db, today.year, today.month),
    ]
    assert tables[0].rows[0][-1] == Decimal("1000.00")
    assert ("Net collection", "1,000.00") in tables[0].footer
    for i, table in enumerate(tables):
        xlsx = report_service.export_xlsx(table, tmp_path / f"r{i}.xlsx")
        pdf = report_service.export_pdf(table, tmp_path / f"r{i}.pdf", "Test Gym")
        assert xlsx.stat().st_size > 0 and pdf.read_bytes().startswith(b"%PDF")


def test_settings_defaults_and_overrides(db):
    assert settings_service.get(db, "gym.currency") == "INR"
    settings_service.set_value(db, "gym.name", "Iron House")
    settings_service.set_value(db, "membership.expiry_alert_days", 10)
    assert settings_service.get(db, "gym.name") == "Iron House"
    assert settings_service.get(db, "membership.expiry_alert_days") == 10
    assert settings_service.all_settings(db)["gym.name"] == "Iron House"
