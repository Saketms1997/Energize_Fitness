"""MongoDB Atlas access: secure client creation and the thin remote store used by sync.

* The connection string lives in Windows Credential Manager (``mongodb:uri``), never in source,
  ``.env`` files, the database or the packaged executable.
* TLS is always on. A URI that tries to disable it is refused.
* Documents: ``{_id: "<uuid>", data: {<row snapshot>}, _sync: {rev, ts, origin}}``.
  ``rev`` is a per-document revision counter bumped by every cloud write; conditional updates on
  ``rev`` give optimistic concurrency, which is what makes pushes safe to repeat.
"""
from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Generator, Iterable
from urllib.parse import parse_qsl, urlsplit

import certifi
from pymongo import MongoClient
from pymongo.errors import (AutoReconnect, ConnectionFailure, DuplicateKeyError, NetworkTimeout,
                            PyMongoError, ServerSelectionTimeoutError)

from config.settings import get_secret, set_secret
from services.errors import BusinessRuleError

URI_SECRET = "mongodb:uri"
SERVER_TIMEOUT_MS = 8000
RECONNECT_DELAY_S = 2.0
log = logging.getLogger(__name__)
# Failures that mean "the cloud is unreachable": abort the whole sync, keep everything queued.
CONNECTIVITY_ERRORS = (ServerSelectionTimeoutError, AutoReconnect, ConnectionFailure, NetworkTimeout)


def _validate_uri(uri: str) -> None:
    if not re.match(r"^mongodb(\+srv)?://", uri):
        raise BusinessRuleError("The MongoDB connection string must start with mongodb:// or mongodb+srv://")
    try:
        # PyMongo also accepts legacy semicolon-delimited options.
        options = parse_qsl(urlsplit(uri).query.replace(";", "&"))
    except ValueError:
        raise BusinessRuleError("The MongoDB connection string is malformed. Copy it again from Atlas.") from None
    for key, value in options:
        key, value = key.lower(), value.lower()
        if (key in {"tls", "ssl"} and value in {"false", "0"}) or (
            key in {"tlsinsecure", "tlsallowinvalidcertificates", "tlsallowinvalidhostnames"}
            and value in {"true", "1"}
        ):
            raise BusinessRuleError("TLS and certificate verification must stay enabled for MongoDB connections.")


def set_uri(uri: str) -> None:
    uri = uri.strip()
    _validate_uri(uri)
    set_secret(URI_SECRET, uri)


def get_uri() -> str | None:
    return get_secret(URI_SECRET, "GYM_MONGODB_URI")


def redact(text: str) -> str:
    """Strip credentials from anything that might contain a connection string."""
    return re.sub(r"(mongodb(?:\+srv)?://)[^@\s/]+@", r"\1***@", text)


def build_client(uri: str | None = None) -> MongoClient:
    uri = get_uri() if uri is None else uri.strip()
    if not uri:
        raise BusinessRuleError("MongoDB is not configured. Add your Atlas connection string in Settings.")
    _validate_uri(uri)
    # Keep PyMongo's certificate/hostname verification defaults; insecure URI overrides are rejected above.
    return MongoClient(uri, tls=True, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=SERVER_TIMEOUT_MS,
                       appname="GymManagement", retryWrites=True)


@dataclass
class _SharedClient:
    uri: str = field(repr=False)
    client: MongoClient
    users: int = 0
    retired: bool = False


_client_lock = Lock()
_shared_client: _SharedClient | None = None


@contextmanager
def client_scope(uri: str | None = None) -> Generator[MongoClient, None, None]:
    """Reuse the desktop client's pool; let in-flight work finish when the URI changes."""
    global _shared_client
    uri = get_uri() if uri is None else uri.strip()
    if not uri:
        raise BusinessRuleError("MongoDB is not configured. Add your Atlas connection string in Settings.")
    with _client_lock:
        if _shared_client is None or _shared_client.uri != uri:
            replacement = _SharedClient(uri, build_client(uri))
            if _shared_client is not None:
                _shared_client.retired = True
                if _shared_client.users == 0:
                    _shared_client.client.close()
            _shared_client = replacement
            log.debug("Created MongoDB client pool")
        shared = _shared_client
        shared.users += 1
    try:
        yield shared.client
    finally:
        with _client_lock:
            shared.users -= 1
            if shared.retired and shared.users == 0:
                shared.client.close()


def close_client() -> None:
    global _shared_client
    with _client_lock:
        if _shared_client is not None:
            _shared_client.retired = True
            if _shared_client.users == 0:
                _shared_client.client.close()
            _shared_client = None


def reset_client(client: MongoClient) -> None:
    """Retire a pool that hit a connectivity failure (e.g. a TLS handshake rejected after the PC's
    network or public IP changed), so the next use builds a fresh client with new DNS/TLS state."""
    global _shared_client
    with _client_lock:
        if _shared_client is None or _shared_client.client is not client:
            return
        _shared_client.retired = True
        if _shared_client.users == 0:
            _shared_client.client.close()
        _shared_client = None
    log.info("Discarded the MongoDB client pool after a connectivity failure")


def is_connectivity_error(exc: BaseException) -> bool:
    return isinstance(exc, CONNECTIVITY_ERRORS)


def connection_error(exc: PyMongoError) -> str:
    detail = redact(str(exc))
    lowered = detail.lower()
    if "certificate_verify_failed" in lowered or "certificate verify failed" in lowered:
        hint = ("MongoDB certificate verification failed. Check this PC's clock, trusted CA bundle, "
                "and any antivirus/VPN TLS inspection. Do not disable certificate verification. ")
    elif "handshake" in lowered or "tlsv1_alert" in lowered:
        hint = ("MongoDB TLS handshake rejected. In Atlas > Network Access, allow this PC's current public IP "
                "and check for expired IP entries. Also check VPN/firewall TLS inspection and cluster status. ")
    else:
        hint = ""
    return hint + detail[:300]


@dataclass(frozen=True)
class ConnectionTest:
    ok: bool
    message: str
    server_version: str = ""


def test_connection(uri: str | None = None, database: str = "gym_management", client: Any = None) -> ConnectionTest:
    """Never raises: returns what the Settings screen should show.

    A connectivity failure on the shared pool is retried once with a freshly built client."""
    attempts = 2 if client is None else 1
    for attempt in range(1, attempts + 1):
        connection = None
        try:
            with (client_scope(uri) if client is None else nullcontext(client)) as connection:
                connection.admin.command("ping")
                info = connection.server_info()
                connection.get_database(database).list_collection_names()  # checks access to the selected database
                return ConnectionTest(True, "Connected to MongoDB.", str(info.get("version", "")))
        except BusinessRuleError as exc:
            return ConnectionTest(False, str(exc))
        except PyMongoError as exc:
            if client is None and connection is not None and is_connectivity_error(exc):
                reset_client(connection)
                if attempt < attempts:
                    log.info("MongoDB unreachable, retrying with a new connection: %s", redact(str(exc))[:200])
                    time.sleep(RECONNECT_DELAY_S)
                    continue
            message = f"Cannot connect: {connection_error(exc)}"
            log.warning("%s", message)
            return ConnectionTest(False, message)
    return ConnectionTest(False, "Cannot connect to MongoDB.")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)  # Mongo stores naive UTC


def to_naive_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class RemoteStore:
    """The few cloud operations sync needs. Tests substitute a mongomock database."""

    def __init__(self, database: Any, device_id: str):
        self.db = database
        self.device_id = device_id

    def ping(self) -> None:
        self.db.client.admin.command("ping")

    def ensure_indexes(self, tables: Iterable[str]) -> None:
        for table in tables:
            self.db[table].create_index("_sync.ts")

    def get(self, table: str, entity_id: str) -> dict | None:
        return self.db[table].find_one({"_id": entity_id})

    def insert(self, table: str, entity_id: str, data: dict) -> int | None:
        """Create the cloud document. Returns its rev, or None if someone created it first."""
        try:
            self.db[table].insert_one({"_id": entity_id, "data": data,
                                       "_sync": {"rev": 1, "ts": _now(), "origin": self.device_id}})
        except DuplicateKeyError:
            return None
        return 1

    def update_if_rev(self, table: str, entity_id: str, expected_rev: int, data: dict) -> int | None:
        """Replace ``data`` only if nobody changed the document since ``expected_rev``."""
        result = self.db[table].update_one(
            {"_id": entity_id, "_sync.rev": expected_rev},
            {"$set": {"data": data, "_sync.ts": _now(), "_sync.origin": self.device_id},
             "$inc": {"_sync.rev": 1}})
        return expected_rev + 1 if result.matched_count else None

    def changed_since(self, table: str, since: datetime | None) -> list[dict]:
        query = {"_sync.ts": {"$gte": to_naive_utc(since)}} if since else {}
        return list(self.db[table].find(query).sort("_sync.ts", 1))
