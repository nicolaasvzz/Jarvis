"""The sweep setup: no peeking at the future, sane fills, honest scoring."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from investment_bot.sweep import engine as E
from investment_bot.sweep.prove import simulate, stats


def _walk(n: int, seed: int, step: int = 900) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # trending stretches and turns, so there are swings, zones and sweeps
    drift = np.repeat(rng.normal(0, 0.0004, n // 200 + 1), 200)[:n]
    ret = drift + rng.normal(0, 0.0015, n)
    close = 100 * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[100.0], close[:-1]])
    wick = np.abs(rng.normal(0, 0.001, (2, n))) * close
    return pd.DataFrame({
        "time": 1_600_000_000 + np.arange(n, dtype=np.int64) * step,
        "open": open_, "high": np.maximum(open_, close) + wick[0],
        "low": np.minimum(open_, close) - wick[1], "close": close,
        "tick_volume": 1, "spread": 0,
    })


def _resample(df: pd.DataFrame, seconds: int) -> pd.DataFrame:
    g = (df["time"] // seconds) * seconds
    out = df.groupby(g).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                            close=("close", "last"), tick_volume=("tick_volume", "sum"),
                            spread=("spread", "max")).reset_index()
    return out.rename(columns={"index": "time"}).rename(columns={g.name: "time"})


@pytest.fixture(scope="module")
def charts():
    m15 = _walk(30_000, seed=7)
    return _resample(m15, 14400), _resample(m15, 3600), m15


def _engine(charts, cut: int | None = None) -> E.Engine:
    h4, h1, m15 = charts
    pf = E.PROFILES["intraday"]
    if cut is not None:
        t = int(m15["time"].iat[cut - 1]) + 900          # when bar cut-1 closed
        m15 = m15.iloc[:cut]
        h1 = h1[h1["time"] + 3600 <= t]                  # only candles closed by then
        h4 = h4[h4["time"] + 14400 <= t]
    return E.Engine("TEST", pf, h4, h1, m15, E.DEFAULT, point=0.01)


def test_it_finds_setups_and_trades(charts):
    eng = _engine(charts)
    trades = eng.run()
    assert len(eng.orders) > 10
    assert trades, "the walk should produce some filled trades"
    for t in trades:
        assert t.side in (1, -1)
        assert (t.target - t.entry) * t.side > 0 and (t.entry - t.stop) * t.side > 0
        assert t.fill_time >= t.signal_time and t.exit_time >= t.fill_time
        assert t.r >= -1.2, "a loss is about one risk unit, never much more"


def test_no_peeking_at_the_future(charts):
    """Cutting history off at any point leaves every earlier decision unchanged."""
    full = _engine(charts)
    full.run()
    for cut in (8_000, 15_000, 22_500):
        part = _engine(charts, cut)
        part.run()
        before = [(o.signal_i, o.side, round(o.entry, 9), round(o.stop, 9), round(o.target, 9))
                  for o in full.orders if o.signal_i < cut]
        got = [(o.signal_i, o.side, round(o.entry, 9), round(o.stop, 9), round(o.target, 9))
               for o in part.orders]
        assert got == before


def test_targets_pay_at_least_min_rr(charts):
    eng = _engine(charts)
    eng.run()
    for o in eng.orders:
        rr = (o.target - o.entry) * o.side / ((o.entry - o.stop) * o.side)
        assert rr >= E.DEFAULT.min_rr - 1e-9 and rr <= E.DEFAULT.max_rr + 1e-9


def test_stop_counts_before_target_in_the_same_bar():
    pf = E.Profile("t", "H4", "H1", "M15", arm_bars=10, entry_valid=5, max_hold=50)
    eng = E.Engine.__new__(E.Engine)
    eng.symbol, eng.pf, eng.p, eng.point = "X", pf, E.DEFAULT, 0.0
    eng.trades, eng.pending = [], None
    eng.l = type("L", (), {"df": pd.DataFrame({"close": [100.0] * 5, "spread": [0] * 5}),
                           "close_t": np.arange(5) * 900})()
    od = E.Order(1, 100.0, 99.0, 103.0, signal_i=0, expires_i=4, zone=(0, 0))
    eng.position = {"order": od, "side": 1, "entry": 100.0, "stop": 99.0, "target": 103.0,
                    "risk": 1.0, "fill_i": 1, "fill_t": 900, "half": False, "banked": 0.0}
    eng._manage(2, 100.0, 104.0, 98.5, 101.0, 1800)   # both touched: assume the stop
    assert eng.trades[0].reason == "stop" and eng.trades[0].r < -1


def test_partial_then_breakeven():
    pf = E.Profile("t", "H4", "H1", "M15", arm_bars=10, entry_valid=5, max_hold=50)
    eng = E.Engine.__new__(E.Engine)
    eng.symbol, eng.pf, eng.p, eng.point = "X", pf, E.Params(cost_bp=0.0), 0.0
    eng.trades, eng.pending = [], None
    eng.l = type("L", (), {"df": pd.DataFrame({"close": [100.0] * 5, "spread": [0] * 5}),
                           "close_t": np.arange(5) * 900})()
    od = E.Order(1, 100.0, 99.0, 105.0, signal_i=0, expires_i=4, zone=(0, 0))
    eng.position = {"order": od, "side": 1, "entry": 100.0, "stop": 99.0, "target": 105.0,
                    "risk": 1.0, "fill_i": 1, "fill_t": 900, "half": False, "banked": 0.0}
    eng._manage(2, 100.5, 102.2, 100.1, 102.0, 1800)  # 2R reached: half off, stop to entry
    assert eng.position["half"] and eng.position["stop"] == 100.0
    eng._manage(3, 101.0, 101.0, 99.9, 100.0, 2700)   # back to entry: the rest at 0R
    assert eng.trades[0].reason == "breakeven" and eng.trades[0].r == pytest.approx(1.0)


def test_scoring():
    trades = [{"fill_time": 86400 * d, "exit_time": 86400 * d + 3600, "r": r}
              for d, r in enumerate([3, -1, -1, 3, -1, 2, -1, -1, 3, -1])]
    s = stats(trades, 0, 86400 * 10)
    assert s["trades"] == 10 and s["sum_r"] == 5.0 and s["win_pct"] == 40.0
    ret, dd = simulate(trades, risk=0.01)
    assert 0.04 < ret < 0.06 and 0 < dd < 0.03


def test_jarvis_status_shows_the_sweep_setup(tmp_path, monkeypatch):
    import json
    from datetime import datetime

    from investment_bot.jarvis_status import _sweep

    monkeypatch.chdir(tmp_path)
    (tmp_path / "sweep.json").write_text(json.dumps({"profiles": {"swing": {
        "words": "daily target", "proven": False, "why_not": ["test t=1.0 is under the luck bar 2.4"],
        "best": {"label": "rr>=3", "test": {"trades": 50, "win_pct": 30.0, "avg_r": 0.1, "t": 1.0,
                                            "per_week": 1.2}},
        "video_rules": {"test": {"avg_r": 0.05}}}}}))
    (tmp_path / "sweep_trader.json").write_text(json.dumps({
        "account": {"server": "MetaQuotes-Demo", "demo": True, "currency": "USD", "leverage": 100,
                    "equity": 100.0},
        "algo_trading": False, "positions": {}, "orders": {}, "closed": [],
        "log": [{"at": "2026-11-17T19:00:00", "symbol": "-", "what": "started"}],
        "equity": [["2026-11-17T19:00", 100.0], ["2026-11-17T19:01", 100.0]]}))
    out = _sweep(datetime.now())
    assert out["MT5 equity"] == 100.0
    assert out["MT5 algo trading"].startswith("OFF")
    assert out["Sweep swing"].startswith("not proven")
    assert out["Sweep backtest"][0]["Test trades"] == 50
    assert len(out["MT5 equity curve"]) == 2


def test_unproven_demo_trades_the_tuned_version(tmp_path, monkeypatch):
    """Forward-testing an unproven profile uses the workbench's version, not the defaults."""
    import json
    from types import SimpleNamespace

    from investment_bot.sweep import trader as tr
    from investment_bot.sweep.engine import DEFAULT

    monkeypatch.chdir(tmp_path)
    (tmp_path / "sweep_champions.json").write_text(
        json.dumps({"swing": {"break_body_atr": 1.2, "htf_sweep_bars": 20}}), encoding="utf-8")
    fake = SimpleNamespace(ACCOUNT_TRADE_MODE_DEMO=0,
                           account_info=lambda: SimpleNamespace(trade_mode=0))
    monkeypatch.setattr(tr.data, "connect", lambda: fake)
    t = tr.SweepTrader(tr.TraderConfig(also_unproven=True, profiles=("swing", "intraday")),
                       say=lambda _: None)
    plans = t.plans()
    assert plans["swing"].break_body_atr == 1.2 and plans["swing"].htf_sweep_bars == 20
    assert plans["intraday"] == DEFAULT   # no tuned version saved: the defaults

    fake.account_info = lambda: SimpleNamespace(trade_mode=2)   # a real-money account
    try:
        t.plans()
        raise AssertionError("unproven profiles must not trade a real account")
    except RuntimeError:
        pass
