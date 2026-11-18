"""Part 4, family 7: breakout after a quiet day."""
import numpy as np
import pandas as pd

from investment_bot.sweep.nr7 import NRParams, run


def _daily(n: int = 1500, seed: int = 13) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    vol = np.exp(rng.normal(0, 0.5, n)) * 0.01
    close = 100 * np.exp(np.cumsum(rng.normal(0, 1, n) * vol))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + vol * 0.3)
    low = np.minimum(open_, close) * (1 - vol * 0.3)
    t = 946684800 + np.arange(n) * 86400
    return pd.DataFrame({"time": t, "open": open_, "high": high, "low": low, "close": close,
                         "tick_volume": 1, "spread": 0})


def test_trades_follow_a_quiet_day_and_lose_at_most_about_1r():
    df = _daily()
    tr = run("TEST", df, NRParams(), 0.01)
    assert len(tr) > 30
    assert {x["side"] for x in tr} == {1, -1}
    for x in tr:
        assert x["r"] > -1.3
        assert x["exit_time"] - x["fill_time"] <= 3 * 86400


def test_no_peeking_at_the_future():
    df = _daily()
    full = run("TEST", df, NRParams(), 0.01)
    for k in (400, 900, 1300):
        part = run("TEST", df.iloc[:k].reset_index(drop=True), NRParams(), 0.01)
        last = int(df["time"].iat[k - 1])
        assert part == [x for x in full if x["exit_time"] < last]


def test_hourly_candles_decide_which_side_came_first():
    from investment_bot.sweep.nr7 import first_touch
    day = 946684800
    h1 = pd.DataFrame({"time": day + np.arange(24) * 3600, "high": [100.5] * 24, "low": [99.5] * 24})
    h1.loc[3, "high"] = 101.2           # the high is broken first (03:00) ...
    h1.loc[10, "low"] = 98.8            # ... and the low later the same day
    assert first_touch(h1, day, 101.0, 99.0) == (1, True)
    h1.loc[10, "low"] = 99.5            # no later low: filled long, not stopped that day
    assert first_touch(h1, day, 101.0, 99.0) == (1, False)
    h1.loc[3, "low"] = 98.8             # one hourly candle touches both: can't tell
    assert first_touch(h1, day, 101.0, 99.0) is None
    assert first_touch(None, day, 101.0, 99.0) is None
