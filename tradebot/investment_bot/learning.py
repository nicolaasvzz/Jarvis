"""Online learning: adapt ensemble member weights from realized outcomes.

The ensemble's members vote on every decision; this module closes the loop by
scoring those votes against what actually happened and shifting weight toward
members that have been right — a multiplicative-weights ("Hedge") update.

Two feedback channels, one update rule:

- **Trade feedback** (the big one): when a round trip closes, each member's
  entry vote is scored against the trade's directional return. A member that
  argued for a losing trade loses weight; one that argued against it gains.
- **Bar feedback** (the fast one): each time a new daily bar arrives, every
  member's previous vote on that symbol is scored against the realized
  bar-to-bar return. Small steps, but ~one per symbol per day, so learning
  is visible long before trades close.

Guardrails, because online learning on few samples overfits eagerly:
returns are capped before they enter the exponent, weights shrink toward
uniform on every update, and a floor keeps any member from being silenced
entirely (it must keep voting so it can earn its way back).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


@dataclass
class LearningConfig:
    enabled: bool = False
    eta_trade: float = 4.0    # step size for closed-trade feedback
    eta_signal: float = 1.5   # step size for per-bar vote feedback
    return_cap: float = 0.10  # |return| clip before entering the exponent
    weight_floor: float = 0.05  # minimum share any member can fall to
    shrink: float = 0.002     # pull toward equal weights per lesson;
    # shrink always points at uniform while learning pushes are random-signed, so
    # it accumulates linearly against a ~sqrt(n) signal. 0.002 = ~43-day memory at
    # 8 lessons/day; 0.02 forgets everything in ~4 days and erases any real edge.
    history: int = 40         # recent learn events kept for display


class AdaptiveWeights:
    """Multiplicative-weights learner over the ensemble's members."""

    def __init__(self, names: list[str], initial_weights: list[float], config: LearningConfig):
        if len(names) != len(initial_weights):
            raise ValueError("names and initial_weights must align")
        self.config = config
        self.names = list(names)
        total = sum(initial_weights)
        if total <= 0:
            raise ValueError("initial weights must sum to a positive number")
        self.weights = {n: w / total for n, w in zip(names, initial_weights)}
        self.stats = {n: {"events": 0, "hits": 0, "score": 0.0} for n in names}
        self.trades_seen = 0
        self.bars_seen = 0
        self.events: deque[dict] = deque(maxlen=config.history)

    # ---------------- feedback channels ----------------

    def trade_feedback(
        self, votes: dict[str, float], direction: int, trade_return: float, label: str = ""
    ) -> bool:
        """Score entry votes against a closed trade's directional return.

        `direction` is the executed trade's side; a member's stance is its
        vote projected onto that side (positive = supported the trade).
        """
        stances = {n: votes.get(n, 0.0) * direction for n in self.names}
        changed = self._apply(stances, trade_return, self.config.eta_trade, "trade", label)
        if changed:
            self.trades_seen += 1
        return changed

    def bar_feedback(self, votes: dict[str, float], bar_return: float, label: str = "") -> bool:
        """Score yesterday's votes against the realized bar return."""
        stances = {n: votes.get(n, 0.0) for n in self.names}
        changed = self._apply(stances, bar_return, self.config.eta_signal, "bar", label)
        if changed:
            self.bars_seen += 1
        return changed

    # ---------------- the update rule ----------------

    def _apply(
        self, stances: dict[str, float], realized: float, eta: float, kind: str, label: str
    ) -> bool:
        cap = self.config.return_cap
        r = max(-cap, min(cap, float(realized)))
        if r == 0.0 or all(abs(s) < 1e-9 for s in stances.values()):
            return False  # nothing to learn from

        before = dict(self.weights)
        for n in self.names:
            gain = stances[n] * r  # >0: member was right, <0: member was wrong
            self.weights[n] *= math.exp(eta * gain)
            if abs(stances[n]) >= 1e-9:
                st = self.stats[n]
                st["events"] += 1
                if gain > 0:
                    st["hits"] += 1
                st["score"] += gain

        self._normalize()
        # Shrink toward uniform, then floor: keeps every voice in the room.
        lam = self.config.shrink
        uniform = 1.0 / len(self.names)
        for n in self.names:
            self.weights[n] = (1 - lam) * self.weights[n] + lam * uniform
        self._floor_and_normalize()

        moves = sorted(
            ((n, self.weights[n] - before[n]) for n in self.names),
            key=lambda kv: abs(kv[1]),
            reverse=True,
        )
        summary = ", ".join(
            f"{n} {before[n]:.0%}→{self.weights[n]:.0%}" for n, d in moves[:2] if abs(d) >= 0.0005
        )
        self.events.append(
            {"kind": kind, "label": label, "return": round(realized, 5), "moves": summary}
        )
        return True

    def _normalize(self) -> None:
        total = sum(self.weights.values())
        for n in self.names:
            self.weights[n] /= total

    def _floor_and_normalize(self) -> None:
        """Normalize to 1 while guaranteeing every weight >= weight_floor.

        Members below the floor are pinned to it and the remaining budget is
        split proportionally among the rest (repeating if that pushes anyone
        else under - standard water-filling).
        """
        floor = self.config.weight_floor
        if floor * len(self.names) >= 1.0:
            for n in self.names:
                self.weights[n] = 1.0 / len(self.names)
            return
        floored: set[str] = set()
        while True:
            free = [n for n in self.names if n not in floored]
            total_free = sum(self.weights[n] for n in free)
            budget = 1.0 - floor * len(floored)
            scaled = {n: self.weights[n] / total_free * budget for n in free}
            newly_low = [n for n in free if scaled[n] < floor]
            if not newly_low:
                for n in floored:
                    self.weights[n] = floor
                self.weights.update(scaled)
                return
            floored.update(newly_low)

    # ---------------- persistence & display ----------------

    def to_dict(self) -> dict:
        return {
            "weights": dict(self.weights),
            "stats": {n: dict(s) for n, s in self.stats.items()},
            "trades_seen": self.trades_seen,
            "bars_seen": self.bars_seen,
            "events": list(self.events),
        }

    def restore(self, state: dict) -> None:
        """Load persisted learning state (ignoring members that no longer exist)."""
        for n, w in (state.get("weights") or {}).items():
            if n in self.weights and w > 0:
                self.weights[n] = float(w)
        self._normalize()
        for n, s in (state.get("stats") or {}).items():
            if n in self.stats:
                self.stats[n] = {
                    "events": int(s.get("events", 0)),
                    "hits": int(s.get("hits", 0)),
                    "score": float(s.get("score", 0.0)),
                }
        self.trades_seen = int(state.get("trades_seen", 0))
        self.bars_seen = int(state.get("bars_seen", 0))
        for e in state.get("events") or []:
            self.events.append(e)

    def summary(self) -> dict:
        members = []
        for n in self.names:
            st = self.stats[n]
            members.append(
                {
                    "name": n,
                    "weight": round(self.weights[n], 4),
                    "events": st["events"],
                    "hits": st["hits"],
                    "hit_rate": round(st["hits"] / st["events"], 3) if st["events"] else None,
                    "score": round(st["score"], 5),
                }
            )
        return {
            "enabled": True,
            "trades_seen": self.trades_seen,
            "bars_seen": self.bars_seen,
            "members": members,
            "recent": list(self.events)[-8:],
        }
