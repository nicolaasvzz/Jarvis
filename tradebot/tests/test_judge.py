"""Part 6: the shared judge must be exact, since every new strategy is scored by it."""
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from investment_bot.sweep import judge
from investment_bot.sweep.bench import group_of


def _daily(opens, start="2010-01-04", spread=0):
    t = pd.date_range(start, periods=len(opens), freq="D")
    o = np.asarray(opens, dtype=float)
    return pd.DataFrame({"time": (t - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1),
                         "open": o, "high": o * 1.01, "low": o * 0.99, "close": o, "tick_volume": 1,
                         "spread": spread})


def test_fill_at_next_open_and_open_to_open_pnl():
    df = _daily([100, 101, 103, 102, 104, 105])
    pos = pd.Series([0, 1, 1, 1, 1, 1], index=df["time"], dtype=float)     # decided long at bar 1's close
    p = judge.market_pnl("US500", df, pos)
    assert list(p["held"]) == [0, 0, 1, 1, 1, 1]                           # filled at bar 2's open
    expect = [0, 0, 102 / 103 - 1, 104 / 102 - 1, 105 / 104 - 1, 0]       # open to next open
    assert np.allclose(p["gross"], expect)
    assert np.isclose(p["cost"].sum(), 1e-4)                               # one entry at 1bp (no spread)
    rate = 0.002 + judge.CFD_MARKUP                                        # 2010 US rate + markup, a long pays
    assert np.allclose(p["fin"].to_numpy()[2:5], -rate / 365)
    assert np.allclose(p["net"], p["gross"] - p["cost"] + p["fin"])


def test_a_position_decided_later_never_changes_earlier_pnl():
    df = _daily([100, 101, 99, 103, 102, 104, 101, 100])
    a = pd.Series([0, 1, 1, 1, 1, 1, 1, 1], index=df["time"], dtype=float)
    b = a.copy()
    b.iloc[4:] = -1                                                        # differs from bar 4's close on
    pa, pb = judge.market_pnl("US500", df, a), judge.market_pnl("US500", df, b)
    assert np.allclose(pa["net"].to_numpy()[:5], pb["net"].to_numpy()[:5])
    assert not np.allclose(pa["net"].to_numpy()[5:7], pb["net"].to_numpy()[5:7])


def test_fx_pairs_earn_or_pay_their_interest_gap():
    t = np.array([int(datetime(2007, 6, 1, tzinfo=timezone.utc).timestamp())])
    long_ = judge.financing("AUDJPY", np.array([1.0]), t, np.array([1]))[0]
    short = judge.financing("AUDJPY", np.array([-1.0]), t, np.array([1]))[0]
    gap = 0.065 - 0.005
    assert math.isclose(long_, (gap - judge.FX_MARKUP) / 365)
    assert math.isclose(short, (-gap - judge.FX_MARKUP) / 365)
    assert judge.is_fx("AUDJPY") and not judge.is_fx("XAUUSD") and not judge.is_fx("US500")


def test_hourly_bars_pay_financing_only_when_a_night_passes():
    t = pd.date_range("2015-03-02 21:00", periods=5, freq="h")              # crosses midnight once
    o = np.full(5, 100.0)
    df = pd.DataFrame({"time": (t - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1),
                       "open": o, "high": o, "low": o, "close": o, "tick_volume": 1, "spread": 0})
    p = judge.market_pnl("US500", df, pd.Series(1.0, index=df["time"]))
    assert (p["fin"] != 0).sum() == 1


def test_positions_indexed_by_dates_are_refused():
    df = _daily([100, 101, 102, 103])
    wrong = pd.Series(1.0, index=pd.to_datetime(df["time"], unit="s"))
    with pytest.raises(ValueError):
        judge.market_pnl("US500", df, wrong)


def _honest(fr, params):
    return {s: (df["close"] > df["close"].rolling(3).mean()).astype(float).set_axis(df["time"])
            for s, df in fr.items()}


def _peeking(fr, params):
    return {s: (df["close"].shift(-1) > df["close"]).astype(float).set_axis(df["time"])
            for s, df in fr.items()}


def test_no_peek_check_catches_a_strategy_that_uses_tomorrow():
    rng = np.random.default_rng(1)
    fr = {"US500": _daily(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))))}
    judge.assert_no_peek(_honest, {}, fr)
    with pytest.raises(AssertionError):
        judge.assert_no_peek(_peeking, {}, fr)


def test_halves_are_disjoint_complete_and_balanced():
    a, b = set(judge.HALF_A), set(judge.HALF_B)
    assert not a & b and a | b == set(judge.MARKETS)
    for g in ("forex", "metals", "indices"):
        na = sum(group_of(m) == g for m in a)
        nb = sum(group_of(m) == g for m in b)
        assert abs(na - nb) <= 1


def test_evaluate_refuses_frames_that_reach_the_locked_years():
    df = _daily(np.linspace(100, 110, 30), start="2018-12-20")
    with pytest.raises(ValueError):
        judge.evaluate(_honest, {}, fr={"US500": df})


def test_locked_look_happens_once_per_name(tmp_path, monkeypatch):
    rng = np.random.default_rng(2)
    df = _daily(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, 9000))), start="2002-01-01")
    monkeypatch.setattr(judge, "frames", lambda symbols=None, tf="D1", dev=True, cache=None: {"US500": df})
    looks = tmp_path / "looks.json"
    res = judge.locked_look("test v1", _honest, {}, looks_file=looks)
    assert set(res["passed"]) == {"yearly_18pct", "drop_25pct", "t_1.65", "quarters_3of4", "pf_1.2", "trades_30"}
    with pytest.raises(RuntimeError):
        judge.locked_look("test v1", _honest, {}, looks_file=looks)


def test_scale_for_cap_keeps_the_worst_drop_at_the_cap():
    rng = np.random.default_rng(3)
    r = rng.normal(0.0003, 0.01, 2000)
    k = judge.scale_for_cap(r)
    assert judge.curve(r, k)[1] <= 0.25 + 1e-9 < judge.curve(r, k * 1.05)[1]
