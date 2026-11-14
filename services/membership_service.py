from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from database.models import Member, MembershipPlan, Payment, Subscription
from services import audit_service, clock, plan_service, settings_service, validators
from services.errors import BusinessRuleError, DuplicateError, NotFoundError
from services.membership_logic import end_date_for, subscription_state


@dataclass(frozen=True)
class MembershipInfo:
    state: str  # active | expiring | upcoming | expired | pending | none
    start_date: date | None = None
    end_date: date | None = None
    plan_name: str | None = None
    subscription_id: uuid.UUID | None = None


NO_MEMBERSHIP = MembershipInfo("none")


def _alert_days(session: Session) -> int:
    return int(settings_service.get(session, "membership.expiry_alert_days"))


def status_map(session: Session, today: date | None = None) -> dict[uuid.UUID, MembershipInfo]:
    """Current membership state for every member that has any subscription."""
    today = today or clock.today()
    alert = _alert_days(session)
    rows = session.execute(
        select(Subscription, MembershipPlan.name)
        .join(MembershipPlan, MembershipPlan.id == Subscription.plan_id)
        .where(Subscription.deleted_at.is_(None), Subscription.status != "cancelled")
    ).all()
    best: dict[uuid.UUID, tuple[tuple, MembershipInfo]] = {}
    for sub, plan_name in rows:
        state = subscription_state(sub.status, sub.start_date, sub.end_date, today, alert)
        # Rank: covering today > upcoming > expired(latest) > pending.
        rank = {"active": 3, "expiring": 3, "upcoming": 2, "expired": 1, "pending": 0}[state]
        key = (rank, sub.end_date)
        info = MembershipInfo(state, sub.start_date, sub.end_date, plan_name, sub.id)
        if sub.member_id not in best or key > best[sub.member_id][0]:
            best[sub.member_id] = (key, info)
    return {member_id: info for member_id, (_, info) in best.items()}


def has_valid_membership(session: Session, member_id: uuid.UUID, today: date | None = None) -> bool:
    info = status_map(session, today).get(member_id, NO_MEMBERSHIP)
    return info.state in ("active", "expiring")


def paid_amounts(session: Session, net: bool = False) -> dict[uuid.UUID, Decimal]:
    """Confirmed money received per subscription.

    Default (gross) counts payments only: a goodwill refund does not make the member owe
    money again. ``net=True`` subtracts refunds (used for revenue and refund limits).
    """
    amount = case((Payment.kind == "refund", -Payment.amount), else_=Payment.amount) if net else Payment.amount
    stmt = (select(Payment.subscription_id, func.sum(amount))
            .where(Payment.status == "confirmed", Payment.deleted_at.is_(None),
                   Payment.subscription_id.is_not(None))
            .group_by(Payment.subscription_id))
    if not net:
        stmt = stmt.where(Payment.kind == "payment")
    return {sid: total or Decimal("0.00") for sid, total in session.execute(stmt).all()}


def create_subscription(session: Session, member_id: uuid.UUID, plan_id: uuid.UUID,
                        start_date: date | None = None, amount_due: Decimal | str | None = None,
                        renewed_from: Subscription | None = None) -> Subscription:
    """New subscription in 'pending' state; payment confirmation activates it."""
    member = session.get(Member, member_id)
    if member is None or member.deleted_at is not None:
        raise NotFoundError("Member not found.")
    if not member.is_active:
        raise BusinessRuleError("This member is deactivated. Reactivate before adding a membership.")
    plan = plan_service.get_plan(session, plan_id)
    if not plan.is_active:
        raise BusinessRuleError(f"Plan '{plan.name}' is not active.")
    unpaid = session.scalars(select(Subscription.id).where(
        Subscription.member_id == member_id, Subscription.status == "pending",
        Subscription.deleted_at.is_(None))).first()
    if unpaid:
        raise DuplicateError("This member already has an unpaid membership. Collect or cancel it first.")
    start = start_date or clock.today()
    due = plan.price if amount_due is None else validators.money(amount_due, "Amount due", allow_zero=True)
    sub = Subscription(
        member_id=member_id, plan_id=plan.id, start_date=start,
        end_date=end_date_for(start, plan.duration_days), amount_due=due,
        status="active" if due == 0 else "pending",  # a free plan needs no payment
        renewed_from_id=renewed_from.id if renewed_from else None,
    )
    session.add(sub)
    session.flush()
    audit_service.log(session, "subscription.create", sub, {"plan": plan.name, "amount_due": str(due)})
    return sub


def renew_subscription(session: Session, member_id: uuid.UUID, plan_id: uuid.UUID | None = None,
                       today: date | None = None, amount_due: Decimal | str | None = None) -> Subscription:
    """Renew: starts the day after the current membership ends, or today if it has lapsed."""
    today = today or clock.today()
    previous = session.scalars(
        select(Subscription).where(
            Subscription.member_id == member_id, Subscription.deleted_at.is_(None),
            Subscription.status == "active",
        ).order_by(Subscription.end_date.desc())
    ).first()
    if previous is None and plan_id is None:
        raise BusinessRuleError("This member has no previous membership. Choose a plan.")
    start = today
    if previous is not None and previous.end_date >= today:
        start = previous.end_date + timedelta(days=1)
    return create_subscription(session, member_id, plan_id or previous.plan_id, start, amount_due, previous)


def cancel_subscription(session: Session, subscription_id: uuid.UUID, reason: str = "") -> Subscription:
    sub = session.get(Subscription, subscription_id)
    if sub is None or sub.deleted_at is not None:
        raise NotFoundError("Subscription not found.")
    if sub.status == "cancelled":
        return sub
    if paid_amounts(session).get(sub.id, Decimal("0")) > 0:
        raise BusinessRuleError("This membership has confirmed payments. Refund them before cancelling.")
    sub.status = "cancelled"
    session.flush()
    audit_service.log(session, "subscription.cancel", sub, {"reason": reason})
    return sub


@dataclass(frozen=True)
class SubscriptionRow:
    id: uuid.UUID
    member_id: uuid.UUID
    member_code: str
    member_name: str
    plan_name: str
    start_date: date
    end_date: date
    amount_due: Decimal
    paid: Decimal
    state: str
    renewed: bool

    @property
    def outstanding(self) -> Decimal:
        return max(Decimal("0.00"), self.amount_due - self.paid)


def list_subscriptions(session: Session, state: str = "all", member_id: uuid.UUID | None = None,
                       today: date | None = None) -> list[SubscriptionRow]:
    today = today or clock.today()
    alert = _alert_days(session)
    paid = paid_amounts(session)
    stmt = (
        select(Subscription, Member, MembershipPlan.name)
        .join(Member, Member.id == Subscription.member_id)
        .join(MembershipPlan, MembershipPlan.id == Subscription.plan_id)
        .where(Subscription.deleted_at.is_(None))
        .order_by(Subscription.end_date)
    )
    if member_id:
        stmt = stmt.where(Subscription.member_id == member_id)
    renewed_ids = set(session.scalars(
        select(Subscription.renewed_from_id).where(Subscription.renewed_from_id.is_not(None),
                                                   Subscription.status != "cancelled")))
    out = []
    for sub, member, plan_name in session.execute(stmt):
        s = subscription_state(sub.status, sub.start_date, sub.end_date, today, alert)
        if state != "all" and s != state:
            continue
        out.append(SubscriptionRow(sub.id, member.id, member.member_code, member.full_name, plan_name,
                                   sub.start_date, sub.end_date, sub.amount_due,
                                   paid.get(sub.id, Decimal("0.00")), s, sub.id in renewed_ids))
    return out
