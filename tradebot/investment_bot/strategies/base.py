"""Strategy contract: history in, directional signal out.

A strategy sees one symbol's OHLCV history up to the current bar close and
returns a Signal: direction (-1 short / 0 flat / +1 long) plus a conviction
in [0, 1]. Strategies are stateless between calls — all state lives in the
history window — which keeps backtest and live behavior identical.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class Signal:
    direction: int  # -1, 0, +1
    conviction: float = 1.0  # 0..1
    reason: str = ""

    def __post_init__(self):
        if self.direction not in (-1, 0, 1):
            raise ValueError(f"direction must be -1/0/+1, got {self.direction}")
        if not 0.0 <= self.conviction <= 1.0:
            raise ValueError(f"conviction must be in [0,1], got {self.conviction}")

    @property
    def score(self) -> float:
        return self.direction * self.conviction


FLAT = Signal(0, 0.0, "flat")


@dataclass
class Strategy(ABC):
    """Base class. Subclasses declare params as dataclass fields."""

    name: str = field(init=False, default="")

    def __post_init__(self):
        if not self.name:
            self.name = type(self).__name__

    @property
    @abstractmethod
    def warmup(self) -> int:
        """Minimum bars of history required before signals are valid."""

    @abstractmethod
    def signal(self, history: pd.DataFrame) -> Signal:
        """Compute the signal from OHLCV history (last row = latest close)."""

    def ready(self, history: pd.DataFrame) -> bool:
        return len(history) >= self.warmup
