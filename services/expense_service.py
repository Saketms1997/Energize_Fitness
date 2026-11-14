from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import Expense
from services import audit_service, clock, settings_service, validators
from services.errors import NotFoundError, ValidationError


def _category(session: Session, category: str) -> str:
    category = validators.required(category, "Category", 60)
    known = {c.lower(): c for c in settings_service.get(session, "expense.categories")}
    return known.get(category.lower(), category)  # unknown categories are allowed, normalised if known


def get_expense(session: Session, expense_id: uuid.UUID) -> Expense:
    expense = session.get(Expense, expense_id)
    if expense is None or expense.deleted_at is not None:
        raise NotFoundError("Expense not found.")
    return expense


def add_expense(session: Session, category: str, amount: Decimal | str, expense_date: date | None,
                payment_method: str, description: str | None = None) -> Expense:
    methods = settings_service.get(session, "payment.methods")
    if payment_method.lower() not in {m.lower() for m in methods}:
        raise ValidationError(f"Payment method must be one of: {', '.join(methods)}.", "Method")
    expense = Expense(category=_category(session, category), amount=validators.money(amount),
                      expense_date=expense_date or clock.today(), payment_method=payment_method,
                      description=validators.optional(description))
    session.add(expense)
    session.flush()
    audit_service.log(session, "expense.add", expense, {"amount": str(expense.amount)})
    return expense


def update_expense(session: Session, expense_id: uuid.UUID, **fields) -> Expense:
    expense = get_expense(session, expense_id)
    old = {"amount": str(expense.amount), "category": expense.category}
    if "category" in fields:
        expense.category = _category(session, fields["category"])
    if "amount" in fields:
        expense.amount = validators.money(fields["amount"])
    if fields.get("expense_date"):
        expense.expense_date = fields["expense_date"]
    if "payment_method" in fields:
        expense.payment_method = fields["payment_method"]
    if "description" in fields:
        expense.description = validators.optional(fields["description"])
    session.flush()
    audit_service.log(session, "expense.update", expense, {"before": old})
    return expense


def delete_expense(session: Session, expense_id: uuid.UUID) -> None:
    expense = get_expense(session, expense_id)
    expense.soft_delete()
    session.flush()
    audit_service.log(session, "expense.delete", expense, {"amount": str(expense.amount)})


def list_expenses(session: Session, first: date, last: date, category: str | None = None) -> list[Expense]:
    stmt = (select(Expense).where(Expense.deleted_at.is_(None), Expense.expense_date >= first,
                                  Expense.expense_date <= last).order_by(Expense.expense_date.desc()))
    if category:
        stmt = stmt.where(Expense.category == category)
    return list(session.scalars(stmt))


@dataclass(frozen=True)
class MonthlyExpenseReport:
    year: int
    month: int
    total: Decimal
    by_category: dict[str, Decimal]
    expenses: list[Expense]


def monthly_report(session: Session, year: int, month: int) -> MonthlyExpenseReport:
    first, last = clock.month_bounds(year, month)
    expenses = list_expenses(session, first, last)
    by_cat = {c: Decimal(t) for c, t in session.execute(
        select(Expense.category, func.sum(Expense.amount)).where(
            Expense.deleted_at.is_(None), Expense.expense_date >= first, Expense.expense_date <= last
        ).group_by(Expense.category).order_by(func.sum(Expense.amount).desc()))}
    return MonthlyExpenseReport(year, month, sum(by_cat.values(), Decimal("0.00")), by_cat, expenses)
