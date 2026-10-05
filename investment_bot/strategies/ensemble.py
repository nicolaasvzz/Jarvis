"""Weighted ensemble: combine member strategies into one net signal.

Each member votes with score = direction * conviction; votes are combined by
configurable weights and the net score must clear a threshold to trade. A
volatility-regime filter can veto entries when the market is too wild.

Weights are per-member and mutable at runtime: `apply_weights` lets an online
learner (see `investment_bot.learning`) shift influence toward members whose
votes have been right, and `signal_and_votes` exposes the per-member votes so
outcomes can be attributed back to the members that argued for them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import indicators as ind
from .base import FLAT, Signal, Strategy


@dataclass
class Ensemble(Strategy):
    members: list[Strategy] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    threshold: float = 0.25  # |net score| needed to emit a direction
    max_volatility: float | None = None  # e.g. 0.60 = veto entries above 60% ann. vol
    long_only: bool = False
    short_threshold: float | None = None  # stricter bar for shorts; None = threshold

    def __post_init__(self):
        super().__post_init__()
        if not self.members:
            raise ValueError("Ensemble needs at least one member strategy")
        if not self.weights:
            self.weights = [1.0] * len(self.members)
        if len(self.weights) != len(self.members):
            raise ValueError("weights must match members")
        total = sum(self.weights)
        if total <= 0:
            raise ValueError("weights must sum to a positive number")
        self.weights = [w / total for w in self.weights]

    @property
    def warmup(self) -> int:
        return max(m.warmup for m in self.members)

    @property
    def member_names(self) -> list[str]:
        return [m.name for m in self.members]

    @property
    def weight_map(self) -> dict[str, float]:
        return {m.name: w for m, w in zip(self.members, self.weights)}

    def apply_weights(self, mapping: dict[str, float]) -> None:
        """Replace weights by member name (missing names keep their weight)."""
        updated = [float(mapping.get(m.name, w)) for m, w in zip(self.members, self.weights)]
        total = sum(updated)
        if total <= 0:
            raise ValueError("weights must sum to a positive number")
        self.weights = [w / total for w in updated]

    def member_signals(self, history: pd.DataFrame) -> dict[str, Signal]:
        return {
            m.name: (m.signal(history) if m.ready(history) else FLAT)
            for m in self.members
        }

    def signal(self, history: pd.DataFrame) -> Signal:
        return self.signal_and_votes(history)[0]

    def signal_and_votes(self, history: pd.DataFrame) -> tuple[Signal, dict[str, float]]:
        """The combined signal plus each member's raw vote (direction*conviction)."""
        signals = self.member_signals(history)
        votes = {name: s.score for name, s in signals.items()}
        net = float(sum(w * s.score for w, s in zip(self.weights, signals.values())))

        if self.max_volatility is not None and len(history) > 25:
            vol = ind.realized_volatility(history["close"], 20).iloc[-1]
            if not np.isnan(vol) and vol > self.max_volatility:
                return Signal(0, 0.0, f"vol veto ({vol:.0%} > {self.max_volatility:.0%})"), votes

        if abs(net) < self.threshold:
            return Signal(0, 0.0, f"net {net:+.2f} below threshold"), votes
        direction = 1 if net > 0 else -1
        if self.long_only and direction < 0:
            return Signal(0, 0.0, f"short vetoed (long-only), net {net:+.2f}"), votes
        if direction < 0 and self.short_threshold is not None and -net < self.short_threshold:
            return Signal(0, 0.0, f"net {net:+.2f} below short threshold"), votes
        voters = ", ".join(
            f"{name}:{s.score:+.2f}" for name, s in signals.items() if s.direction != 0
        )
        return Signal(direction, min(abs(net), 1.0), f"net {net:+.2f} [{voters}]"), votes
