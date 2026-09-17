"""Настройка файлового и консольного логирования приложения."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path.home() / ".dahua_exporter" / "logs"
LOG_FILE = LOG_DIR / "dahua_exporter.log"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Идемпотентно настраивает логирование и возвращает корневой логгер приложения."""
    logger = logging.getLogger("dahua_exporter")
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Возвращает дочерний логгер, предварительно включив запись в файл."""
    setup_logging()
    return logging.getLogger(f"dahua_exporter.{name}")
