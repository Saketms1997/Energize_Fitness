from datetime import date, timedelta
from decimal import Decimal

import pytest

from services import membership_service, payment_service, plan_service
from services.errors import BusinessRuleError, DuplicateError, ValidationError
from services.membership_logic import end_date_for, subscription_state
from tests.factories import make_member, make_paid_membership, make_plan, make_subscription

pytestmark = pytest.mark.db


def test_end_date_is_inclusive():
    assert end_date_for(date(2026, 1, 1), 30) == date(2026, 1, 30)
    assert end_date_for(date(2026, 1, 1), 365) == date(2026, 12, 31)
    with pytest.raises(ValueError):
        end_date_for(date(2026, 1, 1), 0)


def test_subscription_state_rules():
    s, e = date(2026, 1, 1), date(2026, 1, 30)
    assert subscription_state("active", s, e, date(2026, 1, 10)) == "active"
    assert subscription_state("active", s, e, date(2026, 1, 25)) == "expiring"
    assert subscription_state("active", s, e, date(2026, 1, 31)) == "expired"
    assert subscription_state("active", s, e, date(2025, 12, 30)) == "upcoming"
    assert subscription_state("pending", s, e, date(2026, 1, 10)) == "pending"


def test_default_plans_created_once(db):
    assert plan_service.ensure_default_plans(db) == 4
    assert plan_service.ensure_default_plans(db) == 0
    assert [p.duration_days for p in plan_service.list_plans(db)] == [30, 90, 180, 365]


def test_plan_validation_and_duplicate_name(db):
    make_plan(db)
    with pytest.raises(DuplicateError):
        plan_service.create_plan(db, "monthly", 30, "500")
    with pytest.raises(ValidationError):
        plan_service.create_plan(db, "Bad", 0, "500")
    with pytest.raises(ValidationError):
        plan_service.create_plan(db, "Bad", 30, "-5")


def test_custom_duration_plan(db):
    assert plan_service.create_plan(db, "10-day pass", 10, "300").duration_days == 10


def test_new_subscription_is_pending_with_calculated_expiry(db):
    m, plan = make_member(db), make_plan(db)
    sub = make_subscription(db, m, plan, date(2026, 5, 1))
    assert sub.status == "pending" and sub.end_date == date(2026, 5, 30) and sub.amount_due == Decimal("1000.00")


def test_payment_activates_subscription(db):
    m, plan = make_member(db), make_plan(db)
    sub, _ = make_paid_membership(db, m, plan, date(2026, 5, 1))
    assert sub.status == "active"


def test_free_plan_activates_immediately(db):
    m = make_member(db)
    free = plan_service.create_plan(db, "Trial", 7, "0")
    assert make_subscription(db, m, free).status == "active"


def test_second_unpaid_membership_blocked(db):
    m, plan = make_member(db), make_plan(db)
    make_subscription(db, m, plan)
    with pytest.raises(DuplicateError):
        make_subscription(db, m, plan)


def test_inactive_plan_or_member_blocked(db):
    m, plan = make_member(db), make_plan(db)
    plan_service.update_plan(db, plan.id, is_active=False)
    with pytest.raises(BusinessRuleError):
        make_subscription(db, m, plan)


def test_renewal_starts_day_after_current_expiry(db):
    m, plan = make_member(db), make_plan(db)
    sub, _ = make_paid_membership(db, m, plan, date(2026, 5, 1))
    renewal = membership_service.renew_subscription(db, m.id, today=date(2026, 5, 20))
    assert renewal.start_date == date(2026, 5, 31) and renewal.renewed_from_id == sub.id
    assert renewal.end_date == date(2026, 6, 29) and renewal.status == "pending"


def test_renewal_after_lapse_starts_today(db):
    m, plan = make_member(db), make_plan(db)
    make_paid_membership(db, m, plan, date(2026, 1, 1))
    renewal = membership_service.renew_subscription(db, m.id, today=date(2026, 3, 15))
    assert renewal.start_date == date(2026, 3, 15)


def test_renewal_without_history_needs_a_plan(db):
    m, plan = make_member(db), make_plan(db)
    with pytest.raises(BusinessRuleError):
        membership_service.renew_subscription(db, m.id)
    assert membership_service.renew_subscription(db, m.id, plan.id).status == "pending"


def test_status_map_reflects_expiring_window(db):
    m, plan = make_member(db), make_plan(db)
    make_paid_membership(db, m, plan, date(2026, 5, 1))  # ends May 30
    states = lambda d: membership_service.status_map(db, d)[m.id].state  # noqa: E731
    assert states(date(2026, 5, 10)) == "active"
    assert states(date(2026, 5, 25)) == "expiring"
    assert states(date(2026, 6, 2)) == "expired"


def test_cancel_blocked_when_money_was_paid(db):
    m, plan = make_member(db), make_plan(db)
    sub, _ = make_paid_membership(db, m, plan)
    with pytest.raises(BusinessRuleError):
        membership_service.cancel_subscription(db, sub.id)
    unpaid = make_subscription(db, make_member(db, "Ben", "9000000002"), plan)
    assert membership_service.cancel_subscription(db, unpaid.id).status == "cancelled"


def test_list_subscriptions_filters_and_balances(db):
    m, plan = make_member(db), make_plan(db)
    sub = make_subscription(db, m, plan, date(2026, 5, 1))
    payment_service.record_confirmed_payment(db, sub.id, "400", "Cash")
    row = membership_service.list_subscriptions(db, "pending")[0]
    assert (row.paid, row.outstanding) == (Decimal("400.00"), Decimal("600.00"))
    assert membership_service.list_subscriptions(db, "active") == []
