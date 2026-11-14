"""Two-way sync tests: two real PostgreSQL databases (devices A and B) sharing one mocked cloud."""
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal

import mongomock
import pytest
from pymongo.errors import ServerSelectionTimeoutError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import Member, MembershipPlan, Payment, SyncBase, SyncConflict, SyncMeta, SyncQueue
from services import (member_service, mongodb_service, payment_service, plan_service, settings_service,
                      sync_service as sy)
from services.errors import BusinessRuleError, ValidationError
from tests.factories import make_member, make_plan, make_subscription

pytestmark = pytest.mark.db


class Device:
    def __init__(self, engine, cloud):
        self.engine = engine

        @contextmanager
        def scope():
            s = Session(engine, expire_on_commit=False)
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()

        self.scope = scope
        self.sync = sy.SyncEngine(scope, cloud)

    def run(self, fn):
        with self.scope() as s:
            return fn(s)

    def member(self, code_name):
        return self.run(lambda s: s.scalars(select(Member).where(Member.full_name == code_name)).one())


@pytest.fixture
def cloud():
    return mongomock.MongoClient().gym


@pytest.fixture
def A(engine_a, db, cloud):
    db.rollback()
    return Device(engine_a, cloud)


@pytest.fixture
def B(engine_b, db_b, cloud):
    db_b.rollback()
    return Device(engine_b, cloud)


def add_member(dev, name="Asha Rao", phone="9000000001", **kw):
    def go(s):
        m = member_service.register_member(s, full_name=name, phone=phone, **kw)
        return m.id
    return dev.run(go)


def edit(dev, member_id, **fields):
    def go(s):
        for k, v in fields.items():
            setattr(s.get(Member, member_id), k, v)
    dev.run(go)


def pending(dev):
    return dev.run(sy.pending_count)


# ---- outbox ----------------------------------------------------------------------------------

def test_every_change_is_queued_atomically_with_the_change(A):
    mid = add_member(A)
    assert pending(A) == 1
    edit(A, mid, email="a@example.com")
    edit(A, mid, email="b@example.com")
    rows = A.run(lambda s: [(r.operation, r.entity_version) for r in s.scalars(
        select(SyncQueue).order_by(SyncQueue.entity_version))])
    assert rows[0] == ("insert", 1) and rows[1:] == [("update", 2), ("update", 3)][: len(rows) - 1]
    assert pending(A) == 1  # one *entity* waiting, however many edits


def test_rolled_back_transaction_leaves_no_queue_row(A):
    with pytest.raises(RuntimeError):
        with A.scope() as s:
            member_service.register_member(s, full_name="Ghost", phone="9999999999")
            raise RuntimeError("boom")
    assert A.run(lambda s: s.scalar(select(func.count()).select_from(SyncQueue))) == 0


def test_local_only_settings_are_never_queued(A):
    A.run(lambda s: settings_service.set_value(s, "local.backup.dir", "C:/x"))
    A.run(lambda s: settings_service.set_value(s, "gym.name", "Iron House"))
    keys = A.run(lambda s: [r.payload["key"] for r in s.scalars(select(SyncQueue))])
    assert keys == ["gym.name"]


# ---- basic two-way flow -----------------------------------------------------------------------

def test_upload_then_download_creates_identical_record(A, B, cloud):
    mid = add_member(A, address="1 Main St", email="asha@example.com")
    ra = A.sync.sync_now()
    assert (ra.status, ra.uploaded, ra.failed) == ("success", 1, 0) and pending(A) == 0
    assert cloud.members.count_documents({}) == 1
    rb = B.sync.sync_now()
    assert (rb.status, rb.downloaded) == ("success", 1)
    a, b = A.member("Asha Rao"), B.member("Asha Rao")
    assert (b.id, b.member_code, b.address, b.email, b.version) == (a.id, a.member_code, a.address, a.email, a.version)
    assert b.sync_status == "synced" and pending(B) == 0  # downloads are never echoed back


def test_repeated_sync_is_idempotent_and_makes_no_duplicates(A, B, cloud):
    add_member(A)
    A.sync.sync_now()
    rev = cloud.members.find_one()["_sync"]["rev"]
    for _ in range(3):
        for dev in (A, B):
            r = dev.sync.sync_now()
            assert r.failed == 0 and r.conflicts == 0
    assert cloud.members.count_documents({}) == 1 and cloud.members.find_one()["_sync"]["rev"] == rev
    assert B.run(lambda s: s.scalar(select(func.count()).select_from(Member))) == 1
    assert A.run(lambda s: s.scalar(select(func.count()).select_from(Member))) == 1


def test_edit_propagates_both_ways(A, B):
    mid = add_member(A)
    A.sync.sync_now(); B.sync.sync_now()
    edit(A, mid, address="New address")
    A.sync.sync_now()
    r = B.sync.sync_now()
    assert r.updated == 1 and B.member("Asha Rao").address == "New address"
    edit(B, mid, email="b@example.com")
    B.sync.sync_now(); A.sync.sync_now()
    assert A.member("Asha Rao").email == "b@example.com"
    assert A.member("Asha Rao").version == B.member("Asha Rao").version


def test_soft_delete_syncs_and_row_is_retained(A, B):
    mid = add_member(A)
    A.sync.sync_now(); B.sync.sync_now()
    A.run(lambda s: s.get(Member, mid).soft_delete())
    A.sync.sync_now(); B.sync.sync_now()
    b = B.run(lambda s: s.get(Member, mid))
    assert b is not None and b.deleted_at is not None
    assert B.run(lambda s: member_service.search_members(s)) == []


def test_settings_sync_but_machine_settings_stay_local(A, B):
    A.run(lambda s: settings_service.set_value(s, "gym.name", "Iron House"))
    A.run(lambda s: settings_service.set_value(s, "local.backup.dir", "C:/a-only"))
    A.sync.sync_now(); B.sync.sync_now()
    assert B.run(lambda s: settings_service.get(s, "gym.name")) == "Iron House"
    assert B.run(lambda s: settings_service.get(s, "local.backup.dir")) == ""


def test_money_records_and_relations_sync_in_dependency_order(A, B):
    def build(s):
        m = make_member(s)
        sub = make_subscription(s, m, make_plan(s), date(2026, 5, 1))
        payment_service.record_confirmed_payment(s, sub.id, "1000", "Cash")
    A.run(build)
    assert A.sync.sync_now().failed == 0
    rb = B.sync.sync_now()
    assert rb.failed == 0 and rb.downloaded == 4  # plan, member, subscription, payment
    p = B.run(lambda s: s.scalars(select(Payment)).one())
    assert p.amount == Decimal("1000.00") and p.receipt_no.startswith("REC-") and p.status == "confirmed"


def test_refund_with_self_reference_downloads_after_its_original(A, B):
    def build(s):
        m = make_member(s)
        sub = make_subscription(s, m, make_plan(s))
        p = payment_service.record_confirmed_payment(s, sub.id, "1000", "Cash")
        payment_service.refund_payment(s, p.id, "100", "goodwill")
    A.run(build)
    A.sync.sync_now()
    assert B.sync.sync_now().failed == 0
    assert B.run(lambda s: s.scalar(select(func.count()).select_from(Payment))) == 2


# ---- merging & conflicts ----------------------------------------------------------------------

def test_edits_to_different_fields_merge_automatically(A, B, cloud):
    mid = add_member(A)
    A.sync.sync_now(); B.sync.sync_now()
    edit(A, mid, address="A's address")
    edit(B, mid, email="b@example.com")
    A.sync.sync_now()
    rb = B.sync.sync_now()
    assert rb.conflicts == 0 and rb.updated == 1
    merged = B.member("Asha Rao")
    assert (merged.address, merged.email) == ("A's address", "b@example.com")
    A.sync.sync_now()
    a = A.member("Asha Rao")
    assert (a.address, a.email) == ("A's address", "b@example.com")
    assert cloud.members.find_one()["data"]["email"] == "b@example.com"
    assert pending(A) == 0 and pending(B) == 0


def test_same_field_edited_on_both_is_a_conflict_and_nothing_is_overwritten(A, B, cloud):
    mid = add_member(A)
    A.sync.sync_now(); B.sync.sync_now()
    edit(A, mid, address="Address from A")
    edit(B, mid, address="Address from B")
    A.sync.sync_now()
    rb = B.sync.sync_now()
    assert (rb.status, rb.conflicts) == ("partial", 1)
    assert B.member("Asha Rao").address == "Address from B"           # local value untouched
    assert B.member("Asha Rao").sync_status == "conflict"
    assert cloud.members.find_one()["data"]["address"] == "Address from A"  # cloud untouched
    conflicts = B.run(sy.open_conflicts)
    assert len(conflicts) == 1 and conflicts[0].conflicting_fields == ["address"] and not conflicts[0].is_financial
    # syncing again neither duplicates the conflict nor pushes the frozen record
    B.sync.sync_now(); B.sync.sync_now()
    assert len(B.run(sy.open_conflicts)) == 1
    assert cloud.members.find_one()["data"]["address"] == "Address from A"


def test_resolving_in_favour_of_cloud(A, B):
    mid = add_member(A)
    A.sync.sync_now(); B.sync.sync_now()
    edit(A, mid, address="A"); edit(B, mid, address="B")
    A.sync.sync_now(); B.sync.sync_now()
    cid = B.run(sy.open_conflicts)[0].id
    B.run(lambda s: sy.resolve_conflict(s, cid, {"address": "remote"}, resolved_by="owner"))
    assert B.member("Asha Rao").address == "A" and B.member("Asha Rao").sync_status == "synced"
    assert pending(B) == 0 and B.run(sy.open_conflicts) == []
    done = B.run(lambda s: s.get(SyncConflict, cid))
    assert (done.status, done.resolution, done.resolved_by) == ("resolved", "remote", "owner")


def test_resolving_in_favour_of_this_pc_uploads_it(A, B, cloud):
    mid = add_member(A)
    A.sync.sync_now(); B.sync.sync_now()
    edit(A, mid, address="A"); edit(B, mid, address="B")
    A.sync.sync_now(); B.sync.sync_now()
    B.run(lambda s: sy.resolve_conflict(s, s.scalars(select(SyncConflict)).one().id, {"address": "local"}))
    assert B.sync.sync_now().status == "success"
    assert cloud.members.find_one()["data"]["address"] == "B"
    A.sync.sync_now()
    assert A.member("Asha Rao").address == "B" and A.member("Asha Rao").sync_status == "synced"


def test_resolving_with_a_manual_value(A, B, cloud):
    mid = add_member(A)
    A.sync.sync_now(); B.sync.sync_now()
    edit(A, mid, address="A"); edit(B, mid, address="B")
    A.sync.sync_now(); B.sync.sync_now()
    cid = B.run(sy.open_conflicts)[0].id
    B.run(lambda s: sy.resolve_conflict(s, cid, {"address": {"value": "Both agreed"}}))
    B.sync.sync_now(); A.sync.sync_now()
    assert A.member("Asha Rao").address == "Both agreed" == cloud.members.find_one()["data"]["address"]


def test_resolve_validation(A, B):
    mid = add_member(A)
    A.sync.sync_now(); B.sync.sync_now()
    edit(A, mid, address="A", email="a@x.com"); edit(B, mid, address="B", email="b@x.com")
    A.sync.sync_now(); B.sync.sync_now()
    cid = B.run(sy.open_conflicts)[0].id
    with pytest.raises(ValidationError):
        B.run(lambda s: sy.resolve_conflict(s, cid, {"address": "local"}))   # email undecided
    with pytest.raises(ValidationError):
        B.run(lambda s: sy.resolve_conflict(s, cid, {"address": "local", "email": "bogus"}))
    B.run(lambda s: sy.resolve_conflict(s, cid, {"address": "local", "email": "remote"}))
    with pytest.raises(BusinessRuleError):
        B.run(lambda s: sy.resolve_conflict(s, cid, {"address": "local", "email": "remote"}))


def test_payment_conflict_is_flagged_financial_and_never_silently_overwritten(A, B, cloud):
    def build(s):
        sub = make_subscription(s, make_member(s), make_plan(s))
        return payment_service.record_confirmed_payment(s, sub.id, "1000", "Cash").id
    pid = A.run(build)
    A.sync.sync_now(); B.sync.sync_now()
    A.run(lambda s: setattr(s.get(Payment, pid), "reference", "A-REF"))
    B.run(lambda s: setattr(s.get(Payment, pid), "reference", "B-REF"))
    A.sync.sync_now()
    rb = B.sync.sync_now()
    assert rb.conflicts == 1
    conflict = B.run(sy.open_conflicts)[0]
    assert conflict.is_financial and conflict.entity_table == "payments"
    assert B.run(lambda s: s.get(Payment, pid)).reference == "B-REF"
    assert cloud.payments.find_one()["data"]["reference"] == "A-REF"
    from sqlalchemy import select as sel
    from database.models import AuditLog
    assert "sync.conflict" in B.run(lambda s: set(s.scalars(sel(AuditLog.action))))


# ---- failures, interruption, retry -------------------------------------------------------------

def test_unreachable_cloud_loses_nothing_and_next_sync_recovers(A, B, cloud, monkeypatch):
    add_member(A, "One", "9000000001"); add_member(A, "Two", "9000000002"); add_member(A, "Three", "9000000003")
    real_insert, calls = A.sync.remote.insert, {"n": 0}

    def flaky(table, key, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ServerSelectionTimeoutError("connection lost")
        return real_insert(table, key, data)

    monkeypatch.setattr(A.sync.remote, "insert", flaky)
    r = A.sync.sync_now()
    assert r.status == "failed" and "unreachable" in r.error.lower()
    assert cloud.members.count_documents({}) == 1 and pending(A) == 2  # first uploaded, two still queued
    assert A.run(lambda s: s.scalar(select(func.count()).select_from(Member))) == 3  # nothing lost locally
    r2 = A.sync.sync_now()
    assert (r2.status, r2.uploaded, r2.failed) == ("success", 2, 0)
    assert cloud.members.count_documents({}) == 3 and pending(A) == 0
    B.sync.sync_now()
    assert B.run(lambda s: s.scalar(select(func.count()).select_from(Member))) == 3


def test_ping_failure_reports_failed_without_touching_data(A, monkeypatch):
    add_member(A)

    def down():
        raise ServerSelectionTimeoutError("no route to mongodb+srv://user:secret@cluster0.example.net")

    monkeypatch.setattr(A.sync.remote, "ping", down)
    r = A.sync.sync_now()
    assert r.status == "failed" and "secret" not in r.error and pending(A) == 1
    last = A.run(lambda s: sy.list_history(s))[0]
    assert last.status == "failed" and "secret" not in (last.error or "")


def test_tls_handshake_rejection_preserves_queue_and_sync_recovers(A, cloud, monkeypatch):
    add_member(A)
    real_ping = A.sync.remote.ping

    def rejected():
        raise ServerSelectionTimeoutError("SSL handshake failed: TLSV1_ALERT_INTERNAL_ERROR")

    monkeypatch.setattr(A.sync.remote, "ping", rejected)
    report = A.sync.sync_now()
    assert report.status == "failed" and "current public IP" in report.error
    assert pending(A) == 1 and cloud.members.count_documents({}) == 0
    monkeypatch.setattr(A.sync.remote, "ping", real_ping)
    recovered = A.sync.sync_now()
    assert recovered.status == "success" and recovered.uploaded == 1
    assert pending(A) == 0 and cloud.members.count_documents({}) == 1


def test_crash_after_cloud_write_is_recovered_without_duplicates(A, cloud):
    mid = add_member(A)
    member = A.member("Asha Rao")
    # simulate: the upload reached the cloud, but the app died before marking the queue row done
    from database.serialization import serialize
    A.sync.remote.insert("members", str(mid), A.run(lambda s: serialize(s.get(Member, mid))))
    assert pending(A) == 1
    r = A.sync.sync_now()
    assert (r.uploaded, r.failed, r.conflicts) == (0, 0, 0) and pending(A) == 0
    assert cloud.members.count_documents({}) == 1 and A.member("Asha Rao").sync_status == "synced"


def test_one_bad_record_does_not_block_the_rest_and_can_be_retried(A, cloud, monkeypatch):
    bad = add_member(A, "Bad", "9000000001")
    add_member(A, "Good", "9000000002")
    real = A.sync.remote.insert

    def picky(table, key, data):
        if key == str(bad):
            raise ValueError("document rejected")
        return real(table, key, data)

    monkeypatch.setattr(A.sync.remote, "insert", picky)
    r = A.sync.sync_now()
    assert (r.status, r.uploaded, r.failed) == ("partial", 1, 1) and "Bad" not in r.failures[0]
    for _ in range(sy.MAX_ATTEMPTS):
        A.sync.sync_now()
    failed = A.run(lambda s: [(q.status, q.attempts) for q in sy.failed_queue(s)])
    assert failed == [("failed", sy.MAX_ATTEMPTS)]
    assert A.sync.sync_now().failed == 0          # permanently failed rows are parked, not hammered
    monkeypatch.setattr(A.sync.remote, "insert", real)   # the problem is fixed
    assert A.run(sy.retry_failed) == 1
    r = A.sync.sync_now()
    assert (r.status, r.uploaded) == ("success", 1) and cloud.members.count_documents({}) == 2


def test_edit_made_during_upload_is_not_lost(A, cloud, monkeypatch):
    mid = add_member(A)
    real = A.sync.remote.insert

    def insert_then_user_edits(table, key, data):
        rev = real(table, key, data)
        edit(A, mid, address="typed while syncing")  # owner keeps working during the sync
        return rev

    monkeypatch.setattr(A.sync.remote, "insert", insert_then_user_edits)
    A.sync.sync_now()
    assert A.member("Asha Rao").sync_status == "pending" and pending(A) == 1
    monkeypatch.setattr(A.sync.remote, "insert", real)
    A.sync.sync_now()
    assert cloud.members.find_one()["data"]["address"] == "typed while syncing" and pending(A) == 0


def test_sync_is_incremental(A, B, monkeypatch):
    add_member(A)
    A.sync.sync_now(); B.sync.sync_now()
    assert B.run(lambda s: sy._meta_get(s, "pull_cursor")) is not None
    seen = []
    real = B.sync.remote.changed_since
    monkeypatch.setattr(B.sync.remote, "changed_since", lambda t, since: seen.append(since) or real(t, since))
    B.sync.sync_now()
    assert seen and all(s is not None for s in seen)


def test_history_counts_and_last_success(A, B):
    add_member(A, "One", "9000000001"); add_member(A, "Two", "9000000002")
    A.sync.sync_now()
    entry = A.run(lambda s: sy.list_history(s))[0]
    assert (entry.status, entry.uploaded, entry.downloaded, entry.failed) == ("success", 2, 0, 0)
    assert entry.finished_at is not None and A.run(sy.last_success) is not None
    B.sync.sync_now()
    assert B.run(lambda s: sy.list_history(s))[0].downloaded == 2


def test_progress_is_reported(A):
    add_member(A)
    events = []
    A.sync.progress = lambda done, total, msg: events.append((done, total, msg))
    A.sync.sync_now()
    assert events[-1][2] == "Done" and events[-1][0] == events[-1][1] >= 1


# ---- two PCs issuing the same member ID offline ---------------------------------------------------

def test_duplicate_member_codes_from_two_offline_pcs_are_renumbered(A, B):
    a_id = add_member(A, "Alice", "9000000001")      # both PCs hand out GYM-00001 while offline
    b_id = add_member(B, "Bob", "9000000002")
    assert A.member("Alice").member_code == B.member("Bob").member_code == "GYM-00001"
    A.sync.sync_now()
    rb = B.sync.sync_now()
    assert rb.failed == 0 and rb.downloaded == 1
    A.sync.sync_now(); B.sync.sync_now(); A.sync.sync_now()
    for dev in (A, B):
        codes = dev.run(lambda s: sorted(s.scalars(select(Member.member_code))))
        assert len(codes) == 2 and len(set(codes)) == 2
    assert A.member("Alice").member_code == B.member("Alice").member_code
    assert A.member("Bob").member_code == B.member("Bob").member_code
    assert pending(A) == 0 and pending(B) == 0


# ---- default plans / settings created independently on two PCs ------------------------------------

def _live_plans(dev):
    return dev.run(lambda s: sorted((p.name, str(p.id), str(p.price)) for p in s.scalars(
        select(MembershipPlan).where(MembershipPlan.deleted_at.is_(None)))))


def test_same_plans_and_settings_created_on_two_pcs_converge_without_errors(A, B):
    from database.models import GymSetting, Subscription

    A.run(lambda s: plan_service.ensure_default_plans(s))      # the established PC (older rows)
    A.run(lambda s: settings_service.set_value(s, "gym.name", "Energize Fitness"))
    A.run(lambda s: plan_service.update_plan(s, s.scalars(select(MembershipPlan).where(
        MembershipPlan.name == "Monthly")).one().id, price="1500"))
    A.sync.sync_now()
    B.run(lambda s: plan_service.ensure_default_plans(s))      # a new PC: its own defaults, newer
    B.run(lambda s: settings_service.set_value(s, "gym.name", "My Gym"))

    def sub_on_b(s):
        member = member_service.register_member(s, full_name="Neha", phone="9000000009")
        plan = s.scalars(select(MembershipPlan).where(MembershipPlan.name == "Quarterly")).one()
        return make_subscription(s, member, plan).id
    sub_id = B.run(sub_on_b)

    for _ in range(2):
        for dev in (B, A):
            r = dev.sync.sync_now()
            assert r.failed == 0 and r.conflicts == 0, r.failures
    assert _live_plans(A) == _live_plans(B) and len(_live_plans(B)) == 4
    assert ("Monthly", ) == tuple(p[0] for p in _live_plans(B) if p[2] == "1500.00")
    for dev in (A, B):
        assert dev.run(lambda s: settings_service.get(s, "gym.name")) == "Energize Fitness"
        assert dev.run(lambda s: s.scalar(select(func.count()).select_from(GymSetting).where(
            GymSetting.key == "gym.name"))) == 1
    quarterly_a = A.run(lambda s: s.scalars(select(MembershipPlan.id).where(
        MembershipPlan.name == "Quarterly", MembershipPlan.deleted_at.is_(None))).one())
    for dev in (A, B):
        assert dev.run(lambda s: s.get(Subscription, sub_id).plan_id) == quarterly_a
    assert pending(A) == 0 and pending(B) == 0


def test_local_older_duplicate_wins_and_incoming_is_retired(A, B):
    from database.models import GymSetting

    B.run(lambda s: plan_service.create_plan(s, "Monthly", 30, "1200"))     # older, on the PC that syncs second
    B.run(lambda s: settings_service.set_value(s, "gym.phone", "B"))
    A.run(lambda s: plan_service.create_plan(s, "Monthly", 30, "999"))
    A.run(lambda s: settings_service.set_value(s, "gym.phone", "A"))
    A.sync.sync_now()
    r = B.sync.sync_now()
    assert r.failed == 0, r.failures
    A.sync.sync_now(); B.sync.sync_now()
    for dev in (A, B):
        assert [(n, p) for n, _, p in _live_plans(dev)] == [("Monthly", "1200.00")]
        assert dev.run(lambda s: settings_service.get(s, "gym.phone")) == "B"
        assert dev.run(lambda s: s.scalar(select(func.count()).select_from(GymSetting).where(
            GymSetting.key == "gym.phone"))) == 1
    assert pending(A) == 0 and pending(B) == 0


# ---- MongoDB service ------------------------------------------------------------------------------

def test_sync_runner_and_connection_tests_reuse_one_client(app_db, cloud, monkeypatch):
    from unittest.mock import Mock
    from database.connection import session_scope
    from services import sync_runner

    mongodb_service.close_client()
    factory = Mock(return_value=cloud.client)
    close = Mock()
    monkeypatch.setattr(mongodb_service, "get_uri", lambda: "mongodb://configured.example/gym")
    monkeypatch.setattr(mongodb_service, "build_client", factory)
    monkeypatch.setattr(cloud.client, "close", close)
    with session_scope() as s:
        settings_service.set_value(s, "local.mongodb.database", "gym")
    try:
        assert sync_runner.run_sync().status == "success"
        assert sync_runner.test_connection().ok
        assert sync_runner.run_sync().status == "success"
        factory.assert_called_once()
        close.assert_not_called()
    finally:
        mongodb_service.close_client()
    close.assert_called_once()


def test_sync_retries_once_on_a_fresh_client_after_a_handshake_failure(app_db, cloud, monkeypatch):
    from unittest.mock import Mock
    from pymongo.errors import ServerSelectionTimeoutError
    from database.connection import session_scope
    from services import sync_runner

    mongodb_service.close_client()
    broken = Mock()
    broken.__getitem__ = Mock(return_value=Mock(client=broken))
    broken.admin.command.side_effect = ServerSelectionTimeoutError("SSL handshake failed: TLSV1_ALERT_INTERNAL_ERROR")
    factory = Mock(side_effect=[broken, cloud.client])
    monkeypatch.setattr(mongodb_service, "get_uri", lambda: "mongodb://configured.example/gym")
    monkeypatch.setattr(mongodb_service, "build_client", factory)
    monkeypatch.setattr(mongodb_service, "RECONNECT_DELAY_S", 0)
    monkeypatch.setattr(cloud.client, "close", Mock())
    with session_scope() as s:
        settings_service.set_value(s, "local.mongodb.database", "gym")
    try:
        assert sync_runner.run_sync().status == "success"
        assert factory.call_count == 2
        broken.close.assert_called_once()
    finally:
        mongodb_service.close_client()


def test_connection_test_with_working_and_unreachable_cloud():
    ok = mongodb_service.test_connection(client=mongomock.MongoClient(), database="gym")
    assert ok.ok
    from pymongo import MongoClient
    with MongoClient("mongodb://127.0.0.1:1", serverSelectionTimeoutMS=300) as dead:
        bad = mongodb_service.test_connection(client=dead)
    assert not bad.ok and "Cannot connect" in bad.message


def test_connection_string_rules_and_redaction(monkeypatch):
    saved = {}
    monkeypatch.setattr(mongodb_service, "set_secret", lambda k, v: saved.update({k: v}))
    with pytest.raises(BusinessRuleError):
        mongodb_service.set_uri("http://not-mongo")
    with pytest.raises(BusinessRuleError, match="TLS"):
        mongodb_service.set_uri("mongodb://u:p@host/db?tls=false")
    mongodb_service.set_uri("  mongodb+srv://u:p@cluster0.x.mongodb.net/  ")
    assert saved == {"mongodb:uri": "mongodb+srv://u:p@cluster0.x.mongodb.net/"}
    assert "p@" not in mongodb_service.redact("failed mongodb+srv://u:p@cluster0.x.mongodb.net/")
    monkeypatch.setattr(mongodb_service, "get_uri", lambda: None)
    assert not mongodb_service.test_connection().ok


def test_client_always_uses_tls(monkeypatch):
    captured = {}

    class Fake:
        def __init__(self, uri, **kw):
            captured.update(kw)

    monkeypatch.setattr(mongodb_service, "MongoClient", Fake)
    mongodb_service.build_client("mongodb://u:p@h/db")
    assert captured["tls"] is True and captured["tlsCAFile"]


# ---- pure merge logic ------------------------------------------------------------------------------

def test_three_way_merge_rules():
    r = sy.three_way({"a": 1, "b": 1, "c": 1, "d": 1}, {"a": 2, "b": 1, "c": 3, "d": 1},
                     {"a": 1, "b": 2, "c": 4, "d": 1})
    assert r.conflicts == ["c"] and sorted(r.auto_merged) == ["a", "b"]
    assert (r.merged["a"], r.merged["b"], r.merged["d"]) == (2, 2, 1)
    assert sy.three_way({}, {"x": 1}, {"x": 1}).conflicts == []          # identical -> no conflict
    assert sy.three_way({}, {"x": 1}, {"x": 2}).conflicts == ["x"]       # no known base -> must ask
