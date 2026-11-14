import os
import time
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from database.models import BackupHistory, Member, Payment
from services import backup_service as bs, settings_service
from services.errors import BusinessRuleError
from tests.factories import make_member, make_paid_membership, make_plan

pytestmark = pytest.mark.db


@pytest.fixture
def target(engine_a):
    return bs.DbTarget.from_url(engine_a.url)


@pytest.fixture
def data(db):
    for i in range(3):
        m = make_member(db, f"Member {i}", f"90000000{i:02d}")
        make_paid_membership(db, m, make_plan(db) if i == 0 else db.scalars(select(__import__("database.models", fromlist=["MembershipPlan"]).MembershipPlan)).first())
    db.commit()
    return db


def _members(session):
    """Count members, then END the transaction so no lock blocks the restore's DROP TABLE."""
    session.rollback()
    count = session.scalar(select(func.count()).select_from(Member))
    session.rollback()
    return count


def test_both_formats_create_and_validate(data, target, tmp_path):
    for fmt in ("backup", "sql"):
        result = bs.create_backup(target, fmt, tmp_path)
        assert result.path.suffix == bs.FORMATS[fmt] and result.size_bytes > 500 and result.validated
        assert len(result.sha256) == 64
    assert not list(tmp_path.glob("*.partial"))


def test_garbage_and_truncated_files_fail_validation(target, tmp_path):
    bad = tmp_path / "x.backup"
    bad.write_bytes(b"not a backup")
    with pytest.raises(bs.BackupError):
        bs.validate_backup_file(bad, "backup", target=target)
    empty = tmp_path / "e.sql"
    empty.write_bytes(b"")
    with pytest.raises(bs.BackupError):
        bs.validate_backup_file(empty, "sql")
    sql = tmp_path / "t.sql"
    sql.write_text("--\n-- PostgreSQL database dump\n--\nCREATE TABLE public.members (", encoding="utf-8")
    with pytest.raises(bs.BackupError, match="truncated"):
        bs.validate_backup_file(sql, "sql")


def test_wrong_credentials_fail_cleanly_and_leave_no_file(data, target, tmp_path):
    bad = bs.DbTarget(target.host, target.port, target.dbname, target.user, "wrong-password")
    with pytest.raises(bs.BackupError, match="pg_dump failed"):
        bs.create_backup(bad, "backup", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_verify_by_restore_matches_source_counts(data, target, tmp_path):
    result = bs.create_backup(target, "backup", tmp_path)
    counts = bs.verify_by_restore(result.path, "backup", target)
    assert counts["members"] == 3 and counts["payments"] == 3 and counts["subscriptions"] == 3


@pytest.mark.parametrize("fmt", ["backup", "sql"])
def test_restore_brings_data_back_and_keeps_a_safety_copy(data, engine_a, target, tmp_path, fmt):
    saved = bs.create_backup(target, fmt, tmp_path / "b")
    data.execute(text("TRUNCATE members, subscriptions, payments, audit_logs, attendance, whatsapp_logs CASCADE"))
    data.commit()
    assert _members(data) == 0
    safety = bs.restore_backup(saved.path, target, tmp_path / "safety")
    assert _members(data) == 3 and safety.is_file() and safety.name.startswith("pre_restore_")
    assert data.scalar(select(func.sum(Payment.amount))) == Decimal("3000.00")
    data.rollback()


def test_failed_restore_leaves_database_unchanged(data, target, tmp_path):
    saved = bs.create_backup(target, "sql", tmp_path)
    corrupt = tmp_path / "corrupt.sql"
    corrupt.write_text(saved.path.read_text("utf-8").replace("CREATE TABLE public.payments", "CREATE TABEL public.payments"),
                       encoding="utf-8")
    with pytest.raises(bs.BackupError):
        bs.restore_backup(corrupt, target, tmp_path / "safety")
    assert _members(data) == 3


def test_mdb_is_refused_as_restore_source(target, tmp_path):
    fake = tmp_path / "gym.mdb"
    fake.write_bytes(b"x")
    with pytest.raises(bs.BackupError):
        bs.restore_backup(fake, target, tmp_path)


def test_unknown_extension_rejected(tmp_path):
    with pytest.raises(bs.BackupError):
        bs.format_of(tmp_path / "x.zip")


def test_retention_keeps_newest_per_format(tmp_path):
    for i in range(5):
        for suffix in (".backup", ".sql"):
            f = tmp_path / f"gym_2026010{i}_000000{suffix}"
            f.write_bytes(b"x")
            os.utime(f, (time.time() - (10 - i) * 100,) * 2)
    safety = tmp_path / "pre_restore_x.backup"
    safety.write_bytes(b"x")
    removed = bs.apply_retention(tmp_path, keep=2)
    assert len(removed) == 6 and safety.exists()
    assert sorted(p.name for p in tmp_path.glob("gym_*.backup")) == ["gym_20260103_000000.backup", "gym_20260104_000000.backup"]


def test_history_records_success_and_failure(data, target, tmp_path):
    ok = bs.create_backup(target, "backup", tmp_path)
    bs.record_history(data, "backup", ok.path, "manual", ok)
    bs.record_history(data, "sql", tmp_path / "x.sql", "scheduled", None, "disk full")
    rows = {r.status: r for r in bs.list_history(data)}
    assert rows["success"].validated and rows["success"].sha256 == ok.sha256
    assert rows["failed"].error == "disk full" and rows["failed"].trigger == "scheduled"


def test_schedule_logic():
    day = datetime(2026, 5, 10, 1, 0).astimezone()
    assert not bs.is_due(None, day, "02:00")  # before the scheduled time
    after = day.replace(hour=3)
    assert bs.is_due(None, after, "02:00")
    assert not bs.is_due(day.replace(hour=2, minute=30), after, "02:00")  # already done today
    assert bs.is_due(day - timedelta(days=1), after, "02:00")
    assert bs.next_run(day, "02:00").hour == 2 and bs.next_run(day, "02:00").date() == day.date()
    assert bs.next_run(after, "02:00").date() == day.date() + timedelta(days=1)
    with pytest.raises(BusinessRuleError):
        bs.parse_time("25:99:00")


def test_missing_pg_tools_give_actionable_error(monkeypatch):
    monkeypatch.setattr(bs.shutil, "which", lambda n: None)
    monkeypatch.setattr(bs.os, "environ", {"ProgramFiles": r"Z:\nowhere"})
    with pytest.raises(bs.BackupError, match="not found"):
        bs.find_pg_tool("pg_dump", None)


def test_default_backup_dir_prefers_setting(db, tmp_path):
    assert bs.default_backup_dir(db).name == "backups"
    settings_service.set_value(db, "local.backup.dir", str(tmp_path))
    assert bs.default_backup_dir(db) == tmp_path
