from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from database.models import Member, Payment, Subscription, WhatsAppLog
from services import registration_service, settings_service
from services.errors import BusinessRuleError, DuplicateError, ValidationError
from tests.factories import make_plan

pytestmark = pytest.mark.db

FIELDS = {"full_name": "Meera Nair", "phone": "9444444444"}


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_verified_payment_saves_member_membership_and_payment(db):
    plan = make_plan(db)
    result = registration_service.register_with_membership(
        db, FIELDS, plan.id, date(2026, 6, 1), payment={"verified": True, "method": "UPI", "reference": "UTR9"})
    assert result.member.member_code == "GYM-00001"
    assert result.subscription.status == "active" and result.subscription.end_date == date(2026, 6, 30)
    assert result.payment.status == "confirmed" and result.payment.amount == Decimal("1000.00")


def test_unverified_payment_flag_is_refused_and_nothing_saved(db):
    plan = make_plan(db)
    with pytest.raises(BusinessRuleError):
        registration_service.register_with_membership(
            db, FIELDS, plan.id, payment={"verified": False, "method": "UPI", "reference": "x"})
    assert _count(db, Member) == 0 and _count(db, Payment) == 0


def test_pay_later_leaves_membership_pending(db):
    result = registration_service.register_with_membership(db, FIELDS, make_plan(db).id)
    assert result.payment is None and result.subscription.status == "pending"


def test_failure_midway_leaves_no_half_registered_member(db):
    plan = make_plan(db)
    with pytest.raises(ValidationError):  # UPI without a reference fails AFTER member+subscription exist
        registration_service.register_with_membership(
            db, FIELDS, plan.id, payment={"verified": True, "method": "UPI"})
    db.rollback()
    assert _count(db, Member) == 0 and _count(db, Subscription) == 0


def test_duplicate_member_rejected(db):
    plan = make_plan(db)
    registration_service.register_with_membership(db, FIELDS, plan.id)
    with pytest.raises(DuplicateError):
        registration_service.register_with_membership(db, FIELDS, plan.id)


def test_whatsapp_welcome_queued_only_after_confirmed_payment(db):
    settings_service.set_value(db, "whatsapp.enabled", True)
    plan = make_plan(db)
    registration_service.register_with_membership(db, FIELDS, plan.id)  # pay later -> no message
    assert _count(db, WhatsAppLog) == 0
    registration_service.register_with_membership(
        db, {"full_name": "Ravi", "phone": "9555555555"}, plan.id, payment={"verified": True, "method": "Cash"})
    log = db.scalars(select(WhatsAppLog)).one()
    assert log.kind == "registration" and log.status == "queued" and log.to_phone == "919555555555"
    preview = log.payload["preview"]
    assert "Ravi" in preview and "Monthly" in preview and "1,000.00" in preview


def test_no_message_when_whatsapp_disabled(db):
    registration_service.register_with_membership(
        db, FIELDS, make_plan(db).id, payment={"verified": True, "method": "Cash"})
    assert _count(db, WhatsAppLog) == 0
