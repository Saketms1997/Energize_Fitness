"""Row <-> JSON-safe dict conversion shared by the sync outbox and the sync engine."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, Uuid
from sqlalchemy import inspect as sa_inspect

from database.models.base import Base, SyncMixin

# Written by the sync engine itself; never uploaded and never compared for conflicts.
LOCAL_ONLY_FIELDS = {"sync_status", "last_synced_at", "origin_device_id"}
# Identity / bookkeeping fields excluded when comparing business content.
NON_BUSINESS_FIELDS = LOCAL_ONLY_FIELDS | {"id", "created_at", "updated_at", "version"}
LOCAL_SETTING_PREFIX = "local."  # gym_settings keys that stay on this PC only


def sync_models() -> dict[str, type[SyncMixin]]:
    import database.models  # noqa: F401  (registers mappers)

    return {
        m.class_.__tablename__: m.class_
        for m in Base.registry.mappers
        if issubclass(m.class_, SyncMixin)
    }


def is_local_only(obj: Any) -> bool:
    return obj.__tablename__ == "gym_settings" and (getattr(obj, "key", "") or "").startswith(
        LOCAL_SETTING_PREFIX
    )


def _encode(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str, list, dict)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value)!r}")


def serialize(obj: Any) -> dict[str, Any]:
    """Snapshot of every column except the local-only sync bookkeeping."""
    return {
        attr.key: _encode(getattr(obj, attr.key))
        for attr in sa_inspect(type(obj)).column_attrs
        if attr.key not in LOCAL_ONLY_FIELDS
    }


def decode_fields(model: type, payload: dict[str, Any]) -> dict[str, Any]:
    """Turn a payload back into Python values according to the column types."""
    out: dict[str, Any] = {}
    columns = {a.key: a.columns[0] for a in sa_inspect(model).column_attrs}
    for key, raw in payload.items():
        col = columns.get(key)
        if col is None:  # field from a newer app version: ignore rather than crash
            continue
        if raw is None:
            out[key] = None
            continue
        t = col.type
        if isinstance(t, Uuid):
            out[key] = uuid.UUID(raw) if not isinstance(raw, uuid.UUID) else raw
        elif isinstance(t, DateTime):
            out[key] = datetime.fromisoformat(raw) if isinstance(raw, str) else raw
        elif isinstance(t, Date):
            out[key] = date.fromisoformat(raw) if isinstance(raw, str) else raw
        elif isinstance(t, Numeric):
            out[key] = Decimal(str(raw))
        elif isinstance(t, (Integer, Boolean)):
            out[key] = raw
        else:
            out[key] = raw
    return out


def business_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in NON_BUSINESS_FIELDS}
