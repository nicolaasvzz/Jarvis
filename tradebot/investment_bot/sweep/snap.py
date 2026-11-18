"""Part 4, family 6: intraday snap-backs in US stock indices (hourly, long only).

During the US cash session (hourly candles starting ``hour_from``..``hour_to``
in server time), when one candle's return falls below ``k`` times the typical
hourly move (the standard deviation of the last ``vol_n`` hourly returns),
buy at the next candle's open and sell at the open ``hold`` candles later.
Never held overnight, so there's no financing; one trade per market at a time.

No peeking: the signal uses the candle that just closed and the ones before
it; the fill is the next open. Costs: the bigger of ``cost_bp`` and the
candle's spread, each side.

Each trade's return is in units of 1% of the position; the portfolio holds
each market with an equal share of the account and is scaled to the drop cap
like the other families (``tsmom.window``).
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import data
from .dip import D0, D_CHECK_END, D_LEARN_END
from .overnight import US
from .tsmom import window


@dataclass(frozen=True)
class SnapParams:
    k: float = 2.0           # a drop of more than k typical hourly moves
    vol_n: int = 100
    hold: int = 3            # candles held
    hour_from: int = 16      # the cash session, by candle start hour (server time)
    hour_to: int = 21        # last candle that may signal (so the exit is before the close)
    cost_bp: float = 1.0

    def label(self) -> str:
        d = SnapParams()
        diff = [f"{f.name}={getattr(self, f.name)}" for f in fields(self)
                if getattr(self, f.name) != getattr(d, f.name)]
        return f"snap-back {self.k} sd, hold {self.hold}h" + (f" ({', '.join(diff)})" if diff else "")


def trades(symbol: str, h1: pd.DataFrame, p: SnapParams, point: float) -> list[dict[str, Any]]:
    t = h1["time"].to_numpy("int64")
    hour = pd.to_datetime(t, unit="s").hour
    o, c = h1["open"].to_numpy(float), h1["close"].to_numpy(float)
    spread = h1["spread"].to_numpy(float) * point
    ret = np.r_[np.nan, c[1:] / c[:-1] - 1]
    sd = pd.Series(ret).rolling(p.vol_n).std().to_numpy()
    out, busy_until = [], -1
    for i in range(p.vol_n, len(h1) - p.hold - 1):
        if i <= busy_until or not (p.hour_from <= hour[i] <= p.hour_to):
            continue
        if not ret[i] < -p.k * sd[i]:
            continue
        a, b = i + 1, i + 1 + p.hold
        if (t[b] - t[a]) > (p.hold + 1) * 3600:      # a session break inside: skip
            continue
        cost = (max(p.cost_bp * 1e-4 * o[a], spread[a]) + max(p.cost_bp * 1e-4 * o[b], spread[b])) / o[a]
        r = o[b] / o[a] - 1 - cost
        out.append({"symbol": symbol, "fill_time": int(t[a]), "exit_time": int(t[b]),
                    "r": float(r / 0.01), "ret": float(r)})
        busy_until = b - 1
    return out


def all_trades(p: SnapParams, symbols: list[str] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in symbols or US:
        path = data.CACHE / f"{s}_H1.pkl"
        if path.exists():
            out.extend(trades(s, pd.read_pickle(path), p, data.offline_point(s)))
    return out


def portfolio(tr: list[dict[str, Any]], n_markets: int) -> pd.Series:
    """Each market holds an equal share of the account: the day's result is the sum over trades / n."""
    if not tr:
        return pd.Series(dtype=float)
    df = pd.DataFrame(tr)
    day = pd.to_datetime(df["exit_time"], unit="s").dt.normalize()
    s = df.groupby(day)["ret"].sum() / n_markets
    return s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D"), fill_value=0.0)


def bench(versions: list[SnapParams], say: Callable[[str], None] = print,
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
            say(f"{v.label():<34} {part:<5} {w['months']:>5} trades avg {w['avg_r'] / 100:+.4%} pf {w['pf']:.2f} "
                f"t(daily) {w['t_daily']:+.2f} | x{w['scale']}: {w['yearly_pct']:+.1f}%/yr, "
                f"drop {w['drop_pct']:.0f}% | q {w['quarters']}")
    return rows


__all__ = ["SnapParams", "trades", "all_trades", "portfolio", "bench"]
