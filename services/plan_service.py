from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import MembershipPlan
from services import audit_service, validators
from services.errors import DuplicateError, NotFoundError

# Editable starting points created on first run; the owner sets real prices in Settings.
DEFAULT_PLANS = [("Monthly", 30, "1000.00"), ("Quarterly", 90, "2700.00"),
                 ("Half-yearly", 180, "5000.00"), ("Yearly", 365, "9000.00")]


def list_plans(session: Session, active_only: bool = False) -> list[MembershipPlan]:
    stmt = select(MembershipPlan).where(MembershipPlan.deleted_at.is_(None)).order_by(MembershipPlan.duration_days)
    if active_only:
        stmt = stmt.where(MembershipPlan.is_active.is_(True))
    return list(session.scalars(stmt))


def get_plan(session: Session, plan_id: uuid.UUID) -> MembershipPlan:
    plan = session.get(MembershipPlan, plan_id)
    if plan is None or plan.deleted_at is not None:
        raise NotFoundError("Membership plan not found.")
    return plan


def _name_taken(session: Session, name: str, exclude: uuid.UUID | None = None) -> bool:
    stmt = select(MembershipPlan.id).where(
        MembershipPlan.deleted_at.is_(None), MembershipPlan.name.ilike(name)
    )
    if exclude:
        stmt = stmt.where(MembershipPlan.id != exclude)
    return session.scalars(stmt).first() is not None


def create_plan(session: Session, name: str, duration_days: int | str, price: Decimal | str,
                description: str | None = None) -> MembershipPlan:
    name = validators.required(name, "Plan name", 100)
    if _name_taken(session, name):
        raise DuplicateError(f"A plan named '{name}' already exists.")
    plan = MembershipPlan(
        name=name,
        duration_days=validators.positive_int(duration_days, "Duration (days)"),
        price=validators.money(price, "Price", allow_zero=True),
        description=validators.optional(description),
    )
    session.add(plan)
    session.flush()
    audit_service.log(session, "plan.create", plan, {"name": name})
    return plan


def update_plan(session: Session, plan_id: uuid.UUID, *, name: str | None = None,
                duration_days: int | str | None = None, price: Decimal | str | None = None,
                description: str | None = None, is_active: bool | None = None) -> MembershipPlan:
    plan = get_plan(session, plan_id)
    if name is not None:
        name = validators.required(name, "Plan name", 100)
        if _name_taken(session, name, plan.id):
            raise DuplicateError(f"A plan named '{name}' already exists.")
        plan.name = name
    if duration_days is not None:
        plan.duration_days = validators.positive_int(duration_days, "Duration (days)")
    if price is not None:
        plan.price = validators.money(price, "Price", allow_zero=True)
    if description is not None:
        plan.description = validators.optional(description)
    if is_active is not None:
        plan.is_active = is_active
    session.flush()
    audit_service.log(session, "plan.update", plan)
    return plan


def ensure_default_plans(session: Session) -> int:
    """Create the standard plans if no plan exists yet. Returns how many were created."""
    if list_plans(session):
        return 0
    for name, days, price in DEFAULT_PLANS:
        create_plan(session, name, days, price)
    return len(DEFAULT_PLANS)
