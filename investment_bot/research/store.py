"""One SQLite file for everything research collects: items, their scores, and
what paid sources have cost this month."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,          -- "<source>:<the source's own id>"
    source TEXT NOT NULL,         -- alpaca | x
    published TEXT NOT NULL,      -- ISO time, UTC
    headline TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    symbols TEXT NOT NULL DEFAULT '[]',   -- JSON list the source tagged
    engagement INTEGER NOT NULL DEFAULT 0, -- likes + reposts (X only)
    scored INTEGER NOT NULL DEFAULT 0      -- 1 once the scorer has seen it
);
CREATE INDEX IF NOT EXISTS items_published ON items (published);
CREATE INDEX IF NOT EXISTS items_unscored ON items (scored);
CREATE TABLE IF NOT EXISTS scores (
    item_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction REAL NOT NULL,      -- -1 (bad for the price) .. +1 (good)
    strength REAL NOT NULL,       -- 0..1: how much it should move the price
    probability REAL NOT NULL,    -- 0.01..1: chance the predicted move happens in time
    move_pct REAL NOT NULL,       -- the move predicted, in % of the price
    target REAL,                  -- a price level the item names, if any
    horizon_hours REAL NOT NULL,  -- within how long
    event TEXT NOT NULL,          -- earnings, guidance, analyst, ...
    PRIMARY KEY (item_id, symbol)
);
CREATE TABLE IF NOT EXISTS spend (
    month TEXT NOT NULL,          -- 2026-10
    source TEXT NOT NULL,
    usd REAL NOT NULL DEFAULT 0,
    reads INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (month, source)
);
CREATE TABLE IF NOT EXISTS cursor (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Item:
    id: str
    source: str
    published: str
    headline: str
    summary: str = ""
    url: str = ""
    author: str = ""
    symbols: list[str] = field(default_factory=list)
    engagement: int = 0


@dataclass
class Score:
    item_id: str
    symbol: str
    direction: float
    strength: float
    probability: float
    move_pct: float
    target: float | None
    horizon_hours: float
    event: str


def month_of(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m")


class NewsStore:
    def __init__(self, path: str | Path = "news.db"):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(scores)")}
        if columns and "probability" not in columns:
            # Scores from before probabilities: rate everything again.
            self.db.execute("DROP TABLE scores")
            self.db.execute("UPDATE items SET scored = 0")
            self.db.commit()
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ items

    def add(self, items: list[Item]) -> int:
        """Store new items; ones already stored are skipped. Returns how many were new."""
        before = self.db.total_changes
        self.db.executemany(
            "INSERT OR IGNORE INTO items (id, source, published, headline, summary, url,"
            " author, symbols, engagement) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(i.id, i.source, i.published, i.headline, i.summary, i.url, i.author,
              json.dumps(i.symbols), i.engagement) for i in items],
        )
        self.db.commit()
        return self.db.total_changes - before

    def unscored(self, limit: int = 500) -> list[Item]:
        rows = self.db.execute(
            "SELECT * FROM items WHERE scored = 0 ORDER BY published DESC LIMIT ?", (limit,))
        return [self._item(r) for r in rows]

    def save_scores(self, item_ids: list[str], scores: list[Score]) -> None:
        """Record what the scorer said, and mark every item it was shown as scored
        (an item it found nothing in has no score rows, and isn't asked again)."""
        self.db.executemany(
            "INSERT OR REPLACE INTO scores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(s.item_id, s.symbol, s.direction, s.strength, s.probability, s.move_pct,
              s.target, s.horizon_hours, s.event) for s in scores],
        )
        self.db.executemany("UPDATE items SET scored = 1 WHERE id = ?",
                            [(i,) for i in item_ids])
        self.db.commit()

    def scored_since(self, since: datetime) -> list[dict[str, Any]]:
        """Every (item, symbol) score published after `since`, newest first."""
        rows = self.db.execute(
            "SELECT i.id, i.source, i.published, i.headline, i.url, i.engagement,"
            " s.symbol, s.direction, s.strength, s.probability, s.move_pct, s.target,"
            " s.horizon_hours, s.event"
            " FROM scores s JOIN items i ON i.id = s.item_id"
            " WHERE i.published >= ? ORDER BY i.published DESC",
            (since.astimezone(timezone.utc).isoformat(),),
        )
        return [dict(r) for r in rows]

    def count_since(self, since: datetime) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT source, COUNT(*) AS n FROM items WHERE published >= ? GROUP BY source",
            (since.astimezone(timezone.utc).isoformat(),),
        )
        return {r["source"]: r["n"] for r in rows}

    @staticmethod
    def _item(row: sqlite3.Row) -> Item:
        return Item(id=row["id"], source=row["source"], published=row["published"],
                    headline=row["headline"], summary=row["summary"], url=row["url"],
                    author=row["author"], symbols=json.loads(row["symbols"]),
                    engagement=row["engagement"])

    # ------------------------------------------------------------ spend

    def spent(self, source: str, now: datetime) -> float:
        row = self.db.execute("SELECT usd FROM spend WHERE month = ? AND source = ?",
                              (month_of(now), source)).fetchone()
        return float(row["usd"]) if row else 0.0

    def add_spend(self, source: str, usd: float, reads: int, now: datetime) -> None:
        self.db.execute(
            "INSERT INTO spend (month, source, usd, reads) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (month, source) DO UPDATE SET usd = usd + excluded.usd,"
            " reads = reads + excluded.reads",
            (month_of(now), source, usd, reads),
        )
        self.db.commit()

    # ------------------------------------------------------------ cursors

    def cursor(self, name: str) -> str | None:
        row = self.db.execute("SELECT value FROM cursor WHERE name = ?", (name,)).fetchone()
        return row["value"] if row else None

    def set_cursor(self, name: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO cursor VALUES (?, ?)", (name, value))
        self.db.commit()
