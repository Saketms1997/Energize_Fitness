"""Builds a SyncEngine from the saved MongoDB settings and runs it (worker-thread friendly)."""
from __future__ import annotations

import logging
import time
from typing import Callable

from database.connection import session_scope
from services import mongodb_service, settings_service
from services.sync_service import UNREACHABLE_PREFIX, SyncEngine, SyncReport

log = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(mongodb_service.get_uri())


def _sync_once(progress, trigger: str) -> tuple[SyncReport, bool]:
    with mongodb_service.client_scope() as client:
        with session_scope() as s:
            database = settings_service.get(s, "local.mongodb.database") or "gym_management"
        report = SyncEngine(session_scope, client[database], progress).sync_now(trigger)
        unreachable = report.status == "failed" and (report.error or "").startswith(UNREACHABLE_PREFIX)
        if unreachable:
            mongodb_service.reset_client(client)
        return report, unreachable


def run_sync(progress: Callable[[int, int, str], None] | None = None, trigger: str = "manual") -> SyncReport:
    report, unreachable = _sync_once(progress, trigger)
    if unreachable:  # sync is idempotent, so one retry on a fresh client is safe
        log.info("Retrying sync with a new MongoDB connection")
        time.sleep(mongodb_service.RECONNECT_DELAY_S)
        report, _ = _sync_once(progress, trigger)
    return report


def test_connection() -> mongodb_service.ConnectionTest:
    with session_scope() as s:
        database = settings_service.get(s, "local.mongodb.database") or "gym_management"
    return mongodb_service.test_connection(database=database)
