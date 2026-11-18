"""Part 4, family 1: daily trend following."""
import numpy as np
import pandas as pd

from investment_bot.sweep.trend import TrendParams, at_cap, curve, daily, run


def _daily(n: int = 1500, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.choice([-0.002, 0.002], n // 100 + 1), 100)[:n]
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.008, n)))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.002, n))
    high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.004, n)))
    low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.004, n)))
    t = 946684800 + np.arange(n) * 86400
    return pd.DataFrame({"time": t, "open": open_, "high": high, "low": low, "close": close,
                         "tick_volume": 1, "spread": 2})


def test_it_trades_and_each_path_adds_up_to_the_result():
    tr = run("TEST", _daily(), TrendParams(), 0.01)
    assert len(tr) > 10
    for x in tr:
        assert abs(sum(d for _, d in x["path"]) - x["r"]) < 1e-9
        assert x["fill_time"] <= x["exit_time"]
        assert x["r"] > -1.6   # a stop loss is about 1R plus costs (gaps aside)


def test_no_peeking_at_the_future():
    df = _daily()
    full = run("TEST", df, TrendParams(), 0.01)
    for k in (400, 777, 1200):
        part = run("TEST", df.iloc[:k].reset_index(drop=True), TrendParams(), 0.01)
        last = int(df["time"].iat[k - 1])
        assert part == [x for x in full if x["exit_time"] <= last]


def test_costs_only_make_it_worse():
    df = _daily()
    cheap = sum(x["r"] for x in run("TEST", df, TrendParams(cost_bp=0, finance_pa=0), 0.0))
    dear = sum(x["r"] for x in run("TEST", df, TrendParams(cost_bp=5, finance_pa=0.05), 0.01))
    assert dear < cheap


def test_risk_is_sized_to_the_drop_cap():
    tr = run("TEST", _daily(), TrendParams(), 0.01)
    days = daily(tr, 0, 2**40)
    risk, yearly, drop = at_cap(days, 4.0, cap=0.25)
    assert drop <= 0.25 + 1e-9
    assert curve(days, risk * 1.5, 4.0)[1] > drop or risk >= 0.1
