"""A package: a few (or up to 50) indicators that vote together, and how to trade on them.

    score = sum(weight_i * vote_i) / sum(|weight_i|)        (in [-1, 1])

- **Enter** long when the score reaches ``threshold`` (short at -threshold,
  where shorting is allowed), at the next candle's open, after the signal
  has held for ``confirm`` candles.
- **Size** is "how much": ``size`` of equity at full conviction, half that
  at the threshold, in between in proportion.
- **Exit** on a trailing stop ``stop_atr`` ATRs away (checked inside each
  candle), an optional take-profit, when the score falls back below
  ``exit_threshold`` (or to it) in the trade's direction (not before ``min_hold``
  candles), or after ``max_hold`` candles.
- **Pace**: ``cooldown`` candles after an exit before that symbol trades again.

The simulator is per symbol and per trade, with numpy doing the scanning, so
a backtest over hundreds of symbols of 10-minute candles takes seconds. Each
trade costs ``cost`` per side. ``portfolio`` then walks every symbol's trades
in time order, skipping any that would take total exposure over 100%, and
turns them into an equity curve, return, drawdown and win rate.
"""
from __future__ import annotations

import heapq
from dataclasses import asdict, dataclass, field, fields
from typing import Any

import numpy as np

from .catalog import family_of

KNOBS = ("threshold", "exit_threshold", "stop_atr", "take_atr", "max_hold", "min_hold",
         "cooldown", "confirm", "size", "shorts")


@dataclass
class Package:
    weights: dict[str, float]          # feature column -> signed weight
    threshold: float = 0.35
    exit_threshold: float = 0.0
    stop_atr: float = 3.0
    take_atr: float | None = None
    max_hold: int = 0                  # candles; 0 = no limit
    min_hold: int = 0
    cooldown: int = 0
    confirm: int = 1
    size: float = 0.10                 # equity fraction per trade at full conviction
    shorts: bool = True
    name: str = ""

    def __post_init__(self) -> None:
        self.weights = {k: float(v) for k, v in self.weights.items() if abs(float(v)) > 1e-9}
        if not self.weights:
            raise ValueError("a package needs at least one indicator")

    @property
    def features(self) -> list[str]:
        return list(self.weights)

    @property
    def families(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.weights:
            fam = family_of(f)
            out[fam] = out.get(fam, 0) + 1
        return out

    def knobs(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in KNOBS}

    def with_(self, **changes: Any) -> Package:
        data = asdict(self)
        data.update(changes)
        return Package(**data)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["weights"] = {k: round(v, 4) for k, v in self.weights.items()}
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Package:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    def describe(self) -> str:
        fams = ", ".join(f"{n} {fam}" for fam, n in sorted(self.families.items()))
        return f"{len(self.weights)} indicators ({fams}), enter at {self.threshold:.2f}"

    def score(self, matrix: np.ndarray) -> np.ndarray:
        """Composite score per candle from the package's columns (rows x features)."""
        w = np.array(list(self.weights.values()), dtype=np.float32)
        x = np.nan_to_num(matrix, nan=0.0)
        return (x @ w) / float(np.abs(w).sum())


# ------------------------------------------------------------------ one symbol


@dataclass
class Trades:
    """Closed trades for one or more symbols, as parallel arrays."""

    entry_time: np.ndarray = field(default_factory=lambda: np.array([], dtype="int64"))
    exit_time: np.ndarray = field(default_factory=lambda: np.array([], dtype="int64"))
    direction: np.ndarray = field(default_factory=lambda: np.array([], dtype="int8"))
    fraction: np.ndarray = field(default_factory=lambda: np.array([], dtype="float64"))
    ret: np.ndarray = field(default_factory=lambda: np.array([], dtype="float64"))
    bars: np.ndarray = field(default_factory=lambda: np.array([], dtype="int32"))
    reason: np.ndarray = field(default_factory=lambda: np.array([], dtype="int8"))
    symbol: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.ret)

    @staticmethod
    def concat(parts: list[Trades]) -> Trades:
        parts = [p for p in parts if len(p)]
        if not parts:
            return Trades()
        return Trades(
            *(np.concatenate([getattr(p, name) for p in parts])
              for name in ("entry_time", "exit_time", "direction", "fraction", "ret", "bars",
                           "reason")),
            symbol=[s for p in parts for s in p.symbol],
        )


EXIT_REASONS = ("stop", "take profit", "signal", "max hold", "end of data")


def simulate_symbol(
    pkg: Package,
    score: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr: np.ndarray,
    times: np.ndarray,
    can_short: bool,
    cost: float,
    symbol: str = "",
) -> Trades:
    n = len(score)
    if n < 3:
        return Trades()
    theta = pkg.threshold
    longs = score >= theta
    shorts = (score <= -theta) if (pkg.shorts and can_short) else np.zeros(n, dtype=bool)
    if pkg.confirm > 1:
        kernel = np.ones(pkg.confirm)
        longs = np.convolve(longs, kernel)[:n] >= pkg.confirm
        shorts = np.convolve(shorts, kernel)[:n] >= pkg.confirm
    ok = np.isfinite(atr) & (atr > 0)
    long_idx = np.flatnonzero(longs & ok)
    short_idx = np.flatnonzero(shorts & ok)

    rows: list[tuple[int, int, int, float, float, int, int]] = []
    i = 0
    span = max(pkg.max_hold, 0)
    while True:
        a = np.searchsorted(long_idx, i)
        b = np.searchsorted(short_idx, i)
        t_long = long_idx[a] if a < len(long_idx) else n
        t_short = short_idx[b] if b < len(short_idx) else n
        t = min(t_long, t_short)
        if t >= n - 1:
            break
        d = 1 if t_long <= t_short else -1
        conviction = min(max((abs(score[t]) - theta) / max(1 - theta, 1e-6), 0.0), 1.0)
        frac = pkg.size * (0.5 + 0.5 * conviction)
        e = t + 1                      # filled at this candle's open
        entry = open_[e]
        stop0 = entry - d * pkg.stop_atr * atr[t]
        take = entry + d * pkg.take_atr * atr[t] if pkg.take_atr else None

        exit_bar, exit_price, why = n - 1, close[n - 1], 4
        chunk, start = 64, e
        trail_prev = stop0
        while start < n:
            end = min(start + chunk, n)
            if span:
                end = min(end, e + span)
            # stop for candle j uses closes up to j-1
            cl = close[start - 1:end - 1] - d * pkg.stop_atr * atr[start - 1:end - 1]
            if start - 1 < e:          # the entry candle itself uses the initial stop
                cl = cl.copy()
                cl[0] = stop0
            cl = np.nan_to_num(cl, nan=trail_prev)
            if d > 0:
                path = np.maximum.accumulate(np.maximum(cl, trail_prev))
                hit_stop = low[start:end] <= path
                hit_take = high[start:end] >= take if take is not None else None
            else:
                path = np.minimum.accumulate(np.minimum(cl, trail_prev))
                hit_stop = high[start:end] >= path
                hit_take = low[start:end] <= take if take is not None else None
            held = np.arange(start, end) - e + 1
            sig_exit = (score[start:end] * d <= pkg.exit_threshold) & (held >= pkg.min_hold)
            sig_exit &= np.arange(start, end) < n - 1
            events = [
                (np.argmax(hit_stop) if hit_stop.any() else None, 0),
                (np.argmax(hit_take) if hit_take is not None and hit_take.any() else None, 1),
                (np.argmax(sig_exit) if sig_exit.any() else None, 2),
            ]
            if span and end == e + span:
                events.append((end - 1 - start, 3))
            found = [(k, kind) for k, kind in events if k is not None]
            if found:
                k, kind = min(found, key=lambda x: (x[0], x[1]))
                j = start + k
                if kind == 0:
                    exit_bar = j
                    exit_price = min(open_[j], path[k]) if d > 0 else max(open_[j], path[k])
                elif kind == 1:
                    exit_bar = j
                    exit_price = max(open_[j], take) if d > 0 else min(open_[j], take)
                else:                   # decided at the close, filled at the next open
                    exit_bar = min(j + 1, n - 1)
                    exit_price = open_[exit_bar]
                why = kind
                break
            trail_prev = path[-1]
            if span and end >= e + span:
                break
            start = end
            chunk *= 4
        ret = d * (exit_price - entry) / entry - 2 * cost
        rows.append((int(times[e]), int(times[exit_bar]), d, frac, ret, exit_bar - e + 1, why))
        i = max(exit_bar + pkg.cooldown, t + 1)
        if why >= 2 and pkg.cooldown == 0:
            i = max(exit_bar - 1, t + 1)  # a signal exit may reverse on the same close
    if not rows:
        return Trades()
    arr = list(zip(*rows, strict=True))
    return Trades(
        entry_time=np.array(arr[0], dtype="int64"),
        exit_time=np.array(arr[1], dtype="int64"),
        direction=np.array(arr[2], dtype="int8"),
        fraction=np.array(arr[3], dtype="float64"),
        ret=np.array(arr[4], dtype="float64"),
        bars=np.array(arr[5], dtype="int32"),
        reason=np.array(arr[6], dtype="int8"),
        symbol=[symbol] * len(rows),
    )


# ------------------------------------------------------------------ the portfolio


def portfolio(trades: Trades, max_gross: float = 1.0) -> tuple[Trades, dict[str, float]]:
    """Take trades in entry order while exposure allows; equity, return, drawdown."""
    if not len(trades):
        return trades, empty_metrics()
    order = np.lexsort((trades.exit_time, trades.entry_time))
    open_heap: list[tuple[int, float]] = []
    exposure = 0.0
    keep = np.zeros(len(order), dtype=bool)
    for idx in order:
        t0 = trades.entry_time[idx]
        while open_heap and open_heap[0][0] <= t0:
            exposure -= heapq.heappop(open_heap)[1]
        f = trades.fraction[idx]
        if exposure + f <= max_gross + 1e-9:
            keep[idx] = True
            exposure += f
            heapq.heappush(open_heap, (int(trades.exit_time[idx]), f))
    taken = Trades(
        *(getattr(trades, name)[keep] for name in
          ("entry_time", "exit_time", "direction", "fraction", "ret", "bars", "reason")),
        symbol=[s for s, k in zip(trades.symbol, keep, strict=True) if k],
    )
    return taken, metrics(taken)


def empty_metrics() -> dict[str, float]:
    return {"total_return": 0.0, "max_drawdown": 0.0, "num_trades": 0, "win_rate": 0.0,
            "profit_factor": 0.0, "avg_trade": 0.0, "long_pnl": 0.0, "short_pnl": 0.0,
            "avg_bars": 0.0}


def metrics(trades: Trades) -> dict[str, float]:
    if not len(trades):
        return empty_metrics()
    pnl = trades.fraction * trades.ret
    order = np.argsort(trades.exit_time, kind="stable")
    equity = np.cumprod(1.0 + np.maximum(pnl[order], -0.999))  # compounding, as money does
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    drawdown = float(np.min(equity / peak - 1.0))
    gains, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    return {
        "total_return": float(equity[-1] - 1.0),
        "max_drawdown": min(drawdown, 0.0),
        "num_trades": int(len(pnl)),
        "win_rate": float((trades.ret > 0).mean()),
        "profit_factor": float(gains / losses) if losses > 0 else float("inf") if gains else 0.0,
        "avg_trade": float(trades.ret.mean()),
        "long_pnl": float(pnl[trades.direction > 0].sum()),
        "short_pnl": float(pnl[trades.direction < 0].sum()),
        "avg_bars": float(trades.bars.mean()),
    }


def score_of(m: dict[str, float], loss_aversion: float, min_trades: int) -> float:
    """Return plus loss_aversion x drawdown; too few trades can't prove anything."""
    value = float(m["total_return"]) + loss_aversion * float(m["max_drawdown"])
    if m["num_trades"] < min_trades:
        value -= 0.02 * (1 - m["num_trades"] / max(min_trades, 1))
    return value
