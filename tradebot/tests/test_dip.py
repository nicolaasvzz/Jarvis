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
