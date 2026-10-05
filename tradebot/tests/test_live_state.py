"""Live state must survive a restart — including closed trades and learning."""
import json

import pandas as pd
import pytest
from rich.console import Console

from investment_bot.broker.paper import PaperBroker
from investment_bot.config import BotConfig
from investment_bot.data.feed import DataFeed
from investment_bot.data.synthetic import generate_ohlcv
from investment_bot.live import LiveTrader


class AdvancingFeed(DataFeed):
    """Reveals one more bar per cycle, like a new trading day arriving."""

    def __init__(self, symbols, days=400, seed=5):
        self.data = {s: generate_ohlcv(s, days=days, seed=seed + i)
                     for i, s in enumerate(symbols)}
        self.cursor = 300

    def history(self, symbol: str, days: int) -> pd.DataFrame:
        return self.data[symbol].iloc[: self.cursor].tail(days)


SYMBOLS = ["AAA", "BBB"]


def make_trader(tmp_path, feed, learning=True):
    config = BotConfig(raw={
        "universe": SYMBOLS,
        "strategy": {"threshold": 0.15},
        "risk": {"risk_per_trade": 0.02, "atr_stop_multiple": 2.0},
        "live": {"broker": "paper", "state_file": str(tmp_path / "state.json"),
                 "starting_cash": 100_000, "lookback": 300},
        "learning": {"enabled": learning},
    })
    return LiveTrader(
        config=config, feed=feed, strategy=config.build_strategy(),
        broker=PaperBroker(), console=Console(quiet=True), show_dashboard=False,
    )


def run_until_trades(trader, feed, cycles=40):
    for _ in range(cycles):
        trader.run_cycle()
        feed.cursor += 1
    return trader.portfolio.trades


def test_closed_trades_survive_restart(tmp_path):
    feed = AdvancingFeed(SYMBOLS)
    trader = make_trader(tmp_path, feed)
    trades = run_until_trades(trader, feed)
    assert trades, "fixture should produce at least one closed trade"

    reloaded = make_trader(tmp_path, AdvancingFeed(SYMBOLS))
    assert len(reloaded.portfolio.trades) == len(trades)
    a, b = trades[0], reloaded.portfolio.trades[0]
    assert (a.symbol, a.direction, a.reason) == (b.symbol, b.direction, b.reason)
    assert a.pnl == pytest.approx(b.pnl)
    assert a.entry_price == pytest.approx(b.entry_price)
    assert a.exit_time == b.exit_time


def test_state_file_is_json_serializable_and_complete(tmp_path):
    feed = AdvancingFeed(SYMBOLS)
    trader = make_trader(tmp_path, feed)
    run_until_trades(trader, feed, cycles=20)
    state = json.loads((tmp_path / "state.json").read_text())
    for key in ("cash", "positions", "equity_curve", "trades", "signal_memory", "learning"):
        assert key in state, f"state file missing {key!r}"
    assert state["learning"]["weights"]


def test_learned_weights_survive_restart(tmp_path):
    feed = AdvancingFeed(SYMBOLS)
    trader = make_trader(tmp_path, feed)
    run_until_trades(trader, feed, cycles=25)
    learned = dict(trader.learner.weights)
    assert trader.learner.bars_seen > 0

    reloaded = make_trader(tmp_path, AdvancingFeed(SYMBOLS))
    assert reloaded.learner.weights == pytest.approx(learned)
    # The ensemble must actually be using them, not just holding them.
    assert reloaded.strategy.weight_map == pytest.approx(learned)


def test_learning_disabled_leaves_weights_untouched(tmp_path):
    feed = AdvancingFeed(SYMBOLS)
    trader = make_trader(tmp_path, feed, learning=False)
    before = dict(trader.strategy.weight_map)
    run_until_trades(trader, feed, cycles=15)
    assert trader.learner is None
    assert trader.strategy.weight_map == pytest.approx(before)
    assert trader.learning_summary() == {"enabled": False}
