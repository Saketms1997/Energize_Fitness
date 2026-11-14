from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import Attendance, Member, Trainer, TrainerAttendance
from services import audit_service, clock, membership_service
from services.errors import BusinessRuleError, DuplicateError, NotFoundError

STALE_AFTER_HOURS = 16


class NoActiveMembershipError(BusinessRuleError):
    """Check-in refused only because the member has no valid membership (the owner may override)."""


@dataclass(frozen=True)
class AttendanceRow:
    id: uuid.UUID
    person_id: uuid.UUID
    code: str
    name: str
    check_in: datetime
    check_out: datetime | None

    @property
    def minutes(self) -> int | None:
        if self.check_out is None:
            return None
        return int((self.check_out - self.check_in).total_seconds() // 60)


def _open_visit(session: Session, model, key, person_id):
    return session.scalars(select(model).where(
        key == person_id, model.check_out_at.is_(None), model.deleted_at.is_(None))).first()


def check_in(session: Session, member_id: uuid.UUID, at: datetime | None = None,
             allow_without_membership: bool = False) -> Attendance:
    member = session.get(Member, member_id)
    if member is None or member.deleted_at is not None:
        raise NotFoundError("Member not found.")
    if not member.is_active:
        raise BusinessRuleError("This member is deactivated.")
    if _open_visit(session, Attendance, Attendance.member_id, member_id):
        raise DuplicateError(f"{member.full_name} is already checked in.")
    at = at or clock.now()
    if not allow_without_membership and not membership_service.has_valid_membership(
            session, member_id, clock.to_local_date(at)):
        raise NoActiveMembershipError(f"{member.full_name} has no active membership.")
    visit = Attendance(member_id=member_id, check_in_at=at)
    try:
        with session.begin_nested():  # savepoint: a lost race must not undo the caller's other work
            session.add(visit)
            session.flush()  # the partial unique index is the last line of defence
    except IntegrityError as exc:
        raise DuplicateError(f"{member.full_name} is already checked in.") from exc
    audit_service.log(session, "attendance.check_in", visit)
    return visit


def check_out(session: Session, member_id: uuid.UUID, at: datetime | None = None) -> Attendance:
    visit = _open_visit(session, Attendance, Attendance.member_id, member_id)
    if visit is None:
        raise BusinessRuleError("This member is not checked in.")
    visit.check_out_at = max(at or clock.now(), visit.check_in_at)
    session.flush()
    audit_service.log(session, "attendance.check_out", visit)
    return visit


def _rows(session: Session, model, person_model, key, day_from: date, day_to: date, text: str = ""):
    start, end = clock.local_range_bounds(day_from, day_to)
    stmt = (select(model, person_model)
            .join(person_model, person_model.id == key)
            .where(model.deleted_at.is_(None), model.check_in_at >= start, model.check_in_at < end)
            .order_by(model.check_in_at.desc()))
    term = text.strip().lower()
    out = []
    for visit, person in session.execute(stmt):
        code = getattr(person, "member_code", "TRAINER")
        if term and term not in person.full_name.lower() and term not in code.lower():
            continue
        out.append(AttendanceRow(visit.id, person.id, code, person.full_name,
                                 visit.check_in_at, visit.check_out_at))
    return out


def attendance_for_date(session: Session, day: date, text: str = "") -> list[AttendanceRow]:
    return _rows(session, Attendance, Member, Attendance.member_id, day, day, text)


def currently_checked_in(session: Session) -> list[AttendanceRow]:
    rows = session.execute(
        select(Attendance, Member).join(Member, Member.id == Attendance.member_id)
        .where(Attendance.check_out_at.is_(None), Attendance.deleted_at.is_(None))
        .order_by(Attendance.check_in_at)).all()
    return [AttendanceRow(v.id, m.id, m.member_code, m.full_name, v.check_in_at, None) for v, m in rows]


@dataclass(frozen=True)
class MonthlySummaryRow:
    person_id: uuid.UUID
    code: str
    name: str
    days_present: int
    total_minutes: int


def _monthly(session, model, person_model, key, year: int, month: int) -> list[MonthlySummaryRow]:
    first, last = clock.month_bounds(year, month)
    days: dict = defaultdict(set)
    minutes: dict = defaultdict(int)
    people: dict = {}
    for row in _rows(session, model, person_model, key, first, last):
        local_day = clock.to_local_date(row.check_in)
        days[row.person_id].add(local_day)
        minutes[row.person_id] += row.minutes or 0
        people[row.person_id] = row
    return sorted(
        (MonthlySummaryRow(pid, r.code, r.name, len(days[pid]), minutes[pid]) for pid, r in people.items()),
        key=lambda x: (-x.days_present, x.name))


def monthly_summary(session: Session, year: int, month: int) -> list[MonthlySummaryRow]:
    return _monthly(session, Attendance, Member, Attendance.member_id, year, month)


def auto_close_stale(session: Session, max_hours: int = STALE_AFTER_HOURS,
                     now: datetime | None = None) -> int:
    """Close visits left open past ``max_hours`` (forgotten check-outs) so members can check in again."""
    cutoff = (now or clock.now()) - timedelta(hours=max_hours)
    closed = 0
    for model in (Attendance, TrainerAttendance):
        for visit in session.scalars(select(model).where(
                model.check_out_at.is_(None), model.deleted_at.is_(None), model.check_in_at < cutoff)):
            visit.check_out_at = visit.check_in_at + timedelta(hours=max_hours)
            audit_service.log(session, "attendance.auto_close", visit, {"max_hours": max_hours})
            closed += 1
    session.flush()
    return closed


# ---- trainers -----------------------------------------------------------------------------

def trainer_check_in(session: Session, trainer_id: uuid.UUID, at: datetime | None = None) -> TrainerAttendance:
    trainer = session.get(Trainer, trainer_id)
    if trainer is None or trainer.deleted_at is not None:
        raise NotFoundError("Trainer not found.")
    if not trainer.is_active:
        raise BusinessRuleError("This trainer is inactive.")
    if _open_visit(session, TrainerAttendance, TrainerAttendance.trainer_id, trainer_id):
        raise DuplicateError(f"{trainer.full_name} is already checked in.")
    visit = TrainerAttendance(trainer_id=trainer_id, check_in_at=at or clock.now())
    session.add(visit)
    session.flush()
    audit_service.log(session, "trainer_attendance.check_in", visit)
    return visit


def trainer_check_out(session: Session, trainer_id: uuid.UUID, at: datetime | None = None) -> TrainerAttendance:
    visit = _open_visit(session, TrainerAttendance, TrainerAttendance.trainer_id, trainer_id)
    if visit is None:
        raise BusinessRuleError("This trainer is not checked in.")
    visit.check_out_at = max(at or clock.now(), visit.check_in_at)
    session.flush()
    audit_service.log(session, "trainer_attendance.check_out", visit)
    return visit


def trainer_attendance_for_date(session: Session, day: date, text: str = "") -> list[AttendanceRow]:
    return _rows(session, TrainerAttendance, Trainer, TrainerAttendance.trainer_id, day, day, text)


def trainer_monthly_summary(session: Session, year: int, month: int) -> list[MonthlySummaryRow]:
    return _monthly(session, TrainerAttendance, Trainer, TrainerAttendance.trainer_id, year, month)
