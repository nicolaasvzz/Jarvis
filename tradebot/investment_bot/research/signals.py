"""From scored items to one mood per symbol, and the strong ones flagged.

A story repeated by ten outlets, or posted two hundred times, is still one
story: per symbol, news ratings with the same event and direction within
``merge_hours`` merge into one, worth its strongest version. All of a
symbol's posts on X together are one more "story", weighted by how much
engagement they got and capped at ``social_cap``, so posts alone can never
reach the signal threshold: a buy or sell needs real news behind it, and
a symbol whose strongest story is just hype isn't flagged either.

Each story's impact is direction x strength x probability, halved every
``half_life_hours``. A symbol's mood is tanh of the summed impacts, -1..+1.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass
class SignalConfig:
    window_hours: float = 24
    half_life_hours: float = 6
    merge_hours: float = 6
    social_cap: float = 0.3
    threshold: float = 0.5     # |mood| at which a symbol is flagged buy/sell

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SignalConfig:
        return cls(**{k: float(v) for k, v in raw.items() if k in cls.__dataclass_fields__})


def _hours(now: datetime, published: str) -> float:
    return max(0.0, (now - datetime.fromisoformat(published)).total_seconds() / 3600)


def symbol_moods(rows: list[dict[str, Any]], now: datetime,
                 cfg: SignalConfig) -> list[dict[str, Any]]:
    """Rows from ``NewsStore.scored_since``; returns one summary per symbol,
    strongest mood first."""
    decay = math.log(2) / max(cfg.half_life_hours, 0.1)
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_symbol.setdefault(row["symbol"], []).append(row)

    out = []
    for symbol, scored in by_symbol.items():
        stories: list[dict[str, Any]] = []
        social = 0.0
        social_weight = 0.0
        posts = 0
        for row in sorted(scored, key=lambda r: r["published"]):
            age = _hours(now, row["published"])
            impact = (row["direction"] * row["strength"] * row["probability"]
                      * math.exp(-decay * age))
            if row["source"] == "x":
                weight = 1 + math.log1p(max(0, row["engagement"]))
                social += impact * weight
                social_weight += weight
                posts += 1
                continue
            sign = 1 if row["direction"] > 0 else -1 if row["direction"] < 0 else 0
            same = next((s for s in stories if s["event"] == row["event"] and s["sign"] == sign
                         and _hours(datetime.fromisoformat(row["published"]), s["published"])
                         <= cfg.merge_hours), None)
            if same is None:
                stories.append({"event": row["event"], "sign": sign, "impact": impact,
                                "published": row["published"], "headline": row["headline"],
                                "url": row["url"], "copies": 1})
            else:
                same["copies"] += 1
                if abs(impact) > abs(same["impact"]):
                    same.update(impact=impact, headline=row["headline"], url=row["url"])
        social_part = 0.0
        if social_weight:
            social_part = max(-cfg.social_cap, min(cfg.social_cap, social / social_weight))
        news_part = sum(s["impact"] for s in stories)
        mood = math.tanh(news_part + social_part)
        top = max(stories, key=lambda s: abs(s["impact"]), default=None)
        signal = ""
        # A signal needs news pointing the same way, never posts alone, and
        # its strongest story must be more than hype ("shares surge on rumour").
        if (top is not None and top["event"] != "hype" and abs(mood) >= cfg.threshold
                and news_part * mood > 0):
            signal = "buy" if mood > 0 else "sell"
        out.append({
            "symbol": symbol,
            "mood": round(mood, 3),
            "stories": len(stories),
            "copies": sum(s["copies"] for s in stories),
            "posts": posts,
            "social": round(social_part, 3),
            "event": top["event"] if top else "",
            "headline": top["headline"] if top else "",
            "url": top["url"] if top else "",
            "signal": signal,
        })
    return sorted(out, key=lambda s: -abs(s["mood"]))


def since(now: datetime, cfg: SignalConfig) -> datetime:
    return now - timedelta(hours=cfg.window_hours)
