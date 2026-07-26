import numpy as np
import pandas as pd

from investment_bot import indicators as ind


def test_sma_matches_manual(ohlcv):
    got = ind.sma(ohlcv["close"], 10)
    expected = ohlcv["close"].rolling(10).mean()
    pd.testing.assert_series_equal(got, expected, check_names=False)


def test_ema_warmup_is_nan(ohlcv):
    e = ind.ema(ohlcv["close"], 20)
    assert e.iloc[:19].isna().all()
    assert e.iloc[25:].notna().all()


def test_rsi_bounds(ohlcv):
    r = ind.rsi(ohlcv["close"], 14).dropna()
    assert len(r) > 0
    assert (r >= 0).all() and (r <= 100).all()


def test_rsi_all_gains_is_100():
    close = pd.Series(np.arange(1.0, 60.0))
    r = ind.rsi(close, 14)
    assert r.iloc[-1] == 100.0


def test_macd_hist_is_macd_minus_signal(ohlcv):
    m = ind.macd(ohlcv["close"]).dropna()
    np.testing.assert_allclose(m["hist"], m["macd"] - m["signal"])


def test_bollinger_band_ordering(ohlcv):
    b = ind.bollinger(ohlcv["close"]).dropna()
    assert (b["upper"] >= b["mid"]).all()
    assert (b["mid"] >= b["lower"]).all()


def test_atr_positive(ohlcv):
    a = ind.atr(ohlcv["high"], ohlcv["low"], ohlcv["close"]).dropna()
    assert (a > 0).all()


def test_true_range_at_least_high_minus_low(ohlcv):
    tr = ind.true_range(ohlcv["high"], ohlcv["low"], ohlcv["close"]).dropna()
    hl = (ohlcv["high"] - ohlcv["low"]).loc[tr.index]
    assert (tr >= hl - 1e-9).all()


def test_donchian_contains_price(ohlcv):
    d = ind.donchian(ohlcv["high"], ohlcv["low"], 20).dropna()
    highs = ohlcv["high"].loc[d.index]
    assert (d["upper"] >= highs - 1e-9).all()


def test_zscore_zero_mean_series():
    z = ind.zscore(pd.Series(np.ones(50)), 20)
    # Constant series: std = 0 -> NaN, never inf.
    assert z.dropna().empty
