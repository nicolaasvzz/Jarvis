"""Context propagation for structured logs.

Binds key/value fields (e.g. ``task_id``, ``tool``) to the current
execution context so every log record emitted inside a ``with
log_context(...)`` block carries them automatically. Built on
:mod:`contextvars`, so it is safe with both threads and asyncio tasks.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_log_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "jarvis_log_context", default=None
)


def current_context() -> dict[str, Any]:
    """Return a copy of the fields bound to the current context."""
    return dict(_log_context.get() or {})


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Bind ``fields`` to every log record emitted inside the block.

    Nested blocks merge; inner values win for duplicate keys and the outer
    context is restored on exit.
    """
    merged = {**(_log_context.get() or {}), **fields}
    token = _log_context.set(merged)
    try:
        yield
    finally:
        _log_context.reset(token)
