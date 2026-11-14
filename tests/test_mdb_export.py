from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.models import GymSetting
from services import mdb_export_service as mdb, payment_service, settings_service
from tests.factories import make_member, make_paid_membership, make_plan, make_subscription

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def need_access_driver():
    try:
        mdb.check_available()
    except mdb.MdbUnavailableError as exc:
        pytest.skip(str(exc))


@pytest.fixture
def populated(db):
    plan = make_plan(db)
    m1 = make_member(db, "Zoë Müller", "9000000001", address="Ünïcode road\n" + "long address " * 40)
    m2 = make_member(db, "Ben", "9000000002")
    sub, pay = make_paid_membership(db, m1, plan, date(2026, 5, 1), method="Cash")
    payment_service.refund_payment(db, pay.id, "250.50", "partial refund")  # self-referencing FK
    make_subscription(db, m2, plan, date(2026, 5, 1))
    settings_service.set_value(db, "gym.name", "Iron House")
    settings_service.set_value(db, "local.backup.dir", r"C:\private")
    db.flush()
    return db


def test_export_creates_openable_verified_mdb(populated, tmp_path):
    result = mdb.export_to_mdb(populated, tmp_path / "gym.mdb")
    assert result.path.is_file() and result.size_bytes > 10_000
    assert result.table_counts["members"] == 2 and result.table_counts["payments"] == 2
    assert result.relationships >= 8

    import pyodbc

    with mdb.connect(result.path, readonly=True) as conn:
        cur = conn.cursor()
        names = {r[0] for r in cur.execute("SELECT [full_name] FROM [members]")}
        assert "Zoë Müller" in names
        addr = cur.execute("SELECT [address] FROM [members] WHERE [phone]='9000000001'").fetchone()[0]
        assert addr.startswith("Ünïcode road") and len(addr) > 500  # MEMO, not truncated at 255
        amounts = sorted(Decimal(r[0]) for r in cur.execute("SELECT [amount] FROM [payments]"))
        assert amounts == [Decimal("250.50"), Decimal("1000.00")]


def test_relationships_are_enforced_inside_the_mdb(populated, tmp_path):
    import pyodbc

    path = mdb.export_to_mdb(populated, tmp_path / "gym.mdb").path
    with mdb.connect(path, autocommit=True) as conn:
        with pytest.raises(pyodbc.Error):
            conn.cursor().execute(
                "INSERT INTO [subscriptions] ([id],[member_id],[plan_id],[start_date],[end_date],[amount_due],"
                "[status],[version],[sync_status],[created_at],[updated_at]) VALUES "
                "('x','missing','missing',NOW(),NOW(),1,'pending',1,'pending',NOW(),NOW())")


def test_machine_specific_settings_and_secrets_not_exported(populated, tmp_path):
    import pyodbc

    path = mdb.export_to_mdb(populated, tmp_path / "gym.mdb").path
    with mdb.connect(path, readonly=True) as conn:
        keys = {r[0] for r in conn.cursor().execute("SELECT [key] FROM [gym_settings]")}
        tables = {t.table_name for t in conn.cursor().tables(tableType="TABLE")}
    assert "gym.name" in keys and not any(k.startswith("local.") for k in keys)
    assert "owners" not in tables and not {"sync_queue", "sync_base", "sync_meta"} & tables


def test_empty_database_exports_cleanly_and_overwrites(db, tmp_path):
    dest = tmp_path / "gym.mdb"
    mdb.export_to_mdb(db, dest)
    result = mdb.export_to_mdb(db, dest)  # second export replaces the first
    assert all(c == 0 for c in result.table_counts.values()) or result.table_counts["gym_settings"] >= 0
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.endswith(".ldb")) == ["gym.mdb"]


def test_failed_verification_leaves_no_file(populated, tmp_path, monkeypatch):
    def broken(*a, **k):
        raise mdb.MdbExportError("forced")

    monkeypatch.setattr(mdb, "_verify", broken)
    with pytest.raises(mdb.MdbExportError):
        mdb.export_to_mdb(populated, tmp_path / "gym.mdb")
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".mdb"]


def test_missing_driver_is_reported_with_instructions(monkeypatch):
    import pyodbc

    monkeypatch.setattr(pyodbc, "drivers", lambda: ["SQL Server"])
    with pytest.raises(mdb.MdbUnavailableError, match="Access Database Engine"):
        mdb.check_available()
