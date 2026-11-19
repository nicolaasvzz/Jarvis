"""Part 4, family 1: daily trend following across all 45 markets.

The oldest systematic way to hold for weeks: buy when the daily close makes a
new ``entry_n``-day high, sell short on a new ``entry_n``-day low, and ride it
until price closes through the ``exit_n``-day low (high), with a hard stop
``stop_atr`` ATRs away. One position per market.

No peeking: everything is decided on a daily close and filled at the next
open. A stop is hit inside the day (at the open if price gaps through it).

Costs, per side: the bigger of ``cost_bp`` and the candle's own spread, plus a
quarter of that again on stop exits. Holding costs ``finance_pa`` of the
position's size a year (overnight financing on a CFD), charged daily.

Every trade carries its day-by-day result (``path``), so a portfolio of all the
markets can be marked to market each day: the worst drop is the real one, not
just the one between closed trades.

Time is split by calendar for every market at once (``T0``..``T1``): learn is
the first 40%, check the next 30%, and the last 30% is locked. ``bench`` never
shows the locked part; ``locked`` does, once per version, and counts looks.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import data
from .bench import group_of
from .engine import atr
from .prove import quarters, stats

T0 = int(datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp())
T1 = int(datetime(2026, 11, 18, tzinfo=timezone.utc).timestamp())
LEARN_END = T0 + int(0.4 * (T1 - T0))
CHECK_END = T0 + int(0.7 * (T1 - T0))
LOOKS_FILE = "trend_looks.json"
DD_CAP = 0.25
DAY = 86400


@dataclass(frozen=True)
class TrendParams:
    entry_n: int = 50        # breakout: close beyond the last entry_n days' high/low
    exit_n: int = 20         # exit: close beyond the last exit_n days' low/high
    atr_n: int = 20
    stop_atr: float = 3.0    # hard stop, ATRs from the entry
    sides: int = 0           # 0 both, 1 long only, -1 short only
    cost_bp: float = 1.0     # per side, at least
    finance_pa: float = 0.02 # holding cost, share of the position a year

    def label(self) -> str:
        d = TrendParams()
        diff = [f"{f.name}={getattr(self, f.name)}" for f in fields(self)
                if getattr(self, f.name) != getattr(d, f.name)]
        return f"trend {self.entry_n}/{self.exit_n}, stop {self.stop_atr} ATR" + (
            f" ({', '.join(diff)})" if diff else "")


def run(symbol: str, df: pd.DataFrame, p: TrendParams, point: float) -> list[dict[str, Any]]:
    """All the closed trades on one market's daily candles."""
    n = len(df)
    if n < max(p.entry_n, p.exit_n, p.atr_n) + 2:
        return []
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    t = df["time"].to_numpy("int64")
    spread = df["spread"].to_numpy(float) * point if "spread" in df else np.zeros(n)
    a = atr(df, p.atr_n)
    hh = pd.Series(h).rolling(p.entry_n).max().shift(1).to_numpy()
    ll = pd.Series(lo).rolling(p.entry_n).min().shift(1).to_numpy()
    xh = pd.Series(h).rolling(p.exit_n).max().shift(1).to_numpy()
    xl = pd.Series(lo).rolling(p.exit_n).min().shift(1).to_numpy()

    def cost(i: int, price: float) -> float:
        return max(p.cost_bp * 1e-4 * price, spread[i])

    out: list[dict[str, Any]] = []
    pos: dict[str, Any] | None = None
    want = 0          # side to enter at the next open
    leave = False     # exit at the next open

    def close(i: int, price: float, why: str, extra: float = 0.0) -> None:
        nonlocal pos
        s, risk = pos["side"], pos["risk"]
        r = s * (price - pos["entry"]) / risk - pos["paid"] - (cost(i, price) + extra) / risk
        pos["path"].append([int(t[i]), r - pos["mark"]])
        out.append({"symbol": symbol, "profile": "trend", "side": s, "entry": pos["entry"],
                    "stop": pos["stop0"], "fill_time": pos["fill_time"], "exit_time": int(t[i]),
                    "exit_price": float(price), "r": float(r), "reason": why, "path": pos["path"]})
        pos = None

    for i in range(1, n):
        # --- the open: orders decided on yesterday's close
        if pos is not None and leave:
            close(i, o[i], "exit")
        leave = False
        if pos is None and want and a[i - 1] > 0:
            risk = p.stop_atr * a[i - 1]
            pos = {"side": want, "entry": o[i], "risk": risk, "stop": o[i] - want * risk,
                   "fill_time": int(t[i]), "paid": cost(i, o[i]) / risk, "mark": 0.0, "path": []}
            pos["stop0"] = pos["stop"]
            pos["mark"] = -pos["paid"]
            pos["path"].append([int(t[i]), -pos["paid"]])
        want = 0
        # --- during the day: the stop
        if pos is not None:
            s, stop = pos["side"], pos["stop"]
            if s * (o[i] - stop) <= 0:
                close(i, o[i], "stop", 0.25 * cost(i, o[i]))
            elif (s > 0 and lo[i] <= stop) or (s < 0 and h[i] >= stop):
                close(i, stop, "stop", 0.25 * cost(i, stop))
        # --- the close: mark to market, then decide for tomorrow
        if pos is not None:
            days = max(1, (int(t[i]) - (int(t[i - 1]) if i else int(t[i]))) // DAY)
            pos["paid"] += p.finance_pa / 365 * days * c[i] / pos["risk"]
            mark = pos["side"] * (c[i] - pos["entry"]) / pos["risk"] - pos["paid"]
            pos["path"].append([int(t[i]), mark - pos["mark"]])
            pos["mark"] = mark
            if (pos["side"] > 0 and c[i] < xl[i]) or (pos["side"] < 0 and c[i] > xh[i]):
                leave = True
        if pos is None and not leave:
            if c[i] > hh[i] and p.sides >= 0:
                want = 1
            elif c[i] < ll[i] and p.sides <= 0:
                want = -1
    return out


# ------------------------------------------------------------ portfolio ----

def daily(trades: list[dict[str, Any]], t0: int, t1: int) -> list[float]:
    """The portfolio's result each day, in R summed over all open trades."""
    by_day: dict[int, float] = defaultdict(float)
    for tr in trades:
        for when, x in tr["path"]:
            if t0 <= when < t1:
                by_day[when // DAY] += x
    return [by_day[d] for d in sorted(by_day)]


def curve(days: list[float], risk: float, years: float) -> tuple[float, float]:
    """(yearly return, worst drop) compounding ``risk`` per R each day."""
    eq = peak = 1.0
    dd = 0.0
    for x in days:
        eq *= max(1 + risk * x, 0.0)
        if eq <= 0:
            return -1.0, 1.0
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    return eq ** (1 / years) - 1 if years > 0 else 0.0, dd


def at_cap(days: list[float], years: float, cap: float = DD_CAP) -> tuple[float, float, float]:
    """The biggest risk a trade whose worst drop stays within ``cap``: (risk, yearly, drop)."""
    lo_, hi_ = 0.0, 0.10
    if curve(days, hi_, years)[1] <= cap:
        lo_ = hi_
    else:
        for _ in range(30):
            mid = (lo_ + hi_) / 2
            if curve(days, mid, years)[1] <= cap:
                lo_ = mid
            else:
                hi_ = mid
    y, d = curve(days, lo_, years)
    return lo_, y, d


def window(trades: list[dict[str, Any]], t0: int, t1: int) -> dict[str, Any]:
    st = stats([{k: v for k, v in tr.items() if k != "path"} for tr in trades], t0, t1)
    years = (t1 - t0) / (365.25 * DAY)
    risk, yearly, drop = at_cap(daily(trades, t0, t1), years)
    closed = [tr for tr in trades if t0 <= tr["exit_time"] < t1]
    groups = {}
    for g in ("forex", "metals", "indices"):
        rs = [tr["r"] for tr in closed if group_of(tr["symbol"]) == g]
        groups[g] = round(float(np.mean(rs)), 3) if rs else None
    return {**st, "per_year": round(st["trades"] / years, 1), "risk": round(risk, 4),
            "yearly_pct": round(yearly * 100, 1), "drop_pct": round(drop * 100, 1),
            "quarters": quarters(closed, t0, t1), "groups": groups}


def frames(symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    out = {}
    for s in symbols or data.UNIVERSE + data.FRESH:
        path = data.CACHE / f"{s}_D1.pkl"
        if path.exists():
            out[s] = pd.read_pickle(path)
    return out


def all_trades(p: TrendParams, fr: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s, df in fr.items():
        out.extend(run(s, df, p, data.offline_point(s)))
    return out


def bench(versions: list[TrendParams], say: Callable[[str], None] = print,
          fr: dict[str, pd.DataFrame] | None = None,
          split: tuple[int, int, int] = (T0, LEARN_END, CHECK_END)) -> list[dict[str, Any]]:
    """Learn and check results for each version. The locked part is never shown here."""
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
            say(f"{v.label():<48} {part:<5} {w['trades']:>5} trades ({w['per_year']}/yr) "
                f"avg {w['avg_r']:+.3f}R t {w['t']:+.2f} pf {w['pf']:.2f} | at {w['risk']:.2%} risk: "
                f"{w['yearly_pct']:+.1f}%/yr, drop {w['drop_pct']:.0f}% | {w['groups']}")
    return rows


def locked(v: TrendParams, say: Callable[[str], None] = print,
           fr: dict[str, pd.DataFrame] | None = None) -> dict[str, Any]:
    """The one look at the locked last 30%, at the risk chosen on learn+check. Counted."""
    fr = fr if fr is not None else frames()
    tr = all_trades(v, fr)
    years_dev = (CHECK_END - T0) / (365.25 * DAY)
    risk, _, _ = at_cap(daily(tr, T0, CHECK_END), years_dev)
    years = (T1 - CHECK_END) / (365.25 * DAY)
    res = window(tr, CHECK_END, T1)
    res["dev_risk"] = round(risk, 4)
    y, d = curve(daily(tr, CHECK_END, T1), risk, years)
    res["yearly_at_dev_risk_pct"], res["drop_at_dev_risk_pct"] = round(y * 100, 1), round(d * 100, 1)
    looks = json.loads(Path(LOOKS_FILE).read_text()) if Path(LOOKS_FILE).exists() else []
    looks.append({"at": datetime.now().isoformat(timespec="minutes"), "version": v.label(),
                  "params": asdict(v), "result": res})
    Path(LOOKS_FILE).write_text(json.dumps(looks, indent=1))
    say(f"LOCKED look #{len(looks)}: {json.dumps(res)}")
    return res


__all__ = ["TrendParams", "run", "bench", "locked", "window", "curve", "at_cap", "daily"]
