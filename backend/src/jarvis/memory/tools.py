"""Memory operations exposed as Tools.

These let the model deliberately learn ("remember that my preset lives in
D:/Presets") and recall what it learned in later tasks. All are safe —
memory writes are not dangerous actions.
"""

from __future__ import annotations

from jarvis.memory.store import MemoryStore
from jarvis.tools.base import Tool


def build_memory_tools(store: MemoryStore) -> list[Tool]:
    """Create the memory tool set bound to ``store``."""

    def remember_fact(content: str, tag: str = "") -> str:
        """Store a fact to remember across sessions, with an optional tag
        (e.g. software, website, project, folder)."""
        store.remember_fact(content, tag or None)
        return f"Remembered: {content}"

    def recall_facts(tag: str = "") -> list[str]:
        """Recall remembered facts, optionally filtered by tag."""
        return [f.content for f in store.facts(tag or None)]

    def set_preference(key: str, value: str) -> str:
        """Save a user preference as a key/value pair (e.g. photos_folder)."""
        store.set_preference(key, value)
        return f"Preference saved: {key} = {value}"

    def get_preference(key: str) -> str:
        """Look up a previously saved user preference by key."""
        value = store.get_preference(key)
        return value if value is not None else f"No preference stored for {key!r}."

    return [
        Tool(
            name="remember_fact",
            description=remember_fact.__doc__ or "",
            func=remember_fact,
        ),
        Tool(
            name="recall_facts",
            description=recall_facts.__doc__ or "",
            func=recall_facts,
        ),
        Tool(
            name="set_preference",
            description=set_preference.__doc__ or "",
            func=set_preference,
        ),
        Tool(
            name="get_preference",
            description=get_preference.__doc__ or "",
            func=get_preference,
        ),
    ]
