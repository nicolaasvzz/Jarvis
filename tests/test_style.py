import json

import numpy as np
import pandas as pd

from investment_bot.config import BotConfig
from investment_bot.lab.lab import knob_changes
from investment_bot.lab.package import Package
from investment_bot.lab.trader import PackageTrader
from investment_bot.memory import BacktestMemory, TuneConfig, current_values
from investment_bot.session import Session, read_goal
from investment_bot.style import pick, read_style


def test_presets_pin_confidence_and_bet():
    assert not pick("refine").pinned
    assert (pick("fewer").threshold, pick("fewer").size) == (0.70, 0.25)
    assert (pick("more").threshold, pick("more").size) == (0.35, 0.05)
    assert not pick("nonsense").pinned


def test_custom_words_are_read_for_numbers_then_keywords():
    s = read_style("only do trades with high confidence but risk 30% of wallet "
                   "amount/avaliable money also do shorts")
    assert (s.threshold, s.size, s.shorts) == (0.70, 0.30, True)
    s = read_style("only trade when very sure, bet 30%, no shorts")
    assert (s.threshold, s.size, s.shorts) == (0.70, 0.30, False)  # 30 is the bet, not confidence
    s = read_style("80% confidence and 10% per trade")
    assert (s.threshold, s.size, s.shorts) == (0.80, 0.10, None)
    s = read_style("be careful and trade more often, hold longer")
    assert (s.threshold, s.size, s.min_hold) == (0.35, 0.05, 12)
    assert read_style("bet 90% a trade").size == 0.5  # capped
    assert not read_style("make me rich").pinned and read_style("make me rich").reading


def test_the_five_strategy_setup_gets_a_position_cap_and_risk_to_the_stop():
    out = read_style("70% confidence, 25% a trade, no shorts").config_overrides()
    assert out == {"strategy.threshold": 0.7, "risk.max_position_weight": 0.25,
                   "risk.risk_per_trade": 0.02, "strategy.long_only": True}


def test_pinned_package_settings_are_not_mutated():
    pkg = Package({"rsi_14@1h": 1.0})
    labels = [label for _, label in knob_changes(pkg, skip={"threshold", "shorts"})]
    assert labels and not any(lb.startswith(("entry bar", "shorts")) for lb in labels)


def test_a_session_saves_the_picked_strategy_and_leaves_it_alone(tmp_path):
    config = BotConfig(raw={"learning": {"auto_tune": True}})
    memory = BacktestMemory.load(tmp_path / "learned.json")
    session = Session(config=config, memory=memory, tune=TuneConfig.from_config(config),
                      goal=read_goal("learn shorts", ["AAA"], 1.5), seconds=1, round_seconds=1,
                      load_data=lambda _c: {}, state_file=tmp_path / "s.json",
                      style=read_style("75% confidence, bet 20%, no shorts"))
    saved = json.loads((tmp_path / "learned.json").read_text())["overrides"]
    assert saved["strategy.threshold"] == 0.75 and saved["strategy.long_only"] is True
    tuned = session.settings()
    assert current_values(tuned)["strategy.long_only"] is True  # the style beats "learn shorts"
    keys = {k for change in session.options(tuned) for k in change}
    assert not keys & {"strategy.threshold", "risk.risk_per_trade", "strategy.long_only"}


class Broker:
    base_url = "https://paper-api.alpaca.markets"

    def __init__(self):
        self.orders = []

    def account(self):
        return {"equity": "50000"}

    def positions(self):  # a big position the bot didn't open
        return [{"symbol": "TSLA", "qty": "100", "market_value": "40000"}]

    def clock(self):
        return {"is_open": True}

    def place(self, symbol, side, qty, crypto):
        self.orders.append((symbol, side, qty))


class Data:
    def __init__(self, bars):
        self._bars = bars

    def bars(self, symbol, timeframe, days):
        return self._bars


def test_the_trader_uses_the_picked_bet_and_only_the_money_you_gave_it(tmp_path):
    raw = {"lab": {"source": "synthetic", "timeframes": ["10m", "1h"],
                   "results_file": str(tmp_path / "lab.json"),
                   "universe_file": str(tmp_path / "universe.json")}}
    pkg = Package({"rsi_2@10m": 1.0, "close_location@10m": 1.0}, threshold=0.05, size=0.02)
    (tmp_path / "lab.json").write_text(json.dumps({"champion": {"package": pkg.to_dict()}}))
    (tmp_path / "universe.json").write_text(json.dumps({"symbols": [
        {"symbol": "TST", "class": "stock", "shortable": True, "fractionable": True}]}))
    n = 300
    close = np.linspace(100, 130, n)
    index = pd.date_range("2026-03-02 14:30", periods=n, freq="10min", tz="UTC")
    bars = pd.DataFrame({"open": close - 0.05, "high": close + 0.2, "low": close - 0.2,
                         "close": close, "volume": 1000.0}, index=index)
    broker = Broker()
    trader = PackageTrader(BotConfig(raw=raw), broker, Data(bars), say=lambda _: None,
                           state_file=tmp_path / "t.json", style=read_style("bet 20%"),
                           money=1000, now=lambda: index[-1] + pd.Timedelta(minutes=10))
    trader.cycle()
    assert trader.package.size == 0.20
    (symbol, side, qty), = broker.orders  # TSLA (not its own) doesn't use up the $1,000
    assert (symbol, side) == ("TST", "buy")
    assert 1000 * 0.10 <= qty * 130 <= 1000 * 0.20 + 1  # sized on $1,000, not $50,000
    assert "money: $1,000" in trader.session_report().html()
