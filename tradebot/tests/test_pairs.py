"""Part 4, family 5: pairs."""
import numpy as np
import pandas as pd

from investment_bot.sweep.pairs import PairParams, pair_returns


def _pair(n: int = 1200, seed: int = 2):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2013-01-01", periods=n)
    common = np.cumsum(rng.normal(0, 0.01, n))
    gap = np.zeros(n)
    for i in range(1, n):   # a gap that wanders off and comes back
        gap[i] = 0.6 * gap[i - 1] + rng.normal(0, 0.006)
    a = pd.Series(100 * np.exp(common + gap), index=days)
    b = pd.Series(50 * np.exp(common), index=days)
    zero = pd.Series(0.0, index=days)
    return a, b, zero


def test_it_profits_from_a_gap_that_closes_and_trades_add_up():
    a, b, zero = _pair()
    daily, tr = pair_returns(a, b, zero, zero, PairParams(cost_bp=0, finance_pa=0), "A/B")
    assert len(tr) > 10 and daily.sum() > 0
    assert abs(sum(x["r"] for x in tr) * 0.01 - daily.sum()) < 0.02   # an open trade at the end may be left out


def test_no_peeking_at_the_future():
    a, b, zero = _pair()
    full, _ = pair_returns(a, b, zero, zero, PairParams(), "A/B")
    for k in (400, 800, 1100):
        part, _ = pair_returns(a.iloc[:k], b.iloc[:k], zero.iloc[:k], zero.iloc[:k], PairParams(), "A/B")
        pd.testing.assert_series_equal(part, full.iloc[:k], check_names=False)
