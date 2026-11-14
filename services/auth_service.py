"""Owner authentication: Argon2id password hashing, lockout, audit trail.

``authenticate`` returns a result instead of raising on bad credentials, so the caller can commit
the failed-attempt counter (an exception would roll it back and defeat the lockout).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import Owner
from services import audit_service, clock, db_setup_service, validators
from services.errors import NotFoundError, ValidationError

MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=5)
MIN_PASSWORD_LENGTH = 8

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    message: str = ""
    owner_id: uuid.UUID | None = None
    display_name: str = ""
    username: str = ""
    locked_until: datetime | None = None


def check_password_strength(password: str, username: str = "") -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", "Password")
    if username and password.lower() == username.lower():
        raise ValidationError("Password must not be the same as the username.", "Password")
    if password.isdigit() or password.isalpha():
        raise ValidationError("Use a mix of letters and numbers (or symbols) in the password.", "Password")


def has_owner(session: Session) -> bool:
    return (session.scalar(select(func.count()).select_from(Owner)) or 0) > 0


def create_owner(session: Session, username: str, password: str, display_name: str | None = None) -> Owner:
    username = validators.required(username, "Username", 60).lower()
    if session.scalars(select(Owner.id).where(func.lower(Owner.username) == username)).first():
        raise ValidationError("That username is already taken.", "Username")
    check_password_strength(password, username)
    owner = Owner(username=username, password_hash=_hasher.hash(password),
                  display_name=(display_name or username).strip())
    session.add(owner)
    session.flush()
    audit_service.log(session, "auth.owner_created", owner)
    return owner


def authenticate(session: Session, username: str, password: str, now: datetime | None = None) -> AuthResult:
    now = now or clock.now()
    owner = session.scalars(select(Owner).where(func.lower(Owner.username) == (username or "").strip().lower())).first()
    if owner is None or not owner.is_active:
        try:  # burn comparable time so unknown usernames are not distinguishable by speed
            _hasher.verify(_DUMMY_HASH, password or "")
        except (VerificationError, InvalidHashError):
            pass
        return AuthResult(False, "Incorrect username or password.")
    if owner.locked_until and owner.locked_until > now:
        minutes = int((owner.locked_until - now).total_seconds() // 60) + 1
        return AuthResult(False, f"Too many failed attempts. Try again in {minutes} minute(s).",
                          locked_until=owner.locked_until)
    try:
        _hasher.verify(owner.password_hash, password or "")
    except (VerificationError, InvalidHashError):
        owner.failed_attempts += 1
        message = "Incorrect username or password."
        if owner.failed_attempts >= MAX_FAILED_ATTEMPTS:
            owner.locked_until, owner.failed_attempts = now + LOCKOUT, 0
            message = f"Too many failed attempts. Account locked for {int(LOCKOUT.total_seconds() // 60)} minutes."
        audit_service.log(session, "auth.login_failed", owner, {"username": owner.username})
        session.flush()
        return AuthResult(False, message, locked_until=owner.locked_until)
    if _hasher.check_needs_rehash(owner.password_hash):
        owner.password_hash = _hasher.hash(password)
    owner.failed_attempts, owner.locked_until, owner.last_login_at = 0, None, now
    audit_service.log(session, "auth.login", owner, {"username": owner.username})
    session.flush()
    return AuthResult(True, owner_id=owner.id, display_name=owner.display_name, username=owner.username)


def change_password(session: Session, owner_id: uuid.UUID, current: str, new: str) -> None:
    owner = session.get(Owner, owner_id)
    if owner is None:
        raise NotFoundError("Account not found.")
    try:
        _hasher.verify(owner.password_hash, current or "")
    except (VerificationError, InvalidHashError):
        raise ValidationError("Current password is incorrect.", "Current password") from None
    check_password_strength(new, owner.username)
    owner.password_hash = _hasher.hash(new)
    session.flush()
    audit_service.log(session, "auth.password_changed", owner)


def reset_password(session: Session, username: str, admin_password: str, new: str) -> None:
    """Recover an existing active owner after verifying the configured database administrator."""
    username = validators.required(username, "Username", 60).lower()
    check_password_strength(new, username)
    try:
        db_setup_service.verify_admin_password(admin_password)
    except db_setup_service.SetupError as exc:
        raise ValidationError(str(exc), "PostgreSQL administrator password") from None
    owner = session.scalars(select(Owner).where(func.lower(Owner.username) == username).with_for_update()).first()
    if owner is None:
        raise NotFoundError("Account not found. Check the owner username.")
    if not owner.is_active:
        raise ValidationError("This account is inactive. Password recovery does not reactivate accounts.")
    owner.password_hash = _hasher.hash(new)
    owner.failed_attempts, owner.locked_until = 0, None
    audit_service.log(session, "auth.password_reset", owner, {"method": "postgres_admin"})
    session.flush()
