import pytest
from sqlalchemy import inspect, text

from database.connection import check_connection, get_engine, make_engine, session_scope
from database.models import Base


def test_unreachable_database_reports_failure_without_raising():
    status = check_connection(make_engine("postgresql+psycopg://x:y@127.0.0.1:1/none"))
    assert not status.ok and "Cannot connect" in status.message


@pytest.mark.db
def test_connectivity_reports_ok(require_db):
    assert require_db.ok and require_db.database == "gym_management"
    assert require_db.latency_ms is not None


@pytest.mark.db
def test_schema_is_migrated_and_matches_models(require_db):
    assert require_db.schema_revision, "run scripts/setup_database.py first"
    assert set(Base.metadata.tables) <= set(inspect(get_engine()).get_table_names())


@pytest.mark.db
def test_app_role_cannot_run_ddl(require_db):
    with pytest.raises(Exception):
        with session_scope() as s:
            s.execute(text("CREATE TABLE should_not_exist (id int)"))


@pytest.mark.db
def test_app_role_is_not_superuser(require_db):
    with session_scope() as s:
        assert s.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar() is False
