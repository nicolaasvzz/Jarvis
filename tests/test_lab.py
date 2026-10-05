import json

import numpy as np
import pandas as pd
import pytest

from investment_bot.config import BotConfig
from investment_bot.data.alpaca_data import AlpacaData
from investment_bot.lab import lab as lab_module
from investment_bot.lab.catalog import CATALOG, FAMILIES, compute
from investment_bot.lab.features import FeatureStore, multi_timeframe, resample
from investment_bot.lab.lab import Lab, knob_changes
from investment_bot.lab.package import Package, Trades, portfolio, simulate_symbol
from investment_bot.lab.prepare import LabConfig, prepare, scan_universe, synthetic_bars
from investment_bot.lab.trader import PackageTrader


def intraday(days: int = 60, seed: int = 3) -> pd.DataFrame:
    return synthetic_bars("TST", LabConfig(intraday_days=days, seed=seed))


# ------------------------------------------------------------------ indicators


def test_every_family_has_indicators_and_votes_stay_in_range():
    assert {i.family for i in CATALOG.values()} == set(FAMILIES)
    assert len(CATALOG) >= 120
    votes = compute(intraday(80))
    assert list(votes.columns) == list(CATALOG)
    values = votes.to_numpy()
    assert np.nanmax(values) <= 1.0 and np.nanmin(values) >= -1.0
    assert votes.iloc[-1].notna().mean() > 0.95  # nearly everything warmed up by the end


def test_indicators_never_look_ahead():
    """Changing future candles must not change any earlier vote."""
    df = intraday(40)
    cut = len(df) - 50
    changed = df.copy()
    changed.iloc[cut:, :4] *= 1.5
    a = compute(df).iloc[:cut]
    b = compute(changed).iloc[:cut]
    np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), equal_nan=True, atol=1e-6)


def test_short_or_flat_data_does_not_crash():
    flat = pd.DataFrame({c: np.full(30, 10.0) for c in ("open", "high", "low", "close", "volume")},
                        index=pd.date_range("2026-01-01", periods=30, freq="10min", tz="UTC"))
    out = compute(flat)
    assert out.shape == (30, len(CATALOG))


def test_bigger_candles_only_count_once_closed():
    df = intraday(30)
    cut = len(df) - 80
    changed = df.copy()
    changed.iloc[cut:, :4] *= 0.5
    names = ["rsi_14", "ema_cross_9_21", "engulfing"]
    a = multi_timeframe(df, ["10m", "1h", "4h", "1d"], names=names).iloc[:cut]
    b = multi_timeframe(changed, ["10m", "1h", "4h", "1d"], names=names).iloc[:cut]
    np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), equal_nan=True, atol=1e-6)
    # and a 1h value appears on the 10m candle that closes the hour, not before
    hourly = compute(resample(df, "1h"), ["rsi_14"])["rsi_14"]
    first_hour = hourly.dropna().index[0]
    close_row = df.index.get_loc(first_hour + pd.Timedelta(minutes=50))
    assert np.isnan(a["rsi_14@1h"].iloc[close_row - 1]) or a["rsi_14@1h"].iloc[close_row - 1] != \
        pytest.approx(float(hourly.loc[first_hour]))
    assert a["rsi_14@1h"].iloc[close_row] == pytest.approx(float(hourly.loc[first_hour]), abs=1e-5)


# ------------------------------------------------------------------ simulator


def _arrays(close: np.ndarray, spread: float = 0.002):
    n = len(close)
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    atr = np.full(n, close[0] * 0.01)
    times = np.arange(n, dtype="int64") * 600_000_000_000
    return open_, high, low, close, atr, times


def test_enters_next_open_and_exits_when_the_signal_fades():
    close = np.linspace(100, 110, 40)
    score = np.zeros(40)
    score[5:15] = 0.8
    pkg = Package({"rsi_14@10m": 1.0}, threshold=0.5, stop_atr=50)
    t = simulate_symbol(pkg, score, *_arrays(close), can_short=True, cost=0.0)
    assert len(t) == 1
    open_ = _arrays(close)[0]
    assert t.direction[0] == 1
    expected = open_[16] / open_[6] - 1  # in at the open after candle 5, out after candle 15
    assert t.ret[0] == pytest.approx(expected)


def test_trailing_stop_and_cooldown_and_confirm():
    close = np.concatenate([np.linspace(100, 120, 20), np.linspace(120, 90, 20)])
    score = np.full(40, 0.9)
    pkg = Package({"x@10m": 1.0}, threshold=0.5, stop_atr=2.0, cooldown=100)
    t = simulate_symbol(pkg, score, *_arrays(close), can_short=True, cost=0.0)
    assert len(t) == 1 and t.reason[0] == 0  # stopped out, then the cooldown outlasts the data
    assert t.ret[0] > 0  # the stop trailed up behind the rise
    # a signal that must hold 3 candles enters 2 candles later than one that needn't
    score = np.zeros(40)
    score[10:30] = 0.9
    one = simulate_symbol(Package({"x@10m": 1.0}, threshold=0.5, stop_atr=50), score,
                          *_arrays(np.full(40, 100.0)), can_short=True, cost=0.0)
    three = simulate_symbol(Package({"x@10m": 1.0}, threshold=0.5, stop_atr=50, confirm=3),
                            score, *_arrays(np.full(40, 100.0)), can_short=True, cost=0.0)
    step = 600_000_000_000
    assert (three.entry_time[0] - one.entry_time[0]) == 2 * step


def test_no_shorts_where_shorting_isnt_allowed():
    score = np.full(30, -0.9)
    t = simulate_symbol(Package({"x@10m": 1.0}, threshold=0.5), score,
                        *_arrays(np.linspace(100, 90, 30)), can_short=False, cost=0.0)
    assert len(t) == 0


def test_portfolio_never_passes_full_exposure():
    trades = Trades(entry_time=np.array([0, 0, 0]), exit_time=np.array([10, 10, 10]),
                    direction=np.array([1, 1, 1], dtype="int8"),
                    fraction=np.array([0.5, 0.4, 0.3]), ret=np.array([0.1, 0.1, 0.1]),
                    bars=np.array([1, 1, 1], dtype="int32"), reason=np.zeros(3, dtype="int8"),
                    symbol=["A", "B", "C"])
    taken, m = portfolio(trades)
    assert len(taken) == 2 and m["num_trades"] == 2


def test_knob_changes_include_slowing_down():
    labels = [label for _, label in knob_changes(Package({"x@10m": 1.0}))]
    assert any("wait" in label for label in labels)
    assert any("hold at least" in label for label in labels)
    assert any("must hold" in label for label in labels)


# ------------------------------------------------------------------ the lab end to end


def _config(tmp_path, **lab) -> BotConfig:
    raw = {"lab": {"source": "synthetic", "intraday_days": 120, "synthetic_symbols": 4,
                   "workers": 2, "feature_dir": str(tmp_path / "features"),
                   "results_file": str(tmp_path / "lab.json"),
                   "universe_file": str(tmp_path / "universe.json"),
                   "timeframes": ["10m", "1h", "1d"], **lab}}
    return BotConfig(raw=raw)


def test_lab_rounds_scout_then_patterns_then_build_then_challenge(tmp_path, monkeypatch):
    monkeypatch.setattr(lab_module, "MIN_LEFT", 0)
    config = _config(tmp_path)
    store, universe = prepare(config, say=lambda _: None)
    assert len(universe) == 4 and len(store.columns) == 3 * len(CATALOG)
    lab = Lab(LabConfig.from_config(config), store, universe, seconds=75, round_seconds=10,
              say=lambda _: None, seed=1, state_file=tmp_path / "session.json")
    lab.run()
    data = json.loads((tmp_path / "lab.json").read_text())
    kinds = [r["kind"] for r in data["rounds"]]
    assert kinds[:4] == ["scout", "patterns", "build", "challenge"]
    assert len(data["scoreboard"]) == len(store.columns)
    champ = Package.from_dict(data["champion"]["package"])
    assert set(champ.families) == set(FAMILIES)  # at least one from every family
    assert len(champ.weights) <= 50
    assert {"train", "hold", "final"} <= set(data["champion"]["metrics"])
    assert json.loads((tmp_path / "session.json").read_text())["status"] == "finished"


# ------------------------------------------------------------------ Alpaca plumbing


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.text = payload, status, json.dumps(payload)

    def json(self):
        return self.payload


class FakeHTTP:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return FakeResponse(self.pages.pop(0))


def test_alpaca_bars_page_and_keep_regular_hours(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    bar = lambda t: {"t": t, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}  # noqa: E731
    http = FakeHTTP([
        {"bars": {"AAPL": [bar("2026-03-02T13:00:00Z"), bar("2026-03-02T14:30:00Z")]},
         "next_page_token": "x"},
        {"bars": {"AAPL": [bar("2026-03-02T15:00:00Z"), bar("2026-03-02T21:30:00Z")]},
         "next_page_token": None},
    ])
    data = AlpacaData(tmp_path, session=http)
    frame = data._download("AAPL", "10Min", pd.Timestamp("2026-03-01", tz="UTC"))
    # 13:00 UTC (8:00 New York) and 21:30 UTC (16:30) are outside market hours
    assert [t.strftime("%H:%M") for t in frame.index] == ["14:30", "15:00"]
    assert http.calls[1][1]["page_token"] == "x"


class FakeData:
    has_keys = True

    def __init__(self, bars=None):
        self._bars = bars

    def stock_assets(self):
        return [{"symbol": s, "tradable": True, "exchange": "NASDAQ", "shortable": True,
                 "easy_to_borrow": True, "fractionable": True} for s in ("AAA", "BBB", "CCC")] + [
            {"symbol": "BRK.B", "tradable": True, "exchange": "NYSE"}]

    def crypto_assets(self):
        return [{"symbol": "BTC/USD", "tradable": True}, {"symbol": "USDT/USD", "tradable": True}]

    def snapshots(self, symbols):
        price = {"AAA": (50, 1e6), "BBB": (3, 1e9), "CCC": (20, 3e6), "BTC/USD": (60000, 10)}
        return {s: {"prevDailyBar": {"c": price[s][0], "v": price[s][1]}}
                for s in symbols if s in price}

    def bars(self, symbol, timeframe, days):
        return self._bars if timeframe != "1Day" else None


def test_universe_ranks_by_dollar_volume_and_skips_cheap_stocks(tmp_path):
    cfg = LabConfig(stocks=5, crypto=5, universe_file=str(tmp_path / "u.json"))
    picked = [u["symbol"] for u in scan_universe(FakeData(), cfg, lambda _: None)]
    assert picked == ["CCC", "AAA", "BTC/USD"]  # BBB is under $5; no stablecoins, no BRK.B


class FakeBroker:
    base_url = "https://paper-api.alpaca.markets"

    def __init__(self):
        self.orders, self.held = [], {}

    def account(self):
        return {"equity": "100000"}

    def positions(self):
        return [{"symbol": s, "qty": str(q), "market_value": str(q * 100)}
                for s, q in self.held.items()]

    def clock(self):
        return {"is_open": True}

    def place(self, symbol, side, qty, crypto):
        self.orders.append((symbol, side, qty))
        self.held[symbol] = self.held.get(symbol, 0) + (qty if side == "buy" else -qty)
        if self.held[symbol] == 0:
            self.held.pop(symbol)


def test_trader_enters_on_the_package_and_exits_on_its_stop(tmp_path):
    config = _config(tmp_path, timeframes=["10m", "1h"])
    cfg = LabConfig.from_config(config)
    pkg = Package({"rsi_2@10m": 1.0, "close_location@10m": 1.0}, threshold=0.05, stop_atr=1.0)
    (tmp_path / "lab.json").write_text(json.dumps({"champion": {"package": pkg.to_dict()}}))
    (tmp_path / "universe.json").write_text(json.dumps({"symbols": [
        {"symbol": "TST", "class": "stock", "shortable": True, "fractionable": True}]}))
    n = 300
    close = np.linspace(100, 130, n)
    index = pd.date_range("2026-03-02 14:30", periods=n, freq="10min", tz="UTC")
    bars = pd.DataFrame({"open": close - 0.05, "high": close + 0.2, "low": close - 0.2,
                         "close": close, "volume": 1000.0}, index=index)
    clock = {"now": index[-1] + pd.Timedelta(minutes=10)}
    data = FakeData(bars)
    broker = FakeBroker()
    trader = PackageTrader(config, broker, data, say=lambda _: None,
                           state_file=tmp_path / "trader.json", now=lambda: clock["now"])
    trader.cycle()
    assert broker.orders and broker.orders[0][:2] == ("TST", "buy")
    assert "TST" in trader.state.holdings
    # next candle crashes through the stop
    crash = pd.DataFrame({"open": [130.0], "high": [130.0], "low": [100.0], "close": [101.0],
                          "volume": [1000.0]}, index=[index[-1] + pd.Timedelta(minutes=10)])
    data._bars = pd.concat([bars, crash])
    clock["now"] += pd.Timedelta(minutes=10)
    trader.cycle()
    assert broker.orders[-1][:2] == ("TST", "sell")
    assert "TST" not in trader.state.holdings
    assert cfg.results_file.endswith("lab.json")


def test_trader_refuses_real_money_unless_allowed(tmp_path):
    broker = FakeBroker()
    broker.base_url = "https://api.alpaca.markets"
    with pytest.raises(SystemExit):
        PackageTrader(_config(tmp_path), broker, FakeData(), say=lambda _: None,
                      state_file=tmp_path / "t.json")


def test_feature_store_roundtrip(tmp_path):
    df = intraday(20)
    store = FeatureStore(tmp_path)
    feats = multi_timeframe(df, ["10m", "1h"], names=["rsi_14", "gap"])
    from investment_bot.lab.features import base_bars

    store.save("BTC/USD", base_bars(df), feats, "stamp")
    assert store.symbols() == ["BTC/USD"]
    got = store.load("BTC/USD", ["gap@1h", "rsi_14@10m"])
    np.testing.assert_allclose(got[:, 1], feats["rsi_14@10m"].to_numpy(), atol=2e-3,
                               equal_nan=True)
