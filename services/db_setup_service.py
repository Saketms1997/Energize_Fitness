"""First-run database provisioning and automatic schema upgrades.

Used by the setup wizard in the packaged app (no terminal) and by ``scripts/setup_database.py``.
The PostgreSQL superuser password is used once and never stored.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy import create_engine

from config.settings import (ConfigError, Settings, get_secret, get_settings, resource_dir, secret_name,
                             set_secret)
from database.connection import check_connection, make_engine

log = logging.getLogger(__name__)


class SetupError(RuntimeError):
    """Raised with a message that is safe and useful to show the owner."""


@dataclass(frozen=True)
class DbState:
    ready: bool
    reason: str = ""          # why not ready
    needs_migration: bool = False


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(resource_dir() / "database" / "migrations"))
    cfg.set_main_option("prepend_sys_path", str(resource_dir()))
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    engine = make_engine(settings.migrator_url())
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def inspect_state() -> DbState:
    """Is the database configured, reachable and at the current schema? Never raises."""
    settings = get_settings()
    try:
        settings.app_url()
        settings.migrator_url()
    except ConfigError:
        return DbState(False, "The database has not been set up on this PC yet.")
    status = check_connection()
    if not status.ok:
        return DbState(False, status.message)
    try:
        needs = current_revision(settings) != head_revision()
    except Exception as exc:  # noqa: BLE001
        return DbState(False, f"Cannot read the database schema: {exc}")
    return DbState(True, needs_migration=needs)


def upgrade_schema() -> None:
    """Apply any pending Alembic migrations (runs as the schema-owner role)."""
    settings = get_settings()
    engine = make_engine(settings.migrator_url())
    try:
        cfg = alembic_config()
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            conn.commit()
    finally:
        engine.dispose()
    log.info("Database schema is at %s", head_revision())


def _ensure_password(user: str) -> str:
    name = secret_name("postgres", user)
    existing = get_secret(name)
    if existing:
        return existing
    password = secrets.token_urlsafe(24)
    set_secret(name, password)
    return password


def verify_admin_password(password: str) -> None:
    """Verify password authentication against the configured PostgreSQL server, without changing it."""
    if not password:
        raise SetupError("Enter the PostgreSQL 'postgres' administrator password.")
    s = get_settings()
    try:
        with psycopg.connect(host=s.db_host, port=s.db_port, dbname=s.db_name, user="postgres",
                             password=password, connect_timeout=5) as conn:
            if not conn.pgconn.used_password:
                raise SetupError(
                    "Password recovery requires PostgreSQL password authentication. "
                    "Ask the database administrator to enable SCRAM password authentication "
                    "for this connection in pg_hba.conf; trust or Windows-only authentication is not accepted."
                )
            row = conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
            if not row or not row[0]:
                raise SetupError("The PostgreSQL 'postgres' account must be a database administrator.")
    except psycopg.OperationalError as exc:
        if exc.sqlstate == "28P01":
            raise SetupError("The PostgreSQL 'postgres' administrator password is incorrect.") from None
        raise SetupError(
            f"Cannot verify the PostgreSQL administrator at {s.db_host}:{s.db_port}. "
            "Check that PostgreSQL is running and that the administrator can connect to the gym database."
        ) from None


def provision(admin_password: str, host: str | None = None, port: int | None = None) -> None:
    """Create the roles and database, store their passwords in Credential Manager, migrate.

    Safe to repeat: existing roles/databases are kept. Raises SetupError with a clear message.
    """
    s = get_settings()
    host, port = host or s.db_host, port or s.db_port
    migrator_pw, app_pw = _ensure_password(s.db_migrator_user), _ensure_password(s.db_app_user)
    try:
        with psycopg.connect(host=host, port=port, dbname="postgres", user="postgres", password=admin_password,
                             autocommit=True, connect_timeout=5) as conn:
            for user, pw, extra in ((s.db_migrator_user, migrator_pw, " CREATEDB"), (s.db_app_user, app_pw, "")):
                exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone()
                conn.execute(sql.SQL("{} ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEROLE" + extra).format(
                    sql.SQL("ALTER" if exists else "CREATE"), sql.Identifier(user), sql.Literal(pw)))
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (s.db_name,)).fetchone():
                conn.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8'").format(
                    sql.Identifier(s.db_name), sql.Identifier(s.db_migrator_user)))
            conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(s.db_name)))
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(s.db_name), sql.Identifier(s.db_app_user)))
        with psycopg.connect(host=host, port=port, dbname=s.db_name, user=s.db_migrator_user, password=migrator_pw,
                             autocommit=True, connect_timeout=5) as conn:
            mig, app = sql.Identifier(s.db_migrator_user), sql.Identifier(s.db_app_user)
            for stmt in (
                "GRANT USAGE ON SCHEMA public TO {app}",
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app}",
                "ALTER DEFAULT PRIVILEGES FOR ROLE {mig} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app}",
                "ALTER DEFAULT PRIVILEGES FOR ROLE {mig} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {app}",
            ):
                conn.execute(sql.SQL(stmt).format(mig=mig, app=app))
    except psycopg.OperationalError as exc:
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else "connection failed"
        if "password authentication failed" in first:
            raise SetupError("The PostgreSQL 'postgres' password is incorrect.") from exc
        raise SetupError(f"Cannot reach PostgreSQL at {host}:{port}. Is the PostgreSQL service running? ({first})") from exc
    upgrade_schema()
