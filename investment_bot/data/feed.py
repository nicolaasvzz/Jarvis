"""Data feeds: uniform OHLCV access across synthetic, CSV, and Yahoo Finance.

Every feed returns a per-symbol DataFrame with columns
[open, high, low, close, volume] and a sorted DatetimeIndex.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd

from .synthetic import generate_ohlcv

REQUIRED_COLS = ["open", "high", "low", "close", "volume"]


class DataFeed(ABC):
    """Abstract source of historical OHLCV bars."""

    @abstractmethod
    def history(self, symbol: str, days: int) -> pd.DataFrame:
        """Return up to `days` most-recent daily bars for `symbol`."""

    def latest(self, symbol: str, lookback: int = 300) -> pd.DataFrame:
        """History window ending at the most recent available bar."""
        return self.history(symbol, lookback)

    @staticmethod
    def _validate(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        missing = [c for c in REQUIRED_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"{symbol}: feed missing columns {missing}")
        df = df[REQUIRED_COLS].dropna().sort_index()
        if df.empty:
            raise ValueError(f"{symbol}: feed returned no rows")
        return df


class SyntheticFeed(DataFeed):
    """Deterministic regime-switching random market — no network needed."""

    def __init__(self, seed: int = 42, total_days: int = 2520):
        self.seed = seed
        self.total_days = total_days
        self._cache: dict[str, pd.DataFrame] = {}

    def history(self, symbol: str, days: int) -> pd.DataFrame:
        if symbol not in self._cache:
            self._cache[symbol] = generate_ohlcv(
                symbol, days=self.total_days, seed=self.seed
            )
        return self._validate(self._cache[symbol].tail(days), symbol)


class CSVFeed(DataFeed):
    """Reads `<directory>/<SYMBOL>.csv` with date + OHLCV columns."""

    def __init__(self, directory: str):
        self.directory = Path(directory)

    def history(self, symbol: str, days: int) -> pd.DataFrame:
        path = self.directory / f"{symbol}.csv"
        if not path.exists():
            raise FileNotFoundError(f"No data file for {symbol} at {path}")
        df = pd.read_csv(path)
        date_col = next(
            (c for c in df.columns if c.lower() in ("date", "time", "timestamp", "datetime")),
            df.columns[0],
        )
        df[date_col] = pd.to_datetime(df[date_col])
        df = df.set_index(date_col)
        df.columns = [c.lower() for c in df.columns]
        return self._validate(df, symbol).tail(days)


class YahooFeed(DataFeed):
    """Daily bars from Yahoo Finance's public chart API (no API key).

    Results are cached on disk so repeated backtests don't hammer the API.
    """

    CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    def __init__(self, cache_dir: str = "data_cache", cache_ttl_hours: float = 12.0):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_seconds = cache_ttl_hours * 3600

    def history(self, symbol: str, days: int) -> pd.DataFrame:
        cache_file = self.cache_dir / f"{symbol.upper()}.parquet"
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < self.cache_ttl_seconds:
                cached = pd.read_parquet(cache_file)
                if len(cached) >= days:
                    return self._validate(cached, symbol).tail(days)

        df = self._fetch(symbol, days)
        try:
            df.to_parquet(cache_file)
        except (ImportError, OSError):
            pass  # parquet engine or disk unavailable — caching is best-effort
        return self._validate(df, symbol).tail(days)

    def _fetch(self, symbol: str, days: int) -> pd.DataFrame:
        import requests

        # Fetch extra history to cover non-trading days.
        span_days = max(int(days * 1.6), 30)
        params = {
            "range": f"{span_days}d" if span_days <= 365 else f"{span_days // 365 + 1}y",
            "interval": "1d",
            "events": "div,splits",
        }
        resp = requests.get(
            self.CHART_URL.format(symbol=symbol),
            params=params,
            headers={"User-Agent": "Mozilla/5.0 (investment-bot)"},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()["chart"]
        if payload.get("error"):
            raise RuntimeError(f"Yahoo error for {symbol}: {payload['error']}")
        result = payload["result"][0]
        quote = result["indicators"]["quote"][0]
        df = pd.DataFrame(
            {
                "open": quote["open"],
                "high": quote["high"],
                "low": quote["low"],
                "close": quote["close"],
                "volume": quote["volume"],
            },
            index=pd.to_datetime(result["timestamp"], unit="s", utc=True).tz_convert(None).normalize(),
        )
        # Use split/dividend-adjusted closes when available, scaling OHLC to match.
        adjclose = result["indicators"].get("adjclose")
        if adjclose:
            adj = pd.Series(adjclose[0]["adjclose"], index=df.index)
            factor = (adj / df["close"]).fillna(1.0)
            for col in ("open", "high", "low", "close"):
                df[col] = df[col] * factor
        return df


def make_feed(source: str, **kwargs) -> DataFeed:
    """Factory: build a feed from a config `source` string."""
    source = source.lower()
    if source == "synthetic":
        allowed = {k: v for k, v in kwargs.items() if k in ("seed", "total_days")}
        return SyntheticFeed(**allowed)
    if source == "csv":
        return CSVFeed(kwargs.get("directory", "data"))
    if source == "yahoo":
        allowed = {k: v for k, v in kwargs.items() if k in ("cache_dir", "cache_ttl_hours")}
        return YahooFeed(**allowed)
    raise ValueError(f"Unknown data source: {source!r} (expected synthetic, csv, or yahoo)")
