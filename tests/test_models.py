from decimal import Decimal

import pytest
from sqlalchemy import Numeric, Uuid, create_engine, select
from sqlalchemy.orm import Session

import database.models  # noqa: F401
from database.models import Member, MembershipPlan
from database.models.base import SYNC_PENDING, SYNC_SYNCED, Base, SyncMixin
from database.tracking import SKIP_TRACKING

SPEC_TABLES = {
    "members", "membership_plans", "subscriptions", "payments", "attendance", "trainers",
    "trainer_attendance", "expenses", "gym_settings", "whatsapp_logs", "sync_queue",
    "sync_conflicts", "sync_history", "backup_history", "audit_logs",
}


def test_all_spec_tables_exist():
    assert SPEC_TABLES <= set(Base.metadata.tables)


LOCAL_STATE_TABLES = {"sync_base", "sync_meta"}  # keyed by (table, id) / text key by design


def test_every_table_has_uuid_primary_key():
    for table in Base.metadata.tables.values():
        if table.name in LOCAL_STATE_TABLES:
            continue
        pk = list(table.primary_key.columns)
        assert len(pk) == 1 and isinstance(pk[0].type, Uuid), table.name


def test_synced_tables_carry_sync_columns():
    required = {"id", "created_at", "updated_at", "deleted_at", "version", "sync_status"}
    for mapper in Base.registry.mappers:
        if issubclass(mapper.class_, SyncMixin):
            assert required <= set(mapper.local_table.c.keys()), mapper.local_table.name


def test_money_columns_are_numeric_never_float():
    for table in Base.metadata.tables.values():
        for col in table.c:
            if col.name in {"amount", "amount_due", "price"}:
                assert isinstance(col.type, Numeric) and col.type.scale == 2, f"{table.name}.{col.name}"


def test_timestamps_are_timezone_aware():
    for table in Base.metadata.tables.values():
        for col in table.c:
            if col.name.endswith("_at") and hasattr(col.type, "timezone"):
                assert col.type.timezone, f"{table.name}.{col.name}"


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _member(code="M-1"):
    return Member(member_code=code, full_name="Asha Rao", phone="9000000000")


def test_new_row_starts_at_version_1_pending(session):
    m = _member()
    session.add(m)
    session.commit()
    assert m.version == 1 and m.sync_status == SYNC_PENDING and m.deleted_at is None


def test_business_edit_bumps_version_and_marks_pending(session):
    m = _member()
    session.add(m)
    session.commit()
    m.sync_status = SYNC_SYNCED
    session.commit()
    m.full_name = "Asha R"
    session.commit()
    assert m.version == 2 and m.sync_status == SYNC_PENDING


def test_marking_synced_does_not_bump_version(session):
    m = _member()
    session.add(m)
    session.commit()
    m.sync_status = SYNC_SYNCED
    session.commit()
    assert m.version == 1 and m.sync_status == SYNC_SYNCED


def test_skip_tracking_stores_remote_change_as_received(session):
    m = _member()
    session.add(m)
    session.commit()
    session.info[SKIP_TRACKING] = True
    m.full_name, m.version, m.sync_status = "Remote Name", 7, SYNC_SYNCED
    session.commit()
    assert (m.version, m.sync_status) == (7, SYNC_SYNCED)


def test_soft_delete_sets_timestamp_and_bumps_version(session):
    m = _member()
    session.add(m)
    session.commit()
    m.soft_delete()
    session.commit()
    assert m.deleted_at is not None and m.version == 2
    assert session.scalars(select(Member)).one() is m  # row retained, never hard-deleted


def test_money_roundtrips_as_decimal(session):
    plan = MembershipPlan(name="Monthly", duration_days=30, price=Decimal("999.50"))
    session.add(plan)
    session.commit()
    session.expire_all()
    assert session.scalars(select(MembershipPlan.price)).one() == Decimal("999.50")
