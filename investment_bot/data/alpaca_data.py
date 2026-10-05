"""Alpaca market data: intraday and daily candles for stocks and crypto.

    data = AlpacaData()                      # keys from .env / the environment
    data.bars("AAPL", "10Min", days=730)     # cached on disk, topped up after
    data.bars("BTC/USD", "1Day", days=2500)

Stocks need the account's key and secret; crypto candles are public. On the
free plan the full-market ("sip") feed is only available 15 minutes late, so
the default is the "iex" feed: real time, which is what trading every 10
minutes needs, and the same feed the lab learns on. With a paid data plan set
``lab.feed: sip``.

Candles are cached per feed, symbol and timeframe
(``<cache_dir>/<feed or crypto>/<tf>/<SYMBOL>.pkl``,
pickle because pyarrow isn't installed) and later calls only download what
is new since the last cached candle. Stock candles are trimmed to regular
trading hours (9:30-16:00 New York): extended-hours prints are thin and the
bot doesn't trade then.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

DATA_URL = "https://data.alpaca.markets"
PAPER_URL = "https://paper-api.alpaca.markets"
COLUMNS = ["open", "high", "low", "close", "volume"]
NY = "America/New_York"


def load_env(path: str | Path = ".env") -> None:
    """Read KEY=value lines from .env into the environment (real env vars win)."""
    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
    except ImportError:  # python-dotenv is a dependency, but don't depend on it here
        file = Path(path)
        if not file.exists():
            return
        for line in file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def is_crypto(symbol: str) -> bool:
    return "/" in symbol


class AlpacaDataError(RuntimeError):
    pass


class AlpacaData:
    def __init__(
        self,
        cache_dir: str | Path = "data_cache/alpaca",
        feed: str = "iex",
        session: Any = None,
        say: Callable[[str], None] = lambda _line: None,
    ):
        self.cache_dir = Path(cache_dir)
        self.feed = feed
        self.key = os.environ.get("ALPACA_API_KEY", "")
        self.secret = os.environ.get("ALPACA_SECRET_KEY", "")
        self.trading_url = os.environ.get("ALPACA_BASE_URL", PAPER_URL).rstrip("/")
        self.say = say
        if session is None:
            import requests

            session = requests.Session()
        self.http = session

    @property
    def has_keys(self) -> bool:
        return bool(self.key and self.secret)

    # ------------------------------------------------------------ http

    def _headers(self) -> dict[str, str]:
        if not self.has_keys:
            return {}
        return {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}

    def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(6):
            resp = self.http.get(url, params=params, headers=self._headers(), timeout=60)
            if resp.status_code == 429:  # rate limited (200 calls a minute on free plans)
                time.sleep(min(2 ** attempt, 30))
                continue
            if resp.status_code in (500, 502, 503, 504):
                time.sleep(2 + attempt * 2)
                continue
            if resp.status_code == 403 and "subscription" in resp.text:
                raise AlpacaDataError(f"Your Alpaca data plan doesn't allow this: {resp.text[:200]}")
            if resp.status_code in (401, 403):
                raise AlpacaDataError(
                    f"Alpaca refused the request ({resp.status_code}): {resp.text[:200]}. "
                    "Check ALPACA_API_KEY and ALPACA_SECRET_KEY in .env."
                )
            if resp.status_code >= 400:
                raise AlpacaDataError(f"Alpaca error {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        raise AlpacaDataError(f"Alpaca kept rate-limiting {url}; try again in a minute.")

    # ------------------------------------------------------------ candles

    def bars(self, symbol: str, timeframe: str, days: int, refresh: bool = True) -> pd.DataFrame:
        """Up to `days` calendar days of candles, ending now (cached)."""
        feed = "crypto" if is_crypto(symbol) else self.feed
        path = self.cache_dir / feed / timeframe / (symbol.replace("/", "-") + ".pkl")
        start = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
        cached = pd.read_pickle(path) if path.exists() else None
        if cached is not None and len(cached) and cached.index[0] > start + pd.Timedelta(days=7):
            cached = None  # the cache is shorter than asked for: fetch it all again
        if cached is None or not len(cached):
            frame = self._download(symbol, timeframe, start)
        elif refresh:
            fresh = self._download(symbol, timeframe, cached.index[-1] + pd.Timedelta(seconds=1))
            frame = pd.concat([cached, fresh])
            frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        else:
            frame = cached
        if cached is None or len(frame) != len(cached):
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                frame.to_pickle(path)
            except OSError:
                pass  # caching is best-effort
        return frame[frame.index >= start]

    def _download(self, symbol: str, timeframe: str, start: pd.Timestamp) -> pd.DataFrame:
        crypto = is_crypto(symbol)
        if not crypto and not self.has_keys:
            raise AlpacaDataError(
                "Stock candles need ALPACA_API_KEY and ALPACA_SECRET_KEY in .env "
                "(crypto candles work without them)."
            )
        url = f"{DATA_URL}/v1beta3/crypto/us/bars" if crypto else f"{DATA_URL}/v2/stocks/bars"
        params: dict[str, Any] = {
            "symbols": symbol,
            "timeframe": timeframe,
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 10000,
        }
        if not crypto:
            params.update(adjustment="all", feed=self.feed)
            if self.feed == "sip":  # the free plan only serves sip 15 minutes late
                end = pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=16)
                params["end"] = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        rows: list[dict[str, Any]] = []
        while True:
            try:
                payload = self.get(url, params)
            except AlpacaDataError as exc:
                if not crypto and self.feed == "sip" and "subscription" in str(exc).lower():
                    self.say("Alpaca: this plan has no full-market (sip) data; using iex.")
                    params.pop("end", None)
                    self.feed = params["feed"] = "iex"
                    continue
                raise
            rows += (payload.get("bars") or {}).get(symbol) or []
            token = payload.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        return self.to_frame(rows, regular_hours=not crypto and not timeframe.endswith("Day"))

    @staticmethod
    def to_frame(rows: list[dict[str, Any]], regular_hours: bool = False) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz="UTC"), dtype=float)
        frame = pd.DataFrame(rows)
        frame.index = pd.to_datetime(frame["t"], utc=True)
        frame = frame.rename(
            columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
        )[COLUMNS].astype(float)
        if regular_hours:
            local = frame.index.tz_convert(NY)
            minutes = local.hour * 60 + local.minute
            frame = frame[(minutes >= 9 * 60 + 30) & (minutes < 16 * 60)]
        return frame.sort_index()

    # ------------------------------------------------------------ what can be traded

    def stock_assets(self) -> list[dict[str, Any]]:
        if not self.has_keys:
            raise AlpacaDataError("Listing stocks needs the Alpaca key and secret in .env.")
        return self.get(f"{self.trading_url}/v2/assets",
                        {"status": "active", "asset_class": "us_equity"})

    def crypto_assets(self) -> list[dict[str, Any]]:
        if self.has_keys:
            return self.get(f"{self.trading_url}/v2/assets",
                            {"status": "active", "asset_class": "crypto"})
        # Without keys: the pairs Alpaca quotes against USD, from public data.
        return [{"symbol": s, "tradable": True, "class": "crypto"} for s in self.crypto_symbols()]

    def crypto_symbols(self) -> list[str]:
        payload = self.get(f"{DATA_URL}/v1beta3/crypto/us/latest/quotes",
                           {"symbols": ",".join(f"{c}/USD" for c in KNOWN_CRYPTO)})
        return sorted((payload.get("quotes") or {}).keys())

    def snapshots(self, symbols: list[str]) -> dict[str, Any]:
        """Latest daily candle per symbol, in batches (for ranking by trading volume).

        Always from the iex feed: it's free and current, and ranking only needs
        volumes relative to each other."""
        out: dict[str, Any] = {}
        stocks = [s for s in symbols if not is_crypto(s)]
        crypto = [s for s in symbols if is_crypto(s)]
        for i in range(0, len(stocks), 200):
            out.update(self.get(f"{DATA_URL}/v2/stocks/snapshots",
                                {"symbols": ",".join(stocks[i:i + 200]), "feed": "iex"}) or {})
        for i in range(0, len(crypto), 100):
            payload = self.get(f"{DATA_URL}/v1beta3/crypto/us/snapshots",
                               {"symbols": ",".join(crypto[i:i + 100])})
            out.update(payload.get("snapshots") or {})
        return out


# Coins Alpaca has listed against USD; only used to discover pairs without keys.
KNOWN_CRYPTO = [
    "AAVE", "AVAX", "BAT", "BCH", "BTC", "CRV", "DOGE", "DOT", "ETH", "GRT", "LINK", "LTC",
    "MKR", "PEPE", "SHIB", "SOL", "SUSHI", "TRUMP", "UNI", "USDC", "USDT", "XRP", "XTZ", "YFI",
]
