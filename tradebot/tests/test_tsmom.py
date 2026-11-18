"""Part 4, family 2: time-series momentum."""
import numpy as np
import pandas as pd

from investment_bot.sweep.tsmom import MomParams, market_months, returns, weights


def _panel(n: int = 900, seed: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2001-01-01", periods=n)
    c = pd.DataFrame({s: 100 * np.exp(np.cumsum(rng.normal(d, 0.01, n)))
                      for s, d in (("AAAUSD", 0.001), ("BBBUSD", -0.001), ("US500", 0.0))}, index=days)
    c.iloc[:200, 2] = np.nan     # one market starts later
    sp = pd.DataFrame(0.0001, index=days, columns=c.columns)
    return c, sp


def test_positions_change_only_at_month_ends_and_follow_the_trend():
    c, _ = _panel()
    w = weights(c, MomParams())
    a = w["AAAUSD"].dropna()
    months = a.index.to_series().dt.to_period("M")
    month_last = (months != months.shift(-1)).to_numpy()
    changed = (a.diff().fillna(0) != 0).to_numpy()
    assert changed.any() and not (changed & ~month_last).any()
    assert (w["AAAUSD"].dropna() > 0).mean() > 0.6     # the rising market is mostly held long
    assert (w["BBBUSD"].dropna() < 0).mean() > 0.6     # the falling one mostly short


def test_no_peeking_at_the_future():
    c, sp = _panel()
    full, _ = returns(c, sp, MomParams(lookback=120))
    for k in (400, 611, 850):
        part, _ = returns(c.iloc[:k], sp.iloc[:k], MomParams(lookback=120))
        pd.testing.assert_series_equal(part, full.iloc[:k], check_names=False)


def test_costs_only_make_it_worse():
    c, sp = _panel()
    cheap, _ = returns(c, sp * 0, MomParams(lookback=120, cost_bp=0, finance_pa=0))
    dear, _ = returns(c, sp, MomParams(lookback=120, cost_bp=5, finance_pa=0.05))
    assert dear.sum() < cheap.sum()


def test_market_months_add_up():
    c, sp = _panel()
    p = MomParams(lookback=120)
    _, per = returns(c, sp, p)
    tr = market_months(per, p)
    unit = p.target_vol / np.sqrt(12)
    assert abs(sum(x["r"] for x in tr) * unit - per.sum().sum()) < 1e-9
