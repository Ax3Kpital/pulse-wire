"""Local logging: rotating file under logs/ plus stderr."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config.settings import get_settings


def configure_logging() -> None:
    settings = get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(getattr(h, "_news_handler", False) for h in root.handlers):
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        settings.log_dir / "app.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler._news_handler = True  # type: ignore[attr-defined]
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream._news_handler = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)
    root.addHandler(stream)
    root.setLevel(settings.log_level.upper())
    for noisy in ("httpx", "httpcore", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
