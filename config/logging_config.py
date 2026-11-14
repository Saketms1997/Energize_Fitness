from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from config.settings import get_settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    file_handler = RotatingFileHandler(
        settings.log_dir / "gym.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)
    _configured = True
