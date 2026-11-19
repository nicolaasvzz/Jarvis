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




# Approximate yearly average policy rates by currency (public central-bank history, rounded;
# 2025-26 estimated). For the carry test only: good to a point or so, not to the basis point.
_Y = list(range(2000, 2027))
_RATES_RAW = {
    "USD": [POLICY[y] * 100 for y in _Y],
    "EUR": [4.0, 4.3, 3.3, 2.3, 2.0, 2.05, 2.8, 3.9, 3.9, 1.1, 1.0, 1.2, 0.9, 0.6, 0.2, 0.05, 0, 0, 0, 0, 0, 0,
            0.6, 3.8, 3.9, 2.3, 2.0],
    "GBP": [6.0, 5.1, 4.0, 3.7, 4.4, 4.7, 4.6, 5.5, 4.7, 0.6, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.4, 0.3, 0.6, 0.75,
            0.2, 0.1, 1.5, 4.7, 5.1, 4.3, 3.8],
    "JPY": [0.2, 0.1, 0, 0, 0, 0, 0.2, 0.5, 0.5, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, -0.1, -0.1, -0.1, -0.1,
            -0.1, -0.1, -0.1, -0.1, 0.1, 0.5, 0.75],
    "CHF": [3.0, 2.7, 1.0, 0.3, 0.5, 0.75, 1.5, 2.5, 2.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, -0.75, -0.75, -0.75,
            -0.75, -0.75, -0.75, -0.75, 0.0, 1.6, 1.3, 0.3, 0.0],
    "AUD": [6.0, 4.9, 4.6, 4.8, 5.25, 5.5, 5.8, 6.5, 6.7, 3.3, 4.4, 4.6, 3.6, 2.8, 2.5, 2.1, 1.6, 1.5, 1.5, 1.0,
            0.3, 0.1, 1.3, 3.9, 4.35, 3.9, 3.6],
    "NZD": [6.0, 5.6, 5.6, 5.2, 6.1, 7.0, 7.25, 8.0, 7.5, 2.7, 2.7, 2.5, 2.5, 2.5, 3.3, 3.0, 2.1, 1.75, 1.75,
            1.3, 0.4, 0.4, 2.8, 5.4, 5.2, 3.5, 2.5],
    "CAD": [5.5, 4.0, 2.4, 2.9, 2.3, 2.7, 4.0, 4.3, 2.9, 0.4, 0.6, 1.0, 1.0, 1.0, 1.0, 0.6, 0.5, 0.7, 1.4, 1.75,
            0.5, 0.25, 2.0, 4.8, 4.6, 2.9, 2.25],
    "ZAR": [12, 10.5, 12.5, 10.5, 7.6, 7.0, 7.5, 9.8, 11.5, 7.5, 6.0, 5.5, 5.2, 5.0, 5.6, 6.0, 7.0, 6.9, 6.6,
            6.6, 4.0, 3.6, 5.5, 8.0, 8.0, 7.2, 6.8],
}
RATES = {cur: dict(zip(_Y, (v / 100 for v in vals))) for cur, vals in _RATES_RAW.items()}


def carry(pair: str, days: pd.DatetimeIndex) -> np.ndarray:
    """Yearly interest gap of holding ``pair`` long (base rate minus quote rate), per day; NaN if unknown."""
    base, quote = pair[:3], pair[3:6]
    if base not in RATES or quote not in RATES:
        return np.full(len(days), np.nan)
    years = np.clip(np.asarray(days.year), _Y[0], _Y[-1])
    return np.array([RATES[base][int(y)] - RATES[quote][int(y)] for y in years])


__all__ = ["POLICY", "MARKUP", "RATES", "yearly_rate", "charge", "carry"]
