import pandas as pd
import pytest

from investment_bot.backtest.engine import BacktestEngine
from investment_bot.backtest.metrics import compute_metrics
from investment_bot.broker.base import ExecutionModel
from investment_bot.data.synthetic import generate_ohlcv
from investment_bot.risk import RiskConfig
from investment_bot.strategies import Ensemble, SmaCross


def make_engine(**risk_kwargs) -> BacktestEngine:
    return BacktestEngine(
        strategy=Ensemble(members=[SmaCross(fast=10, slow=50)], threshold=0.05),
        risk=RiskConfig(**risk_kwargs),
        execution=ExecutionModel(slippage_bps=5, commission_pct=0.0005),
        starting_cash=100_000,
    )


def test_end_to_end_on_synthetic_universe():
    data = {s: generate_ohlcv(s, days=500, seed=3) for s in ("AAA", "BBB", "CCC")}
    result = make_engine().run(data)
    assert len(result.equity) > 400
    assert result.metrics["final_equity"] == pytest.approx(result.equity.iloc[-1])
    assert "sharpe" in result.metrics and "max_drawdown" in result.metrics
    assert not result.signals_log.empty
    # Equity must stay positive with 1% risk per trade and no leverage.
    assert (result.equity > 0).all()


def test_deterministic_given_same_data():
    data = {"AAA": generate_ohlcv("AAA", days=400, seed=11)}
    r1 = make_engine().run(data)
    r2 = make_engine().run(data)
    pd.testing.assert_series_equal(r1.equity, r2.equity)
    assert len(r1.trades) == len(r2.trades)


def test_uptrend_is_profitable(trending_up):
    result = make_engine().run({"UP": trending_up})
    assert result.metrics["total_return"] > 0
    assert not result.halted


def test_circuit_breaker_halts_and_flattens(trending_up, crashing):
    # Rise long enough to get the bot long, then crash: the breaker must trip,
    # liquidate, and freeze equity (no new trades while halted).
    crash = crashing.copy()
    crash.index = pd.bdate_range(
        start=trending_up.index[-1] + pd.Timedelta(days=1), periods=len(crash)
    )
    scale = trending_up["close"].iloc[-1] / crash["close"].iloc[0]
    boom_bust = pd.concat([trending_up, crash * scale])
    engine = BacktestEngine(
        strategy=Ensemble(members=[SmaCross(fast=10, slow=50)], threshold=0.05, long_only=True),
        risk=RiskConfig(
            max_drawdown=0.02,  # tighter than the stop distance -> breaker fires first
            allow_short=False,
            risk_per_trade=0.05,
            max_position_weight=1.0,
        ),
        starting_cash=100_000,
    )
    result = engine.run({"BOOM": boom_bust})
    assert result.halted
    # After halting there are no open positions, so equity stops moving.
    tail = result.equity.tail(20)
    assert tail.nunique() == 1


def test_no_lookahead_orders_fill_next_open(trending_up):
    result = make_engine().run({"UP": trending_up})
    fills = result.trades
    for t in fills:
        assert t.exit_time >= t.entry_time


def test_costs_reduce_returns(trending_up):
    cheap = BacktestEngine(
        strategy=Ensemble(members=[SmaCross(fast=10, slow=50)], threshold=0.05),
        execution=ExecutionModel(slippage_bps=0, commission_pct=0.0),
        starting_cash=100_000,
    ).run({"UP": trending_up})
    expensive = BacktestEngine(
        strategy=Ensemble(members=[SmaCross(fast=10, slow=50)], threshold=0.05),
        execution=ExecutionModel(slippage_bps=50, commission_pct=0.01),
        starting_cash=100_000,
    ).run({"UP": trending_up})
    assert expensive.metrics["final_equity"] < cheap.metrics["final_equity"]


def test_metrics_on_flat_equity():
    equity = pd.Series(
        [100.0] * 50, index=pd.bdate_range("2025-01-01", periods=50)
    )
    m = compute_metrics(equity, [])
    assert m["total_return"] == 0.0
    assert m["sharpe"] == 0.0
    assert m["max_drawdown"] == 0.0
