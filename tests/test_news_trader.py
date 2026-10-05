import json
from datetime import datetime, timedelta, timezone

import pytest

from investment_bot.config import BotConfig
from investment_bot.jarvis_status import build_status
from investment_bot.research.store import Item, NewsStore, Score
from investment_bot.research.trader import NewsTrader, TradeConfig, calibration

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)


class Resp:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class Prices:
    """Alpaca's latest-trade endpoints, at prices the test sets."""

    def __init__(self, **prices):
        self.prices = {k.replace("_", "/"): v for k, v in prices.items()}

    def get(self, url, params=None, headers=None, timeout=None):
        if "crypto" in url:
            sym = params["symbols"]
            return Resp({"trades": {sym: {"p": self.prices[sym]}}})
        sym = url.split("/stocks/")[1].split("/")[0]
        return Resp({"trade": {"p": self.prices[sym]}})


class Broker:
    base_url = "https://paper-api.alpaca.markets"
    api_key = secret_key = "k"

    def __init__(self, prices, equity=100_000.0, last_equity=None, held=None, is_open=True):
        self.prices = prices
        self.equity, self.last_equity = equity, last_equity or equity
        self.held = dict(held or {})  # symbol (Alpaca's spelling) -> qty
        self.is_open = is_open
        self.orders = []

    def account(self):
        return {"equity": str(self.equity), "last_equity": str(self.last_equity)}

    def positions(self):
        return [{"symbol": s, "qty": str(q),
                 "current_price": str(self.prices.prices.get(s, self.prices.prices.get(
                     s.replace("USD", "/USD"), 0)))}
                for s, q in self.held.items() if q]

    def clock(self):
        return {"is_open": self.is_open}

    def place(self, symbol, side, qty, crypto):
        self.orders.append((symbol, side, qty))
        name = symbol.replace("/", "")
        self.held[name] = self.held.get(name, 0) + (qty if side == "buy" else -qty)


def config(tmp_path, **trade):
    return BotConfig(raw={
        "universe": ["NVDA", "AAPL", "TSLA", "BTC/USD"],
        "live": {"state_file": str(tmp_path / "live.json"), "starting_cash": 1000},
        "learning": {"memory_file": str(tmp_path / "learned.json")},
        "research": {"db_file": str(tmp_path / "news.db"),
                     "state_file": str(tmp_path / "research.json"),
                     "trade": {"state_file": str(tmp_path / "news_trader.json"), **trade}},
    })


def article(store, n, symbol, probability, direction=1.0, minutes_ago=20, source="alpaca",
            event="earnings", target=None, move_pct=3.0, horizon=24, strength=0.8):
    item = Item(id=f"{source}:{n}", source=source,
                published=(NOW - timedelta(minutes=minutes_ago)).isoformat(),
                headline=f"article {n} about {symbol}", symbols=[symbol])
    store.add([item])
    store.save_scores([item.id], [Score(item.id, symbol, direction, strength, probability,
                                        move_pct, target, horizon, event)])


def trader(tmp_path, broker, universe=None, **trade):
    path = tmp_path / "universe.json"
    path.write_text(json.dumps({"symbols": universe or []}), encoding="utf-8")
    store = NewsStore(tmp_path / "news.db")
    t = NewsTrader(config(tmp_path, **trade), broker, store, broker.prices,
                   ["NVDA", "AAPL", "TSLA", "BTC/USD"], universe_file=path, say=lambda _l: None)
    return t, store


def test_the_bet_grows_with_the_probability():
    cfg = TradeConfig()
    assert cfg.size_for(0.54) == 0
    assert cfg.size_for(0.55) == pytest.approx(0.02)
    assert cfg.size_for(0.75) == pytest.approx(0.26)
    assert cfg.size_for(0.95) == pytest.approx(0.50)
    assert cfg.size_for(1.00) == pytest.approx(0.50)


def test_a_likely_article_opens_a_bet_sized_by_its_probability(tmp_path):
    broker = Broker(Prices(NVDA=100.0))
    t, store = trader(tmp_path, broker)
    article(store, 1, "NVDA", 0.75, target=108.0)
    t.cycle(NOW, {})
    assert broker.orders == [("NVDA", "buy", 260)]  # 26% of $100k at $100, whole shares
    h = t.state.holdings["NVDA"]
    assert (h.take, h.stop, h.probability) == (108.0, 96.0, 0.75)
    t.cycle(NOW + timedelta(minutes=5), {})  # the same article never bets twice
    assert len(broker.orders) == 1


def test_far_off_targets_are_ignored_for_the_predicted_move(tmp_path):
    broker = Broker(Prices(AMD=200.0))
    t, store = trader(tmp_path, broker)
    t.symbols.append("AMD")
    article(store, 1, "AMD", 0.65, target=700.0, move_pct=3.0)  # a 12-month analyst target
    t.cycle(NOW, {})
    assert t.state.holdings["AMD"].take == pytest.approx(206.0)


def test_crypto_target_like_btc_to_90k(tmp_path):
    broker = Broker(Prices(BTC_USD=80_000.0))
    t, store = trader(tmp_path, broker)
    article(store, 1, "BTC/USD", 0.95, target=90_000.0, event="macro")
    t.cycle(NOW, {})
    assert broker.orders[0][:2] == ("BTC/USD", "buy")
    assert broker.orders[0][2] == pytest.approx(0.5 * 100_000 / 80_000)  # 50% bet, fractional
    broker.prices.prices["BTC/USD"] = 90_500.0
    t.cycle(NOW + timedelta(hours=3), {})
    assert broker.orders[-1][1] == "sell"
    closed = t.state.closed[-1]
    assert closed["why"] == "target reached" and closed["pnl"] > 0
    assert calibration(t.state.closed) == [
        {"band": "85%-100%", "trades": 1, "won": 1, "win_rate": 1.0, "pnl": closed["pnl"]}]


def test_what_it_wont_bet_on(tmp_path):
    broker = Broker(Prices(NVDA=100.0, AAPL=200.0, TSLA=300.0), held={"AAPL": 5})
    t, store = trader(tmp_path, broker,
                      universe=[{"symbol": "TSLA", "class": "stock", "shortable": False}])
    article(store, 1, "NVDA", 0.54)                       # under 55%
    article(store, 2, "NVDA", 0.9, source="x")            # a post, not news
    article(store, 3, "NVDA", 0.9, event="hype")          # hype
    article(store, 4, "NVDA", 0.9, minutes_ago=180)       # older than 2 hours
    article(store, 5, "NVDA", 0.9, strength=0.05)         # filler
    article(store, 6, "AAPL", 0.9)                        # already held by something else
    article(store, 7, "TSLA", 0.9, direction=-1)          # short, but not shortable
    t.cycle(NOW, {})
    assert broker.orders == []
    article(store, 8, "NVDA", 0.9)
    t.cycle(NOW, {"NVDA": -0.4})                          # the rest of the news disagrees
    assert broker.orders == []


def test_exits_on_stop_time_and_turned_news(tmp_path):
    broker = Broker(Prices(NVDA=100.0, AAPL=100.0, TSLA=100.0))
    t, store = trader(tmp_path, broker)
    for n, sym in enumerate(("NVDA", "AAPL", "TSLA")):
        article(store, n, sym, 0.6, horizon=1)
    t.cycle(NOW, {})
    assert set(t.state.holdings) == {"NVDA", "AAPL", "TSLA"}
    broker.prices.prices["NVDA"] = 95.0                    # 5% down: past the 4% stop
    t.cycle(NOW + timedelta(minutes=5), {"TSLA": -0.6})    # TSLA's news turned
    whys = {c["symbol"]: c["why"] for c in t.state.closed}
    assert whys == {"NVDA": "stop", "TSLA": "news turned"}
    t.cycle(NOW + timedelta(hours=2), {})
    assert {c["symbol"]: c["why"] for c in t.state.closed}["AAPL"] == "time's up"
    assert not t.state.holdings and not any(broker.held.values())


def test_leaves_positions_it_didnt_open_alone(tmp_path):
    broker = Broker(Prices(NVDA=100.0))
    t, store = trader(tmp_path, broker)
    article(store, 1, "NVDA", 0.6)
    t.cycle(NOW, {})
    bought = broker.held["NVDA"]
    broker.held["NVDA"] += 7  # the package trader buys more of the same stock
    broker.prices.prices["NVDA"] = 90.0
    t.cycle(NOW + timedelta(minutes=5), {})
    assert broker.orders[-1] == ("NVDA", "sell", bought)
    assert broker.held["NVDA"] == 7


def test_no_new_bets_after_the_daily_loss_limit(tmp_path):
    broker = Broker(Prices(NVDA=100.0), equity=96_000, last_equity=100_000)
    t, store = trader(tmp_path, broker)
    article(store, 1, "NVDA", 0.9)
    assert t.cycle(NOW, {})["paused"] and broker.orders == []


def test_refuses_real_money_unless_allowed(tmp_path):
    broker = Broker(Prices())
    broker.base_url = "https://api.alpaca.markets"
    with pytest.raises(SystemExit):
        trader(tmp_path, broker)


def test_jarvis_page_shows_news_trades(tmp_path):
    broker = Broker(Prices(NVDA=100.0))
    t, store = trader(tmp_path, broker)
    article(store, 1, "NVDA", 0.75)
    t.cycle(NOW, {})
    (tmp_path / "research.json").write_text(json.dumps({"updated": NOW.isoformat()}))
    status = build_status(config(tmp_path))
    assert status["News trades open"] == 1
    assert status["News positions"][0]["Probability %"] == 75
    assert status["News bet size by probability"]["95%"] == 50.0
