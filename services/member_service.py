from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.models import Attendance, Member, Payment
from services import audit_service, clock, membership_service, settings_service, validators
from services.errors import DuplicateError, NotFoundError, ValidationError
from services.membership_service import NO_MEMBERSHIP

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
STATUS_FILTERS = ("all", "active", "expiring", "expired", "pending", "none", "inactive")


@dataclass(frozen=True)
class MemberRow:
    id: uuid.UUID
    code: str
    name: str
    phone: str
    email: str | None
    joined: date
    is_active: bool
    membership_state: str
    plan_name: str | None
    expiry: date | None


def next_member_code(session: Session) -> str:
    prefix = (settings_service.get(session, "gym.member_code_prefix") or "GYM").strip().upper()
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    codes = session.scalars(select(Member.member_code).where(Member.member_code.like(f"{prefix}-%")))
    highest = max((int(m.group(1)) for c in codes if (m := pattern.match(c))), default=0)
    return f"{prefix}-{highest + 1:05d}"


def get_member(session: Session, member_id: uuid.UUID) -> Member:
    member = session.get(Member, member_id)
    if member is None or member.deleted_at is not None:
        raise NotFoundError("Member not found.")
    return member


def _duplicate_exists(session: Session, name: str, phone: str, exclude: uuid.UUID | None = None) -> bool:
    stmt = select(Member.id).where(
        Member.deleted_at.is_(None), Member.phone == phone, func.lower(Member.full_name) == name.lower()
    )
    if exclude:
        stmt = stmt.where(Member.id != exclude)
    return session.scalars(stmt).first() is not None


def store_photo(source: Path | str, member_id: uuid.UUID) -> str:
    """Copy a photo into the app data folder; returns the path relative to it."""
    src = Path(source)
    if src.suffix.lower() not in PHOTO_EXTENSIONS or not src.is_file():
        raise ValidationError("Photograph must be an image file (jpg, png, bmp, gif, webp).", "Photograph")
    folder = get_settings().data_dir / "photos"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{member_id}{src.suffix.lower()}"
    shutil.copyfile(src, target)
    return f"photos/{target.name}"


def photo_path(member: Member) -> Path | None:
    if not member.photo_path:
        return None
    path = get_settings().data_dir / member.photo_path
    return path if path.is_file() else None


def register_member(session: Session, *, full_name: str, phone: str, email: str | None = None,
                    address: str | None = None, gender: str | None = None,
                    date_of_joining: date | None = None, emergency_contact_name: str | None = None,
                    emergency_contact_phone: str | None = None, notes: str | None = None,
                    photo_source: Path | str | None = None) -> Member:
    name = validators.required(full_name, "Full name", 150)
    phone_n = validators.phone(phone)
    gender_n = validators.gender(gender, bool(settings_service.get(session, "gym.require_gender")))
    if _duplicate_exists(session, name, phone_n):
        raise DuplicateError(f"A member named '{name}' with phone {phone_n} is already registered.")
    member = Member(
        member_code=next_member_code(session), full_name=name, phone=phone_n,
        email=validators.email(email), address=validators.optional(address),
        gender=gender_n, date_of_joining=date_of_joining or clock.today(),
        emergency_contact_name=validators.optional(emergency_contact_name, 150, "Emergency contact name"),
        emergency_contact_phone=validators.phone(emergency_contact_phone, "Emergency contact phone", False),
        notes=validators.optional(notes),
    )
    session.add(member)
    session.flush()
    if photo_source:
        member.photo_path = store_photo(photo_source, member.id)
    session.flush()
    audit_service.log(session, "member.register", member, {"code": member.member_code})
    return member


def update_member(session: Session, member_id: uuid.UUID, **fields) -> Member:
    member = get_member(session, member_id)
    allowed = {"full_name", "phone", "email", "address", "gender", "date_of_joining",
               "emergency_contact_name", "emergency_contact_phone", "notes", "photo_source"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValidationError(f"Cannot edit: {', '.join(sorted(unknown))}")
    name = validators.required(fields.get("full_name", member.full_name), "Full name", 150)
    phone_n = validators.phone(fields.get("phone", member.phone))
    if _duplicate_exists(session, name, phone_n, member.id):
        raise DuplicateError(f"Another member named '{name}' with phone {phone_n} already exists.")
    member.full_name, member.phone = name, phone_n
    if "email" in fields:
        member.email = validators.email(fields["email"])
    if "address" in fields:
        member.address = validators.optional(fields["address"])
    if "gender" in fields:
        member.gender = validators.gender(fields["gender"], bool(settings_service.get(session, "gym.require_gender")))
    if fields.get("date_of_joining"):
        member.date_of_joining = fields["date_of_joining"]
    if "emergency_contact_name" in fields:
        member.emergency_contact_name = validators.optional(fields["emergency_contact_name"], 150)
    if "emergency_contact_phone" in fields:
        member.emergency_contact_phone = validators.phone(fields["emergency_contact_phone"], "Emergency contact phone", False)
    if "notes" in fields:
        member.notes = validators.optional(fields["notes"])
    if fields.get("photo_source"):
        member.photo_path = store_photo(fields["photo_source"], member.id)
    session.flush()
    audit_service.log(session, "member.update", member)
    return member


def set_member_active(session: Session, member_id: uuid.UUID, active: bool) -> Member:
    member = get_member(session, member_id)
    member.is_active = active
    session.flush()
    audit_service.log(session, "member.activate" if active else "member.deactivate", member)
    return member


def search_members(session: Session, text: str = "", status: str = "all",
                   joined_from: date | None = None, joined_to: date | None = None,
                   today: date | None = None) -> list[MemberRow]:
    if status not in STATUS_FILTERS:
        raise ValidationError(f"Unknown status filter '{status}'.")
    stmt = select(Member).where(Member.deleted_at.is_(None)).order_by(Member.member_code)
    term = text.strip()
    if term:
        like = f"%{term}%"
        stmt = stmt.where(or_(Member.full_name.ilike(like), Member.phone.ilike(like),
                              Member.member_code.ilike(like), Member.email.ilike(like)))
    if joined_from:
        stmt = stmt.where(Member.date_of_joining >= joined_from)
    if joined_to:
        stmt = stmt.where(Member.date_of_joining <= joined_to)
    if status == "inactive":
        stmt = stmt.where(Member.is_active.is_(False))
    elif status != "all":
        stmt = stmt.where(Member.is_active.is_(True))
    states = membership_service.status_map(session, today)
    rows = []
    for m in session.scalars(stmt):
        info = states.get(m.id, NO_MEMBERSHIP)
        if status in ("active", "expiring", "expired", "pending", "none") and info.state != status:
            continue
        rows.append(MemberRow(m.id, m.member_code, m.full_name, m.phone, m.email, m.date_of_joining,
                              m.is_active, info.state, info.plan_name, info.end_date))
    return rows


def member_history(session: Session, member_id: uuid.UUID) -> dict:
    """Membership, payment and attendance history for the member detail view."""
    member = get_member(session, member_id)
    payments = session.scalars(
        select(Payment).where(Payment.member_id == member_id, Payment.deleted_at.is_(None))
        .order_by(Payment.paid_at.desc())).all()
    visits = session.scalars(
        select(Attendance).where(Attendance.member_id == member_id, Attendance.deleted_at.is_(None))
        .order_by(Attendance.check_in_at.desc()).limit(500)).all()
    return {
        "member": member,
        "subscriptions": membership_service.list_subscriptions(session, member_id=member_id),
        "payments": payments,
        "attendance": visits,
    }


def export_members_xlsx(rows: list[MemberRow], path: Path | str) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Members"
    ws.append(["Member ID", "Name", "Phone", "Email", "Joined", "Active", "Membership", "Plan", "Expiry"])
    for r in rows:
        ws.append([r.code, r.name, r.phone, r.email or "", r.joined, "Yes" if r.is_active else "No",
                   r.membership_state, r.plan_name or "", r.expiry])
    for col, width in zip("ABCDEFGHI", (14, 28, 16, 30, 12, 8, 14, 16, 12)):
        ws.column_dimensions[col].width = width
    path = Path(path)
    wb.save(path)
    return path
