"""Part 4, family 8: intraday momentum in US indices (first hour predicts the last hour).

Published research (Gao, Han, Li and Zhou, 2018) found that the US market's
move early in the session predicts its move in the last half-hour. On this
broker's hourly candles (server time; the cash session runs about 16:30 to
23:00): the "first hour" is from the close of the 15:00 candle to the close of
the 16:00 candle; the trade is the 22:00 candle (the session's last hour),
bought at its open if the first hour rose and sold short if it fell, closed at
its close. One trade a day, an hour long, never overnight.

No peeking: the first hour is over long before the 22:00 candle opens.
Costs: the bigger of ``cost_bp`` and the candle's spread, each side.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import data
from .dip import D0, D_CHECK_END, D_LEARN_END
from .overnight import US
from .snap import portfolio
from .tsmom import window


@dataclass(frozen=True)
class LastHourParams:
    first_from: int = 15     # the first-hour move: close of this candle ...
    first_to: int = 16       # ... to the close of this one
    trade_h: int = 22        # trade this candle, open to close
    min_move: float = 0.0    # only when the first hour moved at least this much (share of price)
    cost_bp: float = 1.0

    def label(self) -> str:
        d = LastHourParams()
        diff = [f"{f.name}={getattr(self, f.name)}" for f in fields(self)
                if getattr(self, f.name) != getattr(d, f.name)]
        return "first hour -> last hour" + (f" ({', '.join(diff)})" if diff else "")


def trades(symbol: str, h1: pd.DataFrame, p: LastHourParams, point: float) -> list[dict[str, Any]]:
    t = pd.to_datetime(h1["time"], unit="s")
    df = pd.DataFrame({"day": t.dt.normalize(), "hour": t.dt.hour, "time": h1["time"].to_numpy("int64"),
                       "open": h1["open"].to_numpy(float), "close": h1["close"].to_numpy(float),
                       "spread": h1["spread"].to_numpy(float) * point})
    out = []
    for day, g in df.groupby("day", sort=True):
        g = g.set_index("hour")
        if not {p.first_from, p.first_to, p.trade_h} <= set(g.index):
            continue
        first = g.at[p.first_to, "close"] / g.at[p.first_from, "close"] - 1
        if abs(first) < p.min_move or first == 0:
            continue
        side = 1 if first > 0 else -1
        buy, sell = g.at[p.trade_h, "open"], g.at[p.trade_h, "close"]
        sp = g.at[p.trade_h, "spread"]
        cost = 2 * max(p.cost_bp * 1e-4 * buy, sp) / buy
        r = side * (sell / buy - 1) - cost
        tt = int(g.at[p.trade_h, "time"])
        out.append({"symbol": symbol, "side": side, "fill_time": tt, "exit_time": tt + 3600,
                    "r": float(r / 0.01), "ret": float(r)})
    return out


def all_trades(p: LastHourParams, symbols: list[str] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in symbols or US:
        path = data.CACHE / f"{s}_H1.pkl"
        if path.exists():
            out.extend(trades(s, pd.read_pickle(path), p, data.offline_point(s)))
    return out


def bench(versions: list[LastHourParams], say: Callable[[str], None] = print,
          split: tuple[int, int, int] = (D0, D_LEARN_END, D_CHECK_END)) -> list[dict[str, Any]]:
    """Learn and check only; the locked part is never shown here."""
    t0, learn_end, check_end = split
    rows = []
    for v in versions:
        tr = all_trades(v)
        port = portfolio(tr, len(US))
        row = {"version": v.label(), "learn": window(port, tr, t0, learn_end),
               "check": window(port, tr, learn_end, check_end)}
        rows.append(row)
        for part in ("learn", "check"):
            w = row[part]
            say(f"{v.label():<40} {part:<5} {w['months']:>5} trades avg {w['avg_r'] / 100:+.4%} pf {w['pf']:.2f} "
                f"t(daily) {w['t_daily']:+.2f} | x{w['scale']}: {w['yearly_pct']:+.1f}%/yr, "
                f"drop {w['drop_pct']:.0f}% | q {w['quarters']}")
    return rows


__all__ = ["LastHourParams", "trades", "all_trades", "bench"]
