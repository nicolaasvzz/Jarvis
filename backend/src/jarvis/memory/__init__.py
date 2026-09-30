"""Memory: persistence that survives restarts.

A single SQLite database (in the configured data directory) stores four
kinds of long-lived state:

* **conversation** — the running dialogue with the user, so context carries
  across sessions;
* **preferences** — key/value settings the assistant learns (favourite
  folders, presets, default apps);
* **facts** — free-form remembered notes, tagged for retrieval (installed
  software, favourite websites, project info);
* **tasks** — a durable record of every task and its outcome.

The :class:`~jarvis.memory.store.MemoryStore` is the only class that touches
SQL; the rest of the system asks it for typed objects, never rows. SQLite is
used because it needs no server, is transactional, and ships with Python.
"""

from jarvis.memory.models import Fact, Message, Preference
from jarvis.memory.store import MemoryStore

__all__ = ["Fact", "MemoryStore", "Message", "Preference"]
