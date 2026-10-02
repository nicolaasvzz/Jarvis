import pytest

from investment_bot.config import BotConfig
from investment_bot.data.synthetic import generate_ohlcv
from investment_bot.memory import (
    BacktestMemory,
    TuneConfig,
    apply_overrides,
    candidates,
    review_trades,
    run_backtest,
    score,
    split,
)

RAW = {
    "strategy": {
        "threshold": 0.25,
        "members": [
            {"name": "sma_cross", "weight": 1.0, "params": {"fast": 10, "slow": 50}},
            {"name": "rsi", "weight": 1.0},
        ],
    },
    "risk": {"atr_stop_multiple": 3.0},
    "backtest": {"lookback": 120},
    "learning": {"enabled": True, "auto_tune": True},
}


@pytest.fixture
def data():
    return {s: generate_ohlcv(s, days=420, seed=i) for i, s in enumerate(("AAA", "BBB"))}


def metrics(ret, dd, trades=100):
    return {"total_return": ret, "max_drawdown": dd, "num_trades": trades}


def fake_evaluator(table):
    """Score each job by the first matching override; the baseline otherwise."""

    def evaluate(jobs):
        out = []
        for raw, window in jobs:
            m = table["base"][window]
            for marker, by_window in table.items():
                if marker == "base":
                    continue
                section, key, value = marker
                if raw.get(section, {}).get(key) == value:
                    m = by_window[window]
            out.append(m)
        return out

    return evaluate


def test_score_punishes_drawdown_harder_than_it_rewards_return():
    assert score(metrics(0.10, -0.10), 1.5) < score(metrics(0.05, -0.02), 1.5)


def test_overrides_layer_over_config_without_touching_it():
    config = BotConfig(raw=RAW)
    tuned = apply_overrides(
        config,
        {
            "risk.atr_stop_multiple": 2.5,
            "strategy.learned_weights": {"SmaCross": 0.8, "RsiReversion": 0.2},
        },
    )
    assert tuned.build_risk().atr_stop_multiple == 2.5
    weights = tuned.build_strategy().weight_map
    assert weights == pytest.approx({"SmaCross": 0.8, "RsiReversion": 0.2})
    assert config.build_risk().atr_stop_multiple == 3.0  # original untouched


def test_candidates_respect_bounds_and_offer_learned_weights():
    config = BotConfig(raw={**RAW, "risk": {"risk_per_trade": 0.0025}})
    opts = candidates(config, {"SmaCross": 0.9, "RsiReversion": 0.1})
    risk_values = [c["risk.risk_per_trade"] for c in opts if "risk.risk_per_trade" in c]
    assert risk_values == [0.005]  # 0.0 would be below the floor
    assert {"strategy.long_only": True} in opts
    assert any("strategy.learned_weights" in c for c in opts)


def test_keeps_a_change_only_when_both_windows_improve(tmp_path, data):
    config = BotConfig(raw=RAW)
    result = run_backtest(config, data)
    table = {
        "base": {"train": metrics(0.0, -0.10), "hold": metrics(0.0, -0.10)},
        # Big win in training, worse out of sample: overfit, must be rejected.
        ("strategy", "threshold", 0.3): {"train": metrics(0.2, -0.05), "hold": metrics(-0.1, -0.2)},
        # Modest but holds up on both: should be kept.
        ("risk", "atr_stop_multiple", 2.5): {
            "train": metrics(0.01, -0.07),
            "hold": metrics(0.01, -0.08),
        },
        # Fewer losses only because it barely trades: rejected.
        ("strategy", "long_only", True): {
            "train": metrics(0.05, -0.01, 10),
            "hold": metrics(0.05, -0.01, 10),
        },
    }
    memory = BacktestMemory.load(tmp_path / "learned.json")
    rnd = memory.learn(
        config, data, result, None, TuneConfig(enabled=True), evaluate=fake_evaluator(table)
    )
    assert rnd.adopted["change"] == {"risk.atr_stop_multiple": 2.5}
    assert memory.overrides == {"risk.atr_stop_multiple": 2.5}

    # Persisted, and the next run starts from it.
    again = BacktestMemory.load(tmp_path / "learned.json")
    assert again.overrides == {"risk.atr_stop_multiple": 2.5}
    assert len(again.rounds) == 1 and again.rounds[0]["lessons"]
    assert again.apply(config).build_risk().atr_stop_multiple == 2.5


def test_nothing_kept_when_nothing_helps(tmp_path, data):
    config = BotConfig(raw=RAW)
    result = run_backtest(config, data)
    table = {"base": {"train": metrics(0.0, -0.05), "hold": metrics(0.0, -0.05)}}
    memory = BacktestMemory.load(tmp_path / "learned.json")
    rnd = memory.learn(
        config, data, result, None, TuneConfig(enabled=True), evaluate=fake_evaluator(table)
    )
    assert rnd.adopted is None and rnd.tested > 0
    assert memory.overrides == {}
    memory.reset()
    assert not (tmp_path / "learned.json").exists()


def test_real_round_end_to_end(tmp_path, data):
    """No fakes: real backtests on both windows, in-process."""
    config = BotConfig(raw=RAW)
    result = run_backtest(config, data)
    memory = BacktestMemory.load(tmp_path / "learned.json")
    rnd = memory.learn(config, data, result, None, TuneConfig(enabled=True, workers=1))
    assert rnd.tested >= 8
    assert rnd.full_run["num_trades"] == len(result.trades)
    assert len(rnd.considered) <= 5
    if rnd.adopted:
        assert rnd.adopted["train_gain"] >= 0.002 and rnd.adopted["holdout_gain"] >= 0.002


def test_holdout_window_includes_warmup_bars(data):
    train, hold = split(data, 0.3, warmup=50)
    n = len(data["AAA"])
    assert len(train["AAA"]) == int(n * 0.7)
    assert len(hold["AAA"]) == n - int(n * 0.7) + 50


def test_review_names_where_losses_came_from(data):
    result = run_backtest(BotConfig(raw=RAW), data)
    lessons = review_trades(result, {"SmaCross": 0.7, "RsiReversion": 0.3})
    assert lessons
    assert any("SmaCross (70%)" in x for x in lessons)
