from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import MONEY, Base, SyncMixin

SUBSCRIPTION_STATUSES = ("pending", "active", "cancelled")
PAYMENT_KINDS = ("payment", "refund")
PAYMENT_STATUSES = ("pending", "confirmed", "voided")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class MembershipPlan(Base, SyncMixin):
    __tablename__ = "membership_plans"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("duration_days > 0", name="duration_positive"),
        CheckConstraint("price >= 0", name="price_non_negative"),
        Index(
            "uq_membership_plans_name_live", "name", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class Subscription(Base, SyncMixin):
    __tablename__ = "subscriptions"

    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id"), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("membership_plans.id"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_due: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending", server_default="pending")
    renewed_from_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subscriptions.id"))

    member = relationship("Member", back_populates="subscriptions")
    plan = relationship("MembershipPlan")

    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="dates_ordered"),
        CheckConstraint("amount_due >= 0", name="amount_non_negative"),
        CheckConstraint(_in("status", SUBSCRIPTION_STATUSES), name="status_valid"),
        Index("ix_subscriptions_member_id", "member_id"),
        Index("ix_subscriptions_end_date", "end_date"),
    )


class Payment(Base, SyncMixin):
    """Financial record. Never hard-deleted; corrections are refunds or voids."""

    __tablename__ = "payments"

    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id"), nullable=False)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subscriptions.id"))
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="payment", server_default="payment")
    refund_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id"))
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100))
    receipt_no: Mapped[str | None] = mapped_column(String(30), unique=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending", server_default="pending")
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(_in("kind", PAYMENT_KINDS), name="kind_valid"),
        CheckConstraint(_in("status", PAYMENT_STATUSES), name="status_valid"),
        CheckConstraint("kind <> 'refund' OR refund_of_id IS NOT NULL", name="refund_references_payment"),
        Index("ix_payments_member_id", "member_id"),
        Index("ix_payments_paid_at", "paid_at"),
        Index("ix_payments_status", "status"),
    )


class Attendance(Base, SyncMixin):
    __tablename__ = "attendance"

    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id"), nullable=False)
    check_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("check_out_at IS NULL OR check_out_at >= check_in_at", name="out_after_in"),
        Index("ix_attendance_check_in_at", "check_in_at"),
        # Database-level guard against duplicate active check-ins for one member.
        Index(
            "uq_attendance_one_open_per_member", "member_id", unique=True,
            postgresql_where=text("check_out_at IS NULL AND deleted_at IS NULL"),
        ),
    )


class TrainerAttendance(Base, SyncMixin):
    __tablename__ = "trainer_attendance"

    trainer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trainers.id"), nullable=False)
    check_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("check_out_at IS NULL OR check_out_at >= check_in_at", name="out_after_in"),
        Index("ix_trainer_attendance_check_in_at", "check_in_at"),
        Index(
            "uq_trainer_attendance_one_open", "trainer_id", unique=True,
            postgresql_where=text("check_out_at IS NULL AND deleted_at IS NULL"),
        ),
    )


class Expense(Base, SyncMixin):
    __tablename__ = "expenses"

    category: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(30), nullable=False)

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_expenses_expense_date", "expense_date"),
    )


class GymSetting(Base, SyncMixin):
    """Key/value settings. Secrets (API tokens, URIs) live in Credential Manager instead."""

    __tablename__ = "gym_settings"

    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    value: Mapped[str | None] = mapped_column(Text)
