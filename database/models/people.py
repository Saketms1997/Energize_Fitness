from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import Base, CreatedAtMixin, SyncMixin, UuidPkMixin, local_today


class Member(Base, SyncMixin):
    __tablename__ = "members"

    member_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    gender: Mapped[str | None] = mapped_column(String(10))
    date_of_joining: Mapped[date] = mapped_column(
        Date, nullable=False, default=local_today, server_default=func.current_date()
    )
    photo_path: Mapped[str | None] = mapped_column(String(500))
    emergency_contact_name: Mapped[str | None] = mapped_column(String(150))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    notes: Mapped[str | None] = mapped_column(Text)

    subscriptions = relationship("Subscription", back_populates="member")

    __table_args__ = (
        Index("ix_members_full_name", "full_name"),
        Index("ix_members_phone", "phone"),
        Index("ix_members_sync_status", "sync_status"),
    )


class Trainer(Base, SyncMixin):
    __tablename__ = "trainers"

    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200))
    specialization: Mapped[str | None] = mapped_column(String(150))
    joining_date: Mapped[date] = mapped_column(
        Date, nullable=False, default=local_today, server_default=func.current_date()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))

    __table_args__ = (Index("ix_trainers_sync_status", "sync_status"),)


class Owner(Base, UuidPkMixin, CreatedAtMixin):
    """Local-only login account. Credentials are deliberately never synchronized."""

    __tablename__ = "owners"

    username: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    failed_attempts: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
