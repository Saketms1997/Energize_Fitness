from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from database.models import AuditLog
from services.session_context import get_actor


def log(session: Session, action: str, entity: Any = None, details: dict | None = None,
        source: str = "local") -> AuditLog:
    entry = AuditLog(
        actor=get_actor(),
        action=action,
        entity_table=getattr(entity, "__tablename__", None),
        entity_id=getattr(entity, "id", None),
        source=source,
        details=details,
    )
    session.add(entry)
    return entry
