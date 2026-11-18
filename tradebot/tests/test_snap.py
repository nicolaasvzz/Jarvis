"""Part 4, family 6: intraday snap-backs."""
import numpy as np
import pandas as pd

from investment_bot.sweep.snap import SnapParams, trades


def _hourly(days: int = 200, seed: int = 8) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2016-01-04", periods=days * 24, freq="h")
    idx = idx[idx.dayofweek < 5]
    r = rng.normal(0, 0.002, len(idx))
    r[1:] -= 0.4 * np.r_[r[:-1]][: len(r) - 1] * (r[:-1] < -0.004)   # big drops partly bounce back
    close = 1000 * np.exp(np.cumsum(r))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"time": (idx - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1),
                         "open": open_, "high": np.maximum(open_, close), "low": np.minimum(open_, close),
                         "close": close, "tick_volume": 1, "spread": 0})


def test_it_buys_after_big_drops_inside_the_session_only():
    h = _hourly()
    tr = trades("US500", h, SnapParams(cost_bp=0), 0.01)
    assert len(tr) > 10
    for x in tr:
        signal_hour = pd.to_datetime(x["fill_time"] - 3600, unit="s").hour
        assert 16 <= signal_hour <= 21
        assert x["exit_time"] - x["fill_time"] == 3 * 3600
    assert np.mean([x["ret"] for x in tr]) > 0     # the planted bounce is found


def test_no_peeking_at_the_future():
    h = _hourly()
    full = trades("US500", h, SnapParams(), 0.01)
    for k in (1500, 2600, 3300):
        part = trades("US500", h.iloc[:k].reset_index(drop=True), SnapParams(), 0.01)
        last = int(h["time"].iat[k - 1])
        assert part == [x for x in full if x["exit_time"] < last]
