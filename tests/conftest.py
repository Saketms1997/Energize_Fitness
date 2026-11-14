import os

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TEST_DB = "gym_management_test"
TEST_DB_B = "gym_management_test_b"  # second 'device' for two-way sync tests


@pytest.fixture(scope="session")
def pg_status():
    from database.connection import check_connection

    return check_connection()


@pytest.fixture
def require_db(pg_status):
    if not pg_status.ok:
        pytest.skip(f"PostgreSQL not available: {pg_status.message}")
    return pg_status


def _build_engine(name: str):
    """Create (if needed) and migrate a throwaway test database as the schema owner."""
    from alembic import command
    from alembic.config import Config
    from config.settings import ConfigError, get_settings, project_root
    from database.connection import make_engine

    try:
        url = get_settings().migrator_url()
    except ConfigError as exc:
        pytest.skip(str(exc))
    admin = make_engine(url.set(database="postgres")).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).first():
                conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot create test database: {exc}")
    finally:
        admin.dispose()
    engine = make_engine(url.set(database=name))
    cfg = Config(str(project_root() / "alembic.ini"))
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
        conn.commit()
    return engine


def _truncate(engine):
    with engine.begin() as conn:
        names = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"
        )).scalars().all()
        conn.execute(text("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " CASCADE"))


@pytest.fixture(scope="session")
def engine_a():
    eng = _build_engine(TEST_DB)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def engine_b():
    eng = _build_engine(TEST_DB_B)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine_a):
    """Clean test database + a Session. Services flush; the fixture rolls back leftovers."""
    _truncate(engine_a)
    with Session(engine_a) as session:
        yield session
        session.rollback()


@pytest.fixture
def db_b(engine_b):
    _truncate(engine_b)
    with Session(engine_b) as session:
        yield session
        session.rollback()


@pytest.fixture
def app_db(engine_a):
    """Make the application's own session_scope() use the clean test database."""
    from database import connection

    _truncate(engine_a)
    previous = connection._engine
    connection.use_engine(engine_a)
    yield engine_a
    connection._engine = previous
    connection._session_factory = None if previous is None else sessionmaker(previous, expire_on_commit=False)
