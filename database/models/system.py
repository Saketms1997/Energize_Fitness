"""Local operational tables: sync engine state, backups, audit and message logs."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON, PrimaryKeyConstraint, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String,
    Text, UniqueConstraint, Uuid, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, CreatedAtMixin, UuidPkMixin

Json = JSON().with_variant(JSONB(), "postgresql")


class SyncQueue(Base, UuidPkMixin, CreatedAtMixin):
    """Outbox: one row per local change awaiting upload to MongoDB Atlas."""

    __tablename__ = "sync_queue"

    entity_table: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)
    entity_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(Json, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("operation IN ('insert','update','delete')", name="operation_valid"),
        CheckConstraint("status IN ('pending','in_flight','done','failed')", name="status_valid"),
        # Idempotency: the same change can never be queued twice.
        UniqueConstraint("entity_table", "entity_id", "entity_version", "operation", name="uq_sync_queue_change"),
        Index("ix_sync_queue_status_created", "status", "created_at"),
    )


class SyncConflict(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "sync_conflicts"

    entity_table: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    is_financial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    remote_rev: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    base_snapshot: Mapped[dict | None] = mapped_column(Json)
    local_snapshot: Mapped[dict] = mapped_column(Json, nullable=False)
    remote_snapshot: Mapped[dict] = mapped_column(Json, nullable=False)
    conflicting_fields: Mapped[list] = mapped_column(Json, nullable=False)
    auto_merged_fields: Mapped[list] = mapped_column(Json, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open", server_default="open")
    resolution: Mapped[str | None] = mapped_column(String(10))
    resolved_value: Mapped[dict | None] = mapped_column(Json)
    resolved_by: Mapped[str | None] = mapped_column(String(60))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('open','resolved')", name="status_valid"),
        CheckConstraint("resolution IS NULL OR resolution IN ('local','remote','manual')", name="resolution_valid"),
        Index("ix_sync_conflicts_status", "status"),
        Index("ix_sync_conflicts_entity", "entity_table", "entity_id"),
    )


class SyncHistory(Base, UuidPkMixin):
    __tablename__ = "sync_history"

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trigger: Mapped[str] = mapped_column(String(10), nullable=False, default="manual", server_default="manual")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="running", server_default="running")
    uploaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    downloaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    conflicts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status IN ('running','success','partial','failed')", name="status_valid"),
        Index("ix_sync_history_started_at", "started_at"),
    )


class BackupHistory(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "backup_history"

    format: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    trigger: Mapped[str] = mapped_column(String(10), nullable=False, default="manual", server_default="manual")
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    validated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("format IN ('backup','sql','mdb')", name="format_valid"),
        CheckConstraint("status IN ('success','failed')", name="status_valid"),
        Index("ix_backup_history_created_at", "created_at"),
    )


class AuditLog(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "audit_logs"

    actor: Mapped[str | None] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_table: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="local", server_default="local")
    details: Mapped[dict | None] = mapped_column(Json)

    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_entity", "entity_table", "entity_id"),
    )


class WhatsAppLog(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "whatsapp_logs"

    member_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("members.id"))
    to_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    template_name: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict | None] = mapped_column(Json)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="queued", server_default="queued")
    provider_message_id: Mapped[str | None] = mapped_column(String(100))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('queued','sent','delivered','read','failed')", name="status_valid"),
        Index("ix_whatsapp_logs_status", "status"),
        Index("ix_whatsapp_logs_member_id", "member_id"),
    )


class SyncBase(Base):
    """Last state both this PC and the cloud agreed on, per record (3-way merge base)."""

    __tablename__ = "sync_base"

    entity_table: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    remote_rev: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(Json, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (PrimaryKeyConstraint("entity_table", "entity_id", name="pk_sync_base"),)


class SyncMeta(Base):
    """Local-only key/value state of the sync engine (device id, pull cursor)."""

    __tablename__ = "sync_meta"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
