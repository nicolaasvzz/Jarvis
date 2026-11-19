"""Part 4, family 8: first hour predicts the last hour."""
import numpy as np
import pandas as pd

from investment_bot.sweep.lasthour import LastHourParams, trades


def _hourly(days: int = 120, seed: int = 6, follow: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2016-01-04", periods=days * 24, freq="h")
    idx = idx[idx.dayofweek < 5]
    r = rng.normal(0, 0.002, len(idx))
    hours = idx.hour.to_numpy()
    for i in np.flatnonzero(hours == 22):      # plant: the last hour follows the first hour
        j = i - 6
        if j >= 0 and hours[j] == 16:
            r[i] += follow * np.sign(r[j]) * 0.002
    close = 1000 * np.exp(np.cumsum(r))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"time": (idx - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1),
                         "open": open_, "high": np.maximum(open_, close), "low": np.minimum(open_, close),
                         "close": close, "tick_volume": 1, "spread": 0})


def test_one_trade_a_day_on_the_last_hour_finds_a_planted_effect():
    h = _hourly(follow=2.0)
    tr = trades("US500", h, LastHourParams(cost_bp=0), 0.01)
    assert len(tr) > 60
    for x in tr:
        assert pd.to_datetime(x["fill_time"], unit="s").hour == 22 and x["exit_time"] - x["fill_time"] == 3600
    assert np.mean([x["ret"] for x in tr]) > 0


def test_no_peeking_at_the_future():
    h = _hourly()
    full = trades("US500", h, LastHourParams(), 0.01)
    for k in (700, 1500, 2000):
        part = trades("US500", h.iloc[:k].reset_index(drop=True), LastHourParams(), 0.01)
        last = int(h["time"].iat[k - 1])
        assert part == [x for x in full if x["exit_time"] <= last]
