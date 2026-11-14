from types import SimpleNamespace
from unittest.mock import MagicMock

import psycopg
import pytest

from services import db_setup_service as setup


@pytest.fixture
def admin_connection(monkeypatch):
    settings = SimpleNamespace(db_host="configured-server", db_port=5432, db_name="gym_management_test")
    monkeypatch.setattr(setup, "get_settings", lambda: settings)
    connection = MagicMock()
    connection.pgconn.used_password = True
    connection.execute.return_value.fetchone.return_value = (True,)
    connect = MagicMock()
    connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr(setup.psycopg, "connect", connect)
    return connect, connection


def test_admin_proof_uses_only_configured_server_and_checks_superuser(admin_connection):
    connect, connection = admin_connection
    setup.verify_admin_password("private-admin-secret")
    connect.assert_called_once_with(host="configured-server", port=5432, dbname="gym_management_test",
                                   user="postgres", password="private-admin-secret", connect_timeout=5)
    connection.execute.assert_called_once_with("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    connection.__exit__.assert_not_called()
    connect.return_value.__exit__.assert_called_once()


def test_admin_proof_rejects_empty_password_without_connecting(admin_connection):
    connect, _connection = admin_connection
    with pytest.raises(setup.SetupError, match="Enter the PostgreSQL"):
        setup.verify_admin_password("")
    connect.assert_not_called()


def test_admin_proof_cannot_be_bypassed_by_trust_authentication(admin_connection):
    _connect, connection = admin_connection
    connection.pgconn.used_password = False
    with pytest.raises(setup.SetupError, match="password authentication"):
        setup.verify_admin_password("anything-would-work-with-trust")
    connection.execute.assert_not_called()


def test_admin_proof_rejects_non_superuser(admin_connection):
    _connect, connection = admin_connection
    connection.execute.return_value.fetchone.return_value = (False,)
    with pytest.raises(setup.SetupError, match="database administrator"):
        setup.verify_admin_password("private-admin-secret")


@pytest.mark.parametrize("error,expected", [
    (psycopg.errors.InvalidPassword("private-error"), "password is incorrect"),
    (psycopg.OperationalError("private-error"), "Cannot verify"),
])
def test_admin_proof_surfaces_safe_connection_failures(admin_connection, error, expected):
    connect, _connection = admin_connection
    connect.side_effect = error
    with pytest.raises(setup.SetupError, match=expected) as caught:
        setup.verify_admin_password("private-admin-secret")
    assert "private-error" not in str(caught.value)
    assert "private-admin-secret" not in str(caught.value)
