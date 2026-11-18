"""Part 4, family 7: breakout after a quiet day (NR7, daily, both sides).

When a day's range (high - low) is the narrowest of the last ``nr`` days, a
big move often follows. The next day: buy if price trades above that day's
high, sell short if it trades below its low (each ``buf`` of the range
beyond). The stop is the other side; 1R is the distance between the two. Exit
at the close ``hold`` days after the entry day, or at the stop.

No peeking: the quiet day is known at its close; the orders work the next
day. With daily candles we can't tell which side traded first, so a day that
touches both counts as the worst case: filled one way and stopped the other.

Costs: the bigger of ``cost_bp`` and the candle's spread on each side, a
quarter more on stops, and ``finance_pa`` a year for nights held. Each trade
books its result on its exit day (holds are 1-3 days), so the portfolio tools
in ``trend.py`` apply.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import data
from .trend import CHECK_END, DAY, LEARN_END, T0, frames, window


@dataclass(frozen=True)
class NRParams:
    nr: int = 7              # narrowest range of the last nr days
    buf: float = 0.0         # entry this share of the quiet day's range beyond its high/low
    hold: int = 2            # exit at the close this many days after the entry day (0 = same day)
    cost_bp: float = 1.0
    finance_pa: float = 0.02

    def label(self) -> str:
        d = NRParams()
        diff = [f"{f.name}={getattr(self, f.name)}" for f in fields(self)
                if getattr(self, f.name) != getattr(d, f.name)]
        return f"NR{self.nr} breakout, hold {self.hold}d" + (f" ({', '.join(diff)})" if diff else "")


def first_touch(h1: pd.DataFrame | None, day_start: int, up: float, dn: float) -> tuple[int, bool] | None:
    """Inside one daily candle, from its hourly candles: (side touched first, stopped later that day).
    None when there's no hourly data for that day or one hourly candle touched both (can't tell)."""
    if h1 is None:
        return None
    t = h1["time"].to_numpy("int64")
    a, b = np.searchsorted(t, day_start), np.searchsorted(t, day_start + DAY)
    if b <= a:
        return None
    hh, ll = h1["high"].to_numpy(float)[a:b], h1["low"].to_numpy(float)[a:b]
    for k in range(b - a):
        u, d = hh[k] >= up, ll[k] <= dn
        if u and d:
            return None
        if u or d:
            side = 1 if u else -1
            later = ll[k + 1:] <= dn if side > 0 else hh[k + 1:] >= up
            return side, bool(later.any())
    return None


def run(symbol: str, df: pd.DataFrame, p: NRParams, point: float,
        h1: pd.DataFrame | None = None) -> list[dict[str, Any]]:
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    t = df["time"].to_numpy("int64")
    spread = df["spread"].to_numpy(float) * point if "spread" in df else np.zeros(len(df))
    rng = h - lo
    narrow = pd.Series(rng).rolling(p.nr).min().to_numpy()

    def cost(i: int, price: float) -> float:
        return max(p.cost_bp * 1e-4 * price, spread[i])

    out: list[dict[str, Any]] = []
    i = p.nr
    while i < len(df) - 1:
        if not (rng[i] > 0 and rng[i] <= narrow[i]):
            i += 1
            continue
        up, dn = h[i] + p.buf * rng[i], lo[i] - p.buf * rng[i]
        risk = up - dn
        e = i + 1
        hit_up, hit_dn = h[e] >= up, lo[e] <= dn
        if not (hit_up or hit_dn):
            i += 1
            continue
        side = 1 if hit_up else -1
        resolved = first_touch(h1, int(t[e]), up, dn) if (hit_up and hit_dn) else None
        if resolved is not None:
            side = resolved[0]
        level = up if side > 0 else dn
        entry = max(o[e], level) if side > 0 else min(o[e], level)
        stop = dn if side > 0 else up
        exit_i, price, why = None, None, "time"
        if hit_up and hit_dn and (resolved is None or resolved[1]):
            exit_i, price, why = e, stop, "stop"   # both touched: the hourly candles say stopped, or can't tell
        else:
            for j in range(e, min(e + p.hold, len(df) - 1) + 1):
                if j > e and side * (o[j] - stop) <= 0:
                    exit_i, price, why = j, o[j], "stop"
                    break
                if j > e and ((side > 0 and lo[j] <= stop) or (side < 0 and h[j] >= stop)):
                    exit_i, price, why = j, stop, "stop"
                    break
                if j == e + p.hold:
                    exit_i, price = j, c[j]
            if exit_i is None:
                break                               # data ends before the trade does
        extra = 0.25 * cost(exit_i, price) if why == "stop" else 0.0
        nights = max(0, (int(t[exit_i]) - int(t[e])) // DAY)
        r = side * (price - entry) / risk - (cost(e, entry) + cost(exit_i, price) + extra) / risk \
            - p.finance_pa / 365 * nights * entry / risk
        out.append({"symbol": symbol, "side": side, "entry": float(entry), "stop": float(stop),
                    "fill_time": int(t[e]), "exit_time": int(t[exit_i]), "exit_price": float(price),
                    "r": float(r), "reason": why, "path": [[int(t[exit_i]), float(r)]]})
        i = exit_i + 1                              # one trade at a time per market
    return out


def all_trades(p: NRParams, fr: dict[str, pd.DataFrame], hourly: bool = True) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s, df in fr.items():
        path = data.CACHE / f"{s}_H1.pkl"
        h1 = pd.read_pickle(path) if hourly and path.exists() else None
        out.extend(run(s, df, p, data.offline_point(s), h1))
    return out


def bench(versions: list[NRParams], say: Callable[[str], None] = print,
          fr: dict[str, pd.DataFrame] | None = None,
          split: tuple[int, int, int] = (T0, LEARN_END, CHECK_END)) -> list[dict[str, Any]]:
    """Learn and check only; the locked part is never shown here."""
    fr = fr if fr is not None else frames()
    t0, learn_end, check_end = split
    rows = []
    for v in versions:
        tr = all_trades(v, fr)
        row = {"version": v.label(), "learn": window(tr, t0, learn_end),
               "check": window(tr, learn_end, check_end)}
        rows.append(row)
        for part in ("learn", "check"):
            w = row[part]
            say(f"{v.label():<30} {part:<5} {w['trades']:>5} trades ({w['per_year']}/yr) win {w['win_pct']}% "
                f"avg {w['avg_r']:+.3f}R t {w['t']:+.2f} pf {w['pf']:.2f} | at {w['risk']:.2%}: "
                f"{w['yearly_pct']:+.1f}%/yr drop {w['drop_pct']:.0f}% | {w['groups']}")
    return rows


__all__ = ["NRParams", "run", "all_trades", "bench"]
