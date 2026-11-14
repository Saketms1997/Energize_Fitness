"""Runs a set of backups and records them. Used by the Backup page and the scheduler.

Each call manages its own transactions (it is meant to be called from a worker thread).
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config.settings import get_settings
from database.connection import dispose_engine, session_scope
from services import audit_service, backup_service as bs, clock, mdb_export_service, settings_service
from services.backup_service import BackupResult, DbTarget

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Outcome:
    format: str
    ok: bool
    path: Path | None = None
    error: str | None = None


def configured_formats(session) -> list[str]:
    formats = [f for f in settings_service.get(session, "local.backup.formats") if f in bs.FORMATS]
    return formats or ["backup"]


def run_backups(formats: list[str], trigger: str = "manual") -> list[Outcome]:
    with session_scope() as s:
        dest = bs.default_backup_dir(s)
        keep = int(settings_service.get(s, "local.backup.retention"))
        bin_dir = settings_service.get(s, "local.pg_bin_dir") or None
    target = DbTarget.from_url(get_settings().app_url())
    outcomes: list[Outcome] = []
    for fmt in formats:
        stamp = datetime.now()
        try:
            if fmt == "mdb":
                with session_scope() as s:
                    exported = mdb_export_service.export_to_mdb(s, dest / bs.backup_filename("mdb", stamp))
                digest = hashlib.sha256(exported.path.read_bytes()).hexdigest()
                result = BackupResult(exported.path, "mdb", exported.size_bytes, digest, True)
            else:
                result = bs.create_backup(target, fmt, dest, bin_dir, bs.backup_filename(fmt, stamp))
            error = None
        except Exception as exc:  # noqa: BLE001 - recorded in history, other formats still run
            result, error = None, str(exc)
            log.error("%s backup failed: %s", fmt, exc)
        with session_scope() as s:
            bs.record_history(s, fmt, result.path if result else dest / bs.backup_filename(fmt, stamp),
                              trigger, result, error)
        outcomes.append(Outcome(fmt, result is not None, result.path if result else None, error))
    try:
        bs.apply_retention(dest, keep)
    except OSError as exc:
        log.warning("Backup retention could not remove old files: %s", exc)
    return outcomes


def restore_from_file(path: Path) -> Path:
    """Restore a .backup/.sql file over the live database. Returns the safety backup's path."""
    settings = get_settings()
    with session_scope() as s:
        bin_dir = settings_service.get(s, "local.pg_bin_dir") or None
    target = DbTarget.from_url(settings.migrator_url())   # restore needs the schema owner
    dispose_engine()  # drop pooled connections so they cannot block DROP TABLE
    safety = bs.restore_backup(path, target, settings.backup_dir / "pre_restore", bin_dir)
    dispose_engine()
    from services import db_setup_service

    db_setup_service.upgrade_schema()  # an older backup is brought up to the current schema
    with session_scope() as s:
        audit_service.log(s, "backup.restore", None, {"file": str(path), "safety_copy": str(safety)})
    return safety


def verify_file(path: Path) -> dict[str, int]:
    settings = get_settings()
    with session_scope() as s:
        bin_dir = settings_service.get(s, "local.pg_bin_dir") or None
    return bs.verify_by_restore(path, bs.format_of(path), DbTarget.from_url(settings.migrator_url()), bin_dir)


def scheduled_backup_due() -> bool:
    with session_scope() as s:
        if not settings_service.get(s, "local.backup.schedule_enabled"):
            return False
        return bs.is_due(bs.last_scheduled_success(s), clock.now(), settings_service.get(s, "local.backup.time"))


def run_scheduled() -> list[Outcome]:
    with session_scope() as s:
        formats = configured_formats(s)
    return run_backups(formats, "scheduled")
