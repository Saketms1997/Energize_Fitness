"""Application configuration.

Non-secret values come from environment variables / a ``.env`` file.
Secrets (database passwords, later MongoDB and WhatsApp credentials) are kept
in the Windows Credential Manager through ``keyring`` and are never written to
source code, ``.env.example`` or the packaged executable.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import keyring
from dotenv import load_dotenv
from sqlalchemy.engine import URL

APP_NAME = "GymManagement"
KEYRING_SERVICE = "GymManagement"


class ConfigError(RuntimeError):
    """Raised when required configuration (e.g. a database password) is missing."""


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource_dir() -> Path:
    """Folder holding bundled read-only files (migrations). PyInstaller unpacks them to ``_MEIPASS``."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[1]


def app_data_dir() -> Path:
    override = os.environ.get("GYM_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def secret_name(kind: str, ident: str) -> str:
    return f"{kind}:{ident}"


def get_secret(name: str, env_override: str | None = None) -> str | None:
    """Look a secret up in an environment override first, then Credential Manager."""
    if env_override and os.environ.get(env_override):
        return os.environ[env_override]
    try:
        return keyring.get_password(KEYRING_SERVICE, name)
    except keyring.errors.KeyringError:
        return None


def set_secret(name: str, value: str) -> None:
    keyring.set_password(KEYRING_SERVICE, name, value)


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    db_app_user: str
    db_migrator_user: str
    data_dir: Path
    log_dir: Path
    backup_dir: Path
    log_level: str

    def _url(self, user: str, env_override: str) -> URL:
        password = get_secret(secret_name("postgres", user), env_override)
        if not password:
            raise ConfigError(
                f"No password stored for PostgreSQL user '{user}'. "
                "Run: python scripts\\setup_database.py"
            )
        return URL.create(
            "postgresql+psycopg",
            username=user,
            password=password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    def app_url(self) -> URL:
        """Least-privilege URL used by the running application (DML only)."""
        return self._url(self.db_app_user, "GYM_DB_APP_PASSWORD")

    def migrator_url(self) -> URL:
        """Schema-owner URL used only by Alembic and the setup script."""
        return self._url(self.db_migrator_user, "GYM_DB_MIGRATOR_PASSWORD")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = app_data_dir()
    load_dotenv(data_dir / ".env")
    load_dotenv(project_root() / ".env")
    data_dir = app_data_dir()
    log_dir = Path(os.environ.get("GYM_LOG_DIR") or data_dir / "logs")
    backup_dir = Path(os.environ.get("GYM_BACKUP_DIR") or data_dir / "backups")
    for directory in (data_dir, log_dir, backup_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return Settings(
        db_host=os.environ.get("GYM_DB_HOST", "localhost"),
        db_port=int(os.environ.get("GYM_DB_PORT", "5432")),
        db_name=os.environ.get("GYM_DB_NAME", "gym_management"),
        db_app_user=os.environ.get("GYM_DB_APP_USER", "gym_app"),
        db_migrator_user=os.environ.get("GYM_DB_MIGRATOR_USER", "gym_migrator"),
        data_dir=data_dir,
        log_dir=log_dir,
        backup_dir=backup_dir,
        log_level=os.environ.get("GYM_LOG_LEVEL", "INFO").upper(),
    )
