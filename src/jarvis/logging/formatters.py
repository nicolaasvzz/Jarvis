"""Log record formatters.

* :class:`JsonLinesFormatter` — one JSON object per line for the log file;
  machine-searchable with ``grep``/``jq`` and easy to ingest later.
* :class:`ConsoleFormatter` — compact human-readable lines for the console,
  with bound context fields appended.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from jarvis.logging.context import current_context

# Attributes present on every LogRecord; anything else was passed by the
# caller via `extra={...}` and should be surfaced in the output.
_STANDARD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonLinesFormatter(logging.Formatter):
    """Serialise each record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = current_context()
        if context:
            payload["context"] = context
        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_ATTRS
        }
        if extra:
            payload["extra"] = extra
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human-readable line with bound context fields appended in brackets."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        context = current_context()
        if context:
            rendered = " ".join(f"{key}={value}" for key, value in context.items())
            line = f"{line}  [{rendered}]"
        return line
