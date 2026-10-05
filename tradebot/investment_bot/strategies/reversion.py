"""Mean-reversion strategies: Bollinger z-score fade and RSI extremes."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .. import indicators as ind
from .base import FLAT, Signal, Strategy


@dataclass
class BollingerReversion(Strategy):
    """Fade moves outside the Bollinger bands, exit at the middle band.

    Conviction grows with how far beyond the band price has stretched.
    """

    window: int = 20
    num_std: float = 2.0

    @property
    def warmup(self) -> int:
        return self.window + 5

    def signal(self, history: pd.DataFrame) -> Signal:
        close = history["close"]
        z = ind.zscore(close, self.window).iloc[-1]
        if np.isnan(z):
            return FLAT
        if z <= -self.num_std:
            conviction = float(min((abs(z) - self.num_std) / 2.0 + 0.5, 1.0))
            return Signal(1, conviction, f"z={z:.2f} below -{self.num_std} band")
        if z >= self.num_std:
            conviction = float(min((abs(z) - self.num_std) / 2.0 + 0.5, 1.0))
            return Signal(-1, conviction, f"z={z:.2f} above +{self.num_std} band")
        return FLAT


@dataclass
class RsiReversion(Strategy):
    """Buy oversold / sell overbought RSI extremes."""

    window: int = 14
    oversold: float = 30.0
    overbought: float = 70.0

    @property
    def warmup(self) -> int:
        return self.window * 3

    def signal(self, history: pd.DataFrame) -> Signal:
        r = ind.rsi(history["close"], self.window).iloc[-1]
        if np.isnan(r):
            return FLAT
        if r <= self.oversold:
            conviction = float(min((self.oversold - r) / self.oversold + 0.5, 1.0))
            return Signal(1, conviction, f"RSI {r:.1f} oversold")
        if r >= self.overbought:
            conviction = float(min((r - self.overbought) / (100 - self.overbought) + 0.5, 1.0))
            return Signal(-1, conviction, f"RSI {r:.1f} overbought")
        return FLAT
