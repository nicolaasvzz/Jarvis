"""Part 4, family 2: time-series momentum, rebalanced monthly, volatility-scaled.

At each month's last daily close, every market is held long if it rose over
the last ``lookback`` trading days and short if it fell (``sides`` can limit
that). Each position is sized so the market's own volatility (an exponential
average of daily returns over ``vol_n`` days) is ``target_vol`` a year; the
portfolio is the average over the markets that have data that day.

No peeking: a position decided on a close earns returns only from the next
close on (a full day late, which is a little pessimistic).

Costs: changing a position pays the bigger of ``cost_bp`` and the day's candle
spread on the change; holding pays ``finance_pa`` of the position a year.

The portfolio's daily return is then scaled to the biggest size whose worst
drop stays within the cap (``trend.at_cap``). Each market-month is also
written as a "trade" (its result in units of its monthly risk) so the luck bar
can be checked the same way as the other families.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import data
from .bench import group_of
from .prove import quarters, stats
from .trend import CHECK_END, DAY, LEARN_END, T0, T1, curve, frames

DD_CAP = 0.25


@dataclass(frozen=True)
class MomParams:
    lookback: int = 252      # trading days of return that decide the side
    vol_n: int = 60          # span of the volatility estimate
    target_vol: float = 0.10 # each market's yearly volatility
    max_lev: float = 5.0     # most a single market may be sized up
    sides: int = 0           # 0 both, 1 long only
    cost_bp: float = 1.0
    finance_pa: float = 0.02
    mode: str = "momentum"   # "momentum"; "hold" (always long); "above_ma" (long over the ma_n-day average);
                             # "rank" (long the rank_k strongest markets, short the rank_k weakest)
    ma_n: int = 200
    rank_k: int = 5

    def label(self) -> str:
        d = MomParams()
        skip = {"lookback", "mode"} | ({"ma_n"} if self.mode != "above_ma" else set())             | ({"rank_k"} if self.mode != "rank" else set())
        diff = [f"{f.name}={getattr(self, f.name)}" for f in fields(self)
                if getattr(self, f.name) != getattr(d, f.name) and f.name not in skip]
        head = {"momentum": f"momentum {self.lookback}d", "hold": "hold long",
                "above_ma": f"hold long over MA{self.ma_n}",
                "rank": f"rank {self.lookback}d top/bottom {self.rank_k}"}[self.mode]
        return head + (f" ({', '.join(diff)})" if diff else "")


def panel(fr: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Daily closes and spreads (as a share of price), one column per market, by UTC day."""
    closes, spreads = {}, {}
    for s, df in fr.items():
        day = pd.to_datetime(df["time"], unit="s").dt.normalize()
        closes[s] = pd.Series(df["close"].to_numpy(float), index=day)
        spreads[s] = pd.Series(df["spread"].to_numpy(float) * data.offline_point(s)
                               / df["close"].to_numpy(float), index=day)
    c = pd.DataFrame(closes).sort_index()
    sp = pd.DataFrame(spreads).sort_index()
    c = c[~c.index.duplicated()]
    sp = sp[~sp.index.duplicated()]
    return c, sp


def weights(c: pd.DataFrame, p: MomParams) -> pd.DataFrame:
    """The position each market holds after each close (a share of the account, before scaling)."""
    ret = c.pct_change(fill_method=None)
    vol = ret.ewm(span=p.vol_n, min_periods=p.vol_n).std() * math.sqrt(252)
    if p.mode == "hold":
        side = (c * 0 + 1).where(c.notna())
    elif p.mode == "above_ma":
        side = (c > c.rolling(p.ma_n).mean()).astype(float).where(c.rolling(p.ma_n).mean().notna())
    elif p.mode == "rank":
        # Strength = the lookback's return per unit of its own volatility, compared across markets.
        score = (c / c.shift(p.lookback) - 1) / (vol * math.sqrt(p.lookback / 252))
        n_ok = score.notna().sum(axis=1)
        top = score.rank(axis=1, ascending=False) <= p.rank_k
        bottom = score.rank(axis=1, ascending=True) <= p.rank_k
        side = top.astype(float) - (bottom.astype(float) if p.sides <= 0 else 0.0)
        side = side.mul((n_ok >= 2 * p.rank_k).astype(float), axis=0).where(c.notna())
    else:
        side = np.sign(c / c.shift(p.lookback) - 1)
    if p.sides > 0:
        side = side.clip(lower=0)
    w = (side * (p.target_vol / vol)).clip(-p.max_lev, p.max_lev)
    month_end = c.index.to_series().dt.to_period("M") != c.index.to_series().shift(-1).dt.to_period("M")
    # Only the last close of a month decides; the very last row (month not over yet) does not.
    month_end.iloc[-1] = False
    mask = np.repeat(month_end.to_numpy()[:, None], w.shape[1], axis=1)
    w = w.where(mask).ffill()
    return w.where(c.notna())


def returns(c: pd.DataFrame, sp: pd.DataFrame, p: MomParams) -> tuple[pd.Series, pd.DataFrame]:
    """The portfolio's daily return (unscaled), and each market's daily contribution."""
    ret = c.pct_change(fill_method=None)
    w = weights(c, p)
    held = w.shift(1)                       # decided on yesterday's close, earning from today's
    gap = c.index.to_series().diff().dt.days.fillna(1).to_numpy()
    cost_frac = np.maximum(sp.reindex_like(c).fillna(0).to_numpy(), p.cost_bp * 1e-4)
    trade = (held.fillna(0) - held.shift(1).fillna(0)).abs().to_numpy()
    per = held.to_numpy() * ret.to_numpy() - trade * cost_frac \
        - np.abs(held.fillna(0).to_numpy()) * p.finance_pa / 365 * gap[:, None]
    per = pd.DataFrame(per, index=c.index, columns=c.columns).where(held.notna())
    n = per.notna().sum(axis=1)
    port = per.sum(axis=1) / n.where(n > 0)
    return port.fillna(0.0), per


def market_months(per: pd.DataFrame, p: MomParams) -> list[dict[str, Any]]:
    """Each market-month as a trade: its result in units of its monthly risk."""
    unit = p.target_vol / math.sqrt(12)
    out = []
    for (s, month), x in per.stack().groupby([lambda k: k[1], lambda k: k[0].to_period("M")]):
        end = int(month.end_time.normalize().timestamp())
        start = int(month.start_time.normalize().timestamp())
        out.append({"symbol": s, "fill_time": start, "exit_time": end, "r": float(x.sum() / unit)})
    return out


def window(port: pd.Series, trades: list[dict[str, Any]], t0: int, t1: int) -> dict[str, Any]:
    t = ((port.index - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).to_numpy()
    days = port[(t >= t0) & (t < t1)]
    years = (t1 - t0) / (365.25 * DAY)
    lo, hi = 0.0, 20.0
    if curve(list(days), hi, years)[1] <= DD_CAP:
        lo = hi
    else:
        for _ in range(40):
            mid = (lo + hi) / 2
            if curve(list(days), mid, years)[1] <= DD_CAP:
                lo = mid
            else:
                hi = mid
    yearly, drop = curve(list(days), lo, years)
    sd = days.std()
    tdaily = float(days.mean() / sd * math.sqrt(len(days))) if sd > 0 else 0.0
    st = stats(trades, t0, t1)
    closed = [x for x in trades if t0 <= x["exit_time"] < t1]
    groups = {}
    for g in ("forex", "metals", "indices"):
        rs = [x["r"] for x in closed if group_of(x["symbol"]) == g]
        groups[g] = round(float(np.mean(rs)), 3) if rs else None
    return {"months": st["trades"], "avg_r": st["avg_r"], "pf": st["pf"], "t_daily": round(tdaily, 2),
            "scale": round(lo, 2), "yearly_pct": round(yearly * 100, 1), "drop_pct": round(drop * 100, 1),
            "quarters": quarters(closed, t0, t1), "groups": groups}


def bench(versions: list[MomParams], say: Callable[[str], None] = print,
          fr: dict[str, pd.DataFrame] | None = None,
          split: tuple[int, int, int] = (T0, LEARN_END, CHECK_END)) -> list[dict[str, Any]]:
    """Learn and check only. The locked part (after the split's check end) is never shown here."""
    t0, learn_end, check_end = split
    c, sp = panel(fr if fr is not None else frames())
    rows = []
    for v in versions:
        port, per = returns(c, sp, v)
        tr = market_months(per, v)
        row = {"version": v.label(), "learn": window(port, tr, t0, learn_end),
               "check": window(port, tr, learn_end, check_end)}
        rows.append(row)
        for part in ("learn", "check"):
            w = row[part]
            say(f"{v.label():<40} {part:<5} {w['months']:>5} market-months avg {w['avg_r']:+.3f}R "
                f"pf {w['pf']:.2f} t(daily) {w['t_daily']:+.2f} | scaled x{w['scale']}: "
                f"{w['yearly_pct']:+.1f}%/yr, drop {w['drop_pct']:.0f}% | q {w['quarters']} | {w['groups']}")
    return rows


__all__ = ["MomParams", "panel", "weights", "returns", "market_months", "window", "bench", "T1"]
