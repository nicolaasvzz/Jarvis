"""Part 4, family 5: pairs. Two markets that normally move together; bet on their gap closing.

For each pair (a, b), each leg is sized to the same volatility, and the
"spread" is the log price of a minus that of b. When the spread's z-score over
the last ``lookback`` days goes past ``z_in``, the next close holds the spread
the other way (short a / long b when it's stretched up, and the reverse), until
the z-score crosses back through ``z_out`` or ``max_days`` pass. It doesn't
care whether markets rise or fall.

No peeking: a position decided on a close earns from the next close on (one
day late, a little pessimistic), as in ``tsmom.py``. Costs: both legs pay the
bigger of ``cost_bp`` and their spread on every change, and ``finance_pa`` a
year while held.

Each round trip is a "trade" (its result in units of 1% of each leg), and the
portfolio is the average over the pairs with data, scaled to the drop cap
(``tsmom.window``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Any, Callable

import numpy as np
import pandas as pd

from .dip import D0, D_CHECK_END, D_LEARN_END
from .trend import frames
from .tsmom import panel, window

PAIRS = [("US500", "US30"), ("XAUUSD", "XAGUSD"), ("DE40", "EUSTX50"), ("FRA40", "EUSTX50")]


@dataclass(frozen=True)
class PairParams:
    lookback: int = 20
    z_in: float = 2.0
    z_out: float = 0.0
    max_days: int = 20
    vol_n: int = 60
    cost_bp: float = 1.0
    finance_pa: float = 0.02

    def label(self) -> str:
        d = PairParams()
        diff = [f"{f.name}={getattr(self, f.name)}" for f in fields(self)
                if getattr(self, f.name) != getattr(d, f.name)]
        return f"pairs z>{self.z_in} over {self.lookback}d" + (f" ({', '.join(diff)})" if diff else "")


def pair_returns(ca: pd.Series, cb: pd.Series, sa: pd.Series, sb: pd.Series,
                 p: PairParams, name: str) -> tuple[pd.Series, list[dict[str, Any]]]:
    """Daily return of one pair's position (a share of the account per unit size), and its trades."""
    df = pd.concat({"a": ca, "b": cb, "sa": sa, "sb": sb}, axis=1).dropna(subset=["a", "b"])
    ra, rb = df["a"].pct_change(), df["b"].pct_change()
    va = ra.ewm(span=p.vol_n, min_periods=p.vol_n).std()
    vb = rb.ewm(span=p.vol_n, min_periods=p.vol_n).std()
    # Equal-risk legs: each leg's weight is inversely proportional to its volatility.
    wa, wb = (1 / va) / (1 / va + 1 / vb), (1 / vb) / (1 / va + 1 / vb)
    spread = wa * np.log(df["a"]) - wb * np.log(df["b"])
    z = (spread - spread.rolling(p.lookback).mean()) / spread.rolling(p.lookback).std()
    zs, n = z.to_numpy(), len(df)
    side = np.zeros(n)          # the position decided on each close: +1 long a/short b, -1 the reverse
    held = 0
    for i in range(n):
        cur = side[i - 1] if i else 0.0
        if cur != 0:
            held += 1
            back = (cur > 0 and zs[i] >= -p.z_out) or (cur < 0 and zs[i] <= p.z_out)
            if back or held >= p.max_days:
                cur = 0.0
        if cur == 0 and not np.isnan(zs[i]) and abs(zs[i]) > p.z_in:
            cur, held = -float(np.sign(zs[i])), 0
        side[i] = cur
    pos = pd.Series(side, index=df.index).shift(1).fillna(0.0)        # earns from the next close
    wa_h, wb_h = wa.shift(1), wb.shift(1)
    gross = pos * (wa_h * ra - wb_h * rb)
    cost_a = np.maximum(p.cost_bp * 1e-4, (df["sa"].fillna(0) / df["a"]).to_numpy())
    cost_b = np.maximum(p.cost_bp * 1e-4, (df["sb"].fillna(0) / df["b"]).to_numpy())
    change = pos.diff().abs().fillna(pos.abs())
    gap = df.index.to_series().diff().dt.days.fillna(1)
    costs = change * (wa_h * cost_a + wb_h * cost_b) + pos.abs() * p.finance_pa / 365 * gap
    daily = (gross - costs).fillna(0.0)
    trades: list[dict[str, Any]] = []
    t = ((df.index - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).to_numpy()
    open_side, start, acc = 0.0, 0, 0.0
    for i in range(n):
        now = pos.iat[i]
        if open_side and now != open_side:      # closed today: today's exit cost belongs to it
            acc += daily.iat[i]
            trades.append({"symbol": name, "fill_time": int(t[start]), "exit_time": int(t[i]),
                           "r": float(acc / 0.01)})
            open_side = 0.0
        elif open_side:
            acc += daily.iat[i]
        if not open_side and now:
            open_side, start, acc = now, i, (daily.iat[i] if i == 0 or pos.iat[i - 1] == 0 else 0.0)
    return daily, trades


def run_all(p: PairParams, fr: dict[str, pd.DataFrame] | None = None,
            pairs: list[tuple[str, str]] | None = None) -> tuple[pd.Series, list[dict[str, Any]]]:
    pairs = pairs or PAIRS
    names = sorted({s for ab in pairs for s in ab})
    c, sp = panel(fr if fr is not None else frames(names))
    sp_price = sp * c
    cols, trades = {}, []
    for a, b in pairs:
        if a not in c or b not in c:
            continue
        d, tr = pair_returns(c[a], c[b], sp_price[a], sp_price[b], p, f"{a}/{b}")
        cols[f"{a}/{b}"] = d
        trades += tr
    df = pd.DataFrame(cols).sort_index().fillna(0.0)
    return df.sum(axis=1) / max(len(cols), 1), trades


def bench(versions: list[PairParams], say: Callable[[str], None] = print,
          split: tuple[int, int, int] = (D0, D_LEARN_END, D_CHECK_END)) -> list[dict[str, Any]]:
    """Learn and check only; the locked part is never shown here."""
    t0, learn_end, check_end = split
    rows = []
    for v in versions:
        port, tr = run_all(v)
        row = {"version": v.label(), "learn": window(port, tr, t0, learn_end),
               "check": window(port, tr, learn_end, check_end)}
        rows.append(row)
        for part in ("learn", "check"):
            w = row[part]
            say(f"{v.label():<36} {part:<5} {w['months']:>4} trades avg {w['avg_r']:+.3f} (1%) pf {w['pf']:.2f} "
                f"t(daily) {w['t_daily']:+.2f} | x{w['scale']}: {w['yearly_pct']:+.1f}%/yr, "
                f"drop {w['drop_pct']:.0f}% | q {w['quarters']}")
    return rows


__all__ = ["PairParams", "PAIRS", "pair_returns", "run_all", "bench"]
