"""New-member workflow: register -> plan -> amount -> owner verifies UPI payment -> save -> WhatsApp."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from database.models import Member, Payment, Subscription
from services import audit_service, member_service, membership_service, payment_service
from services.errors import BusinessRuleError


@dataclass(frozen=True)
class RegistrationResult:
    member: Member
    subscription: Subscription
    payment: Payment | None


def register_with_membership(session: Session, member_fields: dict, plan_id: uuid.UUID,
                             start_date: date | None = None, amount_due: Decimal | str | None = None,
                             payment: dict | None = None) -> RegistrationResult:
    """Everything is one atomic unit: a failure leaves no half-registered member behind.

    ``payment`` is only passed when the owner has *manually verified* the money arrived:
    ``{"verified": True, "method": "UPI", "reference": "...", "amount": optional}``.
    With ``payment=None`` the membership is saved unpaid (pending) and does not activate.
    """
    if payment is not None and payment.get("verified") is not True:
        raise BusinessRuleError("The owner must confirm that the payment was received.")
    with session.begin_nested():
        member = member_service.register_member(session, **member_fields)
        sub = membership_service.create_subscription(session, member.id, plan_id, start_date, amount_due)
        paid = None
        if payment is not None and sub.amount_due > 0:
            paid = payment_service.record_confirmed_payment(
                session, sub.id, payment.get("amount") or sub.amount_due, payment["method"],
                payment.get("reference"), notes="Registration payment")
        audit_service.log(session, "registration.complete", member,
                          {"subscription": str(sub.id), "paid": paid is not None})
    session.flush()
    if paid is not None:
        _queue_welcome_message(session, member, sub, paid)
    return RegistrationResult(member, sub, paid)


def _queue_welcome_message(session: Session, member: Member, sub: Subscription, payment: Payment) -> None:
    """Queue only; sending happens off the UI thread. Never blocks or fails the registration."""
    from services import whatsapp_service

    try:
        whatsapp_service.queue_registration_message(session, member, sub, payment)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("Could not queue the registration WhatsApp message")
