"""Payments, confirmation, refunds and voids.

Rules that protect the owner's money:
* A payment is only *confirmed* by an explicit owner action - never by displaying a QR code.
* Confirmed payments are never edited or hard-deleted. Corrections are refunds or voids,
  and every one is audit-logged.
* A subscription becomes active only when confirmed money covers the amount due.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from database.models import Member, Payment, Subscription
from services import audit_service, clock, membership_service, settings_service, validators
from services.errors import BusinessRuleError, DuplicateError, NotFoundError, ValidationError
from services.session_context import get_actor

REFERENCE_REQUIRED_METHODS = {"upi", "bank transfer"}
ZERO = Decimal("0.00")


def _get_subscription(session: Session, subscription_id: uuid.UUID) -> Subscription:
    sub = session.get(Subscription, subscription_id)
    if sub is None or sub.deleted_at is not None:
        raise NotFoundError("Membership not found.")
    return sub


def get_payment(session: Session, payment_id: uuid.UUID) -> Payment:
    payment = session.get(Payment, payment_id)
    if payment is None or payment.deleted_at is not None:
        raise NotFoundError("Payment not found.")
    return payment


def gross_paid(session: Session, subscription_id: uuid.UUID) -> Decimal:
    """Confirmed payments received for a membership, before any refunds."""
    return membership_service.paid_amounts(session).get(subscription_id, ZERO)


def net_paid(session: Session, subscription_id: uuid.UUID) -> Decimal:
    """Confirmed payments minus refunds."""
    return membership_service.paid_amounts(session, net=True).get(subscription_id, ZERO)


def outstanding(session: Session, subscription_id: uuid.UUID) -> Decimal:
    sub = _get_subscription(session, subscription_id)
    return max(ZERO, sub.amount_due - gross_paid(session, subscription_id))


def _next_receipt_no(session: Session, prefix: str, when: date) -> str:
    stem = f"{prefix}-{when:%Y%m}-"
    existing = session.scalars(select(Payment.receipt_no).where(Payment.receipt_no.like(f"{stem}%")))
    highest = max((int(m.group(1)) for r in existing if (m := re.search(r"-(\d+)$", r))), default=0)
    return f"{stem}{highest + 1:04d}"


def _check_method(session: Session, method: str, reference: str | None) -> tuple[str, str | None]:
    allowed = {m.lower(): m for m in settings_service.get(session, "payment.methods")}
    method = (method or "").strip()
    if method.lower() not in allowed:
        raise ValidationError(f"Payment method must be one of: {', '.join(allowed.values())}.", "Method")
    method = allowed[method.lower()]
    ref = validators.optional(reference, 100, "Reference")
    if method.lower() in REFERENCE_REQUIRED_METHODS and not ref:
        raise ValidationError(f"A reference (transaction/UTR number) is required for {method}.", "Reference")
    return method, ref


def _reference_in_use(session: Session, method: str, reference: str | None) -> bool:
    if not reference:
        return False
    return session.scalars(select(Payment.id).where(
        func.lower(Payment.method) == method.lower(), func.lower(Payment.reference) == reference.lower(),
        Payment.kind == "payment", Payment.status.in_(("pending", "confirmed")),
        Payment.deleted_at.is_(None))).first() is not None


def recompute_subscription(session: Session, subscription_id: uuid.UUID) -> Subscription:
    """Activate when fully paid; fall back to pending if a void/refund un-covers it."""
    sub = _get_subscription(session, subscription_id)
    paid = gross_paid(session, sub.id)
    if sub.status == "pending" and paid >= sub.amount_due:
        sub.status = "active"
    elif sub.status == "active" and paid < sub.amount_due and sub.amount_due > 0:
        sub.status = "pending"
    session.flush()
    return sub


def create_pending_payment(session: Session, subscription_id: uuid.UUID, amount: Decimal | str,
                           method: str, reference: str | None = None, paid_at: datetime | None = None,
                           notes: str | None = None) -> Payment:
    """Record that money is expected/claimed but NOT yet verified by the owner."""
    sub = _get_subscription(session, subscription_id)
    amount_d = validators.money(amount)
    method, ref = _check_method(session, method, reference)
    if _reference_in_use(session, method, ref):
        raise DuplicateError(f"{method} reference '{ref}' has already been recorded.")
    pending_total = session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.subscription_id == sub.id, Payment.kind == "payment", Payment.status == "pending",
        Payment.deleted_at.is_(None)))
    if amount_d + Decimal(pending_total) > outstanding(session, sub.id):
        raise BusinessRuleError(f"Amount exceeds the outstanding balance of {outstanding(session, sub.id)}.")
    payment = Payment(member_id=sub.member_id, subscription_id=sub.id, kind="payment", amount=amount_d,
                      method=method, reference=ref, status="pending",
                      paid_at=paid_at or clock.now(), notes=validators.optional(notes))
    session.add(payment)
    session.flush()
    audit_service.log(session, "payment.create_pending", payment, {"amount": str(amount_d), "method": method})
    return payment


def confirm_payment(session: Session, payment_id: uuid.UUID, confirmed_by: str | None = None) -> Payment:
    payment = get_payment(session, payment_id)
    if payment.status == "confirmed":
        return payment
    if payment.status != "pending":
        raise BusinessRuleError(f"A {payment.status} payment cannot be confirmed.")
    if payment.kind != "payment":
        raise BusinessRuleError("Only payments can be confirmed here.")
    sub = _get_subscription(session, payment.subscription_id)
    if payment.amount > outstanding(session, sub.id):
        raise BusinessRuleError("This payment is larger than the outstanding balance.")
    payment.status = "confirmed"
    payment.confirmed_by = confirmed_by or get_actor()
    payment.receipt_no = _next_receipt_no(session, "REC", clock.to_local_date(payment.paid_at))
    session.flush()
    recompute_subscription(session, sub.id)
    audit_service.log(session, "payment.confirm", payment,
                      {"amount": str(payment.amount), "receipt_no": payment.receipt_no})
    return payment


def record_confirmed_payment(session: Session, subscription_id: uuid.UUID, amount: Decimal | str,
                             method: str, reference: str | None = None, paid_at: datetime | None = None,
                             notes: str | None = None, confirmed_by: str | None = None,
                             notify: bool = False) -> Payment:
    """Owner has verified receipt of the money: create and confirm in one transaction.

    ``notify`` queues a WhatsApp payment confirmation (the registration flow sends its own welcome
    message instead, so it leaves this off).
    """
    payment = create_pending_payment(session, subscription_id, amount, method, reference, paid_at, notes)
    confirm_payment(session, payment.id, confirmed_by)
    if notify:
        queue_confirmation_message(session, payment)
    return payment


def queue_confirmation_message(session: Session, payment: Payment) -> None:
    """Best effort: a messaging problem must never undo or block a payment."""
    from services import whatsapp_service

    try:
        member, sub = session.get(Member, payment.member_id), session.get(Subscription, payment.subscription_id)
        whatsapp_service.queue_payment_confirmation(session, member, sub, payment)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("Could not queue the payment confirmation message")


@dataclass(frozen=True)
class CollectionContext:
    subscription_id: uuid.UUID
    member_name: str
    member_code: str
    plan_name: str
    period: str
    amount_due: Decimal
    paid: Decimal
    outstanding: Decimal


def collection_context(session: Session, subscription_id: uuid.UUID) -> CollectionContext:
    sub = _get_subscription(session, subscription_id)
    member = session.get(Member, sub.member_id)
    paid = gross_paid(session, sub.id)
    return CollectionContext(sub.id, member.full_name, member.member_code, sub.plan.name,
                             f"{sub.start_date:%d %b %Y} - {sub.end_date:%d %b %Y}", sub.amount_due, paid,
                             max(ZERO, sub.amount_due - paid))


def void_payment(session: Session, payment_id: uuid.UUID, reason: str) -> Payment:
    reason = validators.required(reason, "Reason", 300)
    payment = get_payment(session, payment_id)
    if payment.kind != "payment" or payment.status == "voided":
        raise BusinessRuleError("Only an unvoided payment can be voided.")
    refunded = session.scalar(select(func.count()).select_from(Payment).where(
        Payment.refund_of_id == payment.id, Payment.status == "confirmed", Payment.deleted_at.is_(None)))
    if refunded:
        raise BusinessRuleError("This payment has refunds against it and cannot be voided.")
    payment.status = "voided"
    payment.notes = f"{payment.notes + chr(10) if payment.notes else ''}VOIDED: {reason}"
    session.flush()
    if payment.subscription_id:
        recompute_subscription(session, payment.subscription_id)
    audit_service.log(session, "payment.void", payment, {"reason": reason})
    return payment


def refundable_amount(session: Session, payment_id: uuid.UUID) -> Decimal:
    payment = get_payment(session, payment_id)
    refunded = session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.refund_of_id == payment.id, Payment.status == "confirmed", Payment.deleted_at.is_(None)))
    return payment.amount - Decimal(refunded)


def refund_payment(session: Session, payment_id: uuid.UUID, amount: Decimal | str, reason: str,
                   method: str | None = None, reference: str | None = None) -> Payment:
    reason = validators.required(reason, "Reason", 300)
    original = get_payment(session, payment_id)
    if original.kind != "payment" or original.status != "confirmed":
        raise BusinessRuleError("Only a confirmed payment can be refunded.")
    amount_d = validators.money(amount, "Refund amount")
    if amount_d > refundable_amount(session, original.id):
        raise BusinessRuleError(f"Refund exceeds the refundable amount of {refundable_amount(session, original.id)}.")
    method, ref = _check_method(session, method or original.method,
                                reference or ("REFUND" if (method or original.method).lower() in REFERENCE_REQUIRED_METHODS else None))
    refund = Payment(member_id=original.member_id, subscription_id=original.subscription_id, kind="refund",
                     refund_of_id=original.id, amount=amount_d, method=method, reference=ref,
                     status="confirmed", paid_at=clock.now(), confirmed_by=get_actor(), notes=reason)
    refund.receipt_no = _next_receipt_no(session, "RFD", clock.today())
    session.add(refund)
    session.flush()
    if original.subscription_id:
        sub = _get_subscription(session, original.subscription_id)
        if net_paid(session, sub.id) <= 0 and sub.status != "cancelled":
            sub.status = "cancelled"  # everything was refunded: the membership no longer stands
    audit_service.log(session, "payment.refund", refund,
                      {"amount": str(amount_d), "original": str(original.id), "reason": reason})
    return refund


@dataclass(frozen=True)
class PaymentRow:
    id: uuid.UUID
    receipt_no: str | None
    member_code: str
    member_name: str
    kind: str
    status: str
    amount: Decimal
    method: str
    reference: str | None
    paid_at: datetime
    subscription_id: uuid.UUID | None


def list_payments(session: Session, status: str | None = None, member_id: uuid.UUID | None = None,
                  first: date | None = None, last: date | None = None) -> list[PaymentRow]:
    stmt = (select(Payment, Member).join(Member, Member.id == Payment.member_id)
            .where(Payment.deleted_at.is_(None)).order_by(Payment.paid_at.desc()))
    if status:
        stmt = stmt.where(Payment.status == status)
    if member_id:
        stmt = stmt.where(Payment.member_id == member_id)
    if first and last:
        start, end = clock.local_range_bounds(first, last)
        stmt = stmt.where(Payment.paid_at >= start, Payment.paid_at < end)
    return [PaymentRow(p.id, p.receipt_no, m.member_code, m.full_name, p.kind, p.status, p.amount,
                       p.method, p.reference, p.paid_at, p.subscription_id)
            for p, m in session.execute(stmt)]


def outstanding_subscriptions(session: Session) -> list[membership_service.SubscriptionRow]:
    """Memberships with money still owed (pending payments)."""
    return [r for r in membership_service.list_subscriptions(session)
            if r.state not in ("cancelled",) and r.outstanding > 0]


@dataclass(frozen=True)
class CollectionSummary:
    by_method: dict[str, Decimal]
    payments_total: Decimal
    refunds_total: Decimal

    @property
    def net(self) -> Decimal:
        return self.payments_total - self.refunds_total


def collection_summary(session: Session, first: date, last: date) -> CollectionSummary:
    start, end = clock.local_range_bounds(first, last)
    signed = case((Payment.kind == "refund", -Payment.amount), else_=Payment.amount)
    rows = session.execute(
        select(Payment.method, func.sum(signed)).where(
            Payment.status == "confirmed", Payment.deleted_at.is_(None),
            Payment.paid_at >= start, Payment.paid_at < end).group_by(Payment.method)).all()
    pay_total, refund_total = session.execute(
        select(
            func.coalesce(func.sum(case((Payment.kind == "payment", Payment.amount), else_=0)), 0),
            func.coalesce(func.sum(case((Payment.kind == "refund", Payment.amount), else_=0)), 0),
        ).where(Payment.status == "confirmed", Payment.deleted_at.is_(None),
                Payment.paid_at >= start, Payment.paid_at < end)).one()
    return CollectionSummary({m: Decimal(t) for m, t in rows}, Decimal(pay_total), Decimal(refund_total))
