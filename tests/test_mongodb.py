import ssl
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

from services import mongodb_service as mongo
from services.errors import BusinessRuleError


@pytest.fixture(autouse=True)
def isolated_client(monkeypatch):
    mongo.close_client()
    monkeypatch.setattr(mongo, "get_uri", lambda: "mongodb://configured.example/gym")
    monkeypatch.setattr(mongo, "RECONNECT_DELAY_S", 0)
    yield
    mongo.close_client()


@pytest.fixture
def factory(monkeypatch):
    factory = Mock(side_effect=lambda uri: Mock(spec=MongoClient, admin=Mock()))
    monkeypatch.setattr(mongo, "build_client", factory)
    return factory


def test_repeated_connection_tests_and_sync_borrow_same_client(factory):
    with mongo.client_scope() as client:
        client.server_info.return_value = {"version": "8.0"}
    for _ in range(10):
        assert mongo.test_connection().ok
    with mongo.client_scope() as again:
        assert again is client
    assert factory.call_count == 1
    client.close.assert_not_called()
    mongo.close_client()
    client.close.assert_called_once()


def test_concurrent_workers_share_one_pool(factory):
    barrier = Barrier(4)

    def borrow(_index):
        with mongo.client_scope() as client:
            barrier.wait(timeout=5)
            return client

    with ThreadPoolExecutor(max_workers=4) as executor:
        clients = list(executor.map(borrow, range(4)))
    assert all(client is clients[0] for client in clients)
    assert factory.call_count == 1


def test_uri_changes_do_not_close_a_client_still_used_by_sync(factory):
    with mongo.client_scope() as old:
        with mongo.client_scope("mongodb://replacement.example/gym") as replacement:
            assert replacement is not old
            old.close.assert_not_called()
        replacement.close.assert_not_called()
    old.close.assert_called_once()
    with mongo.client_scope("mongodb://replacement.example/gym") as current:
        assert current is replacement
    assert factory.call_count == 2


def test_invalid_replacement_does_not_discard_working_pool(factory):
    with mongo.client_scope() as old:
        pass
    factory.side_effect = BusinessRuleError("Invalid replacement")
    with pytest.raises(BusinessRuleError):
        with mongo.client_scope("bad-uri"):
            pytest.fail("Invalid replacement must not yield")
    with mongo.client_scope() as current:
        assert current is old
    old.close.assert_not_called()


def test_shutdown_defers_close_until_active_work_finishes(factory):
    with mongo.client_scope() as client:
        mongo.close_client()
        client.close.assert_not_called()
    client.close.assert_called_once()
    mongo.close_client()
    client.close.assert_called_once()


HANDSHAKE_ERROR = ("SSL handshake failed: ac-x-shard-00-00.example.mongodb.net:27017: "
                   "[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error (_ssl.c:1032)")


def test_connectivity_failure_discards_pool_and_retries_with_a_fresh_client(factory):
    with mongo.client_scope() as stale:
        stale.admin.command.side_effect = ServerSelectionTimeoutError(HANDSHAKE_ERROR)
    result = mongo.test_connection()
    assert result.ok
    assert factory.call_count == 2
    stale.close.assert_called_once()
    with mongo.client_scope() as current:
        assert current is not stale
    assert factory.call_count == 2


def test_non_connectivity_failure_keeps_pool_and_does_not_retry(factory):
    from pymongo.errors import OperationFailure
    with mongo.client_scope() as client:
        client.admin.command.side_effect = OperationFailure("bad auth : authentication failed")
    assert not mongo.test_connection().ok
    assert factory.call_count == 1
    client.close.assert_not_called()


def test_reset_of_an_old_client_does_not_discard_the_current_pool(factory):
    with mongo.client_scope() as current:
        pass
    mongo.reset_client(Mock(spec=MongoClient))
    with mongo.client_scope() as again:
        assert again is current
    current.close.assert_not_called()


def test_external_clients_remain_owned_by_the_caller():
    client = Mock(spec=MongoClient, admin=Mock())
    client.server_info.return_value = {"version": "8.0"}
    assert mongo.test_connection(client=client).ok
    client.close.assert_not_called()


@pytest.mark.parametrize("option", [
    "tls=false", "ssl=false", "tls=falsehood&tlsAllowInvalidCertificates=true",
    "tlsInsecure=true", "tlsAllowInvalidCertificates=true", "tlsAllowInvalidHostnames=true",
    "TLSINSECURE=%74rue", "appName=gym;tlsInsecure=true", "appName=gym;tlsAllowInvalidCertificates=true",
])
def test_saved_and_environment_uris_cannot_disable_tls_checks(monkeypatch, option):
    uri = f"mongodb://localhost/gym?{option}"
    store = Mock()
    monkeypatch.setattr(mongo, "set_secret", store)
    with pytest.raises(BusinessRuleError, match="TLS"):
        mongo.set_uri(uri)
    store.assert_not_called()
    monkeypatch.setattr(mongo, "get_uri", lambda: uri)
    result = mongo.test_connection()
    assert not result.ok and "TLS" in result.message


def test_malformed_uri_has_a_safe_domain_error():
    with pytest.raises(BusinessRuleError, match="malformed"):
        mongo.set_uri("mongodb://[broken")


@pytest.mark.parametrize("options", [
    "", "&tlsInsecure=false", "&tlsAllowInvalidCertificates=false", "&appName=gym%3BtlsInsecure%3Dtrue",
])
def test_secure_uri_options_preserve_certificate_and_hostname_checks(options):
    client = mongo.build_client(f"mongodb://localhost/gym?connect=false{options}")
    try:
        context = client.options.pool_options._ssl_context
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
    finally:
        client.close()


def test_persistent_handshake_failure_has_actionable_diagnostics_and_redacts_credentials(monkeypatch, caplog):
    def failing(_uri):
        client = Mock(spec=MongoClient, admin=Mock())
        client.admin.command.side_effect = ServerSelectionTimeoutError(f"{HANDSHAKE_ERROR} mongodb://sensitive-user:sensitive-password@cluster.example/gym")
        return client

    factory = Mock(side_effect=failing)
    monkeypatch.setattr(mongo, "build_client", factory)
    result = mongo.test_connection()
    assert not result.ok
    assert factory.call_count == 2  # retried once with a fresh client
    assert "current public IP" in result.message and "expired IP entries" in result.message
    assert "TLSV1_ALERT_INTERNAL_ERROR" in result.message
    assert "sensitive-password" not in result.message + caplog.text
    assert "sensitive-user" not in result.message + caplog.text


def test_certificate_failure_does_not_recommend_disabling_verification():
    message = mongo.connection_error(ServerSelectionTimeoutError("CERTIFICATE_VERIFY_FAILED"))
    assert "PC's clock" in message and "Do not disable" in message
