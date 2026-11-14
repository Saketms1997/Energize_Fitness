import pytest

from config import settings as cfg


def test_missing_password_raises_config_error(monkeypatch):
    monkeypatch.setattr(cfg, "get_secret", lambda name, env_override=None: None)
    with pytest.raises(cfg.ConfigError):
        cfg.get_settings().app_url()


def test_password_comes_from_secret_store(monkeypatch):
    monkeypatch.setattr(cfg, "get_secret", lambda name, env_override=None: "s3cret")
    url = cfg.get_settings().app_url()
    assert url.username == "gym_app" and url.password == "s3cret"
    assert url.drivername == "postgresql+psycopg"


def test_runtime_directories_are_created():
    s = cfg.get_settings()
    assert s.log_dir.is_dir() and s.backup_dir.is_dir()
