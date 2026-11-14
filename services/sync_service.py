"""Two-way synchronization between local PostgreSQL and MongoDB Atlas.

Design (the guarantees are the point):

* **Outbox.** Every local change is queued in ``sync_queue`` in the *same transaction* as the change
  (see ``database/tracking.py``). Sync uploads queued entities - it never scans whole tables.
* **Local-first.** Sync only ever adds or merges; it never deletes local rows. If the cloud is
  unreachable, nothing is lost and the queue simply waits.
* **Idempotent.** Cloud writes are conditional on the document's ``rev`` (optimistic concurrency), so
  replaying an interrupted sync is safe: if the cloud already holds identical content the record is
  just marked synced.
* **Three-way merge.** For each record we keep ``sync_base`` - the content both sides last agreed on.
  A field changed on only one side is merged automatically; a field changed on both sides to
  different values is a *conflict*: nothing is overwritten, the record is frozen locally and the
  owner decides (``resolve_conflict``). Payments and other money records are never auto-overwritten.
* **Incremental pull.** Cloud documents carry a timestamp; each sync reads only documents newer than
  the stored cursor (with an overlap window; re-reading is harmless because of idempotency).
"""
from __future__ import annotations

import logging
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (GymSetting, Member, MembershipPlan, Subscription, SyncBase, SyncConflict, SyncHistory,
                             SyncMeta, SyncQueue)
from database.models.base import SYNC_CONFLICT, SYNC_PENDING, SYNC_SYNCED, utcnow
from database.serialization import business_view, decode_fields, serialize, sync_models
from database.tracking import SKIP_TRACKING, _enqueue
from services import audit_service
from services.errors import BusinessRuleError, NotFoundError, ValidationError
from services.mongodb_service import CONNECTIVITY_ERRORS, RemoteStore, connection_error, redact, to_naive_utc

log = logging.getLogger(__name__)

# Parents before children, so foreign keys always resolve while applying downloads.
SYNC_ORDER = ["membership_plans", "members", "trainers", "gym_settings", "subscriptions", "payments",
              "attendance", "trainer_attendance", "expenses"]
FINANCIAL_TABLES = {"payments", "expenses", "subscriptions", "membership_plans"}
MAX_ATTEMPTS = 5
PULL_OVERLAP = timedelta(minutes=10)
UNREACHABLE_PREFIX = "Cloud unreachable: "
RACE_RETRIES = 3

Scope = Callable[[], AbstractContextManager[Session]]
Progress = Callable[[int, int, str], None]


# ---- pure merge logic ----------------------------------------------------------------------

@dataclass(frozen=True)
class MergeResult:
    merged: dict[str, Any]
    conflicts: list[str]
    auto_merged: list[str]


def three_way(base: dict[str, Any], local: dict[str, Any], remote: dict[str, Any]) -> MergeResult:
    """Field-level merge. ``base`` is what both sides last agreed on ({} if unknown)."""
    merged: dict[str, Any] = {}
    conflicts: list[str] = []
    auto: list[str] = []
    for key in sorted(set(local) | set(remote)):
        b, l, r = base.get(key), local.get(key), remote.get(key)
        if l == r:
            merged[key] = l
        elif l == b:        # only the cloud changed it
            merged[key] = r
            auto.append(key)
        elif r == b:        # only this PC changed it
            merged[key] = l
            auto.append(key)
        else:               # both changed it, differently
            merged[key] = l
            conflicts.append(key)
    return MergeResult(merged, conflicts, auto)


@dataclass
class SyncReport:
    uploaded: int = 0     # records created in the cloud
    downloaded: int = 0   # records created locally
    updated: int = 0      # existing records changed (either side, incl. merges)
    failed: int = 0
    conflicts: int = 0
    status: str = "running"
    error: str | None = None
    failures: list[str] = field(default_factory=list)


# ---- small helpers -------------------------------------------------------------------------

def _meta_get(s: Session, key: str) -> str | None:
    row = s.get(SyncMeta, key)
    return row.value if row else None


def _meta_set(s: Session, key: str, value: str | None) -> None:
    row = s.get(SyncMeta, key)
    if row is None:
        s.add(SyncMeta(key=key, value=value))
    else:
        row.value = value


def get_device_id(scope: Scope) -> str:
    with scope() as s:
        device = _meta_get(s, "device_id")
        if not device:
            device = uuid.uuid4().hex
            _meta_set(s, "device_id", device)
        return device


def _set_base(s: Session, table: str, entity_id: uuid.UUID, rev: int, snapshot: dict) -> None:
    row = s.get(SyncBase, (table, entity_id))
    if row is None:
        s.add(SyncBase(entity_table=table, entity_id=entity_id, remote_rev=rev, snapshot=snapshot,
                       updated_at=utcnow()))
    else:
        row.remote_rev, row.snapshot, row.updated_at = rev, snapshot, utcnow()


def _has_pending(s: Session, table: str, entity_id: uuid.UUID) -> bool:
    return s.scalars(select(SyncQueue.id).where(
        SyncQueue.entity_table == table, SyncQueue.entity_id == entity_id,
        SyncQueue.status == "pending")).first() is not None


def _rank(created_at: datetime | None, entity_id: uuid.UUID) -> tuple[datetime, str]:
    """Deterministic order for duplicates: the oldest record wins, the id breaks ties.
    Every PC computes the same winner, so duplicates converge instead of flip-flopping."""
    when = created_at or datetime.max.replace(tzinfo=timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc), str(entity_id)


def _natural_key_clash(s: Session, model: type, values: dict, entity_id: uuid.UUID) -> Any:
    """A different local row holding the same unique business key (e.g. default plans or settings
    created independently on two PCs before their first sync)."""
    if model is GymSetting:
        return s.scalars(select(GymSetting).where(GymSetting.key == values.get("key"),
                                                  GymSetting.id != entity_id)).first()
    if model is MembershipPlan and values.get("deleted_at") is None:
        return s.scalars(select(MembershipPlan).where(MembershipPlan.name == values.get("name"),
                                                      MembershipPlan.deleted_at.is_(None),
                                                      MembershipPlan.id != entity_id)).first()
    return None


def _mark_changed(s: Session, obj: Any, operation: str) -> None:
    """Queue a change made by the sync engine itself (automatic tracking is off while applying downloads)."""
    obj.version = (obj.version or 1) + 1
    obj.updated_at = utcnow()
    if obj.sync_status != SYNC_CONFLICT:
        obj.sync_status = SYNC_PENDING
    _enqueue(s, obj, operation)


class _Race(Exception):
    """The cloud document changed between our read and our conditional write."""


# ---- the engine ----------------------------------------------------------------------------

class SyncEngine:
    def __init__(self, scope: Scope, database: Any, progress: Progress | None = None):
        self.scope = scope
        self.remote = RemoteStore(database, get_device_id(scope))
        self.models = sync_models()
        self.progress = progress or (lambda done, total, msg: None)

    # -- public ------------------------------------------------------------------------------

    def sync_now(self, trigger: str = "manual") -> SyncReport:
        report = SyncReport()
        history_id = self._start_history(trigger)
        started = utcnow()
        try:
            self.remote.ping()
            self.remote.ensure_indexes(SYNC_ORDER)
            cursor = self._cursor()
            incoming = [(t, d) for t in SYNC_ORDER for d in self._sorted(self.remote.changed_since(t, cursor))]
            done, total = 0, len(incoming)
            for table, doc in incoming:
                self.progress(done, total, f"Downloading {table}")
                self._pull_one(table, doc, report)
                done += 1
            entities = self._pending_entities()
            total = done + len(entities)
            for table, entity_id in entities:
                self.progress(done, total, f"Uploading {table}")
                self._push_one(table, entity_id, report)
                done += 1
            if report.failed == 0:
                self._set_cursor(started - PULL_OVERLAP)
            report.status = "success" if not (report.failed or report.conflicts) else "partial"
            self.progress(total, total, "Done")
        except CONNECTIVITY_ERRORS as exc:
            report.status, report.error = "failed", f"{UNREACHABLE_PREFIX}{connection_error(exc)}"
            log.warning("Sync aborted, cloud unreachable: %s", report.error)
        except Exception as exc:  # noqa: BLE001 - recorded, never raised into the UI thread
            report.status, report.error = "failed", redact(f"{type(exc).__name__}: {exc}")[:500]
            log.exception("Sync failed")
        finally:
            self._finish_history(history_id, report)
        return report

    # -- history / cursor --------------------------------------------------------------------

    def _start_history(self, trigger: str) -> uuid.UUID:
        with self.scope() as s:
            entry = SyncHistory(started_at=utcnow(), trigger=trigger, status="running")
            s.add(entry)
            s.flush()
            return entry.id

    def _finish_history(self, history_id: uuid.UUID, report: SyncReport) -> None:
        with self.scope() as s:
            entry = s.get(SyncHistory, history_id)
            entry.finished_at, entry.status = utcnow(), report.status
            entry.uploaded, entry.downloaded, entry.updated = report.uploaded, report.downloaded, report.updated
            entry.failed, entry.conflicts, entry.error = report.failed, report.conflicts, report.error

    def _cursor(self) -> datetime | None:
        with self.scope() as s:
            value = _meta_get(s, "pull_cursor")
            return datetime.fromisoformat(value) if value else None

    def _set_cursor(self, when: datetime) -> None:
        with self.scope() as s:
            _meta_set(s, "pull_cursor", when.isoformat())

    @staticmethod
    def _sorted(docs: list[dict]) -> list[dict]:
        return sorted(docs, key=lambda d: (str(d.get("data", {}).get("created_at", "")), d["_id"]))

    def _pending_entities(self) -> list[tuple[str, uuid.UUID]]:
        with self.scope() as s:
            rows = s.execute(select(SyncQueue.entity_table, SyncQueue.entity_id, func.min(SyncQueue.created_at))
                             .where(SyncQueue.status == "pending")
                             .group_by(SyncQueue.entity_table, SyncQueue.entity_id)).all()
        order = {t: i for i, t in enumerate(SYNC_ORDER)}
        return [(t, e) for t, e, _ in sorted(rows, key=lambda r: (order.get(r[0], 99), r[2]))
                if t in order]

    # -- download ----------------------------------------------------------------------------

    def _pull_one(self, table: str, doc: dict, report: SyncReport) -> None:
        model, rdata, rrev = self.models[table], doc["data"], doc["_sync"]["rev"]
        entity_id = uuid.UUID(doc["_id"])
        if table == "gym_settings" and str(rdata.get("key", "")).startswith("local."):
            return
        with self.scope() as s:
            s.info[SKIP_TRACKING] = True
            try:
                with s.begin_nested():
                    obj, base = s.get(model, entity_id), s.get(SyncBase, (table, entity_id))
                    if obj is None:
                        if self._create_local(s, model, table, entity_id, rdata, rrev):
                            report.downloaded += 1
                    elif base is not None and base.remote_rev >= rrev:
                        return  # already have this revision
                    elif obj.sync_status == SYNC_SYNCED and not _has_pending(s, table, entity_id):
                        self._overwrite_local(s, obj, table, rdata, rrev)
                        report.updated += 1
                    # else: this PC has its own pending changes; merged in the upload phase
            except Exception as exc:  # noqa: BLE001
                report.failed += 1
                msg = f"download {table} {doc['_id'][:8]}: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
                report.failures.append(msg)
                log.warning("Sync %s", msg)
                audit_service.log(s, "sync.apply_failed", None, {"table": table, "id": doc["_id"], "error": msg},
                                  source="sync")

    def _create_local(self, s: Session, model: type, table: str, entity_id: uuid.UUID, rdata: dict,
                      rev: int) -> bool:
        """Create a downloaded row. Returns False if it was a duplicate that was intentionally not stored."""
        values = decode_fields(model, rdata)
        clash = _natural_key_clash(s, model, values, entity_id)
        if clash is not None:
            return self._create_duplicate(s, model, table, entity_id, values, rdata, rev, clash)
        renumbered = False
        if model is Member:
            clash = s.scalars(select(Member.id).where(Member.member_code == values["member_code"],
                                                      Member.id != entity_id)).first()
            if clash:  # two PCs issued the same member ID while offline: renumber the incoming one
                from services import member_service

                values["member_code"] = member_service.next_member_code(s)
                renumbered = True
        obj = model(**values, sync_status=SYNC_SYNCED, last_synced_at=utcnow())
        s.add(obj)
        s.flush()
        _set_base(s, table, entity_id, rev, rdata)
        if renumbered:
            obj.version, obj.sync_status = (obj.version or 1) + 1, SYNC_PENDING
            _enqueue(s, obj, "update")  # uploads the new member code on this sync
            audit_service.log(s, "sync.member_code_renumbered", obj,
                              {"original": rdata["member_code"], "new": obj.member_code}, source="sync")
        return True

    def _create_duplicate(self, s: Session, model: type, table: str, entity_id: uuid.UUID, values: dict,
                          rdata: dict, rev: int, local: Any) -> bool:
        """Two PCs created the same plan name / setting key independently. Keep the oldest everywhere."""
        incoming_wins = _rank(values.get("created_at"), entity_id) < _rank(local.created_at, local.id)
        if model is GymSetting:
            if not incoming_wins:
                audit_service.log(s, "sync.duplicate_ignored", local,
                                  {"key": local.key, "ignored_id": str(entity_id)}, source="sync")
                return False
            # The key is unique even for deleted rows, so the losing local row is removed;
            # its value is kept in the audit log.
            audit_service.log(s, "sync.duplicate_replaced", local,
                              {"key": local.key, "replaced_value": local.value, "kept_id": str(entity_id)},
                              source="sync")
            for row in s.scalars(select(SyncQueue).where(SyncQueue.entity_table == table,
                                                         SyncQueue.entity_id == local.id,
                                                         SyncQueue.status == "pending")):
                row.status, row.processed_at = "done", utcnow()
            base = s.get(SyncBase, (table, local.id))
            if base is not None:
                s.delete(base)
            s.delete(local)
            s.flush()
            self._insert_downloaded(s, model, table, entity_id, values, rdata, rev)
            return True
        # Membership plans: the loser stays as a retired row so subscriptions that reference it remain
        # valid. Its deleted_at is its own created_at, so every PC writes identical content.
        if incoming_wins:
            local.deleted_at = local.created_at
            _mark_changed(s, local, "delete")
            s.flush()
            self._insert_downloaded(s, model, table, entity_id, values, rdata, rev)
            moved = 0
            for sub in s.scalars(select(Subscription).where(Subscription.plan_id == local.id)):
                sub.plan_id = entity_id
                _mark_changed(s, sub, "update")
                moved += 1
            s.flush()
            audit_service.log(s, "sync.duplicate_plan_merged", local,
                              {"name": local.name, "kept_id": str(entity_id), "subscriptions_moved": moved,
                               "retired_price": str(local.price)}, source="sync")
        else:
            values["deleted_at"] = values.get("created_at")
            obj = self._insert_downloaded(s, model, table, entity_id, values, rdata, rev)
            _mark_changed(s, obj, "delete")
            s.flush()
            audit_service.log(s, "sync.duplicate_plan_merged", obj,
                              {"name": obj.name, "kept_id": str(local.id)}, source="sync")
        return True

    def _insert_downloaded(self, s: Session, model: type, table: str, entity_id: uuid.UUID, values: dict,
                           rdata: dict, rev: int) -> Any:
        obj = model(**values, sync_status=SYNC_SYNCED, last_synced_at=utcnow())
        s.add(obj)
        s.flush()
        _set_base(s, table, entity_id, rev, rdata)
        return obj

    def _overwrite_local(self, s: Session, obj: Any, table: str, rdata: dict, rev: int) -> None:
        for key, value in decode_fields(type(obj), rdata).items():
            if key not in ("id", "created_at"):
                setattr(obj, key, value)
        obj.sync_status, obj.last_synced_at = SYNC_SYNCED, utcnow()
        s.flush()
        _set_base(s, table, obj.id, rev, rdata)

    # -- upload ------------------------------------------------------------------------------

    def _push_one(self, table: str, entity_id: uuid.UUID, report: SyncReport) -> None:
        model = self.models[table]
        with self.scope() as s:
            s.info[SKIP_TRACKING] = True
            obj = s.get(model, entity_id)
            queued = list(s.scalars(select(SyncQueue).where(
                SyncQueue.entity_table == table, SyncQueue.entity_id == entity_id, SyncQueue.status == "pending")))
            if not queued:
                return
            if obj is None:
                for row in queued:
                    row.status, row.processed_at = "done", utcnow()
                return
            if obj.sync_status == SYNC_CONFLICT:
                return  # frozen until the owner resolves the conflict
            try:
                with s.begin_nested():
                    self._reconcile(s, model, table, obj, report)
            except CONNECTIVITY_ERRORS:
                raise
            except Exception as exc:  # noqa: BLE001 - this record fails, the rest carry on
                report.failed += 1
                msg = f"upload {table} {str(entity_id)[:8]}: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
                report.failures.append(msg)
                log.warning("Sync %s", msg)
                for row in s.scalars(select(SyncQueue).where(
                        SyncQueue.entity_table == table, SyncQueue.entity_id == entity_id,
                        SyncQueue.status == "pending")):
                    row.attempts += 1
                    row.last_error = msg[:500]
                    if row.attempts >= MAX_ATTEMPTS:
                        row.status = "failed"

    def _reconcile(self, s: Session, model: type, table: str, obj: Any, report: SyncReport) -> None:
        entity_id, key = obj.id, str(obj.id)
        for _ in range(RACE_RETRIES):
            try:
                payload, remote = serialize(obj), self.remote.get(table, key)
                base = s.get(SyncBase, (table, entity_id))
                if remote is None:
                    rev = self.remote.insert(table, key, payload)
                    if rev is None:
                        raise _Race
                    self._finish(s, obj, table, rev, payload, payload["version"])
                    report.uploaded += 1
                    return
                rdata, rrev = remote["data"], remote["_sync"]["rev"]
                if business_view(rdata) == business_view(payload):  # cloud already has this content
                    self._finish(s, obj, table, rrev, rdata, payload["version"])
                    return
                if base is not None and base.remote_rev == rrev:     # cloud unchanged: fast-forward
                    new_rev = self.remote.update_if_rev(table, key, rrev, payload)
                    if new_rev is None:
                        raise _Race
                    self._finish(s, obj, table, new_rev, payload, payload["version"])
                    report.updated += 1
                    return
                self._merge(s, model, table, obj, payload, rdata, rrev, base, report)
                return
            except _Race:
                continue
        raise BusinessRuleError("The cloud record kept changing during upload; it will be retried next sync.")

    def _merge(self, s: Session, model: type, table: str, obj: Any, payload: dict, rdata: dict, rrev: int,
               base: SyncBase | None, report: SyncReport) -> None:
        base_view = business_view(base.snapshot) if base is not None else {}
        result = three_way(base_view, business_view(payload), business_view(rdata))
        if result.conflicts:
            self._open_conflict(s, table, obj, payload, rdata, rrev, base, result)
            report.conflicts += 1
            return
        for key, value in decode_fields(model, result.merged).items():
            if key != "id" and getattr(obj, key) != value:
                setattr(obj, key, value)
        obj.version = max(obj.version or 1, int(rdata.get("version", 1))) + 1
        obj.updated_at = utcnow()
        s.flush()
        merged_payload = serialize(obj)
        new_rev = self.remote.update_if_rev(table, str(obj.id), rrev, merged_payload)
        if new_rev is None:
            raise _Race
        self._finish(s, obj, table, new_rev, merged_payload, merged_payload["version"])
        report.updated += 1
        audit_service.log(s, "sync.merged", obj, {"fields_merged": result.auto_merged}, source="sync")

    def _open_conflict(self, s: Session, table: str, obj: Any, payload: dict, rdata: dict, rrev: int,
                       base: SyncBase | None, result: MergeResult) -> None:
        conflict = s.scalars(select(SyncConflict).where(
            SyncConflict.entity_table == table, SyncConflict.entity_id == obj.id,
            SyncConflict.status == "open")).first()
        if conflict is None:
            conflict = SyncConflict(entity_table=table, entity_id=obj.id, local_snapshot=payload,
                                    remote_snapshot=rdata, conflicting_fields=[])
            s.add(conflict)
        conflict.is_financial = table in FINANCIAL_TABLES
        conflict.remote_rev = rrev
        conflict.base_snapshot = base.snapshot if base is not None else None
        conflict.local_snapshot, conflict.remote_snapshot = payload, rdata
        conflict.conflicting_fields, conflict.auto_merged_fields = result.conflicts, result.auto_merged
        obj.sync_status = SYNC_CONFLICT
        audit_service.log(s, "sync.conflict", obj, {"fields": result.conflicts}, source="sync")

    def _finish(self, s: Session, obj: Any, table: str, rev: int, snapshot: dict, pushed_version: int) -> None:
        """Cloud and this PC now agree on ``snapshot`` as of ``pushed_version``."""
        _set_base(s, table, obj.id, rev, snapshot)
        for row in s.scalars(select(SyncQueue).where(
                SyncQueue.entity_table == table, SyncQueue.entity_id == obj.id,
                SyncQueue.status == "pending", SyncQueue.entity_version <= pushed_version)):
            row.status, row.processed_at, row.last_error = "done", utcnow(), None
        s.flush()
        s.refresh(obj)
        if obj.version == pushed_version:  # no newer local edit slipped in while we were uploading
            obj.sync_status, obj.last_synced_at = SYNC_SYNCED, utcnow()


# ---- owner actions & queries ----------------------------------------------------------------

def pending_count(session: Session) -> int:
    return session.scalar(select(func.count(func.distinct(SyncQueue.entity_id))).where(
        SyncQueue.status == "pending")) or 0


def failed_queue(session: Session) -> list[SyncQueue]:
    return list(session.scalars(select(SyncQueue).where(
        (SyncQueue.status == "failed") | ((SyncQueue.status == "pending") & (SyncQueue.attempts > 0)))
        .order_by(SyncQueue.created_at)))


def retry_failed(session: Session) -> int:
    """Put every failed upload back in the queue with a fresh attempt counter."""
    count = 0
    for row in session.scalars(select(SyncQueue).where(SyncQueue.status == "failed")):
        row.status, row.attempts, row.last_error = "pending", 0, None
        count += 1
    session.flush()
    return count


def last_success(session: Session) -> datetime | None:
    return session.scalar(select(func.max(SyncHistory.finished_at)).where(SyncHistory.status == "success"))


def list_history(session: Session, limit: int = 100) -> list[SyncHistory]:
    return list(session.scalars(select(SyncHistory).order_by(SyncHistory.started_at.desc()).limit(limit)))


def open_conflicts(session: Session) -> list[SyncConflict]:
    return list(session.scalars(select(SyncConflict).where(SyncConflict.status == "open")
                                .order_by(SyncConflict.created_at)))


def resolve_conflict(session: Session, conflict_id: uuid.UUID, choices: dict[str, Any],
                     resolved_by: str | None = None) -> SyncConflict:
    """Owner decision. ``choices[field]`` is ``"local"``, ``"remote"`` or ``{"value": <manual value>}``.

    The record then carries the decided content and is uploaded on the next sync (on top of the
    cloud revision the owner just looked at, so it cannot silently clobber newer cloud edits).
    """
    from services.session_context import get_actor

    conflict = session.get(SyncConflict, conflict_id)
    if conflict is None:
        raise NotFoundError("Conflict not found.")
    if conflict.status != "open":
        raise BusinessRuleError("This conflict has already been resolved.")
    missing = [f for f in conflict.conflicting_fields if f not in choices]
    if missing:
        raise ValidationError(f"Choose a value for: {', '.join(missing)}")
    local, remote = business_view(conflict.local_snapshot), business_view(conflict.remote_snapshot)
    base = business_view(conflict.base_snapshot) if conflict.base_snapshot else {}
    final = three_way(base, local, remote).merged
    for name in conflict.conflicting_fields:
        pick = choices[name]
        if pick == "local":
            final[name] = local.get(name)
        elif pick == "remote":
            final[name] = remote.get(name)
        elif isinstance(pick, dict) and "value" in pick:
            final[name] = pick["value"]
        else:
            raise ValidationError(f"Invalid choice for {name}.")
    model = sync_models()[conflict.entity_table]
    obj = session.get(model, conflict.entity_id)
    if obj is None:
        raise NotFoundError("The conflicting record no longer exists locally.")
    _set_base(session, conflict.entity_table, obj.id, conflict.remote_rev, conflict.remote_snapshot)
    obj.version = max(obj.version or 1, int(conflict.remote_snapshot.get("version", 1)))
    for key, value in decode_fields(model, final).items():
        if key != "id" and getattr(obj, key) != value:
            setattr(obj, key, value)
    obj.sync_status = SYNC_PENDING
    session.flush()  # change tracking bumps the version and queues the upload
    if business_view(serialize(obj)) == remote:  # owner accepted the cloud version entirely
        obj.sync_status, obj.last_synced_at = SYNC_SYNCED, utcnow()
        for row in session.scalars(select(SyncQueue).where(
                SyncQueue.entity_table == conflict.entity_table, SyncQueue.entity_id == obj.id,
                SyncQueue.status == "pending")):
            row.status, row.processed_at = "done", utcnow()
    conflict.status, conflict.resolved_at = "resolved", utcnow()
    conflict.resolved_by = resolved_by or get_actor()
    conflict.resolution = ("local" if all(v == "local" for v in choices.values()) else
                           "remote" if all(v == "remote" for v in choices.values()) else "manual")
    conflict.resolved_value = {k: v for k, v in final.items() if k in conflict.conflicting_fields}
    session.flush()
    audit_service.log(session, "sync.conflict_resolved", obj,
                      {"resolution": conflict.resolution, "fields": conflict.conflicting_fields})
    return conflict
