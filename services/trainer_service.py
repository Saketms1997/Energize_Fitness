from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from database.models import Trainer
from services import audit_service, clock, validators
from services.errors import DuplicateError, NotFoundError


def get_trainer(session: Session, trainer_id: uuid.UUID) -> Trainer:
    trainer = session.get(Trainer, trainer_id)
    if trainer is None or trainer.deleted_at is not None:
        raise NotFoundError("Trainer not found.")
    return trainer


def _duplicate(session: Session, name: str, phone: str, exclude: uuid.UUID | None = None) -> bool:
    stmt = select(Trainer.id).where(Trainer.deleted_at.is_(None), Trainer.phone == phone,
                                    func.lower(Trainer.full_name) == name.lower())
    if exclude:
        stmt = stmt.where(Trainer.id != exclude)
    return session.scalars(stmt).first() is not None


def register_trainer(session: Session, *, full_name: str, phone: str, email: str | None = None,
                     specialization: str | None = None, joining_date: date | None = None) -> Trainer:
    name = validators.required(full_name, "Full name", 150)
    phone_n = validators.phone(phone)
    if _duplicate(session, name, phone_n):
        raise DuplicateError(f"Trainer '{name}' with phone {phone_n} is already registered.")
    trainer = Trainer(full_name=name, phone=phone_n, email=validators.email(email),
                      specialization=validators.optional(specialization, 150, "Specialization"),
                      joining_date=joining_date or clock.today())
    session.add(trainer)
    session.flush()
    audit_service.log(session, "trainer.register", trainer)
    return trainer


def update_trainer(session: Session, trainer_id: uuid.UUID, **fields) -> Trainer:
    trainer = get_trainer(session, trainer_id)
    name = validators.required(fields.get("full_name", trainer.full_name), "Full name", 150)
    phone_n = validators.phone(fields.get("phone", trainer.phone))
    if _duplicate(session, name, phone_n, trainer.id):
        raise DuplicateError(f"Another trainer '{name}' with phone {phone_n} exists.")
    trainer.full_name, trainer.phone = name, phone_n
    if "email" in fields:
        trainer.email = validators.email(fields["email"])
    if "specialization" in fields:
        trainer.specialization = validators.optional(fields["specialization"], 150)
    if fields.get("joining_date"):
        trainer.joining_date = fields["joining_date"]
    session.flush()
    audit_service.log(session, "trainer.update", trainer)
    return trainer


def set_trainer_active(session: Session, trainer_id: uuid.UUID, active: bool) -> Trainer:
    trainer = get_trainer(session, trainer_id)
    trainer.is_active = active
    session.flush()
    audit_service.log(session, "trainer.activate" if active else "trainer.deactivate", trainer)
    return trainer


def list_trainers(session: Session, text: str = "", active: bool | None = None) -> list[Trainer]:
    stmt = select(Trainer).where(Trainer.deleted_at.is_(None)).order_by(Trainer.full_name)
    if text.strip():
        like = f"%{text.strip()}%"
        stmt = stmt.where(or_(Trainer.full_name.ilike(like), Trainer.phone.ilike(like),
                              Trainer.specialization.ilike(like)))
    if active is not None:
        stmt = stmt.where(Trainer.is_active.is_(active))
    return list(session.scalars(stmt))
