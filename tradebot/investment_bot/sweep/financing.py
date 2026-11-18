"""What a broker charges to hold a CFD overnight, from the historical US policy rate.

A typical retail CFD broker charges a long position the benchmark interest rate
plus a markup, and pays (or charges) a short position the rate minus the
markup. So in years of high rates a long costs a lot to hold and a short may
even earn. A flat yearly charge misses both.

``POLICY`` is the yearly average US effective federal funds rate (approximate,
public figures; 2026 is an estimate). Every market is charged on the US rate:
a simplification (euro, yen and other rates differed), good enough to show
when financing decides a verdict.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

POLICY = {
    1999: 0.050, 2000: 0.062, 2001: 0.039, 2002: 0.017, 2003: 0.011, 2004: 0.014, 2005: 0.032,
    2006: 0.050, 2007: 0.050, 2008: 0.019, 2009: 0.002, 2010: 0.002, 2011: 0.001, 2012: 0.001,
    2013: 0.001, 2014: 0.001, 2015: 0.001, 2016: 0.004, 2017: 0.010, 2018: 0.018, 2019: 0.022,
    2020: 0.004, 2021: 0.001, 2022: 0.017, 2023: 0.050, 2024: 0.051, 2025: 0.042, 2026: 0.036,
}
MARKUP = 0.025


def yearly_rate(days: pd.DatetimeIndex) -> np.ndarray:
    years = np.asarray(days.year)
    return np.array([POLICY.get(int(y), POLICY[min(POLICY)] if y < min(POLICY) else POLICY[max(POLICY)])
                     for y in years])


def charge(held: np.ndarray, days: pd.DatetimeIndex, markup: float = MARKUP) -> np.ndarray:
    """Yearly financing as a share of each position (rows = days, columns = markets):
    longs pay rate + markup; shorts pay markup - rate (negative = the short is paid)."""
    rate = yearly_rate(days)[:, None]
    return np.where(held > 0, held * (rate + markup), np.where(held < 0, -held * (markup - rate), 0.0))


__all__ = ["POLICY", "MARKUP", "yearly_rate", "charge"]
