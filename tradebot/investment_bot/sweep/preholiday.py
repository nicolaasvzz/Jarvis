"""Part 4, family 9: the pre-holiday effect in US stock indices.

US stocks have historically risen on the last trading day before a market
holiday. The rule: hold the US indices only for that day, bought at the
previous daily close and sold at that day's close. About ten days a year.

The holidays come from a fixed calendar (``NYSE``, an approximation of the
New York Stock Exchange's: New Year, Martin Luther King Jr. Day, Presidents'
Day, Good Friday, Memorial Day, Juneteenth from 2022, Independence Day,
Labor Day, Thanksgiving, Christmas), so nothing peeks at prices.

Costs: the bigger of ``cost_bp`` and the candle's spread on each side, plus
one night's financing (three over a weekend) at ``finance_pa``.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Callable

import numpy as np
import pandas as pd
from pandas.tseries.holiday import (AbstractHolidayCalendar, GoodFriday, Holiday, USLaborDay,
                                    USMartinLutherKingJr, USMemorialDay, USPresidentsDay,
                                    USThanksgivingDay, nearest_workday)

from . import data
from .dip import D0, D_CHECK_END, D_LEARN_END
from .overnight import US
from .snap import portfolio
from .tsmom import window


class NYSE(AbstractHolidayCalendar):
    rules = [
        Holiday("New Year", month=1, day=1, observance=nearest_workday),
        USMartinLutherKingJr, USPresidentsDay, GoodFriday, USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date=datetime(2022, 1, 1), observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay, USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


@dataclass(frozen=True)
class HolidayParams:
    cost_bp: float = 1.0
    finance_pa: float = 0.02

    def label(self) -> str:
        d = HolidayParams()
        diff = [f"{f.name}={getattr(self, f.name)}" for f in fields(self)
                if getattr(self, f.name) != getattr(d, f.name)]
        return "pre-holiday day" + (f" ({', '.join(diff)})" if diff else "")


def pre_holiday_days(start: str = "2000-01-01", end: str = "2027-12-31") -> set:
    """The last weekday before each holiday (the calendar alone decides)."""
    hol = NYSE().holidays(start, end)
    hset = set(hol)
    out = set()
    for h in hol:
        d = h - pd.offsets.BDay(1)
        while d in hset:
            d -= pd.offsets.BDay(1)
        out.add(d.normalize())
    return out


def trades(symbol: str, d1: pd.DataFrame, p: HolidayParams, point: float,
           days: set | None = None) -> list[dict[str, Any]]:
    days = days if days is not None else pre_holiday_days()
    t = d1["time"].to_numpy("int64")
    day = pd.to_datetime(t, unit="s").normalize()
    c = d1["close"].to_numpy(float)
    sp = d1["spread"].to_numpy(float) * point
    out = []
    for i in range(1, len(d1)):
        if day[i] not in days:
            continue
        buy, sell = c[i - 1], c[i]
        cost = (max(p.cost_bp * 1e-4 * buy, sp[i - 1]) + max(p.cost_bp * 1e-4 * sell, sp[i])) / buy
        nights = max(1, (int(t[i]) - int(t[i - 1])) // 86400)
        r = sell / buy - 1 - cost - p.finance_pa / 365 * nights
        out.append({"symbol": symbol, "side": 1, "fill_time": int(t[i - 1]), "exit_time": int(t[i]),
                    "r": float(r / 0.01), "ret": float(r)})
    return out


def all_trades(p: HolidayParams, symbols: list[str] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    days = pre_holiday_days()
    for s in symbols or US:
        path = data.CACHE / f"{s}_D1.pkl"
        if path.exists():
            out.extend(trades(s, pd.read_pickle(path), p, data.offline_point(s), days))
    return out


def bench(versions: list[HolidayParams], say: Callable[[str], None] = print,
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
            say(f"{v.label():<28} {part:<5} {w['months']:>4} trades avg {w['avg_r'] / 100:+.3%} pf {w['pf']:.2f} "
                f"t(daily) {w['t_daily']:+.2f} | x{w['scale']}: {w['yearly_pct']:+.1f}%/yr, drop {w['drop_pct']:.0f}%")
    return rows


__all__ = ["NYSE", "HolidayParams", "pre_holiday_days", "trades", "all_trades", "bench"]
