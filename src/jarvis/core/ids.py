"""Short, sortable, human-friendly identifiers.

IDs are a short prefix plus a time-ordered random suffix so that, when they
show up in logs and the phone UI, you can tell a task from a step from an
approval at a glance and newer IDs sort after older ones.
"""

from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(value: int) -> str:
    if value == 0:
        return "0"
    digits: list[str] = []
    while value:
        value, rem = divmod(value, 36)
        digits.append(_ALPHABET[rem])
    return "".join(reversed(digits))


def new_id(prefix: str) -> str:
    """Return an identifier like ``task-lm3x9f2a4k``.

    The middle segment encodes the current time (millisecond resolution) so
    IDs created later sort lexicographically after earlier ones; the final
    segment is random to avoid collisions within the same millisecond.
    """
    timestamp = _base36(int(time.time() * 1000))
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(4))
    return f"{prefix}-{timestamp}{suffix}"
