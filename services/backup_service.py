"""PostgreSQL backup, validation, restore, retention and scheduling.

PostgreSQL ``.backup`` (custom format) is the authoritative restore path. The ``.sql`` dump is a
portable text alternative. The Access ``.mdb`` export lives in ``mdb_export_service`` and is NOT a
restorable PostgreSQL backup.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import shutil
import subprocess
import time as _time
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from sqlalchemy import Engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.connection import make_engine
from database.models import BackupHistory, Base
from services import audit_service, clock, settings_service
from services.errors import BusinessRuleError

log = logging.getLogger(__name__)

FORMATS = {"backup": ".backup", "sql": ".sql", "mdb": ".mdb"}
SUBPROCESS_TIMEOUT = 900  # seconds
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class BackupError(BusinessRuleError):
    pass


@dataclass(frozen=True)
class DbTarget:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_url(cls, url: URL) -> "DbTarget":
        return cls(url.host or "localhost", url.port or 5432, url.database or "", url.username or "",
                   url.password or "")

    def with_db(self, dbname: str) -> "DbTarget":
        return DbTarget(self.host, self.port, dbname, self.user, self.password)

    def conn_args(self) -> list[str]:
        return ["-h", self.host, "-p", str(self.port), "-U", self.user]

    def env(self) -> dict[str, str]:
        return {**os.environ, "PGPASSWORD": self.password, "PGCLIENTENCODING": "UTF8"}


@dataclass(frozen=True)
class BackupResult:
    path: Path
    format: str
    size_bytes: int
    sha256: str
    validated: bool


# ---- tools --------------------------------------------------------------------------------

def find_pg_tool(name: str, configured_dir: str | None = None) -> Path:
    exe = f"{name}.exe" if os.name == "nt" else name
    candidates: list[Path] = []
    if configured_dir:
        candidates.append(Path(configured_dir) / exe)
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    if os.name == "nt":
        roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL"]
        for root in roots:
            if root.is_dir():
                versions = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
                candidates += [v / "bin" / exe for v in versions]
    for path in candidates:
        if path.is_file():
            return path
    raise BackupError(
        f"PostgreSQL tool '{exe}' was not found. Install PostgreSQL (it includes the client tools) or set "
        "the PostgreSQL 'bin' folder in Settings > Backup.")


def _run(cmd: list[str], target: DbTarget, timeout: int = SUBPROCESS_TIMEOUT) -> subprocess.CompletedProcess:
    log.info("Running %s", Path(cmd[0]).name)
    try:
        return subprocess.run(cmd, env=target.env(), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f"{Path(cmd[0]).name} timed out after {timeout} seconds.") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tail(text_: str, limit: int = 600) -> str:
    return text_.strip()[-limit:] or "no output"


# ---- create & validate --------------------------------------------------------------------

def default_backup_dir(session: Session | None = None) -> Path:
    configured = settings_service.get(session, "local.backup.dir") if session is not None else ""
    return Path(configured) if configured else get_settings().backup_dir


def backup_filename(fmt: str, when: datetime | None = None) -> str:
    return f"gym_{(when or datetime.now()):%Y%m%d_%H%M%S}{FORMATS[fmt]}"


def create_backup(target: DbTarget, fmt: str, dest_dir: Path, bin_dir: str | None = None,
                  filename: str | None = None) -> BackupResult:
    """Dump the database to ``dest_dir`` and validate the file. The file only appears on success."""
    if fmt not in ("backup", "sql"):
        raise BackupError(f"Unsupported PostgreSQL backup format '{fmt}'.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / (filename or backup_filename(fmt))
    partial = final.with_suffix(final.suffix + ".partial")
    cmd = [str(find_pg_tool("pg_dump", bin_dir)), *target.conn_args(), "-d", target.dbname,
           "--no-owner", "--no-acl", "-f", str(partial)]
    cmd += ["-Fc"] if fmt == "backup" else ["-Fp", "--clean", "--if-exists"]
    try:
        proc = _run(cmd, target)
        if proc.returncode != 0:
            raise BackupError(f"pg_dump failed: {_tail(proc.stderr)}")
        validate_backup_file(partial, fmt, bin_dir, target)
        partial.replace(final)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return BackupResult(final, fmt, final.stat().st_size, _sha256(final), True)


def _expected_tables() -> set[str]:
    import database.models  # noqa: F401

    return set(Base.metadata.tables)


def validate_backup_file(path: Path, fmt: str, bin_dir: str | None = None,
                         target: DbTarget | None = None) -> None:
    """Cheap structural validation (no restore). Raises BackupError if the file is unusable."""
    if not path.is_file() or path.stat().st_size == 0:
        raise BackupError("Backup file is missing or empty.")
    expected = _expected_tables() - {"alembic_version"}
    if fmt == "backup":
        tool = find_pg_tool("pg_restore", bin_dir)
        proc = _run([str(tool), "--list", str(path)], target or DbTarget("localhost", 5432, "", "", ""), 120)
        if proc.returncode != 0:
            raise BackupError(f"Not a valid PostgreSQL backup: {_tail(proc.stderr)}")
        present = set(re.findall(r"TABLE DATA public (\w+)", proc.stdout))
        missing = expected - present
        if missing:
            raise BackupError(f"Backup is incomplete; tables missing: {', '.join(sorted(missing))}")
    elif fmt == "sql":
        head = path.read_bytes()[:200].decode("utf-8", "replace")
        tail = path.read_bytes()[-400:].decode("utf-8", "replace")
        if "PostgreSQL database dump" not in head or "PostgreSQL database dump complete" not in tail:
            raise BackupError("SQL dump is truncated or not a PostgreSQL dump.")
        body = path.read_text("utf-8", errors="replace")
        missing = {t for t in expected if f"CREATE TABLE public.{t} " not in body}
        if missing:
            raise BackupError(f"SQL dump is incomplete; tables missing: {', '.join(sorted(missing))}")
    else:
        raise BackupError(f"Cannot validate format '{fmt}'.")


def _row_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in sorted(_expected_tables())
                if conn.execute(text("SELECT to_regclass(:n)"), {"n": f"public.{t}"}).scalar()}


def verify_by_restore(path: Path, fmt: str, admin: DbTarget, bin_dir: str | None = None) -> dict[str, int]:
    """Strong validation: restore into a throwaway database and return its per-table row counts."""
    scratch = f"gym_verify_{secrets.token_hex(4)}"
    admin_engine = make_engine(URL.create("postgresql+psycopg", admin.user, admin.password, admin.host,
                                          admin.port, "postgres")).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch}"'))
        _restore_into(path, fmt, admin.with_db(scratch), bin_dir)
        scratch_engine = make_engine(URL.create("postgresql+psycopg", admin.user, admin.password, admin.host,
                                                admin.port, scratch))
        try:
            return _row_counts(scratch_engine)
        finally:
            scratch_engine.dispose()
    finally:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        admin_engine.dispose()


# ---- restore ------------------------------------------------------------------------------

def _restore_into(path: Path, fmt: str, target: DbTarget, bin_dir: str | None) -> None:
    if fmt == "backup":
        cmd = [str(find_pg_tool("pg_restore", bin_dir)), *target.conn_args(), "-d", target.dbname,
               "--single-transaction", "--exit-on-error", "--clean", "--if-exists", "--no-owner", "--no-acl",
               str(path)]
    elif fmt == "sql":
        cmd = [str(find_pg_tool("psql", bin_dir)), *target.conn_args(), "-d", target.dbname,
               "-v", "ON_ERROR_STOP=1", "--single-transaction", "-q", "-f", str(path)]
    else:
        raise BackupError("Only .backup and .sql files can be restored. An .mdb export is not a PostgreSQL backup.")
    proc = _run(cmd, target)
    if proc.returncode != 0:
        raise BackupError(f"Restore failed and was rolled back (database unchanged): {_tail(proc.stderr)}")


def format_of(path: Path) -> str:
    for fmt, suffix in FORMATS.items():
        if path.suffix.lower() == suffix:
            return fmt
    raise BackupError(f"Unrecognised backup file type '{path.suffix}'.")


def restore_backup(path: Path, target: DbTarget, safety_dir: Path, bin_dir: str | None = None) -> Path:
    """Restore ``path`` over ``target``. Returns the safety backup of the data that was replaced.

    Order: validate the file -> back up current data -> restore in ONE transaction. If anything
    fails the database is left exactly as it was.
    """
    fmt = format_of(path)
    validate_backup_file(path, fmt, bin_dir, target)
    safety = create_backup(target, "backup", safety_dir,
                           bin_dir, filename=f"pre_restore_{datetime.now():%Y%m%d_%H%M%S}.backup").path
    _restore_into(path, fmt, target, bin_dir)
    log.warning("Database restored from %s (previous data saved in %s)", path, safety)
    return safety


# ---- history, retention, schedule ---------------------------------------------------------

def record_history(session: Session, fmt: str, path: Path, trigger: str, result: BackupResult | None,
                   error: str | None = None) -> BackupHistory:
    entry = BackupHistory(format=fmt, path=str(path), trigger=trigger,
                          status="success" if result else "failed",
                          size_bytes=result.size_bytes if result else None,
                          sha256=result.sha256 if result else None,
                          validated=bool(result and result.validated), error=error)
    session.add(entry)
    session.flush()
    audit_service.log(session, "backup.create" if result else "backup.failed", entry,
                      {"format": fmt, "trigger": trigger, "error": error})
    return entry


def apply_retention(directory: Path, keep: int) -> list[Path]:
    """Delete the oldest backups beyond ``keep`` per format. Safety (pre_restore_) files are kept."""
    removed = []
    for fmt, suffix in FORMATS.items():
        files = sorted((p for p in directory.glob(f"gym_*{suffix}") if p.is_file()),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[max(keep, 1):]:
            old.unlink(missing_ok=True)
            removed.append(old)
    return removed


def parse_time(value: str) -> time:
    try:
        hour, minute = (int(x) for x in value.split(":"))
        return time(hour, minute)
    except ValueError as exc:
        raise BusinessRuleError(f"Backup time must look like 02:30 (got '{value}').") from exc


def is_due(last_success: datetime | None, now: datetime, at: str) -> bool:
    """True when today's scheduled time has passed and no successful scheduled backup happened since."""
    scheduled = datetime.combine(now.date(), parse_time(at)).astimezone()
    if now.astimezone() < scheduled:
        return False
    return last_success is None or last_success.astimezone() < scheduled


def next_run(now: datetime, at: str) -> datetime:
    scheduled = datetime.combine(now.astimezone().date(), parse_time(at)).astimezone()
    return scheduled if scheduled > now.astimezone() else scheduled + timedelta(days=1)


def last_scheduled_success(session: Session) -> datetime | None:
    from sqlalchemy import func, select

    return session.scalar(select(func.max(BackupHistory.created_at)).where(
        BackupHistory.trigger == "scheduled", BackupHistory.status == "success"))


def list_history(session: Session, limit: int = 200) -> list[BackupHistory]:
    from sqlalchemy import select

    return list(session.scalars(select(BackupHistory).order_by(BackupHistory.created_at.desc()).limit(limit)))
