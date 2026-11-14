"""Who is performing the current actions (recorded in the audit log)."""
import uuid

_actor = "system"
_owner_id: uuid.UUID | None = None


def set_actor(name: str, owner_id: uuid.UUID | None = None) -> None:
    global _actor, _owner_id
    _actor, _owner_id = name or "system", owner_id


def get_actor() -> str:
    return _actor


def get_owner_id() -> uuid.UUID | None:
    return _owner_id
