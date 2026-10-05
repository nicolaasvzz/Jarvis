"""Regime-switching synthetic OHLCV generator.

Produces realistic-looking daily price series by switching between bull,
bear, chop, and crash regimes via a Markov chain. Deterministic per
(symbol, seed) so backtests and tests are reproducible.
"""
from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

# Regime: (daily drift, daily vol, transition stickiness)
_REGIMES = {
    "bull": (0.0009, 0.010),
    "bear": (-0.0007, 0.016),
    "chop": (0.0001, 0.008),
    "crash": (-0.0060, 0.045),
}
_NAMES = list(_REGIMES)

# Row = from-regime, column = to-regime probability.
_TRANSITIONS = np.array(
    [
        # bull   bear   chop   crash
        [0.986, 0.004, 0.008, 0.002],  # bull
        [0.010, 0.975, 0.010, 0.005],  # bear
        [0.012, 0.008, 0.979, 0.001],  # chop
        [0.150, 0.250, 0.100, 0.500],  # crash: short-lived
    ]
)


def generate_ohlcv(
    symbol: str,
    days: int = 1260,
    start_price: float | None = None,
    seed: int = 42,
    end: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Generate `days` business days of OHLCV ending at `end` (default today)."""
    # Blend the symbol into the seed so each ticker gets its own path.
    rng = np.random.default_rng(seed + zlib.crc32(symbol.encode()))
    if start_price is None:
        start_price = float(rng.uniform(20, 400))

    regime = int(rng.integers(0, len(_NAMES)))
    closes = np.empty(days)
    opens = np.empty(days)
    highs = np.empty(days)
    lows = np.empty(days)
    volumes = np.empty(days)

    price = start_price
    for i in range(days):
        regime = int(rng.choice(len(_NAMES), p=_TRANSITIONS[regime]))
        drift, vol = _REGIMES[_NAMES[regime]]
        ret = rng.normal(drift, vol)
        o = price * (1 + rng.normal(0, vol * 0.3))
        c = price * (1 + ret)
        intraday = abs(rng.normal(0, vol)) * price
        highs[i] = max(o, c) + intraday * rng.uniform(0.2, 1.0)
        lows[i] = max(0.01, min(o, c) - intraday * rng.uniform(0.2, 1.0))
        opens[i] = o
        closes[i] = c
        volumes[i] = rng.lognormal(13, 0.6) * (1 + 3 * vol)
        price = c

    end_ts = pd.Timestamp(end) if end is not None else pd.Timestamp.today().normalize()
    index = pd.bdate_range(end=end_ts, periods=days)
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes.astype(np.int64),
        },
        index=index,
    )
