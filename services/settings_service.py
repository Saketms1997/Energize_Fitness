"""Owner-configurable settings stored in ``gym_settings`` (secrets live in Credential Manager)."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import GymSetting

DEFAULTS: dict[str, Any] = {
    "gym.name": "My Gym",
    "gym.address": "",
    "gym.phone": "",
    "gym.email": "",
    "gym.logo_path": "",
    "gym.currency": "INR",
    "gym.require_gender": False,
    "gym.member_code_prefix": "GYM",
    "gym.country_code": "91",
    "payment.methods": ["Cash", "UPI", "Bank transfer", "Card"],
    "payment.upi_id": "",
    "payment.upi_payee_name": "",
    "payment.upi_qr_path": "",
    "membership.expiry_alert_days": 7,
    "expense.categories": ["Rent", "Salaries", "Electricity", "Equipment", "Maintenance", "Marketing", "Other"],
    "whatsapp.enabled": False,
    "whatsapp.phone_number_id": "",
    "whatsapp.api_version": "v21.0",
    "whatsapp.language": "en",
    "whatsapp.reminder_days": [7, 1],
    "whatsapp.renewal_reminder_days_after": [3],
    "whatsapp.templates": {},
    # Machine-specific values (prefix "local.") are never synchronized.
    "local.backup.dir": "",
    "local.backup.schedule_enabled": False,
    "local.backup.time": "02:00",
    "local.backup.retention": 14,
    "local.backup.formats": ["backup", "sql"],
    "local.pg_bin_dir": "",
    "local.mongodb.database": "gym_management",
    "local.sync.auto_minutes": 0,
}


def _row(session: Session, key: str) -> GymSetting | None:
    return session.scalars(
        select(GymSetting).where(GymSetting.key == key, GymSetting.deleted_at.is_(None))
    ).first()


def get(session: Session, key: str) -> Any:
    """Typed value: stored JSON if present, else the built-in default."""
    default = DEFAULTS.get(key)
    row = _row(session, key)
    if row is None or row.value is None:
        return default
    try:
        value = json.loads(row.value)
    except ValueError:
        return row.value
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return value


def set_value(session: Session, key: str, value: Any) -> None:
    encoded = json.dumps(value)
    row = _row(session, key)
    if row is None:
        session.add(GymSetting(key=key, value=encoded))
    elif row.value != encoded:
        row.value = encoded
    session.flush()


def all_settings(session: Session) -> dict[str, Any]:
    return {key: get(session, key) for key in DEFAULTS}
