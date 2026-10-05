import json

import pytest

from investment_bot.backtest.engine import BacktestEngine
from investment_bot.broker.base import ExecutionModel
from investment_bot.data.synthetic import generate_ohlcv
from investment_bot.learning import AdaptiveWeights, LearningConfig
from investment_bot.risk import RiskConfig
from investment_bot.strategies import BollingerReversion, Ensemble, SmaCross


NAMES = ["trend", "reversion", "rsi"]


def make_learner(**overrides) -> AdaptiveWeights:
    cfg = LearningConfig(enabled=True, **overrides)
    return AdaptiveWeights(NAMES, [1.0, 1.0, 1.0], cfg)


def test_losing_trade_shifts_weight_away_from_supporters():
    learner = make_learner()
    votes = {"trend": 1.0, "reversion": 0.0, "rsi": -0.5}
    changed = learner.trade_feedback(votes, direction=1, trade_return=-0.05, label="AAPL")
    assert changed
    # trend argued for the losing long -> punished; rsi argued against -> rewarded
    assert learner.weights["trend"] < 1 / 3
    assert learner.weights["rsi"] > 1 / 3
    assert learner.weights["trend"] < learner.weights["reversion"] < learner.weights["rsi"]
    assert sum(learner.weights.values()) == pytest.approx(1.0)


def test_winning_trade_rewards_supporters():
    learner = make_learner()
    votes = {"trend": 0.8, "reversion": -0.2, "rsi": 0.0}
    learner.trade_feedback(votes, direction=1, trade_return=0.04, label="MSFT")
    assert learner.weights["trend"] > 1 / 3 > learner.weights["reversion"]


def test_short_trade_attribution_respects_direction():
    learner = make_learner()
    # A short that made money: the member who voted short was right.
    votes = {"trend": -1.0, "reversion": 1.0, "rsi": 0.0}
    learner.trade_feedback(votes, direction=-1, trade_return=0.03, label="TSLA")
    assert learner.weights["trend"] > learner.weights["reversion"]


def test_bar_feedback_learns_from_daily_moves():
    learner = make_learner()
    before = dict(learner.weights)
    learner.bar_feedback({"trend": 1.0, "reversion": -1.0, "rsi": 0.0}, 0.02, label="SPY")
    assert learner.weights["trend"] > before["trend"]
    assert learner.weights["reversion"] < before["reversion"]
    assert learner.bars_seen == 1


def test_flat_votes_or_zero_return_teach_nothing():
    learner = make_learner()
    assert not learner.bar_feedback({n: 0.0 for n in NAMES}, 0.05)
    assert not learner.trade_feedback({"trend": 1.0}, 1, 0.0)
    assert learner.weights == pytest.approx({n: 1 / 3 for n in NAMES})


def test_floor_holds_under_repeated_punishment():
    learner = make_learner()
    for _ in range(200):
        learner.trade_feedback({"trend": 1.0, "reversion": 0.0, "rsi": 0.0}, 1, -0.10)
    assert learner.weights["trend"] >= learner.config.weight_floor * 0.99
    assert sum(learner.weights.values()) == pytest.approx(1.0)


def test_return_cap_limits_single_lesson():
    capped, wild = make_learner(), make_learner()
    votes = {"trend": 1.0, "reversion": 0.0, "rsi": 0.0}
    capped.trade_feedback(votes, 1, -0.10)
    wild.trade_feedback(votes, 1, -5.0)  # absurd return must not nuke the weight
    assert wild.weights["trend"] == pytest.approx(capped.weights["trend"])


def test_stats_track_hits_and_events():
    learner = make_learner()
    learner.trade_feedback({"trend": 1.0, "reversion": -1.0, "rsi": 0.0}, 1, 0.05)
    assert learner.stats["trend"] == {"events": 1, "hits": 1, "score": pytest.approx(0.05)}
    assert learner.stats["reversion"]["hits"] == 0
    assert learner.stats["rsi"]["events"] == 0  # abstained, not scored
    summary = learner.summary()
    assert summary["enabled"] and summary["trades_seen"] == 1
    assert {m["name"] for m in summary["members"]} == set(NAMES)


def test_state_roundtrip_survives_json():
    learner = make_learner()
    learner.trade_feedback({"trend": 1.0, "reversion": -0.5, "rsi": 0.2}, 1, -0.03, label="NVDA")
    learner.bar_feedback({"trend": 0.5, "reversion": 0.0, "rsi": -0.5}, 0.01, label="SPY")
    blob = json.loads(json.dumps(learner.to_dict()))  # via-JSON, like live_state.json

    fresh = make_learner()
    fresh.restore(blob)
    assert fresh.weights == pytest.approx(learner.weights)
    assert fresh.stats == learner.stats
    assert fresh.trades_seen == 1 and fresh.bars_seen == 1
    assert list(fresh.events) == list(learner.events)


def test_ensemble_apply_weights_by_name():
    e = Ensemble(members=[SmaCross(fast=10, slow=50), BollingerReversion()], threshold=0.05)
    e.apply_weights({"SmaCross": 3.0, "BollingerReversion": 1.0})
    assert e.weight_map == pytest.approx({"SmaCross": 0.75, "BollingerReversion": 0.25})


def test_backtest_with_learner_adapts_and_stays_sane():
    strategy = Ensemble(
        members=[SmaCross(fast=10, slow=50), BollingerReversion()], threshold=0.05
    )
    learner = AdaptiveWeights(
        strategy.member_names, list(strategy.weights), LearningConfig(enabled=True)
    )
    engine = BacktestEngine(
        strategy=strategy,
        risk=RiskConfig(),
        execution=ExecutionModel(slippage_bps=5, commission_pct=0.0005),
        starting_cash=100_000,
        learner=learner,
    )
    data = {s: generate_ohlcv(s, days=500, seed=11) for s in ("AAA", "BBB")}
    result = engine.run(data)
    assert learner.bars_seen > 100  # lessons flowed every bar
    assert learner.weights != pytest.approx({n: 0.5 for n in learner.names})
    assert strategy.weight_map == pytest.approx(learner.weights)  # ensemble follows
    assert (result.equity > 0).all()
