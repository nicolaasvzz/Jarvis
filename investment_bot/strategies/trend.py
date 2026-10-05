"""Trend-following strategies: moving-average cross, MACD, channel breakout."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .. import indicators as ind
from .base import FLAT, Signal, Strategy


@dataclass
class SmaCross(Strategy):
    """Golden/death cross: long when fast SMA above slow SMA.

    Conviction scales with the normalized spread between the averages.
    """

    fast: int = 20
    slow: int = 100

    def __post_init__(self):
        super().__post_init__()
        if self.fast >= self.slow:
            raise ValueError("fast window must be < slow window")

    @property
    def warmup(self) -> int:
        return self.slow + 5

    def signal(self, history: pd.DataFrame) -> Signal:
        close = history["close"]
        fast = ind.sma(close, self.fast).iloc[-1]
        slow = ind.sma(close, self.slow).iloc[-1]
        if np.isnan(fast) or np.isnan(slow) or slow == 0:
            return FLAT
        spread = (fast - slow) / slow
        conviction = float(min(abs(spread) / 0.05, 1.0))
        if spread > 0:
            return Signal(1, conviction, f"SMA{self.fast}>{self.slow} by {spread:+.2%}")
        return Signal(-1, conviction, f"SMA{self.fast}<{self.slow} by {spread:+.2%}")


@dataclass
class MacdMomentum(Strategy):
    """MACD histogram momentum: long when MACD is above its signal line."""

    fast: int = 12
    slow: int = 26
    smooth: int = 9

    @property
    def warmup(self) -> int:
        return self.slow + self.smooth + 10

    def signal(self, history: pd.DataFrame) -> Signal:
        close = history["close"]
        hist = ind.macd_hist(close, self.fast, self.slow, self.smooth).iloc[-1]
        if np.isnan(hist):
            return FLAT
        # Normalize histogram by price so conviction is scale-free.
        norm = abs(hist) / close.iloc[-1]
        conviction = float(min(norm / 0.01, 1.0))
        direction = 1 if hist > 0 else -1
        return Signal(direction, conviction, f"MACD hist {hist:+.3f}")


@dataclass
class DonchianBreakout(Strategy):
    """Turtle-style breakout: long on new N-day high, short on new N-day low.

    Between breakouts, holds the last breakout direction until the mid-channel
    is crossed against it.
    """

    entry_window: int = 55
    exit_window: int = 20

    @property
    def warmup(self) -> int:
        return self.entry_window + 5

    def signal(self, history: pd.DataFrame) -> Signal:
        high, low, close = history["high"], history["low"], history["close"]
        # Exclude the current bar from the channel so today's print can break it.
        prior_high, prior_low = high.iloc[:-1], low.iloc[:-1]
        entry_upper, entry_lower = ind.donchian_last(prior_high, prior_low, self.entry_window)
        exit_upper, exit_lower = ind.donchian_last(prior_high, prior_low, self.exit_window)
        c = close.iloc[-1]
        if np.isnan([entry_upper, entry_lower, exit_upper, exit_lower]).any():
            return FLAT
        if c > entry_upper:
            return Signal(1, 1.0, f"breakout above {self.entry_window}d high")
        if c < entry_lower:
            return Signal(-1, 1.0, f"breakdown below {self.entry_window}d low")
        mid = (exit_upper + exit_lower) / 2
        # Weak continuation bias from position within the exit channel.
        if c > mid:
            return Signal(1, 0.3, "above exit-channel mid")
        if c < mid:
            return Signal(-1, 0.3, "below exit-channel mid")
        return FLAT
