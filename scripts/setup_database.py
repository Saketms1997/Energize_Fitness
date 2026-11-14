"""One-time local database setup (command-line form of the app's first-run wizard).

    .venv\Scripts\python.exe scripts\setup_database.py

Asks for the PostgreSQL 'postgres' password (used once, never stored), creates the least-privilege
roles and the gym_management database, stores their passwords in Windows Credential Manager and
applies the migrations. Safe to re-run. Set GYM_DB_ADMIN_PASSWORD to run without a prompt.
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import get_settings  # noqa: E402
from services import db_setup_service  # noqa: E402


def main() -> int:
    s = get_settings()
    password = os.environ.get("GYM_DB_ADMIN_PASSWORD") or getpass.getpass(
        f"Password for PostgreSQL user 'postgres' on {s.db_host}:{s.db_port}: ")
    try:
        db_setup_service.provision(password)
    except db_setup_service.SetupError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    print("Database ready. Start the app with:  .venv\Scripts\python.exe main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
