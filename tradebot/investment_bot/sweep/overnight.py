"""Part 4, family 4: the overnight drift in US stock indices.

Historically most of the US indices' gains came while the cash market was
closed. On this broker's hourly candles (server time), the US cash session
runs from about 16:30 to 23:00. The rule: buy at the close of the hourly
candle that ends at 23:00 (``entry_h`` = 22, the candle's start hour), sell at
the open of the next day's ``exit_h`` candle (16 = just before the cash open),
flat during the day. Times come from the calendar only, so nothing peeks.

Costs: the bigger of ``cost_bp`` and the candle's spread on each side, plus
``finance_pa`` a year for every calendar night held (three over a weekend).

Each night on each market is a "trade" (its return in units of 1% of the
position), and the portfolio is the average over the markets open that
night, scaled to the drop cap like the other families (``tsmom.window``).
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import data
from .dip import D0, D_CHECK_END, D_LEARN_END
from .tsmom import window

US = ["US500", "US30", "US2000", "USTEC"]


@dataclass(frozen=True)
class NightParams:
    entry_h: int = 22        # buy at the close of this hourly candle
    exit_h: int = 16         # sell at the open of this hourly candle, the next trading day
    cost_bp: float = 1.0
    finance_pa: float = 0.02
    sides: int = 1           # 1 = hold the night long; -1 = hold it short (a control)
    ma_n: int = 0            # >0: only nights when the previous daily close is above its ma_n-day average

    def label(self) -> str:
        d = NightParams()
        diff = [f"{f.name}={getattr(self, f.name)}" for f in fields(self)
                if getattr(self, f.name) != getattr(d, f.name)]
        return "overnight long" + (f" ({', '.join(diff)})" if diff else "")


def uptrend_days(d1: pd.DataFrame, n: int) -> set:
    """Days whose PREVIOUS daily close was above its n-day average (known before that day starts)."""
    c = d1["close"]
    ok = (c > c.rolling(n).mean()).shift(1, fill_value=False)
    days = pd.to_datetime(d1["time"], unit="s").dt.normalize()
    return set(days[ok.to_numpy(bool)])


def nights(symbol: str, h1: pd.DataFrame, p: NightParams, point: float,
           allowed: set | None = None) -> list[dict[str, Any]]:
    t = pd.to_datetime(h1["time"], unit="s")
    hour = t.dt.hour.to_numpy()
    close, open_ = h1["close"].to_numpy(float), h1["open"].to_numpy(float)
    spread = h1["spread"].to_numpy(float) * point
    ins = np.flatnonzero(hour == p.entry_h)
    outs = np.flatnonzero(hour == p.exit_h)
    out = []
    for i in ins:
        k = np.searchsorted(outs, i + 1)
        if k >= len(outs):
            break
        j = outs[k]
        if j < len(h1) and (t.iat[j] - t.iat[i]).days > 4:   # a gap in the data, not a night
            continue
        nxt = np.searchsorted(ins, i + 1)
        if nxt < len(ins) and ins[nxt] < j:
            continue  # another close comes before the exit candle (a short day): skip this night
        if allowed is not None and t.iat[i].normalize() not in allowed:
            continue
        buy, sell = close[i], open_[j]
        cost = (max(p.cost_bp * 1e-4 * buy, spread[i]) + max(p.cost_bp * 1e-4 * sell, spread[j])) / buy
        held = max(1, (t.iat[j].normalize() - t.iat[i].normalize()).days)
        ret = p.sides * (sell / buy - 1) - cost - p.finance_pa / 365 * held
        out.append({"symbol": symbol, "fill_time": int(h1["time"].iat[i]),
                    "exit_time": int(h1["time"].iat[j]), "r": float(ret / 0.01), "ret": float(ret)})
    return out


def portfolio(trades: list[dict[str, Any]]) -> pd.Series:
    """The average night's return over the markets held, by the day the night ends."""
    df = pd.DataFrame(trades)
    day = pd.to_datetime(df["exit_time"], unit="s").dt.normalize()
    return df.groupby(day)["ret"].mean().sort_index()


def all_nights(p: NightParams, symbols: list[str] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in symbols or US:
        path = data.CACHE / f"{s}_H1.pkl"
        if path.exists():
            allowed = None
            if p.ma_n:
                d1 = data.CACHE / f"{s}_D1.pkl"
                allowed = uptrend_days(pd.read_pickle(d1), p.ma_n) if d1.exists() else set()
            out.extend(nights(s, pd.read_pickle(path), p, data.offline_point(s), allowed))
    return out


def bench(versions: list[NightParams], say: Callable[[str], None] = print,
          symbols: list[str] | None = None) -> list[dict[str, Any]]:
    """Learn and check only (the index split); the locked part is never shown here."""
    rows = []
    for v in versions:
        tr = all_nights(v, symbols)
        port = portfolio(tr)
        row = {"version": v.label(), "learn": window(port, tr, D0, D_LEARN_END),
               "check": window(port, tr, D_LEARN_END, D_CHECK_END)}
        rows.append(row)
        for part in ("learn", "check"):
            w = row[part]
            say(f"{v.label():<36} {part:<5} {w['months']:>5} nights avg {w['avg_r'] / 100:+.4%} pf {w['pf']:.2f} "
                f"t(daily) {w['t_daily']:+.2f} | x{w['scale']}: {w['yearly_pct']:+.1f}%/yr, "
                f"drop {w['drop_pct']:.0f}% | q {w['quarters']}")
    return rows


__all__ = ["NightParams", "US", "nights", "portfolio", "all_nights", "bench"]
