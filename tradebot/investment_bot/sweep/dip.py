"""Part 4, family 3: buy the dip in stock indices (long only).

Stock indices drift up over time and tend to bounce after short, sharp
pullbacks. The rule: when the close is above its ``ma_n``-day average (an
uptrend) and the ``rsi_n``-day RSI is under ``rsi_in`` (a sharp pullback), buy
at the next open. Sell at the next open after a close above the ``exit_ma``-day
average, or after ``max_days`` days. A hard stop sits ``stop_atr`` ATRs under
the entry; it is also the size of 1R.

No peeking, costs, financing and the daily marked-to-market paths work exactly
as in ``trend.py``, so the same portfolio tools (``trend.window``) apply.

Index daily data on this broker only starts in 2012 (and 2019-22 for most), so
this family has its own calendar split over ``D0``..``trend.T1``: learn the
first 40%, check the next 30%, the last 30% locked.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import data
from .engine import atr
from .trend import DAY, T1, frames, window

INDICES = ["US500", "US30", "USTEC", "US2000", "DE40", "UK100", "JPN225",
           "AUS200", "FRA40", "ESP35", "EUSTX50", "HK50"]
D0 = int(datetime(2012, 8, 6, tzinfo=timezone.utc).timestamp())
D_LEARN_END = D0 + int(0.4 * (T1 - D0))
D_CHECK_END = D0 + int(0.7 * (T1 - D0))


@dataclass(frozen=True)
class DipParams:
    ma_n: int = 200          # uptrend filter (0 = none)
    rsi_n: int = 2
    rsi_in: float = 10.0     # buy when RSI is under this
    exit_ma: int = 5         # sell after a close above this average
    max_days: int = 10       # or after this many days
    stop_atr: float = 3.0    # hard stop, and the size of 1R
    atr_n: int = 20
    cost_bp: float = 1.0
    finance_pa: float = 0.02
    sides: int = 1           # 1 = buy dips only; 0 = also sell short sharp rises (the mirror rule)
    entry: str = "rsi"       # "rsi"; "volspike" = buy when 10-day volatility is spike x the 100-day
    spike: float = 1.5

    def label(self) -> str:
        d = DipParams()
        diff = [f"{f.name}={getattr(self, f.name)}" for f in fields(self)
                if getattr(self, f.name) != getattr(d, f.name)]
        head = "dip RSI2<10 over MA200" if self.entry == "rsi" else f"vol spike x{self.spike}"
        return head + (f" ({', '.join(diff)})" if diff else "")


def rsi(close: np.ndarray, n: int) -> np.ndarray:
    """Wilder's RSI; each value uses closes up to and including that day only."""
    d = np.diff(close, prepend=close[0])
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    au = pd.Series(up).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    ad = pd.Series(dn).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + au / ad)
    return np.where(ad == 0, 100.0, out)


def run(symbol: str, df: pd.DataFrame, p: DipParams, point: float) -> list[dict[str, Any]]:
    n = len(df)
    if n < max(p.ma_n, p.atr_n, p.exit_ma) + 2:
        return []
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    t = df["time"].to_numpy("int64")
    spread = df["spread"].to_numpy(float) * point if "spread" in df else np.zeros(n)
    a = atr(df, p.atr_n)
    ma = pd.Series(c).rolling(p.ma_n).mean().to_numpy() if p.ma_n else np.full(n, np.nan)
    xma = pd.Series(c).rolling(p.exit_ma).mean().to_numpy() if p.exit_ma else np.full(n, np.nan)
    r_ = rsi(c, p.rsi_n)
    lr = pd.Series(np.log(c)).diff()
    vol_ratio = (lr.rolling(10).std() / lr.rolling(100).std()).to_numpy()

    def cost(i: int, price: float) -> float:
        return max(p.cost_bp * 1e-4 * price, spread[i])

    out: list[dict[str, Any]] = []
    pos: dict[str, Any] | None = None
    want = 0
    leave = False

    def close(i: int, price: float, why: str, extra: float = 0.0) -> None:
        nonlocal pos
        risk, s = pos["risk"], pos["side"]
        r = s * (price - pos["entry"]) / risk - pos["paid"] - (cost(i, price) + extra) / risk
        pos["path"].append([int(t[i]), r - pos["mark"]])
        out.append({"symbol": symbol, "profile": "dip", "side": s, "entry": pos["entry"],
                    "stop": pos["stop"], "fill_time": pos["fill_time"], "exit_time": int(t[i]),
                    "exit_price": float(price), "r": float(r), "reason": why, "path": pos["path"]})
        pos = None

    for i in range(1, n):
        if pos is not None and leave:
            close(i, o[i], "exit")
        leave = False
        if pos is None and want and a[i - 1] > 0:
            risk = p.stop_atr * a[i - 1]
            paid = cost(i, o[i]) / risk
            pos = {"side": want, "entry": o[i], "risk": risk, "stop": o[i] - want * risk,
                   "fill_time": int(t[i]),
                   "paid": paid, "mark": -paid, "path": [[int(t[i]), -paid]], "bars": 0}
        want = 0
        if pos is not None:
            s = pos["side"]
            if s * (o[i] - pos["stop"]) <= 0:
                close(i, o[i], "stop", 0.25 * cost(i, o[i]))
            elif (s > 0 and lo[i] <= pos["stop"]) or (s < 0 and h[i] >= pos["stop"]):
                close(i, pos["stop"], "stop", 0.25 * cost(i, pos["stop"]))
        if pos is not None:
            pos["bars"] += 1
            days = max(1, (int(t[i]) - int(t[i - 1])) // DAY)
            pos["paid"] += p.finance_pa / 365 * days * c[i] / pos["risk"]
            mark = pos["side"] * (c[i] - pos["entry"]) / pos["risk"] - pos["paid"]
            pos["path"].append([int(t[i]), mark - pos["mark"]])
            pos["mark"] = mark
            if (p.exit_ma and pos["side"] * (c[i] - xma[i]) > 0) or pos["bars"] >= p.max_days:
                leave = True
        if pos is None and not leave:
            up = not p.ma_n or c[i] > ma[i]
            down = not p.ma_n or c[i] < ma[i]
            if p.entry == "volspike":
                if vol_ratio[i] > p.spike and (not p.ma_n or c[i] > ma[i]):
                    want = 1
            elif up and r_[i] < p.rsi_in:
                want = 1
            elif p.sides == 0 and down and r_[i] > 100 - p.rsi_in:
                want = -1
    return out


def all_trades(p: DipParams, fr: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s, df in fr.items():
        out.extend(run(s, df, p, data.offline_point(s)))
    return out


def bench(versions: list[DipParams], say: Callable[[str], None] = print,
          fr: dict[str, pd.DataFrame] | None = None,
          split: tuple[int, int, int] = (D0, D_LEARN_END, D_CHECK_END)) -> list[dict[str, Any]]:
    """Learn and check only; the locked part (after the split's check end) is never shown here."""
    fr = fr if fr is not None else frames(INDICES)
    t0, learn_end, check_end = split
    rows = []
    for v in versions:
        tr = all_trades(v, fr)
        row = {"version": v.label(), "learn": window(tr, t0, learn_end),
               "check": window(tr, learn_end, check_end)}
        rows.append(row)
        for part in ("learn", "check"):
            w = row[part]
            say(f"{v.label():<44} {part:<5} {w['trades']:>4} trades ({w['per_year']}/yr) "
                f"win {w['win_pct']}% avg {w['avg_r']:+.3f}R t {w['t']:+.2f} pf {w['pf']:.2f} | "
                f"at {w['risk']:.2%} risk: {w['yearly_pct']:+.1f}%/yr, drop {w['drop_pct']:.0f}% "
                f"| q {w['quarters']}")
    return rows


__all__ = ["DipParams", "INDICES", "rsi", "run", "bench", "D0", "D_LEARN_END", "D_CHECK_END"]
