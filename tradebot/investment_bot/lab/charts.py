"""Three charts of one symbol - 1-hour, 30-minute and 4-hour - lined up on the 1-hour one.

The champ-set builder and its trader decide once an hour, when a 1-hour
candle closes, and read all three charts at that moment:

- the **1h** chart, where trades are decided;
- the **30m** chart, the closer look: the half-hour candle that closes with
  the hour counts;
- the **4h** chart, the bigger picture: a 4-hour candle only counts once it
  has closed.

All three are built from 10-minute candles. Stock candles follow the trading
day, as stock charts do: they start at the 9:30 New York open, and the day's
last one ends at the 16:00 close. So a day has 13 half-hours, 7 hours (the
last one half an hour long) and two 4-hour candles, 9:30-13:30 and
13:30-16:00, whatever the season. Crypto trades around the clock, so its
candles start on the hour in UTC (4-hour ones at 00:00, 04:00, ...).

Every decision row (one per 1-hour candle) also carries what the trading rules
need: that candle's prices, its ATR(14), the 4-hour chart's ATR(14) (how far
a few hours usually move), when it closes, and for stocks when that trading
day ends.

**Bad prints are cleaned first** (``clean``). Alpaca's crypto candles are
mostly quotes rather than trades (60-95% of 10-minute candles have no volume
for most coins), and some carry one-candle spikes that snap straight back
(AAVE 250 -> 290 -> 249 in twenty minutes). A target "filled" on such a spike
is money no one could have made. So no 10-minute candle may move further from
the previous clean price than ``CLEAN_K`` times that symbol's usual 10-minute
move, and no wick may reach further than that beyond the candle's body. The
filter only looks back, so the builder and the trader see the same candles;
it starts afresh each trading day for stocks (overnight gaps are real) and
after any hole in the data.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import indicators as ind
from .catalog import CATALOG, compute
from .features import duration, ns, resample

CHARTS = ("1h", "30m", "4h")   # the decision chart first: feature columns follow this order
DECIDE = "1h"
NY = "America/New_York"
OPEN = pd.Timedelta(hours=9, minutes=30)
CLOSE = pd.Timedelta(hours=16)
BAR_COLUMNS = ["open", "high", "low", "close", "volume"]
VERSION = 3                    # bump when chart building changes: stores get rebuilt
CLEAN_K = 6.0                  # furthest a 10-minute candle may move, in usual moves
CLEAN_WINDOW = 144             # usual move: over the last day of 10-minute candles
CLEAN_FLOOR = 1e-4             # ...but never taken as less than 1 bp
RESET_AFTER = pd.Timedelta(hours=1)


def clean(raw: pd.DataFrame, stock: bool) -> pd.DataFrame:
    """10-minute candles with bad prints tamed (see the module notes). Looks back only."""
    if len(raw) < 2:
        return raw
    o, h, lo, c = (raw[k].to_numpy(dtype=float).copy() for k in ("open", "high", "low", "close"))
    with np.errstate(divide="ignore", invalid="ignore"):
        moves = pd.Series(np.abs(np.diff(np.log(c), prepend=np.nan)))
    # A robust standard deviation of the 10-minute moves before this candle.
    usual = moves.rolling(CLEAN_WINDOW, min_periods=12).median().shift(1) * 1.4826
    limit = np.maximum(np.nan_to_num(usual.to_numpy(), nan=np.inf), CLEAN_FLOOR) * CLEAN_K
    starts = raw.index
    fresh = np.zeros(len(raw), dtype=bool)
    fresh[0] = True
    fresh[1:] = (starts[1:] - starts[:-1]) > RESET_AFTER
    if stock:
        days = starts.tz_convert(NY).normalize()
        fresh[1:] |= days[1:] != days[:-1]
    prev = c[0]
    for i in range(len(c)):
        lim = limit[i]
        if not fresh[i] and np.isfinite(lim) and prev > 0:
            lo_p, hi_p = prev * math.exp(-lim), prev * math.exp(lim)
            o[i] = min(max(o[i], lo_p), hi_p)
            c[i] = min(max(c[i], lo_p), hi_p)
        if np.isfinite(lim) and c[i] > 0:
            top, bottom = max(o[i], c[i]), min(o[i], c[i])
            h[i] = max(min(h[i], top * math.exp(lim)), top)
            lo[i] = min(max(lo[i], bottom * math.exp(-lim)), bottom)
        prev = c[i]
    out = raw.copy()
    out["open"], out["high"], out["low"], out["close"] = o, h, lo, c
    return out


def candles(raw: pd.DataFrame, tf: str, stock: bool) -> tuple[pd.DataFrame, np.ndarray]:
    """``tf`` candles from 10-minute ones (indexed by start), and when each closes (ns)."""
    step = duration(tf)
    if raw.empty:
        return raw[BAR_COLUMNS].iloc[:0], np.array([], dtype="int64")
    if not stock:
        out = resample(raw[BAR_COLUMNS], tf)
        return out, ns(out.index) + step.value
    # Stocks: buckets counted from the 9:30 open of each New York trading day. Done in
    # wall-clock time, then put back in UTC (no trading day has a daylight-saving change).
    wall = raw.index.tz_convert(NY).tz_localize(None)
    day = wall.normalize()
    bucket = (wall - day - OPEN) // step
    start_wall = day + OPEN + bucket * step
    start = pd.DatetimeIndex(start_wall).tz_localize(NY).tz_convert("UTC")
    grouped = raw[BAR_COLUMNS].groupby(start)
    out = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last",
                       "volume": "sum"})
    out.index = pd.DatetimeIndex(out.index).as_unit("ns")
    out.index.name = None
    out = out.dropna(subset=["close"])
    begins = out.index.tz_convert(NY).tz_localize(None)
    closes = np.minimum((begins + step).to_numpy(), (begins.normalize() + CLOSE).to_numpy())
    closes = pd.DatetimeIndex(closes).tz_localize(NY).tz_convert("UTC")
    return out, ns(closes)


def session_ends(index: pd.DatetimeIndex) -> np.ndarray:
    """For each candle start, when that New York trading day closes (ns)."""
    wall = index.tz_convert(NY).tz_localize(None)
    ends = pd.DatetimeIndex(wall.normalize() + CLOSE).tz_localize(NY).tz_convert("UTC")
    return ns(ends)


def _align(values: np.ndarray, known: np.ndarray, at: np.ndarray) -> np.ndarray:
    """Each row of ``at``: the last row of ``values`` known by then (NaN before any)."""
    pos = np.searchsorted(known, at, side="right") - 1
    out = np.full((len(at),) + values.shape[1:], np.nan, dtype=np.float32)
    ok = pos >= 0
    out[ok] = values[pos[ok]]
    return out


def decision_features(
    raw: pd.DataFrame,
    stock: bool,
    names: dict[str, list[str]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The 1-hour decision rows and every indicator on all three charts, aligned to them.

    ``raw`` holds 10-minute candles (cleaned here). ``names`` limits the indicators per chart
    ({"1h": [...], "4h": [...]}); None means all of them on every chart.
    Returns (bars, features): bars has the 1-hour candle's prices plus ``atr``,
    ``atr4h``, ``closes`` and ``session_end`` (0 for crypto); feature columns are
    ``<indicator>@<chart>``.
    """
    raw = clean(raw[BAR_COLUMNS], stock)
    frames = {tf: candles(raw, tf, stock) for tf in CHARTS}
    decide, decided = frames[DECIDE]
    bars = decide.astype(float).copy()
    bars["atr"] = ind.atr(bars["high"], bars["low"], bars["close"], 14).to_numpy()
    four, four_closes = frames["4h"]
    atr4 = ind.atr(four["high"].astype(float), four["low"].astype(float),
                   four["close"].astype(float), 14).to_numpy()
    bars["atr4h"] = _align(atr4.reshape(-1, 1), four_closes, decided)[:, 0].astype(float)
    bars["closes"] = decided
    bars["session_end"] = session_ends(bars.index) if stock else np.zeros(len(bars), "int64")
    parts = []
    for tf in CHARTS:
        wanted = list(CATALOG) if names is None else names.get(tf) or []
        if not wanted:
            continue
        frame, closes = frames[tf]
        values = compute(frame, wanted)
        if tf == DECIDE:
            aligned = values.to_numpy(dtype=np.float32)
        else:
            aligned = _align(values.to_numpy(dtype=np.float32), closes, decided)
        parts.append(pd.DataFrame(aligned, index=bars.index,
                                  columns=[f"{c}@{tf}" for c in values.columns]))
    features = pd.concat(parts, axis=1) if parts else pd.DataFrame(index=bars.index)
    return bars, features


def all_columns() -> list[str]:
    return [f"{n}@{tf}" for tf in CHARTS for n in CATALOG]


def chart_of(feature: str) -> str:
    return feature.split("@", 1)[1]
