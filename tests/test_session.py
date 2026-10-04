import json

import pytest

from investment_bot.config import BotConfig
from investment_bot.data.synthetic import generate_ohlcv
from investment_bot.jarvis_status import build_status
from investment_bot.memory import BacktestMemory, TuneConfig, candidates
from investment_bot.session import Session, parse_duration, read_goal

RAW = {
    "strategy": {
        "threshold": 0.25,
        "long_only": True,
        "members": [
            {"name": "sma_cross", "weight": 1.0, "params": {"fast": 10, "slow": 50}},
            {"name": "rsi", "weight": 1.0},
        ],
    },
    "risk": {"atr_stop_multiple": 3.0},
    "backtest": {"lookback": 120},
    "learning": {"enabled": True, "auto_tune": True},
}


def test_durations():
    assert parse_duration("10m") == 600
    assert parse_duration("2h") == 7200
    assert parse_duration("10d") == 864000
    assert parse_duration("90") == 5400  # plain numbers are minutes
    with pytest.raises(ValueError):
        parse_duration("soon")


def test_goal_is_read_for_keywords():
    goal = read_goal("learn how to do shorts on TSLA, carefully", ["TSLA", "SPY"], 1.5)
    assert goal.side == -1 and goal.symbols == ("TSLA",)
    assert goal.loss_aversion == 3.0
    assert read_goal("", ["SPY"], 1.5).reading == ["cut losses overall (no keywords recognised)"]
    assert read_goal("more profit from longs", [], 1.5).side == 1
    # "learn" isn't "earn": no accidental reading of the goal
    assert read_goal("learn how to do shorts", [], 1.5).loss_aversion == 1.5


def test_short_threshold_vetoes_weak_shorts_only():
    config = BotConfig(raw={**RAW, "strategy": {**RAW["strategy"], "long_only": False,
                                                 "short_threshold": 0.9}})
    assert config.build_strategy().short_threshold == 0.9
    assert any("strategy.short_threshold" in c for c in candidates(config))
    # long-only: the short bar isn't a knob worth turning
    assert not any("strategy.short_threshold" in c for c in candidates(BotConfig(raw=RAW)))


def test_a_short_session_learns_and_saves_each_round(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = {s: generate_ohlcv(s, days=320, seed=i) for i, s in enumerate(("AAA", "BBB"))}
    config = BotConfig(raw=RAW)
    memory = BacktestMemory.load(tmp_path / "learned.json")
    lines = []
    session = Session(
        config=config,
        memory=memory,
        tune=TuneConfig.from_config(config),
        goal=read_goal("learn shorts", ["AAA", "BBB"], 1.5),
        seconds=1,
        round_seconds=1,
        load_data=lambda _cfg: data,
        say=lines.append,
        workers=2,
        state_file=tmp_path / "learning_session.json",
        seed=3,
    )
    # Learning shorts backtests with shorts on, though the config is long-only.
    assert session.settings().build_strategy().long_only is False
    assert session.run() == "finished"
    assert session.rounds_done == 1  # one round even when time is short, then it stops
    saved = json.loads((tmp_path / "learned.json").read_text())
    assert len(saved["rounds"]) == 1
    assert any("shorts" in lesson.lower() for lesson in saved["rounds"][0]["lessons"])
    state = json.loads((tmp_path / "learning_session.json").read_text())
    assert state["status"] == "finished" and state["goal"] == "learn shorts"
    assert any(line.startswith("Goal: learn shorts") for line in lines)

    status = build_status(BotConfig(raw={**RAW, "learning": {"memory_file": "learned.json"}}))
    assert status["Learning"] == "finished"
    assert status["Learning goal"] == "learn shorts"
