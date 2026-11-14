"""Declarative base, shared column mixins and constructor defaults.

Every synchronized record carries: UUID primary key, created_at, updated_at,
deleted_at (soft delete), a monotonically increasing ``version`` and sync
metadata. Python-side column defaults are applied when an object is *constructed*
(not only at INSERT) so an object's id/version are known immediately - the sync
outbox and audit log both need them before the first flush.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Integer, MetaData, Numeric, String, Uuid, event, func
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SYNC_PENDING = "pending"
SYNC_SYNCED = "synced"
SYNC_CONFLICT = "conflict"

MONEY = Numeric(12, 2)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def local_today():
    return datetime.now().date()


def money(value: object) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UuidPkMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )


class SyncMixin(UuidPkMixin, CreatedAtMixin):
    """Columns required on every record that is synchronized with MongoDB Atlas."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    sync_status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=SYNC_PENDING, server_default=SYNC_PENDING
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin_device_id: Mapped[str | None] = mapped_column(String(64))

    def soft_delete(self) -> None:
        self.deleted_at = utcnow()


@event.listens_for(Base, "init", propagate=True)
def _apply_python_defaults(target: object, _args: tuple, kwargs: dict) -> None:
    """Fill scalar/callable column defaults at construction time."""
    for attr in sa_inspect(type(target)).column_attrs:
        if attr.key in kwargs:
            continue
        default = attr.columns[0].default
        if default is None:
            continue
        if getattr(default, "is_scalar", False):
            kwargs[attr.key] = default.arg
        elif getattr(default, "is_callable", False):
            kwargs[attr.key] = default.arg(None)
