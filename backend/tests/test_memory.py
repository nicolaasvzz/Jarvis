"""Tests for the SQLite-backed Memory store."""

from __future__ import annotations

from pathlib import Path

from jarvis.core.models import Task, TaskStatus
from jarvis.memory import MemoryStore


def store_at(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.db")


def test_conversation_is_ordered_and_persists(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    store = MemoryStore(db_path)
    store.add_message("user", "hello")
    store.add_message("assistant", "hi there")
    store.close()

    # Reopen — memory must survive a restart.
    reopened = MemoryStore(db_path)
    messages = reopened.recent_messages()
    assert [(m.role, m.content) for m in messages] == [
        ("user", "hello"),
        ("assistant", "hi there"),
    ]


def test_preferences_upsert(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    store.set_preference("photos_folder", "D:/Photos")
    assert store.get_preference("photos_folder") == "D:/Photos"
    store.set_preference("photos_folder", "E:/Pics")
    assert store.get_preference("photos_folder") == "E:/Pics"
    assert store.get_preference("missing") is None
    assert len(store.all_preferences()) == 1


def test_facts_can_be_tagged_and_filtered(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    store.remember_fact("Photoshop is installed", tag="software")
    store.remember_fact("Likes GitHub", tag="website")
    store.remember_fact("untagged note")
    assert len(store.facts()) == 3
    software = store.facts(tag="software")
    assert len(software) == 1
    assert software[0].content == "Photoshop is installed"


def test_task_history_roundtrip(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    task = Task(request="organize photos")
    task.touch(TaskStatus.COMPLETED)
    task.result = "done"
    store.save_task(task)

    # Saving again with a new status updates in place, not duplicates.
    task.touch(TaskStatus.COMPLETED)
    store.save_task(task)

    history = store.task_history()
    assert len(history) == 1
    assert history[0]["request"] == "organize photos"
    assert history[0]["status"] == "completed"
