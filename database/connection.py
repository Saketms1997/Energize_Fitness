from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from config.settings import ConfigError, get_settings

log = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def make_engine(url: URL | str) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3, "options": "-c timezone=UTC"},
    )


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        _engine = make_engine(get_settings().app_url())
        _session_factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def use_engine(engine: Engine) -> None:
    """Point the application at a specific engine (tests, restores)."""
    global _engine, _session_factory
    _engine = engine
    _session_factory = sessionmaker(engine, expire_on_commit=False)


def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any error."""
    get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass(frozen=True)
class ConnectionStatus:
    ok: bool
    message: str
    database: str = ""
    server_version: str = ""
    latency_ms: float | None = None
    schema_revision: str | None = None


def check_connection(engine: Engine | None = None) -> ConnectionStatus:
    """Probe the database. Never raises; the UI shows whatever this returns."""
    try:
        eng = engine or get_engine()
    except ConfigError as exc:
        return ConnectionStatus(False, str(exc))
    try:
        started = time.perf_counter()
        with eng.connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar_one()
            database = conn.execute(text("SELECT current_database()")).scalar_one()
            latency = (time.perf_counter() - started) * 1000
            try:
                revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:
                conn.rollback()
                revision = None
        message = "Connected" if revision else "Connected - schema not migrated"
        return ConnectionStatus(True, message, database, str(version), latency, revision)
    except Exception as exc:  # noqa: BLE001 - surface any driver error as status
        log.warning("Database connectivity check failed: %s", exc)
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return ConnectionStatus(False, f"Cannot connect: {first_line}")
