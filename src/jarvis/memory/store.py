"""SQLite-backed persistent memory.

One connection, a schema created on first use, and small typed methods for
each kind of state. All writes are committed immediately so nothing is lost
if the process stops; the database file lives in the configured data
directory and therefore persists across restarts and reboots.

The store is intentionally the *only* SQL in the codebase — callers work
with :mod:`jarvis.memory.models` objects, keeping persistence swappable.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.logging import get_logger
from jarvis.memory.models import Fact, Message, Preference

_log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preferences (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL,
    tag        TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id         TEXT PRIMARY KEY,
    request    TEXT NOT NULL,
    status     TEXT NOT NULL,
    result     TEXT,
    error      TEXT,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class MemoryStore:
    """Durable store for conversations, preferences, facts, and tasks."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        _log.info("memory store opened", extra={"path": str(db_path)})

    # -- conversation ----------------------------------------------------
    def add_message(self, role: str, content: str) -> None:
        self._conn.execute(
            "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, _now_iso()),
        )
        self._conn.commit()

    def recent_messages(self, limit: int = 20) -> list[Message]:
        rows = self._conn.execute(
            "SELECT role, content, created_at FROM messages "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        messages = [
            Message(role=r["role"], content=r["content"], created_at=_parse(r["created_at"]))
            for r in rows
        ]
        messages.reverse()  # return in chronological order
        return messages

    # -- preferences -----------------------------------------------------
    def set_preference(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, value, _now_iso()),
        )
        self._conn.commit()

    def get_preference(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM preferences WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def all_preferences(self) -> list[Preference]:
        rows = self._conn.execute(
            "SELECT key, value, updated_at FROM preferences ORDER BY key"
        ).fetchall()
        return [
            Preference(key=r["key"], value=r["value"], updated_at=_parse(r["updated_at"]))
            for r in rows
        ]

    # -- facts -----------------------------------------------------------
    def remember_fact(self, content: str, tag: str | None = None) -> int:
        cursor = self._conn.execute(
            "INSERT INTO facts (content, tag, created_at) VALUES (?, ?, ?)",
            (content, tag, _now_iso()),
        )
        self._conn.commit()
        return int(cursor.lastrowid or 0)

    def facts(self, tag: str | None = None) -> list[Fact]:
        if tag is None:
            rows = self._conn.execute(
                "SELECT id, content, tag, created_at FROM facts ORDER BY id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, content, tag, created_at FROM facts WHERE tag = ? "
                "ORDER BY id",
                (tag,),
            ).fetchall()
        return [
            Fact(
                id=r["id"],
                content=r["content"],
                tag=r["tag"],
                created_at=_parse(r["created_at"]),
            )
            for r in rows
        ]

    # -- tasks -----------------------------------------------------------
    def save_task(self, task: Any) -> None:
        """Persist a :class:`~jarvis.core.models.Task` (passed structurally)."""
        payload = task.model_dump_json()
        self._conn.execute(
            "INSERT INTO tasks (id, request, status, result, error, payload, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status = excluded.status, "
            "result = excluded.result, error = excluded.error, "
            "payload = excluded.payload, updated_at = excluded.updated_at",
            (
                task.id,
                task.request,
                task.status.value,
                task.result,
                task.error,
                payload,
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
            ),
        )
        self._conn.commit()

    def task_history(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT payload FROM tasks ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
