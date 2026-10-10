"""The champ-set builder (30m/1h/4h charts, one setup trading the 1h) and its trader."""
import json

import numpy as np
import pandas as pd
import pytest

from investment_bot.config import BotConfig
from investment_bot.lab import champ as champ_module
from investment_bot.lab.champ import ChampBuilder, look_alike_pool, prepare_charts
from investment_bot.lab.charts import NY, candles, clean, decision_features
from investment_bot.lab.setup_trader import SetupTrader, latest_close
from investment_bot.lab.setups import CLOSE, STOP, TAKE, TIME, Setup, barrier, fit_model, luck_bar


def ten_minute(start: str, periods: int, tz: str = "UTC", step: float = 0.0,
               seed: int = 1) -> pd.DataFrame:
    """10-minute candles from `start` (wall time in `tz`), a gentle walk with drift `step`."""
    rng = np.random.default_rng(seed)
    index = pd.date_range(start, periods=periods, freq="10min", tz=tz).tz_convert("UTC")
    close = 100 * np.exp(np.cumsum(step + 0.001 * rng.standard_normal(periods)))
    open_ = np.concatenate([[100.0], close[:-1]])
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.001,
                         "low": np.minimum(open_, close) * 0.999, "close": close,
                         "volume": 1000.0}, index=index)


def stock_days(first: str, days: int, step: float = 0.0, seed: int = 1) -> pd.DataFrame:
    """Regular-hours 10-minute stock candles (9:30-16:00 New York) for `days` weekdays."""
    parts = [ten_minute(f"{d.date()} 09:30", 39, NY, step, seed + i)
             for i, d in enumerate(pd.bdate_range(first, periods=days))]
    out = pd.concat(parts)
    out[["open", "high", "low", "close"]] *= np.repeat(  # stitch the days into one walk
        np.cumprod([1.0] + [p["close"].iloc[-1] / 100 for p in parts[:-1]]), 39)[:, None]
    return out


# ------------------------------------------------------------------ charts


@pytest.mark.parametrize("day", ["2026-01-14", "2026-07-15"])  # winter and summer time
def test_stock_candles_follow_the_trading_day(day):
    raw = stock_days(day, 1)
    to_ny = lambda ts: [str(t.tz_convert(NY).time())[:5] for t in ts]  # noqa: E731
    hours, closes = candles(raw, "1h", stock=True)
    assert to_ny(hours.index) == ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]
    assert to_ny(pd.to_datetime(closes, utc=True))[-1] == "16:00"  # the last hour is half one
    fours, closes = candles(raw, "4h", stock=True)
    assert to_ny(fours.index) == ["09:30", "13:30"]
    assert to_ny(pd.to_datetime(closes, utc=True)) == ["13:30", "16:00"]
    assert len(candles(raw, "30m", stock=True)[0]) == 13


def test_crypto_candles_start_on_the_utc_hour():
    raw = ten_minute("2026-03-01 00:00", 6 * 24)
    fours, closes = candles(raw, "4h", stock=False)
    assert [t.hour for t in fours.index] == [0, 4, 8, 12, 16, 20]
    assert (closes - fours.index.as_unit("ns").asi8 == 4 * 3_600_000_000_000).all()


def test_charts_never_look_ahead_and_bigger_candles_count_once_closed():
    raw = ten_minute("2026-03-01 00:00", 6 * 24 * 12, seed=4)
    names = {"1h": ["rsi_14"], "30m": ["rsi_14"], "4h": ["rsi_14"]}
    bars, feats = decision_features(raw, stock=False, names=names)
    cut = 6 * 24 * 10
    changed = raw.copy()
    changed.iloc[cut:, :4] *= 1.3
    bars2, feats2 = decision_features(changed, stock=False, names=names)
    early = bars.index < raw.index[cut] - pd.Timedelta(hours=1)
    np.testing.assert_allclose(feats[early].to_numpy(), feats2[early].to_numpy(), atol=1e-6,
                               equal_nan=True)
    # the 4h value only moves on the decision row that closes its 4h candle
    four = feats["rsi_14@4h"].to_numpy()
    moved = np.flatnonzero(np.abs(np.diff(four)) > 0) + 1
    assert all(bars.index[i].hour % 4 == 3 for i in moved)  # the 1h candle 03:00-04:00 closes it


def test_clean_tames_a_bad_print_but_keeps_real_gaps():
    raw = ten_minute("2026-03-01 00:00", 400, seed=2)
    spiky = raw.copy()
    spiky.iloc[300, spiky.columns.get_loc("close")] *= 1.2   # one candle 20% off, then back
    spiky.iloc[300, spiky.columns.get_loc("high")] *= 1.2
    tamed = clean(spiky, stock=False)
    assert tamed["close"].iloc[300] < raw["close"].iloc[300] * 1.05
    assert tamed["high"].iloc[300] < raw["high"].iloc[300] * 1.06
    np.testing.assert_allclose(tamed["close"].iloc[:300], raw["close"].iloc[:300])
    # stocks: a gap from one day's close to the next day's open is real
    days = stock_days("2026-03-02", 3)
    days.iloc[39:, :4] *= 1.25
    np.testing.assert_allclose(clean(days, stock=True)["close"].iloc[39], days["close"].iloc[39])


# ------------------------------------------------------------------ the setup


def test_triple_barrier_is_cautious():
    # (trades x candles) in ATRs, signed toward the trade
    opn = np.array([[0, 0.2, 0.0], [0, -2.0, 0.0], [0, 3.0, 0.0], [0, 0.1, 0.2], [0, 0, 0]])
    fav = np.array([[2.5, 0.0, 0.0], [0.3, -1.5, 0.0], [0.4, 3.5, 0.0], [0.5, 0.4, 0.6],
                    [0.2, 0.3, 0.9]])
    adv = np.array([[-1.2, 0.0, 0.0], [-0.2, -2.5, 0.0], [-0.1, 2.5, 0.0], [-0.3, -0.2, -0.1],
                    [-0.1, 0, 0]])
    cls = np.array([[0.1, 0.0, 0.0], [0.1, -2.2, 0.0], [0.3, 3.2, 0.0], [0.2, 0.3, 0.45],
                    [0.1, 0.25, 0.8]])
    limit = np.array([3, 3, 3, 3, 2])
    why_end = np.array([TIME, TIME, TIME, TIME, CLOSE])
    result, ended, why = barrier(opn, fav, adv, cls, 1.0, 2.0, limit, why_end)
    assert (result[0], why[0], ended[0]) == (-1.0, STOP, 0)    # both touched: the stop counts
    assert (result[1], why[1]) == (-2.0, STOP)                  # opened past the stop: that open
    assert (result[2], why[2]) == (2.0, TAKE)                   # opened past the target: target
    assert (result[3], why[3], ended[3]) == (0.45, TIME, 2)     # time's up: the close
    assert (result[4], why[4], ended[4]) == (0.25, CLOSE, 1)    # the day's close


def test_confidence_model_learns_which_chart_matters():
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 1, (4000, 3))
    p = 1 / (1 + np.exp(-(-1 + 3 * x[:, 1])))   # only the 4h agreement matters
    y = (rng.uniform(size=4000) < p).astype(float)
    b, w1, w4, w30 = fit_model(x, y)
    assert w4 > 2 and abs(w1) < 0.5 and abs(w30) < 0.5


def test_luck_bar_rises_with_the_number_of_tries():
    bars = [luck_bar(n) for n in (2, 10, 36, 100, 1000)]
    assert bars == sorted(bars) and 2.0 < luck_bar(36) < 2.3 and luck_bar(1) == 0.0


def test_look_alikes_count_once():
    rng = np.random.default_rng(1)
    a = rng.standard_normal(500)
    x = np.column_stack([a, a + 0.05 * rng.standard_normal(500), rng.standard_normal(500)])
    entries = [{"family": "trend"}, {"family": "trend"}, {"family": "volume"}]
    pool, groups = look_alike_pool(x, entries, 10, 0.7)
    assert groups == 2 and pool == [0, 2]


def test_setup_decides_only_when_all_three_charts_agree():
    setup = Setup({"1h": {"a@1h": 1}, "30m": {"b@30m": 1}, "4h": {"c@4h": 1}}, entry=0.3,
                  agree_4h=0.1, agree_30m=0.0, model=[2.0, 0, 0, 0], confidence=0.6)
    scores = {"1h": np.array([0.5, 0.5, 0.5, -0.5, 0.1]),
              "4h": np.array([0.3, -0.2, 0.3, -0.3, 0.9]),
              "30m": np.array([0.1, 0.1, -0.1, -0.2, 0.9])}
    d, p = setup.decide(scores)
    assert d.tolist() == [1, 0, 0, -1, 0]
    assert setup.decide(scores, can_short=False)[0].tolist() == [1, 0, 0, 0, 0]


# ------------------------------------------------------------------ the builder end to end


def _config(tmp_path, **champ) -> BotConfig:
    return BotConfig(raw={
        "lab": {"source": "synthetic", "intraday_days": 150, "synthetic_symbols": 4,
                "synthetic_crypto": 2, "workers": 2, "min_trades": 5,
                "universe_file": str(tmp_path / "universe.json")},
        "champ": {"feature_dir": str(tmp_path / "charts"),
                  "results_file": str(tmp_path / "champ.json"), **champ}})


def test_builder_masters_three_charts_per_class_and_counts_every_try(tmp_path):
    config = _config(tmp_path)
    store, universe = prepare_charts(config, say=lambda _: None)
    assert {u["class"] for u in universe} == {"stock", "crypto"}
    assert len(store.columns) == 3 * 126 or len(store.columns) % 3 == 0
    builder = ChampBuilder(config, store, universe, seconds=600, say=lambda _: None, seed=1,
                           state_file=tmp_path / "session.json")
    assert builder.run() == "finished"
    data = json.loads((tmp_path / "champ.json").read_text())
    for cls in ("stock", "crypto"):
        entry = data["classes"][cls]
        assert set(entry["charts"]) == {"4h", "1h", "30m"}
        champ = entry["champion"]
        setup = Setup.from_dict(champ["setup"])
        assert all(setup.sets[tf] for tf in ("1h", "30m", "4h"))
        assert all(abs(w) == 1 for tf in setup.sets for w in setup.sets[tf].values())
        assert {"train", "test", "final"} <= set(champ["metrics"])
        assert len(champ["windows"]) == 4
        assert setup.hours <= 6 and setup.sl_atr > 0 and setup.tp_atr > 0
        assert champ["proven"] == all(champ["checks"].values())
        if not champ["proven"]:
            assert setup.size == 0.02
    first = dict(data["trials"])
    assert json.loads((tmp_path / "session.json").read_text())["status"] == "finished"
    page = builder.report("finished").html()
    assert "Champ-set builder" in page and "indicators on each chart" in page

    again = ChampBuilder(config, store, universe, seconds=600, say=lambda _: None, seed=2,
                         state_file=tmp_path / "session.json")
    again.run()
    data = json.loads((tmp_path / "champ.json").read_text())
    for cls in ("stock", "crypto"):  # tries add up, and the old champion competed again
        assert data["trials"][cls] > first[cls]
        assert data["classes"][cls]["champion"]["luck_bar"] >= luck_bar(first[cls])
        names = [t["name"] for t in data["classes"][cls]["tried"]]
        assert len(names) <= 10


def test_no_shorts_pinned_means_long_only(tmp_path):
    from investment_bot.style import read_style

    config = _config(tmp_path, classes=["stock"])
    store, universe = prepare_charts(config, say=lambda _: None)
    builder = ChampBuilder(config, store, universe, seconds=600, say=lambda _: None, seed=1,
                           state_file=tmp_path / "s.json", style=read_style("no shorts"))
    builder.run()
    setup = Setup.from_dict(json.loads((tmp_path / "champ.json").read_text())
                            ["classes"]["stock"]["champion"]["setup"])
    assert setup.shorts is False
    w = builder.windows([u["symbol"] for u in universe])
    cands = builder.gather([u["symbol"] for u in universe], setup.sets)
    trades, _ = champ_module.run_setup(setup.with_(confidence=0.0), cands,
                                       builder._mask(cands, w["train"]))
    assert len(trades) and (trades.direction > 0).all()


# ------------------------------------------------------------------ the trader


class FakeBroker:
    base_url = "https://paper-api.alpaca.markets"

    def __init__(self, equity=100_000.0, day_trades=0, is_open=True):
        self.orders, self.held = [], {}
        self.equity, self.day_trades, self.is_open = equity, day_trades, is_open

    def account(self):
        return {"equity": str(self.equity), "daytrade_count": self.day_trades}

    def positions(self):
        return [{"symbol": s.replace("/", ""), "qty": str(q), "market_value": str(q * 100)}
                for s, q in self.held.items()]

    def clock(self):
        return {"is_open": self.is_open}

    def place(self, symbol, side, qty, crypto):
        self.orders.append((symbol, side, qty))
        self.held[symbol] = self.held.get(symbol, 0) + (qty if side == "buy" else -qty)
        if abs(self.held[symbol]) < 1e-9:
            self.held.pop(symbol)


class FakeData:
    def __init__(self, frames):
        self.frames = frames

    def bars(self, symbol, timeframe, days):
        return self.frames[symbol]


TREND = {"1h": {"price_vs_sma_20@1h": 1.0, "linreg_slope_20@1h": 1.0},
         "30m": {"price_vs_sma_20@30m": 1.0}, "4h": {"price_vs_sma_20@4h": 1.0}}


def _trader(tmp_path, symbol, item_class, frames, broker, proven=True, news=None, **champ):
    setup = Setup(TREND, entry=0.05, model=[4.0, 0, 0, 0], confidence=0.6, sl_atr=1.5,
                  tp_atr=50.0, hours=3, size=0.1)
    (tmp_path / "champ.json").write_text(json.dumps({"classes": {item_class: {"champion": {
        "setup": setup.to_dict(), "proven": proven}}}}))
    (tmp_path / "universe.json").write_text(json.dumps({"symbols": [
        {"symbol": symbol, "class": item_class, "shortable": True, "fractionable": True}]}))
    config = BotConfig(raw={"lab": {"universe_file": str(tmp_path / "universe.json")},
                            "champ": {"results_file": str(tmp_path / "champ.json"), **champ}})
    clock = {"now": None}
    trader = SetupTrader(config, broker, FakeData(frames), say=lambda _: None,
                         state_file=tmp_path / "trader.json", now=lambda: clock["now"],
                         news=news or (lambda when: {}))
    return trader, clock


def _rising_crypto(hours: int) -> pd.DataFrame:
    return ten_minute("2026-03-01 00:00", 6 * hours, step=0.0004, seed=5)


def test_trader_enters_on_the_hour_and_exits_on_the_stop(tmp_path):
    raw = _rising_crypto(24 * 12)
    broker = FakeBroker()
    trader, clock = _trader(tmp_path, "BTC/USD", "crypto", {"BTC/USD": raw}, broker)
    clock["now"] = raw.index[-1] + pd.Timedelta(minutes=10, seconds=15)
    assert latest_close(clock["now"], stock=False) == raw.index[-1] + pd.Timedelta(minutes=10)
    trader.cycle()
    assert broker.orders and broker.orders[0][:2] == ("BTC/USD", "buy")
    trade = trader.state.trades["BTC/USD"]
    assert trade.stop < trade.entry < trade.take
    trader.cycle()  # same hour: nothing new
    assert len(broker.orders) == 1
    crash = raw.iloc[[-1]].copy()
    crash.index = crash.index + pd.Timedelta(minutes=10)
    crash[["open", "high", "low", "close"]] = [trade.entry, trade.entry, trade.stop * 0.99,
                                               trade.stop * 0.995]
    trader.data.frames["BTC/USD"] = pd.concat([raw, crash])
    clock["now"] += pd.Timedelta(minutes=10)
    trader.cycle()
    assert broker.orders[-1][:2] == ("BTC/USD", "sell") and not trader.state.trades
    assert "closed long: stop" in trader.state.log[-1]["what"]
    assert "2 order(s)" in trader.session_report().html()


def test_news_stretches_a_trade_then_ends_it_when_the_news_turns(tmp_path):
    raw = _rising_crypto(24 * 12)
    broker = FakeBroker()
    news = {"BTC/USD": {"mood": 0.8, "threshold": 0.5, "horizon": 24.0, "move_pct": 30.0,
                        "headline": "Big news"}}
    trader, clock = _trader(tmp_path, "BTC/USD", "crypto", {"BTC/USD": raw}, broker,
                            news=lambda when: news)
    clock["now"] = raw.index[-1] + pd.Timedelta(minutes=10, seconds=15)
    trader.cycle()
    trade = trader.state.trades["BTC/USD"]
    plain_take = trade.take
    # three hours later, still rising: the normal time limit... but the news agrees
    more = ten_minute(str((raw.index[-1] + pd.Timedelta(minutes=10)).tz_localize(None)), 18,
                      step=0.0001, seed=9)
    more[["open", "high", "low", "close"]] *= raw["close"].iloc[-1] / 100
    trader.data.frames["BTC/USD"] = pd.concat([raw, more])
    clock["now"] = more.index[-1] + pd.Timedelta(minutes=10, seconds=15)
    trader.cycle()
    trade = trader.state.trades["BTC/USD"]
    assert trade.stretched and trade.plain_why == "time limit"
    assert abs(trade.take - trade.entry) <= 3 * abs(plain_take - trade.entry) + 1e-9
    news["BTC/USD"]["mood"] = -0.7   # the news turns
    clock["now"] += pd.Timedelta(minutes=10)
    trader.cycle()
    assert not trader.state.trades
    assert trader.state.stretched[-1]["why"] == "news turned"
    assert "plain" in trader.state.stretched[-1]


def test_stock_trades_close_by_the_bell_and_small_accounts_keep_day_trades(tmp_path):
    raw = stock_days("2026-03-02", 40, step=0.0004, seed=3)
    day = raw.index[-1].tz_convert(NY).normalize()
    at = lambda h, m: (day + pd.Timedelta(hours=h, minutes=m)).tz_convert("UTC")  # noqa: E731
    upto = lambda t: raw[raw.index + pd.Timedelta(minutes=10) <= t]  # noqa: E731
    broker = FakeBroker(equity=1000.0, day_trades=2)
    trader, clock = _trader(tmp_path, "AAA", "stock", {"AAA": upto(at(13, 30))}, broker)
    clock["now"] = at(13, 30) + pd.Timedelta(seconds=15)
    trader.cycle()
    assert broker.orders and broker.orders[0][:2] == ("AAA", "buy")  # the last day trade
    trade = trader.state.trades["AAA"]
    trade.until = (at(17, 0)).isoformat()            # a long time limit: the bell comes first
    trade.take = trade.entry * 10                     # and a target out of reach
    trader.data.frames["AAA"] = upto(at(15, 50))
    clock["now"] = at(15, 50) + pd.Timedelta(seconds=15)
    trader.cycle()
    assert not trader.state.trades and "day's close" in trader.state.log[-1]["what"]
    # all three day trades used: no new stock trades
    broker2 = FakeBroker(equity=1000.0, day_trades=3)
    (tmp_path / "small").mkdir()
    trader2, clock2 = _trader(tmp_path / "small", "AAA", "stock", {"AAA": upto(at(13, 30))},
                              broker2)
    clock2["now"] = at(13, 30) + pd.Timedelta(seconds=15)
    trader2.cycle()
    assert not broker2.orders
    assert any("no day trades left" in e["what"] for e in trader2.state.log)


def test_real_money_needs_permission_and_a_proven_setup(tmp_path):
    raw = _rising_crypto(24 * 12)
    broker = FakeBroker()
    broker.base_url = "https://api.alpaca.markets"
    with pytest.raises(SystemExit):
        _trader(tmp_path, "BTC/USD", "crypto", {"BTC/USD": raw}, broker)
    trader, clock = _trader(tmp_path, "BTC/USD", "crypto", {"BTC/USD": raw}, FakeBroker(),
                            proven=False)
    trader.real_money = True                          # as if allowed in the config
    clock["now"] = raw.index[-1] + pd.Timedelta(minutes=10, seconds=15)
    trader.cycle()
    assert not trader.broker.orders                   # not proven: no real money


def test_jarvis_page_shows_the_champion_setups(tmp_path, monkeypatch):
    from investment_bot.jarvis_status import build_status

    monkeypatch.chdir(tmp_path)
    setup = Setup(TREND, confidence=0.55, model=[0.1, 0.4, 0.9, 0.0])
    (tmp_path / "champ.json").write_text(json.dumps({"classes": {"crypto": {"champion": {
        "setup": setup.to_dict(), "proven": False,
        "checks": {"made money on test": False, "enough trades": True},
        "metrics": {"test": {"total_return": -0.01}, "final": {"total_return": 0.02}}}}}}))
    (tmp_path / "lab.json").write_text(json.dumps({"champion": {"package": {
        "weights": {"rsi_14@1h": 1.0}, "threshold": 0.3, "size": 0.1}}}))
    out = build_status(BotConfig(raw={"champ": {"results_file": "champ.json"}}))
    assert out["Crypto champion"].startswith("not proven")
    assert out["Crypto: not proven because"] == "made money on test"
    assert out["Crypto final check %"] == 2.0
    assert {r["Chart"] for r in out["Champion charts"]} == {"4h", "1h", "30m"}
    assert "Champion package" not in out  # the older lab's champion is no longer shown
