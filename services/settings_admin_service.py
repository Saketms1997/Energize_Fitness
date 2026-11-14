"""Validated saving of the Settings screens (kept out of the widgets so it can be tested)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from services import audit_service, backup_service, files, settings_service as ss, upi_service, validators
from services.errors import ValidationError


def _list(text: str, field: str, numbers: bool = False) -> list:
    items = [p.strip() for p in (text or "").replace("\n", ",").split(",") if p.strip()]
    if numbers:
        try:
            items = [int(i) for i in items]
        except ValueError:
            raise ValidationError(f"{field} must be whole numbers separated by commas.", field) from None
        if any(i < 0 for i in items):
            raise ValidationError(f"{field} cannot be negative.", field)
    seen: list = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def save_gym_profile(session: Session, v: dict[str, Any]) -> None:
    prefix = validators.required(v["member_code_prefix"], "Member ID prefix", 10).upper()
    if not prefix.isalnum():
        raise ValidationError("Member ID prefix may contain only letters and digits.", "Member ID prefix")
    country = validators.required(v["country_code"], "Country code", 4).lstrip("+")
    if not country.isdigit():
        raise ValidationError("Country code must be digits, e.g. 91.", "Country code")
    alert = validators.positive_int(v["expiry_alert_days"], "Expiry alert days")
    ss.set_value(session, "gym.name", validators.required(v["name"], "Gym name", 100))
    ss.set_value(session, "gym.address", (v["address"] or "").strip())
    ss.set_value(session, "gym.phone", (validators.phone(v["phone"], "Contact number", False) or ""))
    ss.set_value(session, "gym.email", validators.email(v["email"]) or "")
    ss.set_value(session, "gym.currency", validators.required(v["currency"], "Currency", 5).upper())
    ss.set_value(session, "gym.member_code_prefix", prefix)
    ss.set_value(session, "gym.country_code", country)
    ss.set_value(session, "gym.require_gender", bool(v["require_gender"]))
    ss.set_value(session, "membership.expiry_alert_days", alert)
    if v.get("logo"):
        ss.set_value(session, "gym.logo_path", files.store_image(v["logo"], "logo", "Logo"))
    audit_service.log(session, "settings.gym_profile")


def save_payment_settings(session: Session, v: dict[str, Any]) -> None:
    methods = _list(v["methods"], "Payment methods")
    if not methods:
        raise ValidationError("Add at least one payment method.", "Payment methods")
    categories = _list(v["categories"], "Expense categories")
    if not categories:
        raise ValidationError("Add at least one expense category.", "Expense categories")
    upi_id = (v["upi_id"] or "").strip()
    if upi_id and "@" not in upi_id:
        raise ValidationError("UPI ID must look like name@bank.", "UPI ID")
    ss.set_value(session, "payment.methods", methods)
    ss.set_value(session, "expense.categories", categories)
    ss.set_value(session, "payment.upi_id", upi_id)
    ss.set_value(session, "payment.upi_payee_name", (v["payee"] or "").strip())
    if v.get("qr_image"):
        ss.set_value(session, "payment.upi_qr_path", upi_service.store_qr_image(v["qr_image"]))
    audit_service.log(session, "settings.payments")


def save_backup_settings(session: Session, v: dict[str, Any]) -> None:
    backup_service.parse_time(v["time"])
    retention = validators.positive_int(v["retention"], "Keep last")
    formats = [f for f in ("backup", "sql", "mdb") if v.get(f"fmt_{f}")]
    if not formats:
        raise ValidationError("Choose at least one backup format.", "Formats")
    folder = (v["folder"] or "").strip()
    if folder:
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValidationError(f"Cannot use that folder: {exc}", "Backup folder") from exc
    pg_bin = (v["pg_bin"] or "").strip()
    if pg_bin and not (Path(pg_bin) / "pg_dump.exe").is_file():
        raise ValidationError("pg_dump.exe was not found in that PostgreSQL bin folder.", "PostgreSQL bin folder")
    ss.set_value(session, "local.backup.dir", folder)
    ss.set_value(session, "local.backup.schedule_enabled", bool(v["schedule"]))
    ss.set_value(session, "local.backup.time", v["time"].strip())
    ss.set_value(session, "local.backup.retention", retention)
    ss.set_value(session, "local.backup.formats", formats)
    ss.set_value(session, "local.pg_bin_dir", pg_bin)
    audit_service.log(session, "settings.backup")


def save_whatsapp_settings(session: Session, v: dict[str, Any]) -> None:
    phone_id = (v["phone_number_id"] or "").strip()
    if v["enabled"] and not phone_id:
        raise ValidationError("Enter the WhatsApp phone number ID before switching sending on.", "Phone number ID")
    ss.set_value(session, "whatsapp.enabled", bool(v["enabled"]))
    ss.set_value(session, "whatsapp.phone_number_id", phone_id)
    ss.set_value(session, "whatsapp.api_version", validators.required(v["api_version"], "API version", 10))
    ss.set_value(session, "whatsapp.language", validators.required(v["language"], "Default language", 10))
    ss.set_value(session, "whatsapp.reminder_days", _list(v["reminder_days"], "Reminder days", True))
    ss.set_value(session, "whatsapp.renewal_reminder_days_after", _list(v["renewal_days"], "Renewal reminder days", True))
    audit_service.log(session, "settings.whatsapp")


def save_cloud_settings(session: Session, v: dict[str, Any]) -> None:
    ss.set_value(session, "local.mongodb.database", validators.required(v["database"], "Database name", 60))
    minutes = (v["auto_minutes"] or "0").strip()
    if not minutes.isdigit():
        raise ValidationError("Automatic sync interval must be a whole number of minutes (0 = off).", "Automatic sync")
    ss.set_value(session, "local.sync.auto_minutes", int(minutes))
    audit_service.log(session, "settings.cloud")
