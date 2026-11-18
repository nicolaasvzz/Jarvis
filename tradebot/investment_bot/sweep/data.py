"""Candles and symbol details from the MetaTrader 5 desktop terminal.

The terminal must be running and logged in. This module never logs in itself,
so no password ever passes through the bot. Candles are cached in
``data_cache/mt5/`` so a backtest doesn't refetch them.

MT5 candle times are the broker's server time. That's fine: every chart uses
the same clock.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

CACHE = Path("data_cache") / "mt5"
MAX_BARS = 99_000   # the terminal refuses more than its "max bars in chart" (100k)

# What the bot looks at: liquid forex, metals and indices on the MetaQuotes demo.
UNIVERSE = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "EURAUD", "USDZAR",
    "XAUUSD", "XAGUSD",
    "US500", "US30", "USTEC", "DE40", "UK100", "JPN225",
]


def _mt5():
    try:
        import MetaTrader5 as mt5  # Windows only
    except ImportError as e:
        raise RuntimeError("MetaTrader5 package missing: pip install MetaTrader5") from e
    return mt5


def connect():
    """Attach to the running terminal. Raises if it isn't running or logged in."""
    mt5 = _mt5()
    # The terminal serves one busy client at a time (a backtest downloading
    # history, say): give up after a minute instead of hanging.
    if not mt5.initialize(timeout=60_000):
        raise RuntimeError(f"MT5 terminal not reachable: {mt5.last_error()}. "
                           "Open MetaTrader 5 and log in to the demo account first "
                           "(and wait for a running sweep backtest to finish downloading).")
    if mt5.account_info() is None:
        raise RuntimeError("MT5 is open but not logged in to an account.")
    return mt5


def timeframe(mt5, tf: str) -> int:
    return {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1}[tf]


def rates(mt5, symbol: str, tf: str, count: int = MAX_BARS, include_open: bool = False) -> pd.DataFrame:
    """The latest ``count`` candles. The still-forming candle is dropped unless asked for."""
    mt5.symbol_select(symbol, True)
    r = None
    for _ in range(3):
        r = mt5.copy_rates_from_pos(symbol, timeframe(mt5, tf), 0, min(count, MAX_BARS))
        if r is not None and len(r):
            break
        time.sleep(1)
    if r is None or not len(r):
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "tick_volume", "spread"])
    df = pd.DataFrame(r)[["time", "open", "high", "low", "close", "tick_volume", "spread"]]
    df["time"] = df["time"].astype("int64")
    if not include_open:
        df = df.iloc[:-1]
    return df.reset_index(drop=True)


def cached(mt5, symbol: str, tf: str, refresh: bool = True) -> pd.DataFrame:
    """All the history the terminal gives, kept on disk and topped up."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}_{tf}.pkl"
    old = pd.read_pickle(path) if path.exists() else None
    if old is not None and not refresh:
        return old
    new = rates(mt5, symbol, tf) if mt5 is not None else None
    if new is None or not len(new):
        return old if old is not None else pd.DataFrame()
    if old is not None and len(old):
        new = pd.concat([old[old["time"] < new["time"].iat[0]], new]).reset_index(drop=True)
    new.to_pickle(path)
    return new


@dataclass
class Spec:
    symbol: str
    point: float
    digits: int
    contract: float
    vol_min: float
    vol_step: float
    vol_max: float
    tick_size: float
    tick_value: float
    stops_level: int

    def money_per_unit(self, lots: float) -> float:
        """Account money one whole price unit is worth at ``lots``."""
        return lots * self.tick_value / self.tick_size if self.tick_size else 0.0

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def spec(mt5, symbol: str) -> Spec | None:
    mt5.symbol_select(symbol, True)
    s = mt5.symbol_info(symbol)
    if s is None:
        return None
    sp = Spec(symbol, s.point, s.digits, s.trade_contract_size, s.volume_min, s.volume_step,
              s.volume_max, s.trade_tick_size, s.trade_tick_value, s.trade_stops_level)
    _save_spec(sp)
    return sp


SPECS = CACHE / "specs.json"


def _save_spec(sp: Spec) -> None:
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        known = json.loads(SPECS.read_text(encoding="utf-8")) if SPECS.exists() else {}
        known[sp.symbol] = sp.to_dict()
        SPECS.write_text(json.dumps(known, indent=1), encoding="utf-8")
    except (OSError, ValueError):
        pass


def offline_point(symbol: str) -> float:
    """The price step of a symbol, without the terminal: from the last time it was
    seen, or a good guess (it only converts the candles' spread to a price)."""
    try:
        known = json.loads(SPECS.read_text(encoding="utf-8"))
        if symbol in known:
            return float(known[symbol]["point"])
    except (OSError, ValueError):
        pass
    if symbol.endswith("JPY"):
        return 0.001
    if len(symbol) == 6 and symbol.isalpha() and not symbol.startswith(("XA", "XP")):
        return 0.00001
    return 0.01


def offline_symbols(tfs: set[str]) -> list[str]:
    """Symbols whose candles are all already in the cache for these charts."""
    return [s for s in UNIVERSE if all((CACHE / f"{s}_{tf}.pkl").exists() for tf in tfs)]
