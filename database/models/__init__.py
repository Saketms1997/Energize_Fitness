"""Importing this package registers every table on ``Base.metadata``."""
from database.models.base import Base, SyncMixin
from database.models.business import (
    Attendance, Expense, GymSetting, MembershipPlan, Payment, Subscription, TrainerAttendance,
)
from database.models.people import Member, Owner, Trainer
from database.models.system import (
    AuditLog, BackupHistory, SyncBase, SyncConflict, SyncHistory, SyncMeta, SyncQueue, WhatsAppLog,
)

__all__ = [
    "Base", "SyncMixin", "Member", "Trainer", "Owner", "MembershipPlan", "Subscription", "Payment",
    "Attendance", "TrainerAttendance", "Expense", "GymSetting", "SyncQueue", "SyncConflict",
    "SyncHistory", "SyncBase", "SyncMeta", "BackupHistory", "AuditLog", "WhatsAppLog",
]

import database.tracking  # noqa: E402,F401  (registers the change-tracking/outbox listener)
