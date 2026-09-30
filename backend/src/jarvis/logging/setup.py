"""Logging configuration.

Configures the ``jarvis`` logger tree (not the root logger, so third-party
libraries keep their own logging behaviour) with a console handler and a
rotating JSON-lines file handler.

``setup_logging`` is idempotent: calling it again replaces the previously
installed handlers instead of stacking duplicates, so records are never
emitted twice after a reconfiguration.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from jarvis.config.schema import LoggingConfig
from jarvis.logging.formatters import ConsoleFormatter, JsonLinesFormatter

_LOGGER_NAME = "jarvis"
_LOG_FILE_NAME = "jarvis.jsonl"


def setup_logging(config: LoggingConfig) -> Path:
    """Configure the ``jarvis`` logger tree; returns the log file path."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(config.level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    if config.console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(ConsoleFormatter())
        logger.addHandler(console_handler)

    config.directory.mkdir(parents=True, exist_ok=True)
    log_file = config.directory / _LOG_FILE_NAME
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=config.file_max_bytes,
        backupCount=config.file_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonLinesFormatter())
    logger.addHandler(file_handler)

    return log_file


def get_logger(name: str) -> logging.Logger:
    """Return a logger inside the ``jarvis`` namespace.

    ``get_logger("planner")`` and ``get_logger("jarvis.planner")`` both
    return the ``jarvis.planner`` logger, so modules can use their own
    ``__name__`` directly.
    """
    if name == _LOGGER_NAME or name.startswith(f"{_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")
