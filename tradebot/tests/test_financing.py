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
