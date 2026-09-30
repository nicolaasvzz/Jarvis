"""Logging module.

Structured, searchable logging for every Jarvis action.

Two outputs are configured (see :func:`jarvis.logging.setup.setup_logging`):

* a human-readable console stream, and
* a rotating JSON-lines file (one JSON object per line) that can be
  searched with ``grep``/``jq`` today and ingested by better tooling later.

Use :func:`get_logger` to obtain a namespaced logger and
:func:`log_context` to bind fields (``task_id``, ``tool``, ...) to every
record emitted inside a block — including from code that doesn't know
about the task, such as libraries called by a tool.
"""

from jarvis.logging.context import current_context, log_context
from jarvis.logging.setup import get_logger, setup_logging

__all__ = ["current_context", "get_logger", "log_context", "setup_logging"]
