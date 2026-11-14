from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from database.models import Attendance
from services import attendance_service, clock
from services.errors import BusinessRuleError, DuplicateError
from tests.factories import make_member, make_paid_membership, make_plan

pytestmark = pytest.mark.db


@pytest.fixture
def member(db):
    m = make_member(db)
    make_paid_membership(db, m, make_plan(db))
    return m


def test_check_in_requires_active_membership(db):
    m = make_member(db)
    with pytest.raises(BusinessRuleError):
        attendance_service.check_in(db, m.id)
    assert attendance_service.check_in(db, m.id, allow_without_membership=True)


def test_check_in_then_out_records_duration(db, member):
    t0 = clock.now()
    attendance_service.check_in(db, member.id, at=t0)
    visit = attendance_service.check_out(db, member.id, at=t0 + timedelta(minutes=75))
    rows = attendance_service.attendance_for_date(db, clock.to_local_date(t0))
    assert rows[0].minutes == 75 and visit.check_out_at is not None


def test_duplicate_active_check_in_prevented(db, member):
    attendance_service.check_in(db, member.id)
    with pytest.raises(DuplicateError):
        attendance_service.check_in(db, member.id)


def test_database_itself_blocks_two_open_visits(db, member):
    now = clock.now()
    db.add(Attendance(member_id=member.id, check_in_at=now))
    db.flush()
    db.add(Attendance(member_id=member.id, check_in_at=now + timedelta(minutes=1)))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_can_check_in_again_after_check_out(db, member):
    attendance_service.check_in(db, member.id)
    attendance_service.check_out(db, member.id)
    assert attendance_service.check_in(db, member.id)


def test_check_out_without_check_in(db, member):
    with pytest.raises(BusinessRuleError):
        attendance_service.check_out(db, member.id)


def test_deactivated_member_cannot_check_in(db, member):
    member.is_active = False
    db.flush()
    with pytest.raises(BusinessRuleError):
        attendance_service.check_in(db, member.id)


def test_search_and_currently_checked_in(db, member):
    attendance_service.check_in(db, member.id)
    today = clock.today()
    assert len(attendance_service.attendance_for_date(db, today, "asha")) == 1
    assert attendance_service.attendance_for_date(db, today, "zzz") == []
    assert [r.name for r in attendance_service.currently_checked_in(db)] == ["Asha Rao"]


def test_date_wise_attendance(db, member):
    yesterday = clock.now() - timedelta(days=1)
    attendance_service.check_in(db, member.id, at=yesterday, allow_without_membership=True)
    attendance_service.check_out(db, member.id, at=yesterday + timedelta(hours=1))
    assert len(attendance_service.attendance_for_date(db, clock.to_local_date(yesterday))) == 1
    assert attendance_service.attendance_for_date(db, clock.today()) == []


def test_monthly_summary_counts_distinct_days(db, member):
    base = datetime(2026, 3, 10, 9, 0).astimezone()
    for day in (0, 0, 2):  # two visits on one day count once
        at = base + timedelta(days=day, hours=len(db.new))
        attendance_service.check_in(db, member.id, at=at.astimezone(timezone.utc), allow_without_membership=True)
        attendance_service.check_out(db, member.id, at=(at + timedelta(minutes=30)).astimezone(timezone.utc))
    summary = attendance_service.monthly_summary(db, 2026, 3)
    assert summary[0].days_present == 2 and summary[0].total_minutes == 90


def test_auto_close_stale_sessions(db, member):
    old = clock.now() - timedelta(hours=30)
    attendance_service.check_in(db, member.id, at=old, allow_without_membership=True)
    assert attendance_service.auto_close_stale(db, max_hours=16) == 1
    assert attendance_service.check_in(db, member.id)  # free to check in again


def test_trainer_attendance(db):
    from services import trainer_service

    t = trainer_service.register_trainer(db, full_name="Coach Dan", phone="9333333333")
    attendance_service.trainer_check_in(db, t.id)
    with pytest.raises(DuplicateError):
        attendance_service.trainer_check_in(db, t.id)
    attendance_service.trainer_check_out(db, t.id)
    with pytest.raises(BusinessRuleError):
        attendance_service.trainer_check_out(db, t.id)
    assert len(attendance_service.trainer_attendance_for_date(db, clock.today())) == 1
    now = clock.now()
    assert attendance_service.trainer_monthly_summary(db, now.year, now.month)[0].days_present == 1
