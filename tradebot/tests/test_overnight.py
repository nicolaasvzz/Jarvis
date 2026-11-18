"""Part 4, family 4: the overnight drift."""
import numpy as np
import pandas as pd

from investment_bot.sweep.overnight import NightParams, nights


def _hourly(days: int = 120, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2013-01-07", periods=days * 24, freq="h")
    idx = idx[idx.dayofweek < 5]
    close = 1000 * np.exp(np.cumsum(rng.normal(0, 0.001, len(idx))))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"time": (idx - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1),
                         "open": open_, "high": np.maximum(open_, close) * 1.0005,
                         "low": np.minimum(open_, close) * 0.9995, "close": close,
                         "tick_volume": 1, "spread": 0})


def test_each_night_runs_from_the_close_to_the_next_open():
    h = _hourly()
    tr = nights("US500", h, NightParams(cost_bp=0, finance_pa=0), 0.01)
    t = pd.to_datetime(h["time"], unit="s")
    assert len(tr) > 50
    for x in tr:
        a, b = pd.to_datetime(x["fill_time"], unit="s"), pd.to_datetime(x["exit_time"], unit="s")
        assert a.hour == 22 and b.hour == 16 and 0 < (b - a).days + 1 <= 4
        buy = h["close"][t == a].iat[0]
        sell = h["open"][t == b].iat[0]
        assert abs(x["ret"] - (sell / buy - 1)) < 1e-12


def test_no_peeking_at_the_future():
    h = _hourly()
    full = nights("US500", h, NightParams(), 0.01)
    for k in (700, 1300, 1900):
        part = nights("US500", h.iloc[:k].reset_index(drop=True), NightParams(), 0.01)
        last = int(h["time"].iat[k - 1])
        assert part == [x for x in full if x["exit_time"] <= last]


def test_costs_and_weekends_cost_more():
    h = _hourly()
    free = nights("US500", h, NightParams(cost_bp=0, finance_pa=0), 0.01)
    paid = nights("US500", h, NightParams(cost_bp=1, finance_pa=0.02), 0.01)
    gaps = [b["ret"] - a["ret"] for a, b in zip(free, paid)]
    assert all(g < 0 for g in gaps)
    assert min(gaps) < max(gaps)   # weekend nights pay three nights' financing
