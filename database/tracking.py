"""Automatic change tracking and the transactional sync outbox.

On every flush, for each synchronized row whose *business* content changed we:
  * bump ``version`` / ``updated_at`` and mark the row ``pending``;
  * append a ``sync_queue`` row (the outbox) in the SAME transaction, so a change
    can never be committed without being queued, nor queued without being committed.

The sync engine sets ``session.info[SKIP_TRACKING] = True`` while applying remote
changes so they are stored exactly as received and not echoed back to the cloud.
"""
from __future__ import annotations

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from database.models.base import SYNC_PENDING, SyncMixin, utcnow
from database.models.system import SyncQueue
from database.serialization import LOCAL_ONLY_FIELDS, is_local_only, serialize

SKIP_TRACKING = "skip_change_tracking"
_BOOKKEEPING = LOCAL_ONLY_FIELDS | {"updated_at", "version"}


def _enqueue(session: Session, obj: SyncMixin, operation: str) -> None:
    if is_local_only(obj):
        return
    session.add(
        SyncQueue(
            entity_table=obj.__tablename__,
            entity_id=obj.id,
            operation=operation,
            entity_version=obj.version,
            payload=serialize(obj),
        )
    )


@event.listens_for(Session, "before_flush")
def _track_changes(session: Session, _ctx: object, _instances: object) -> None:
    if session.info.get(SKIP_TRACKING):
        return
    for obj in list(session.new):
        if isinstance(obj, SyncMixin):
            _enqueue(session, obj, "insert")
    for obj in list(session.dirty):
        if not isinstance(obj, SyncMixin) or not session.is_modified(obj, include_collections=False):
            continue
        changed = {
            a.key for a in inspect(obj).attrs if a.history.has_changes() and a.key not in _BOOKKEEPING
        }
        if not changed:
            continue
        obj.version = (obj.version or 1) + 1
        obj.updated_at = utcnow()
        obj.sync_status = SYNC_PENDING
        _enqueue(session, obj, "delete" if "deleted_at" in changed and obj.deleted_at else "update")
