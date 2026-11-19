"""Rate-based CFD financing."""
import numpy as np
import pandas as pd

from investment_bot.sweep.financing import charge


def test_longs_pay_more_and_shorts_less_when_rates_are_high():
    days = pd.DatetimeIndex(["2021-06-01", "2024-06-01"])
    long_ = charge(np.array([[1.0], [1.0]]), days)[:, 0]
    short = charge(np.array([[-1.0], [-1.0]]), days)[:, 0]
    assert long_[1] > long_[0] + 0.04          # 2024 (about 5%) costs far more than 2021 (about 0%)
    assert short[1] < 0 < short[0]             # in 2024 a short is paid; in 2021 it pays the markup
    assert charge(np.array([[0.0]]), days[:1])[0, 0] == 0


def test_carry_gap_and_mode():
    from investment_bot.sweep.financing import carry
    from investment_bot.sweep.tsmom import MomParams, returns, weights
    days = pd.bdate_range("2007-01-01", periods=600)
    assert carry("AUDJPY", days)[0] > 0.05          # 2007: about 6.5% against 0.5%
    assert np.isnan(carry("XAUUSD", days)).all()     # not a currency pair
    rng = np.random.default_rng(1)
    c = pd.DataFrame({"AUDJPY": 90 * np.exp(np.cumsum(rng.normal(0, 0.006, 600))),
                      "EURCHF": 1.6 * np.exp(np.cumsum(rng.normal(0, 0.003, 600)))}, index=days)
    w = weights(c, MomParams(mode="carry"))
    assert (w["AUDJPY"].dropna() > 0).all()          # long the high-rate currency
    sp = pd.DataFrame(0.0, index=days, columns=c.columns)
    flat = pd.DataFrame(90.0, index=days, columns=["AUDJPY"])   # price never moves: only carry is earned
    r, _ = returns(flat, sp[["AUDJPY"]], MomParams(mode="carry", cost_bp=0, finance_pa=0))
    assert r.sum() > 0
