"""Small helpers that build realistic data through the real services."""
from __future__ import annotations

from datetime import date

from services import member_service, membership_service, payment_service, plan_service


def make_member(db, name="Asha Rao", phone="9000000001", **kw):
    return member_service.register_member(db, full_name=name, phone=phone, **kw)


def make_plan(db, name="Monthly", days=30, price="1000"):
    return plan_service.create_plan(db, name, days, price)


def make_subscription(db, member, plan, start: date | None = None, **kw):
    return membership_service.create_subscription(db, member.id, plan.id, start, **kw)


def make_paid_membership(db, member, plan, start: date | None = None, method="Cash", amount_due=None):
    sub = make_subscription(db, member, plan, start, amount_due=amount_due)
    payment = payment_service.record_confirmed_payment(db, sub.id, sub.amount_due, method)
    return sub, payment
