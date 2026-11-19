"""Part 4, family 3: buy the dip in indices."""
import numpy as np
import pandas as pd

from investment_bot.sweep.dip import DipParams, rsi, run


def _daily(n: int = 1500, seed: int = 9) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(0.0004 + rng.normal(0, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.002, n))
    high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.004, n)))
    low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.004, n)))
    t = 1344211200 + np.arange(n) * 86400
    return pd.DataFrame({"time": t, "open": open_, "high": high, "low": low, "close": close,
                         "tick_volume": 1, "spread": 20})


def test_rsi_uses_only_the_past():
    c = _daily()["close"].to_numpy()
    full = rsi(c, 2)
    assert np.allclose(rsi(c[:700], 2), full[:700])
    assert ((full >= 0) & (full <= 100)).all()


def test_it_buys_dips_only_and_paths_add_up():
    tr = run("US500", _daily(), DipParams(), 0.01)
    assert len(tr) > 10
    assert all(x["side"] == 1 for x in tr)
    for x in tr:
        assert abs(sum(d for _, d in x["path"]) - x["r"]) < 1e-9
        assert x["exit_time"] - x["fill_time"] <= 12 * 86400


def test_no_peeking_at_the_future():
    df = _daily()
    full = run("US500", df, DipParams(), 0.01)
    for k in (500, 901, 1333):
        part = run("US500", df.iloc[:k].reset_index(drop=True), DipParams(), 0.01)
        last = int(df["time"].iat[k - 1])
        assert part == [x for x in full if x["exit_time"] <= last]


def test_both_sides_mirror_and_do_not_peek():
    df = _daily(seed=11)
    both = run("EURUSD", df, DipParams(sides=0, ma_n=0), 0.00001)
    assert {x["side"] for x in both} == {1, -1}
    for x in both:
        assert abs(sum(d for _, d in x["path"]) - x["r"]) < 1e-9
    longs = run("EURUSD", df, DipParams(sides=1, ma_n=0), 0.00001)
    assert len(both) >= len(longs)
    part = run("EURUSD", df.iloc[:900].reset_index(drop=True), DipParams(sides=0, ma_n=0), 0.00001)
    last = int(df["time"].iat[899])
    assert part == [x for x in both if x["exit_time"] <= last]


def test_vol_spike_buys_after_a_volatility_jump_and_does_not_peek():
    rng = np.random.default_rng(21)
    n = 1200
    vol = np.where((np.arange(n) % 300) > 280, 0.04, 0.008)     # a burst of volatility every 300 days
    close = 100 * np.exp(np.cumsum(rng.normal(0, 1, n) * vol))
    open_ = np.r_[close[0], close[:-1]]
    df = pd.DataFrame({"time": 1344211200 + np.arange(n) * 86400, "open": open_,
                       "high": np.maximum(open_, close) * 1.002, "low": np.minimum(open_, close) * 0.998,
                       "close": close, "tick_volume": 1, "spread": 0})
    p = DipParams(entry="volspike", ma_n=0, exit_ma=0, max_days=20, stop_atr=10)
    tr = run("US500", df, p, 0.01)
    phase = [((x["fill_time"] - 1344211200) // 86400) % 300 for x in tr]
    near = sum(d > 280 or d < 15 for d in phase)             # during a burst or just after it
    assert tr and near > len(tr) / 2                         # mostly at the bursts (a 10-day estimate is noisy)
    part = run("US500", df.iloc[:900].reset_index(drop=True), p, 0.01)
    assert part == [x for x in tr if x["exit_time"] <= int(df["time"].iat[899])]


def test_calm_filter_only_trades_when_volatility_is_low():
    df = _daily(n=1500, seed=9)
    calm = run("US500", df, DipParams(calm=True), 0.01)
    every = run("US500", df, DipParams(), 0.01)
    assert len(calm) < len(every)
    part = run("US500", df.iloc[:1000].reset_index(drop=True), DipParams(calm=True), 0.01)
    assert part == [x for x in calm if x["exit_time"] <= int(df["time"].iat[999])]
