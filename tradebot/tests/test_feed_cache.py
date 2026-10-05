"""The Yahoo disk cache must work on a plain `pip install` (no parquet engine)."""
import pandas as pd

from investment_bot.data.feed import YahooFeed
from investment_bot.data.synthetic import generate_ohlcv


def _feed_with_fake_network(tmp_path, monkeypatch):
    frame = generate_ohlcv("AAPL", days=200, seed=1)
    calls: list[str] = []

    def fake_fetch(self, symbol, days):
        calls.append(symbol)
        return frame

    monkeypatch.setattr(YahooFeed, "_fetch", fake_fetch)
    return YahooFeed(cache_dir=str(tmp_path)), calls


def test_second_call_is_served_from_cache_and_matches(tmp_path, monkeypatch):
    feed, calls = _feed_with_fake_network(tmp_path, monkeypatch)
    first = feed.history("AAPL", 100)
    second = feed.history("AAPL", 100)
    assert calls == ["AAPL"]  # only one "download"
    pd.testing.assert_frame_equal(first, second, check_freq=False)


def test_corrupt_cache_falls_back_to_a_fresh_download(tmp_path, monkeypatch):
    feed, calls = _feed_with_fake_network(tmp_path, monkeypatch)
    (tmp_path / "AAPL.csv").write_bytes(b"\x00\x01 not,a\ncsv\xff")
    out = feed.history("AAPL", 100)
    assert calls == ["AAPL"]
    assert len(out) == 100
