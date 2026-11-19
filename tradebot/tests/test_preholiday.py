"""Part 4, family 9: the pre-holiday effect."""
import numpy as np
import pandas as pd

from investment_bot.sweep.preholiday import HolidayParams, pre_holiday_days, trades


def test_calendar_knows_the_days_before_us_holidays():
    days = pre_holiday_days("2024-01-01", "2024-12-31")
    for d in ("2024-07-03", "2024-11-27", "2024-12-24", "2024-03-28"):   # July 4, Thanksgiving, Christmas, Good Friday
        assert pd.Timestamp(d) in days
    assert pd.Timestamp("2024-10-11") not in days                       # Columbus Day: markets open
    assert 9 <= len(days) <= 11


def test_one_day_trades_on_those_days_only():
    t = pd.bdate_range("2016-01-04", "2018-12-31")
    rng = np.random.default_rng(2)
    close = 2000 * np.exp(np.cumsum(rng.normal(0, 0.01, len(t))))
    d1 = pd.DataFrame({"time": (t - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1),
                       "open": close, "high": close, "low": close, "close": close, "spread": 0})
    tr = trades("US500", d1, HolidayParams(cost_bp=0, finance_pa=0), 0.01)
    days = pre_holiday_days()
    assert 25 <= len(tr) <= 33
    for x in tr:
        assert pd.to_datetime(x["exit_time"], unit="s").normalize() in days
        i = int(np.flatnonzero(d1["time"].to_numpy() == x["exit_time"])[0])
        assert abs(x["ret"] - (close[i] / close[i - 1] - 1)) < 1e-12
