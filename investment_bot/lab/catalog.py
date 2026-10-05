"""Every indicator the bot knows, each turned into a vote between -1 and +1.

An indicator here is a function of one symbol's candles that returns a
series in [-1, 1]: positive leans "price goes up", negative "price goes
down", the size is how strongly. The orientation is only the textbook
reading (RSI high = strong momentum, not "overbought"): the scout learns from
history whether each one should be followed or faded, and packages carry
that sign in their weights. NaN while there isn't enough history yet.

Non-directional indicators (ATR, band width, choppiness, volume) are framed
directionally too, usually as "strength x direction", so that every family
can vote. Families: trend, momentum, volatility, volume, candles.

All of it is vectorized pandas/numpy, except Parabolic SAR and Supertrend,
which are path-dependent and run as small loops.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .. import indicators as ind

FAMILIES = ("trend", "momentum", "volatility", "volume", "candles")


@dataclass(frozen=True)
class Indicator:
    name: str
    family: str
    fn: Callable[[Bars], pd.Series]


CATALOG: dict[str, Indicator] = {}


def _register(name: str, family: str):
    def wrap(fn: Callable[[Bars], pd.Series]) -> Callable[[Bars], pd.Series]:
        if name in CATALOG:
            raise ValueError(f"indicator {name!r} registered twice")
        CATALOG[name] = Indicator(name, family, fn)
        return fn

    return wrap


def _add(name: str, family: str, fn: Callable[[Bars], pd.Series]) -> None:
    _register(name, family)(fn)


# ---------------------------------------------------------------- helpers


class Bars:
    """One symbol's candles, plus intermediate values shared by indicators."""

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.o = df["open"].astype(float)
        self.h = df["high"].astype(float)
        self.l = df["low"].astype(float)
        self.c = df["close"].astype(float)
        self.v = df["volume"].astype(float)
        self._cache: dict[Any, pd.Series] = {}

    def memo(self, key: Any, make: Callable[[], pd.Series]) -> pd.Series:
        if key not in self._cache:
            self._cache[key] = make()
        return self._cache[key]

    @property
    def atr(self) -> pd.Series:
        """ATR(14), floored so flat stretches don't divide by zero."""
        return self.memo("atr", lambda: ind.atr(self.h, self.l, self.c, 14).clip(
            lower=self.c.abs() * 1e-5))

    @property
    def typical(self) -> pd.Series:
        return self.memo("typical", lambda: (self.h + self.l + self.c) / 3)

    @property
    def body(self) -> pd.Series:
        return self.memo("body", lambda: self.c - self.o)

    @property
    def rng(self) -> pd.Series:
        return self.memo("rng", lambda: (self.h - self.l).replace(0.0, np.nan))

    def ema(self, n: int, series: str = "c") -> pd.Series:
        return self.memo(("ema", n, series), lambda: ind.ema(getattr(self, series), n))

    def sma(self, n: int, series: str = "c") -> pd.Series:
        return self.memo(("sma", n, series), lambda: ind.sma(getattr(self, series), n))

    @property
    def trend10(self) -> pd.Series:
        """-1/+1: the prior 5 bars fell/rose (context for candle patterns)."""
        return self.memo("trend10", lambda: np.sign(self.c.shift(1) - self.c.shift(6)))


def _safe(x: pd.Series) -> pd.Series:
    return x.replace([np.inf, -np.inf], np.nan)


def _tanh(x: pd.Series, scale: float = 1.0) -> pd.Series:
    return np.tanh(_safe(x) / scale)


def _osc(x: pd.Series, mid: float = 50.0, half: float = 50.0) -> pd.Series:
    return ((_safe(x) - mid) / half).clip(-1.0, 1.0)


def _z(x: pd.Series, window: int = 100) -> pd.Series:
    return np.tanh(ind.zscore(_safe(x), window) / 2.0)


def _per_atr(x: pd.Series, b: Bars, scale: float = 2.0) -> pd.Series:
    return _tanh(x / b.atr, scale)


def _wma(s: pd.Series, n: int) -> pd.Series:
    weights = np.arange(1, n + 1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda a: a @ weights / weights.sum(), raw=True)


def _rolling_sum_weighted(y: np.ndarray, n: int) -> np.ndarray:
    """sum_{k=0}^{n-1} k * y[t-n+1+k] for each t (NaN for t < n-1)."""
    out = np.full(len(y), np.nan)
    if len(y) >= n:
        out[n - 1:] = np.convolve(y, np.arange(n, dtype=float), mode="valid")
    return out


def _linreg_slope(s: pd.Series, n: int) -> pd.Series:
    """Rolling least-squares slope per bar."""
    y = s.to_numpy(dtype=float)
    sx = n * (n - 1) / 2
    sxx = (n - 1) * n * (2 * n - 1) / 6
    sy = s.rolling(n, min_periods=n).sum().to_numpy()
    sxy = _rolling_sum_weighted(np.nan_to_num(y), n)
    sxy[np.isnan(sy)] = np.nan
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    return pd.Series(slope, index=s.index)


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def _dmi(b: Bars, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    def make() -> pd.Series:
        up = b.h.diff()
        down = -b.l.diff()
        plus_dm = up.where((up > down) & (up > 0), 0.0)
        minus_dm = down.where((down > up) & (down > 0), 0.0)
        tr = _wilder(ind.true_range(b.h, b.l, b.c), n).replace(0.0, np.nan)
        pdi = 100 * _wilder(plus_dm, n) / tr
        mdi = 100 * _wilder(minus_dm, n) / tr
        dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0.0, np.nan)
        adx = _wilder(dx, n)
        return pd.concat([pdi, mdi, adx], axis=1)

    frame = b.memo(("dmi", n), make)
    return frame.iloc[:, 0], frame.iloc[:, 1], frame.iloc[:, 2]


def _stoch_k(b: Bars, n: int = 14) -> pd.Series:
    def make() -> pd.Series:
        lo = b.l.rolling(n, min_periods=n).min()
        hi = b.h.rolling(n, min_periods=n).max()
        return 100 * (b.c - lo) / (hi - lo).replace(0.0, np.nan)

    return b.memo(("stoch", n), make)


def _rsi(b: Bars, n: int) -> pd.Series:
    return b.memo(("rsi", n), lambda: ind.rsi(b.c, n))


def _streak(c: pd.Series) -> pd.Series:
    """Consecutive up (+) or down (-) closes."""
    sign = np.sign(c.diff()).fillna(0.0)
    groups = (sign != sign.shift()).cumsum()
    return sign.groupby(groups).cumsum()


def _psar(b: Bars, step: float = 0.02, cap: float = 0.2) -> pd.Series:
    high, low = b.h.to_numpy(), b.l.to_numpy()
    n = len(high)
    out = np.full(n, np.nan)
    if n < 3:
        return pd.Series(out, index=b.c.index)
    rising, af = True, step
    sar, extreme = low[0], high[0]
    for i in range(1, n):
        sar = sar + af * (extreme - sar)
        if rising:
            sar = min(sar, low[i - 1], low[i - 2] if i > 1 else low[i - 1])
            if low[i] < sar:
                rising, sar, extreme, af = False, extreme, low[i], step
            elif high[i] > extreme:
                extreme, af = high[i], min(af + step, cap)
        else:
            sar = max(sar, high[i - 1], high[i - 2] if i > 1 else high[i - 1])
            if high[i] > sar:
                rising, sar, extreme, af = True, extreme, high[i], step
            elif low[i] < extreme:
                extreme, af = low[i], min(af + step, cap)
        out[i] = sar
    return pd.Series(out, index=b.c.index)


def _supertrend(b: Bars, n: int = 10, mult: float = 3.0) -> pd.Series:
    """+1 / -1 Supertrend direction."""
    atr = ind.atr(b.h, b.l, b.c, n).to_numpy()
    mid = ((b.h + b.l) / 2).to_numpy()
    close = b.c.to_numpy()
    upper_basic, lower_basic = mid + mult * atr, mid - mult * atr
    out = np.full(len(close), np.nan)
    upper = lower = np.nan
    direction = 1.0
    for i in range(len(close)):
        if np.isnan(atr[i]):
            continue
        if np.isnan(upper):
            upper, lower = upper_basic[i], lower_basic[i]
        else:
            upper = upper_basic[i] if (upper_basic[i] < upper or close[i - 1] > upper) else upper
            lower = lower_basic[i] if (lower_basic[i] > lower or close[i - 1] < lower) else lower
        if close[i] > upper:
            direction = 1.0
        elif close[i] < lower:
            direction = -1.0
        out[i] = direction
    return pd.Series(out, index=b.c.index)


# ---------------------------------------------------------------- trend

for fast, slow in ((10, 50), (20, 100), (50, 200)):
    _add(f"sma_cross_{fast}_{slow}", "trend",
         lambda b, f=fast, s=slow: _per_atr(b.sma(f) - b.sma(s), b, 3.0))
for fast, slow in ((9, 21), (12, 26), (20, 50)):
    _add(f"ema_cross_{fast}_{slow}", "trend",
         lambda b, f=fast, s=slow: _per_atr(b.ema(f) - b.ema(s), b, 2.0))
for n in (20, 50, 200):
    _add(f"price_vs_sma_{n}", "trend", lambda b, n=n: _per_atr(b.c - b.sma(n), b, 3.0))
_add("price_vs_ema_100", "trend", lambda b: _per_atr(b.c - b.ema(100), b, 3.0))


@_register("hma_slope_20", "trend")
def _hma_slope(b: Bars) -> pd.Series:
    n = 20
    raw = 2 * _wma(b.c, n // 2) - _wma(b.c, n)
    hma = _wma(raw, int(np.sqrt(n)))
    return _per_atr(hma.diff(), b, 0.3)


@_register("dema_slope_20", "trend")
def _dema_slope(b: Bars) -> pd.Series:
    e1 = b.ema(20)
    dema = 2 * e1 - ind.ema(e1, 20)
    return _per_atr(dema.diff(), b, 0.3)


@_register("tema_slope_20", "trend")
def _tema_slope(b: Bars) -> pd.Series:
    e1 = b.ema(20)
    e2 = ind.ema(e1, 20)
    tema = 3 * e1 - 3 * e2 + ind.ema(e2, 20)
    return _per_atr(tema.diff(), b, 0.3)


@_register("kama_slope_10", "trend")
def _kama_slope(b: Bars) -> pd.Series:
    n, fast, slow = 10, 2 / 3, 2 / 31
    close = b.c.to_numpy()
    change = np.abs(b.c - b.c.shift(n)).to_numpy()
    vol = b.c.diff().abs().rolling(n, min_periods=n).sum().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        er = np.where(vol > 0, change / vol, 0.0)
    sc = (er * (fast - slow) + slow) ** 2
    kama = np.full(len(close), np.nan)
    for i in range(n, len(close)):
        prev = kama[i - 1] if not np.isnan(kama[i - 1]) else close[i - 1]
        kama[i] = prev + (0.0 if np.isnan(sc[i]) else sc[i]) * (close[i] - prev)
    return _per_atr(pd.Series(kama, index=b.c.index).diff(), b, 0.2)


for fast, slow, sig in ((12, 26, 9), (5, 35, 5)):
    _add(f"macd_hist_{fast}_{slow}", "trend",
         lambda b, f=fast, s=slow, g=sig: _per_atr(ind.macd(b.c, f, s, g)["hist"], b, 0.3))
_add("macd_line_12_26", "trend", lambda b: _per_atr(ind.macd(b.c)["macd"], b, 1.0))


@_register("dmi_diff_14", "trend")
def _dmi_diff(b: Bars) -> pd.Series:
    pdi, mdi, _ = _dmi(b)
    return ((pdi - mdi) / (pdi + mdi).replace(0.0, np.nan)).clip(-1, 1)


@_register("adx_trend_14", "trend")
def _adx_trend(b: Bars) -> pd.Series:
    pdi, mdi, adx = _dmi(b)
    return (np.sign(pdi - mdi) * adx / 50).clip(-1, 1)


for n in (14, 25):
    _add(f"aroon_osc_{n}", "trend", lambda b, n=n: (
        (b.h.rolling(n + 1, min_periods=n + 1).apply(np.argmax, raw=True)
         - b.l.rolling(n + 1, min_periods=n + 1).apply(np.argmin, raw=True)) / n).clip(-1, 1))


def _ichimoku(b: Bars) -> pd.DataFrame:
    def make() -> pd.DataFrame:
        def mid(n: int) -> pd.Series:
            return (b.h.rolling(n, min_periods=n).max() + b.l.rolling(n, min_periods=n).min()) / 2

        tenkan, kijun = mid(9), mid(26)
        span_a = ((tenkan + kijun) / 2).shift(26)
        span_b = mid(52).shift(26)
        return pd.DataFrame({"tenkan": tenkan, "kijun": kijun, "a": span_a, "b": span_b})

    return b.memo("ichimoku", make)


_add("ichimoku_tk_cross", "trend",
     lambda b: _per_atr(_ichimoku(b)["tenkan"] - _ichimoku(b)["kijun"], b, 2.0))


@_register("ichimoku_cloud", "trend")
def _ichimoku_cloud(b: Bars) -> pd.Series:
    ich = _ichimoku(b)
    top = ich[["a", "b"]].max(axis=1, skipna=False)
    bottom = ich[["a", "b"]].min(axis=1, skipna=False)
    above = (b.c - top).clip(lower=0)
    below = (b.c - bottom).clip(upper=0)
    return _per_atr(above + below, b, 2.0)


_add("psar_14", "trend", lambda b: _per_atr(b.c - _psar(b), b, 2.0))
_add("supertrend_10_3", "trend", _supertrend)
_add("supertrend_20_2", "trend", lambda b: _supertrend(b, 20, 2.0))
_add("trix_15", "trend",
     lambda b: _z(ind.ema(ind.ema(ind.ema(b.c, 15), 15), 15).pct_change(), 100))


@_register("vortex_14", "trend")
def _vortex(b: Bars) -> pd.Series:
    n = 14
    vm_plus = (b.h - b.l.shift(1)).abs().rolling(n, min_periods=n).sum()
    vm_minus = (b.l - b.h.shift(1)).abs().rolling(n, min_periods=n).sum()
    tr = ind.true_range(b.h, b.l, b.c).rolling(n, min_periods=n).sum().replace(0.0, np.nan)
    return _tanh((vm_plus - vm_minus) / tr, 0.3)


for n in (20, 50):
    _add(f"linreg_slope_{n}", "trend", lambda b, n=n: _per_atr(_linreg_slope(b.c, n), b, 0.15))
for n in (20, 55):
    _add(f"donchian_pos_{n}", "trend", lambda b, n=n: (
        2 * (b.c - b.l.rolling(n, min_periods=n).min())
        / (b.h.rolling(n, min_periods=n).max() - b.l.rolling(n, min_periods=n).min())
        .replace(0.0, np.nan) - 1).clip(-1, 1))


@_register("donchian_breakout_20", "trend")
def _donchian_breakout(b: Bars) -> pd.Series:
    ch = ind.donchian(b.h.shift(1), b.l.shift(1), 20)
    out = pd.Series(0.0, index=b.c.index)
    out[b.c > ch["upper"]] = 1.0
    out[b.c < ch["lower"]] = -1.0
    out[ch["upper"].isna()] = np.nan
    return out


@_register("stc_10_23_50", "trend")
def _stc(b: Bars) -> pd.Series:
    macd_line = b.ema(23) - b.ema(50)

    def stoch(s: pd.Series, n: int = 10) -> pd.Series:
        lo, hi = s.rolling(n, min_periods=n).min(), s.rolling(n, min_periods=n).max()
        return 100 * (s - lo) / (hi - lo).replace(0.0, np.nan)

    k1 = stoch(macd_line).ewm(alpha=0.5, adjust=False).mean()
    stc = stoch(k1).ewm(alpha=0.5, adjust=False).mean()
    return _osc(stc)


_add("dpo_20", "trend", lambda b: _per_atr(b.c.shift(11) - b.sma(20), b, 2.0))


@_register("ema_ribbon", "trend")
def _ribbon(b: Bars) -> pd.Series:
    lines = [b.ema(n) for n in (8, 13, 21, 34, 55)]
    score = sum(np.sign(lines[i] - lines[i + 1]) for i in range(len(lines) - 1))
    return score / (len(lines) - 1)


@_register("heikin_ashi_trend", "trend")
def _heikin_ashi(b: Bars) -> pd.Series:
    ha_close = (b.o + b.h + b.l + b.c) / 4

    values = np.empty(len(ha_close))
    closes = ha_close.to_numpy()
    opens = b.o.to_numpy()
    values[0] = (opens[0] + closes[0]) / 2
    for i in range(1, len(values)):
        values[i] = (values[i - 1] + closes[i - 1]) / 2
    color = np.sign(ha_close - pd.Series(values, index=b.c.index))
    return color.rolling(3, min_periods=3).mean()


@_register("market_structure_10", "trend")
def _structure(b: Bars) -> pd.Series:
    higher_high = (b.h > b.h.shift(1)).astype(float)
    lower_low = (b.l < b.l.shift(1)).astype(float)
    return (higher_high - lower_low).rolling(10, min_periods=10).mean()


_add("mcginley_vs_price", "trend", lambda b: _per_atr(b.c - _mcginley(b), b, 2.0))


def _mcginley(b: Bars, n: int = 14) -> pd.Series:
    close = b.c.to_numpy()
    out = np.full(len(close), np.nan)
    if len(close):
        out[0] = close[0]
    for i in range(1, len(close)):
        prev = out[i - 1]
        ratio = close[i] / prev if prev else 1.0
        out[i] = prev + (close[i] - prev) / (n * ratio ** 4)
    series = pd.Series(out, index=b.c.index)
    series.iloc[:n] = np.nan
    return series


# ---------------------------------------------------------------- momentum

for n in (2, 7, 14, 21):
    _add(f"rsi_{n}", "momentum", lambda b, n=n: _osc(_rsi(b, n)))
_add("stoch_k_14", "momentum", lambda b: _osc(_stoch_k(b)))
_add("stoch_d_14", "momentum", lambda b: _osc(_stoch_k(b).rolling(3, min_periods=3).mean()))
_add("stoch_cross_14", "momentum",
     lambda b: ((_stoch_k(b) - _stoch_k(b).rolling(3, min_periods=3).mean()) / 25).clip(-1, 1))


@_register("stochrsi_14", "momentum")
def _stochrsi(b: Bars) -> pd.Series:
    r = _rsi(b, 14)
    lo, hi = r.rolling(14, min_periods=14).min(), r.rolling(14, min_periods=14).max()
    return _osc(100 * (r - lo) / (hi - lo).replace(0.0, np.nan))



_add("cci_20", "momentum", lambda b: _tanh(
    (b.typical - ind.sma(b.typical, 20))
    / (0.015 * b.typical.rolling(20, min_periods=20).apply(
        lambda a: np.mean(np.abs(a - a.mean())), raw=True)).replace(0.0, np.nan), 150))
for n in (5, 10, 20):
    _add(f"roc_{n}", "momentum", lambda b, n=n: _z(b.c.pct_change(n), 100))
_add("momentum_10", "momentum", lambda b: _per_atr(b.c - b.c.shift(10), b, 3.0))


@_register("ultimate_osc", "momentum")
def _ultimate(b: Bars) -> pd.Series:
    prev = b.c.shift(1)
    bp = b.c - pd.concat([b.l, prev], axis=1).min(axis=1)
    tr = pd.concat([b.h, prev], axis=1).max(axis=1) - pd.concat([b.l, prev], axis=1).min(axis=1)

    def avg(n: int) -> pd.Series:
        return bp.rolling(n, min_periods=n).sum() / tr.rolling(n, min_periods=n).sum().replace(
            0.0, np.nan)

    return _osc(100 * (4 * avg(7) + 2 * avg(14) + avg(28)) / 7)


@_register("tsi_25_13", "momentum")
def _tsi(b: Bars) -> pd.Series:
    d = b.c.diff()
    num = ind.ema(ind.ema(d, 25), 13)
    den = ind.ema(ind.ema(d.abs(), 25), 13).replace(0.0, np.nan)
    return (num / den).clip(-1, 1) * 2


_add("awesome_osc", "momentum", lambda b: _per_atr(
    ind.sma((b.h + b.l) / 2, 5) - ind.sma((b.h + b.l) / 2, 34), b, 2.0))
_add("ppo_hist", "momentum", lambda b: _z(
    (ind.macd(b.c)["hist"] / b.c), 100))


@_register("cmo_14", "momentum")
def _cmo(b: Bars) -> pd.Series:
    d = b.c.diff()
    up = d.clip(lower=0).rolling(14, min_periods=14).sum()
    down = (-d.clip(upper=0)).rolling(14, min_periods=14).sum()
    return ((up - down) / (up + down).replace(0.0, np.nan)).clip(-1, 1)


@_register("kst", "momentum")
def _kst(b: Bars) -> pd.Series:
    parts = [(10, 10, 1), (15, 10, 2), (20, 10, 3), (30, 15, 4)]
    kst = sum(w * ind.sma(b.c.pct_change(r), s) for r, s, w in parts)
    return _z(kst, 100)


@_register("rvi_10", "momentum")
def _rvi(b: Bars) -> pd.Series:
    num = (b.c - b.o).rolling(10, min_periods=10).mean()
    den = (b.h - b.l).rolling(10, min_periods=10).mean().replace(0.0, np.nan)
    return _tanh(num / den, 0.3)


@_register("connors_rsi", "momentum")
def _connors(b: Bars) -> pd.Series:
    streak_rsi = ind.rsi(_streak(b.c), 2)
    rank = b.c.pct_change().rolling(100, min_periods=100).rank(pct=True) * 100
    return _osc((_rsi(b, 3) + streak_rsi + rank) / 3)


@_register("fisher_10", "momentum")
def _fisher(b: Bars) -> pd.Series:
    mid = (b.h + b.l) / 2
    lo, hi = mid.rolling(10, min_periods=10).min(), mid.rolling(10, min_periods=10).max()
    x = (2 * (mid - lo) / (hi - lo).replace(0.0, np.nan) - 1).clip(-0.999, 0.999)
    x = x.ewm(alpha=0.33, adjust=False).mean().clip(-0.999, 0.999)
    return _tanh(0.5 * np.log((1 + x) / (1 - x)), 2.0)


_add("coppock", "momentum", lambda b: _z(
    _wma(b.c.pct_change(14) + b.c.pct_change(11), 10), 100))
_add("balance_of_power", "momentum",
     lambda b: ((b.body / b.rng).rolling(14, min_periods=14).mean() * 2).clip(-1, 1))
_add("elder_power_13", "momentum",
     lambda b: _per_atr((b.h - b.ema(13)) + (b.l - b.ema(13)), b, 3.0))
_add("percent_rank_20", "momentum",
     lambda b: b.c.rolling(20, min_periods=20).rank(pct=True) * 2 - 1)
_add("streak", "momentum", lambda b: (_streak(b.c) / 5).clip(-1, 1))
_add("qstick_10", "momentum", lambda b: _per_atr(ind.sma(b.body, 10), b, 0.3))
_add("zscore_20", "momentum", lambda b: (ind.zscore(b.c, 20) / 2).clip(-1, 1))


# ---------------------------------------------------------------- volatility

_add("bb_pctb_20", "volatility", lambda b: (2 * ind.bollinger(b.c)["pct_b"] - 1).clip(-1.5, 1.5)
     .clip(-1, 1))


@_register("bb_squeeze_breakout", "volatility")
def _squeeze(b: Bars) -> pd.Series:
    bands = ind.bollinger(b.c)
    width = bands["bandwidth"]
    tight = width <= width.rolling(120, min_periods=60).quantile(0.2)
    direction = np.sign(b.c - bands["mid"])
    return (direction * tight.astype(float)).where(width.notna())


def _keltner(b: Bars, n: int = 20, mult: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = b.ema(n)
    return mid, mid + mult * b.atr, mid - mult * b.atr


@_register("keltner_pos_20", "volatility")
def _keltner_pos(b: Bars) -> pd.Series:
    mid, upper, lower = _keltner(b)
    return (2 * (b.c - lower) / (upper - lower).replace(0.0, np.nan) - 1).clip(-1, 1)


@_register("keltner_breakout", "volatility")
def _keltner_breakout(b: Bars) -> pd.Series:
    mid, upper, lower = _keltner(b)
    out = pd.Series(0.0, index=b.c.index)
    out[b.c > upper] = 1.0
    out[b.c < lower] = -1.0
    return out.where(mid.notna())


_add("atr_expansion_dir", "volatility", lambda b: (
    (b.atr / ind.atr(b.h, b.l, b.c, 50) - 1).clip(-1, 1)
    * np.sign(b.c - b.c.shift(5))))


@_register("choppiness_dir_14", "volatility")
def _chop(b: Bars) -> pd.Series:
    n = 14
    tr_sum = ind.true_range(b.h, b.l, b.c).rolling(n, min_periods=n).sum()
    span = (b.h.rolling(n, min_periods=n).max() - b.l.rolling(n, min_periods=n).min()).replace(
        0.0, np.nan)
    chop = 100 * np.log10(tr_sum / span) / np.log10(n)
    return ((61.8 - chop) / 40).clip(-1, 1) * np.sign(b.c - b.c.shift(n))


_add("vol_regime", "volatility", lambda b: -_z(ind.realized_volatility(b.c, 20), 250))


@_register("ulcer_14", "volatility")
def _ulcer(b: Bars) -> pd.Series:
    peak = b.c.rolling(14, min_periods=14).max()
    dd = 100 * (b.c - peak) / peak
    ulcer = np.sqrt((dd ** 2).rolling(14, min_periods=14).mean())
    return -_z(ulcer, 100)


@_register("stddev_channel_50", "volatility")
def _std_channel(b: Bars) -> pd.Series:
    n = 50
    slope = _linreg_slope(b.c, n)
    mean = b.c.rolling(n, min_periods=n).mean()
    fitted = mean + slope * (n - 1) / 2
    resid_std = (b.c - fitted).rolling(n, min_periods=n).std(ddof=0).replace(0.0, np.nan)
    return _tanh((b.c - fitted) / (2 * resid_std), 1.0)


@_register("nr7_breakout", "volatility")
def _nr7(b: Bars) -> pd.Series:
    rng = b.h - b.l
    narrow = (rng == rng.rolling(7, min_periods=7).min()).shift(1, fill_value=False)
    out = pd.Series(0.0, index=b.c.index)
    out[narrow & (b.c > b.h.shift(1))] = 1.0
    out[narrow & (b.c < b.l.shift(1))] = -1.0
    return out.where(rng.rolling(8, min_periods=8).count() == 8)


@_register("mass_index", "volatility")
def _mass(b: Bars) -> pd.Series:
    rng = b.h - b.l
    e1 = ind.ema(rng, 9)
    ratio = e1 / ind.ema(e1, 9).replace(0.0, np.nan)
    mass = ratio.rolling(25, min_periods=25).sum()
    return _z(mass, 100) * -np.sign(b.c - b.sma(20))  # bulge = reversal of the trend


_add("gap", "volatility", lambda b: _per_atr(b.o - b.c.shift(1), b, 1.0))
_add("donchian_squeeze_dir", "volatility", lambda b: (
    -_z(b.h.rolling(20, min_periods=20).max() - b.l.rolling(20, min_periods=20).min(), 100)
    * np.sign(b.c - b.sma(20))))
_add("atr_pct_z", "volatility", lambda b: _z(b.atr / b.c, 100))


# ---------------------------------------------------------------- volume


def _obv(b: Bars) -> pd.Series:
    return b.memo("obv", lambda: (np.sign(b.c.diff()).fillna(0.0) * b.v).cumsum())


def _ad_line(b: Bars) -> pd.Series:
    def make() -> pd.Series:
        mfm = ((b.c - b.l) - (b.h - b.c)) / b.rng
        return (mfm.fillna(0.0) * b.v).cumsum()

    return b.memo("ad", make)


_add("obv_slope_20", "volume", lambda b: _z(_linreg_slope(_obv(b), 20), 100))
_add("obv_vs_ema", "volume", lambda b: _z(_obv(b) - ind.ema(_obv(b), 20), 100))


@_register("cmf_20", "volume")
def _cmf(b: Bars) -> pd.Series:
    mfv = (((b.c - b.l) - (b.h - b.c)) / b.rng).fillna(0.0) * b.v
    vol = b.v.rolling(20, min_periods=20).sum().replace(0.0, np.nan)
    return (2 * mfv.rolling(20, min_periods=20).sum() / vol).clip(-1, 1)


_add("ad_slope_20", "volume", lambda b: _z(_linreg_slope(_ad_line(b), 20), 100))
_add("chaikin_osc", "volume", lambda b: _z(ind.ema(_ad_line(b), 3) - ind.ema(_ad_line(b), 10), 100))


@_register("mfi_14", "volume")
def _mfi(b: Bars) -> pd.Series:
    flow = b.typical * b.v
    up = flow.where(b.typical.diff() > 0, 0.0).rolling(14, min_periods=14).sum()
    down = flow.where(b.typical.diff() < 0, 0.0).rolling(14, min_periods=14).sum()
    return _osc(100 * up / (up + down).replace(0.0, np.nan))


_add("force_index_13", "volume", lambda b: _z(ind.ema(b.c.diff() * b.v, 13), 100))
_add("ease_of_movement_14", "volume", lambda b: _z(ind.sma(
    ((b.h + b.l) / 2).diff() * (b.h - b.l) / b.v.replace(0.0, np.nan), 14), 100))
_add("vpt_slope_20", "volume", lambda b: _z(_linreg_slope(
    (b.c.pct_change().fillna(0.0) * b.v).cumsum(), 20), 100))


@_register("nvi_trend", "volume")
def _nvi(b: Bars) -> pd.Series:
    ret = b.c.pct_change().fillna(0.0)
    quieter = b.v < b.v.shift(1)
    nvi = (1 + ret.where(quieter, 0.0)).cumprod()
    return _tanh((nvi / ind.ema(nvi, 100) - 1) * 50, 1.0)


@_register("pvi_trend", "volume")
def _pvi(b: Bars) -> pd.Series:
    ret = b.c.pct_change().fillna(0.0)
    louder = b.v > b.v.shift(1)
    pvi = (1 + ret.where(louder, 0.0)).cumprod()
    return _tanh((pvi / ind.ema(pvi, 100) - 1) * 50, 1.0)


@_register("vwap_dev_20", "volume")
def _vwap(b: Bars) -> pd.Series:
    pv = (b.typical * b.v).rolling(20, min_periods=20).sum()
    vol = b.v.rolling(20, min_periods=20).sum().replace(0.0, np.nan)
    return _per_atr(b.c - pv / vol, b, 2.0)


_add("volume_z_dir", "volume", lambda b: (
    (ind.zscore(np.log1p(b.v), 50) / 3).clip(-1, 1).clip(lower=0) * np.sign(b.body)))
_add("volume_spike_dir", "volume", lambda b: (
    (b.v > 2 * b.sma(20, "v")).astype(float) * np.sign(b.body)).where(b.sma(20, "v").notna()))


@_register("klinger_osc", "volume")
def _klinger(b: Bars) -> pd.Series:
    trend = np.sign(b.typical.diff()).fillna(0.0)
    vf = b.v * trend
    return _z(ind.ema(vf, 34) - ind.ema(vf, 55), 100)


_add("volume_osc_dir", "volume", lambda b: (
    ((b.ema(5, "v") - b.ema(20, "v")) / b.ema(20, "v").replace(0.0, np.nan)).clip(-1, 1).clip(
        lower=0) * np.sign(b.c - b.c.shift(5))))


@_register("up_down_volume_20", "volume")
def _updown(b: Bars) -> pd.Series:
    sign = np.sign(b.c.diff())
    up = b.v.where(sign > 0, 0.0).rolling(20, min_periods=20).sum()
    down = b.v.where(sign < 0, 0.0).rolling(20, min_periods=20).sum()
    return ((up - down) / (up + down).replace(0.0, np.nan)).clip(-1, 1)


@_register("relative_volume_trend", "volume")
def _rvol_trend(b: Bars) -> pd.Series:
    rvol = b.v / b.sma(50, "v").replace(0.0, np.nan)
    return _tanh((rvol - 1).clip(lower=0) * np.sign(b.c - b.ema(20)), 1.0)


# ---------------------------------------------------------------- candles


def _candle(name: str, fn: Callable[[Bars], pd.Series]) -> None:
    def wrapped(b: Bars) -> pd.Series:
        out = fn(b).astype(float)
        return out.where(b.c.shift(6).notna())

    _add(name, "candles", wrapped)


def _flag(cond: pd.Series, value: float = 1.0) -> pd.Series:
    return cond.fillna(False).astype(float) * value


def _small_body(b: Bars, frac: float = 0.1) -> pd.Series:
    return b.body.abs() <= frac * b.rng


def _upper_wick(b: Bars) -> pd.Series:
    return b.h - pd.concat([b.o, b.c], axis=1).max(axis=1)


def _lower_wick(b: Bars) -> pd.Series:
    return pd.concat([b.o, b.c], axis=1).min(axis=1) - b.l


_candle("doji_reversal", lambda b: _flag(_small_body(b)) * -b.trend10)
_candle("hammer", lambda b: _flag(
    (_lower_wick(b) >= 2 * b.body.abs()) & (_upper_wick(b) <= 0.3 * b.body.abs() + 0.1 * b.rng)
    & (b.trend10 < 0)))
_candle("inverted_hammer", lambda b: _flag(
    (_upper_wick(b) >= 2 * b.body.abs()) & (_lower_wick(b) <= 0.1 * b.rng) & (b.trend10 < 0)))
_candle("hanging_man", lambda b: _flag(
    (_lower_wick(b) >= 2 * b.body.abs()) & (_upper_wick(b) <= 0.1 * b.rng) & (b.trend10 > 0),
    -1.0))
_candle("shooting_star", lambda b: _flag(
    (_upper_wick(b) >= 2 * b.body.abs()) & (_lower_wick(b) <= 0.1 * b.rng) & (b.trend10 > 0),
    -1.0))


def _engulfing(b: Bars) -> pd.Series:
    prev_body = b.body.shift(1)
    bull = (prev_body < 0) & (b.body > 0) & (b.o <= b.c.shift(1)) & (b.c >= b.o.shift(1))
    bear = (prev_body > 0) & (b.body < 0) & (b.o >= b.c.shift(1)) & (b.c <= b.o.shift(1))
    return _flag(bull) - _flag(bear)


_candle("engulfing", _engulfing)


def _harami(b: Bars) -> pd.Series:
    prev_top = pd.concat([b.o, b.c], axis=1).max(axis=1).shift(1)
    prev_bot = pd.concat([b.o, b.c], axis=1).min(axis=1).shift(1)
    inside = (pd.concat([b.o, b.c], axis=1).max(axis=1) < prev_top) & (
        pd.concat([b.o, b.c], axis=1).min(axis=1) > prev_bot)
    return _flag(inside & (b.body.shift(1) < 0) & (b.body > 0)) - _flag(
        inside & (b.body.shift(1) > 0) & (b.body < 0))


_candle("harami", _harami)
_candle("piercing_line", lambda b: _flag(
    (b.body.shift(1) < 0) & (b.o < b.l.shift(1)) & (b.c > (b.o.shift(1) + b.c.shift(1)) / 2)
    & (b.c < b.o.shift(1))))
_candle("dark_cloud_cover", lambda b: _flag(
    (b.body.shift(1) > 0) & (b.o > b.h.shift(1)) & (b.c < (b.o.shift(1) + b.c.shift(1)) / 2)
    & (b.c > b.o.shift(1)), -1.0))


def _stars(b: Bars) -> pd.Series:
    first, middle = b.body.shift(2), b.body.shift(1)
    small_mid = middle.abs() <= 0.3 * first.abs()
    morning = (first < 0) & small_mid & (b.body > 0) & (b.c > (b.o.shift(2) + b.c.shift(2)) / 2)
    evening = (first > 0) & small_mid & (b.body < 0) & (b.c < (b.o.shift(2) + b.c.shift(2)) / 2)
    return _flag(morning) - _flag(evening)


_candle("morning_evening_star", _stars)


def _three(b: Bars) -> pd.Series:
    ups = (b.body > 0) & (b.body.shift(1) > 0) & (b.body.shift(2) > 0) & (
        b.c > b.c.shift(1)) & (b.c.shift(1) > b.c.shift(2))
    downs = (b.body < 0) & (b.body.shift(1) < 0) & (b.body.shift(2) < 0) & (
        b.c < b.c.shift(1)) & (b.c.shift(1) < b.c.shift(2))
    return _flag(ups) - _flag(downs)


_candle("three_soldiers_crows", _three)
_candle("marubozu", lambda b: _flag(
    (_upper_wick(b) + _lower_wick(b)) <= 0.05 * b.rng) * np.sign(b.body))
_candle("spinning_top", lambda b: _flag(
    (b.body.abs() <= 0.3 * b.rng) & (_upper_wick(b) > b.body.abs())
    & (_lower_wick(b) > b.body.abs())) * -b.trend10)
_candle("tweezer", lambda b: _flag(
    ((b.l - b.l.shift(1)).abs() <= 0.05 * b.atr) & (b.trend10 < 0) & (b.body > 0)) - _flag(
    ((b.h - b.h.shift(1)).abs() <= 0.05 * b.atr) & (b.trend10 > 0) & (b.body < 0)))
_candle("kicker", lambda b: _flag(
    (b.body.shift(1) < 0) & (b.o > b.o.shift(1)) & (b.body > 0)) - _flag(
    (b.body.shift(1) > 0) & (b.o < b.o.shift(1)) & (b.body < 0)))


def _three_inside(b: Bars) -> pd.Series:
    harami = _harami(b).shift(1)
    return _flag((harami > 0) & (b.c > b.h.shift(1))) - _flag((harami < 0) & (b.c < b.l.shift(1)))


_candle("three_inside", _three_inside)


def _three_outside(b: Bars) -> pd.Series:
    engulf = _engulfing(b).shift(1)
    return _flag((engulf > 0) & (b.c > b.c.shift(1))) - _flag((engulf < 0) & (b.c < b.c.shift(1)))


_candle("three_outside", _three_outside)


def _three_methods(b: Bars) -> pd.Series:
    big_first = b.body.shift(4).abs() >= b.atr.shift(4)
    inside = (b.h.shift(1).rolling(3).max() <= b.h.shift(4)) & (
        b.l.shift(1).rolling(3).min() >= b.l.shift(4))
    rising = big_first & (b.body.shift(4) > 0) & inside & (b.body > 0) & (b.c > b.c.shift(4))
    falling = big_first & (b.body.shift(4) < 0) & inside & (b.body < 0) & (b.c < b.c.shift(4))
    return _flag(rising) - _flag(falling)


_candle("three_methods", _three_methods)
_candle("belt_hold", lambda b: _flag(
    (b.body > 0) & ((b.o - b.l) <= 0.05 * b.rng) & (b.body >= 0.7 * b.rng) & (b.trend10 < 0)
) - _flag((b.body < 0) & ((b.h - b.o) <= 0.05 * b.rng) & (b.body.abs() >= 0.7 * b.rng)
          & (b.trend10 > 0)))
_candle("inside_bar_breakout", lambda b: _flag(
    (b.h.shift(1) < b.h.shift(2)) & (b.l.shift(1) > b.l.shift(2)) & (b.c > b.h.shift(2))) - _flag(
    (b.h.shift(1) < b.h.shift(2)) & (b.l.shift(1) > b.l.shift(2)) & (b.c < b.l.shift(2))))
_candle("outside_bar", lambda b: _flag(
    (b.h > b.h.shift(1)) & (b.l < b.l.shift(1))) * np.sign(b.body))
_candle("close_location", lambda b: (2 * (b.c - b.l) / b.rng - 1).fillna(0.0))
_candle("wick_rejection", lambda b: ((_lower_wick(b) - _upper_wick(b)) / b.rng).fillna(0.0))
_candle("pin_bar", lambda b: _flag(_lower_wick(b) >= 0.66 * b.rng) - _flag(
    _upper_wick(b) >= 0.66 * b.rng))
_candle("gap_and_go", lambda b: _flag((b.o > b.h.shift(1)) & (b.body > 0)) - _flag(
    (b.o < b.l.shift(1)) & (b.body < 0)))
def _abandoned_baby(b: Bars) -> pd.Series:
    star = _small_body(b).shift(1, fill_value=False)
    bull = star & (b.h.shift(1) < b.l.shift(2)) & (b.l > b.h.shift(1))
    bear = star & (b.l.shift(1) > b.h.shift(2)) & (b.h < b.l.shift(1))
    return _flag(bull) - _flag(bear)


_candle("abandoned_baby", _abandoned_baby)


# ---------------------------------------------------------------- compute


def compute(df: pd.DataFrame, names: list[str] | None = None) -> pd.DataFrame:
    """Every indicator (or `names`) for one symbol's candles, as float32 votes."""
    bars = Bars(df)
    columns: dict[str, np.ndarray] = {}
    with np.errstate(all="ignore"):
        for name in names or list(CATALOG):
            series = CATALOG[name].fn(bars)
            values = np.array(series, dtype=float)
            values[~np.isfinite(values)] = np.nan
            columns[name] = np.clip(values, -1.0, 1.0).astype(np.float32)
    return pd.DataFrame(columns, index=df.index)


def family_of(feature: str) -> str:
    """'rsi_14@1h' -> 'momentum'."""
    return CATALOG[feature.split("@", 1)[0]].family
