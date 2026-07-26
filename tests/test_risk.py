import pandas as pd
import pytest

from investment_bot.portfolio import Portfolio
from investment_bot.risk import RiskConfig, RiskEngine


@pytest.fixture
def engine() -> RiskEngine:
    return RiskEngine(RiskConfig())


def test_sizing_respects_risk_budget(engine, ohlcv):
    portfolio = Portfolio(starting_cash=100_000)
    price = float(ohlcv["close"].iloc[-1])
    qty = engine.size_position(1, 1.0, price, ohlcv, portfolio)
    assert qty > 0
    atr = engine.atr(ohlcv)
    risked = qty * atr * engine.config.atr_stop_multiple
    assert risked <= 100_000 * engine.config.risk_per_trade * 1.01
    assert qty * price <= 100_000 * engine.config.max_position_weight * 1.01


def test_conviction_scales_size(engine, ohlcv):
    portfolio = Portfolio(starting_cash=100_000)
    price = float(ohlcv["close"].iloc[-1])
    full = engine.size_position(1, 1.0, price, ohlcv, portfolio)
    half = engine.size_position(1, 0.5, price, ohlcv, portfolio)
    assert 0 < half <= full


def test_no_short_when_disallowed(ohlcv):
    engine = RiskEngine(RiskConfig(allow_short=False))
    portfolio = Portfolio(starting_cash=100_000)
    price = float(ohlcv["close"].iloc[-1])
    assert engine.size_position(-1, 1.0, price, ohlcv, portfolio) == 0


def test_exposure_cap_blocks_new_entries(engine, ohlcv):
    portfolio = Portfolio(starting_cash=100_000)
    portfolio.apply_fill("X", 1000, 100.0, 0.0, pd.Timestamp("2026-01-05"))
    portfolio.mark({"X": 100.0}, pd.Timestamp("2026-01-05"))  # 100% gross
    price = float(ohlcv["close"].iloc[-1])
    assert engine.size_position(1, 1.0, price, ohlcv, portfolio) == 0


def test_circuit_breaker_latches(engine):
    assert not engine.check_circuit_breaker(100_000)
    assert not engine.check_circuit_breaker(80_000)  # -20%, under the 25% limit
    assert engine.check_circuit_breaker(74_000)  # -26% -> trips
    assert engine.check_circuit_breaker(150_000)  # stays halted even on recovery


def test_trailing_stop_never_loosens(engine):
    stop = engine.trail_stop(1, current_stop=95.0, close=100.0, atr_value=2.0)
    assert stop == 95.0  # candidate 94 would loosen -> keep 95
    stop = engine.trail_stop(1, current_stop=95.0, close=110.0, atr_value=2.0)
    assert stop == pytest.approx(104.0)
    short_stop = engine.trail_stop(-1, current_stop=105.0, close=95.0, atr_value=2.0)
    assert short_stop == pytest.approx(101.0)


def test_stop_hit_directions(engine):
    assert engine.stop_hit(1, stop=95.0, low=94.0, high=100.0)
    assert not engine.stop_hit(1, stop=95.0, low=96.0, high=100.0)
    assert engine.stop_hit(-1, stop=105.0, low=100.0, high=106.0)


def test_invalid_configs_rejected():
    with pytest.raises(ValueError):
        RiskConfig(risk_per_trade=0.5)
    with pytest.raises(ValueError):
        RiskConfig(max_drawdown=1.5)
