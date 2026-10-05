"""Every indicator on every candle size, lined up on the smallest candle.

The bot steps through history on its smallest candle (10 minutes). For each
of those it knows every indicator on every timeframe, as a column named
``<indicator>@<tf>`` (``rsi_14@4h``). A bigger candle's value only appears
once that candle has *closed*: the 4h RSI for 12:00-16:00 is first visible
on the 10-minute candle that closes at 16:00. Nothing peeks ahead.

Intraday timeframes are built from the 10-minute candles; daily candles come
from their own, longer download when there is one.

The result for each symbol goes to a ``FeatureStore`` on disk: float16
(every vote is in [-1, 1], so three decimals is plenty, at half the space),
column-major, memory-mapped, so a backtest that needs 12 columns reads 12
columns and not 650.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .. import indicators as ind
from .catalog import CATALOG, compute

TIMEFRAMES: dict[str, pd.Timedelta] = {
    "1m": pd.Timedelta(minutes=1),
    "5m": pd.Timedelta(minutes=5),
    "10m": pd.Timedelta(minutes=10),
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
    "2h": pd.Timedelta(hours=2),
    "4h": pd.Timedelta(hours=4),
    "1d": pd.Timedelta(days=1),
}
ALPACA_TF = {"1m": "1Min", "5m": "5Min", "10m": "10Min", "15m": "15Min", "30m": "30Min",
             "1h": "1Hour", "2h": "2Hour", "4h": "4Hour", "1d": "1Day"}


def ns(index: pd.DatetimeIndex) -> np.ndarray:
    """Timestamps as int64 nanoseconds (pandas 3 may store them in other units)."""
    return index.as_unit("ns").asi8


def duration(tf: str) -> pd.Timedelta:
    if tf not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe {tf!r}; use one of {', '.join(TIMEFRAMES)}")
    return TIMEFRAMES[tf]


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Bigger candles from smaller ones, labelled by their start."""
    out = df.resample(duration(tf), label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["close"])


def multi_timeframe(
    base: pd.DataFrame,
    timeframes: list[str],
    daily: pd.DataFrame | None = None,
    names: list[str] | dict[str, list[str]] | None = None,
) -> pd.DataFrame:
    """All indicators on all timeframes, aligned to `base`'s candles.

    `base` is the smallest timeframe (the first of `timeframes`), indexed by
    candle start. Row t holds what is known when candle t closes. `names`
    limits the indicators: one list for every timeframe, or {tf: [names]}.
    """
    base_tf = timeframes[0]
    decided = base.index + duration(base_tf)  # when each base candle closes
    parts = []
    for tf in timeframes:
        wanted = names.get(tf) if isinstance(names, dict) else names
        if isinstance(names, dict) and not wanted:
            continue
        if tf == base_tf:
            frame = base
        elif tf == "1d" and daily is not None and len(daily):
            frame = daily
        else:
            frame = resample(base, tf)
        values = compute(frame, wanted)
        if tf == base_tf:
            aligned = values
        else:
            known = frame.index + duration(tf)
            pos = np.searchsorted(ns(known), ns(decided), side="right") - 1
            arr = values.to_numpy()
            out = np.full((len(base), arr.shape[1]), np.nan, dtype=np.float32)
            ok = pos >= 0
            out[ok] = arr[pos[ok]]
            aligned = pd.DataFrame(out, index=base.index, columns=values.columns)
        aligned.columns = [f"{c}@{tf}" for c in aligned.columns]
        parts.append(aligned)
    return pd.concat(parts, axis=1) if parts else pd.DataFrame(index=base.index)


def columns_by_tf(features: list[str]) -> dict[str, list[str]]:
    """['rsi_14@1h', 'cci_20@1h', 'gap@1d'] -> {'1h': ['rsi_14', 'cci_20'], '1d': ['gap']}"""
    out: dict[str, list[str]] = {}
    for f in features:
        name, tf = f.split("@", 1)
        out.setdefault(tf, []).append(name)
    return out


def all_columns(timeframes: list[str], names: list[str] | None = None) -> list[str]:
    return [f"{n}@{tf}" for tf in timeframes for n in (names or list(CATALOG))]


def base_bars(base: pd.DataFrame) -> pd.DataFrame:
    """What the simulator needs per base candle: prices plus ATR(14) for stops."""
    out = base[["open", "high", "low", "close"]].astype(float).copy()
    out["atr"] = ind.atr(out["high"], out["low"], out["close"], 14)
    return out


class FeatureStore:
    """Per-symbol feature matrices on disk: ``<dir>/<SYMBOL>/{feat.npy,bars.pkl}``."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._columns: list[str] | None = None
        self._index: dict[str, int] = {}

    @staticmethod
    def key(symbol: str) -> str:
        return symbol.replace("/", "-")

    @property
    def columns(self) -> list[str]:
        if self._columns is None:
            path = self.dir / "columns.json"
            self._columns = json.loads(path.read_text()) if path.exists() else []
            self._index = {c: i for i, c in enumerate(self._columns)}
        return self._columns

    def col_index(self, names: list[str]) -> list[int]:
        _ = self.columns
        return [self._index[n] for n in names]

    def save(self, symbol: str, bars: pd.DataFrame, features: pd.DataFrame, stamp: str) -> None:
        cols = list(features.columns)
        if self.columns and self.columns != cols:
            raise ValueError("feature columns changed; clear the feature folder and rebuild")
        if not self.columns:
            (self.dir / "columns.json").write_text(json.dumps(cols))
            self._columns, self._index = cols, {c: i for i, c in enumerate(cols)}
        folder = self.dir / self.key(symbol)
        folder.mkdir(exist_ok=True)
        matrix = np.asfortranarray(features.to_numpy(dtype=np.float16))
        np.save(folder / "feat.tmp.npy", matrix)
        (folder / "feat.tmp.npy").replace(folder / "feat.npy")
        bars = bars.copy()
        bars.index = bars.index.as_unit("ns")
        bars.to_pickle(folder / "bars.pkl")
        (folder / "stamp.txt").write_text(stamp)
        (folder / "symbol.txt").write_text(symbol)

    def stamp(self, symbol: str) -> str:
        path = self.dir / self.key(symbol) / "stamp.txt"
        return path.read_text() if path.exists() else ""

    def symbols(self) -> list[str]:
        out = []
        for folder in sorted(self.dir.iterdir()) if self.dir.exists() else []:
            if (folder / "feat.npy").exists() and (folder / "symbol.txt").exists():
                out.append((folder / "symbol.txt").read_text().strip())
        return out

    def bars(self, symbol: str) -> pd.DataFrame:
        return pd.read_pickle(self.dir / self.key(symbol) / "bars.pkl")

    def matrix(self, symbol: str) -> np.ndarray:
        """The whole feature matrix, memory-mapped (rows x columns, float16)."""
        return np.load(self.dir / self.key(symbol) / "feat.npy", mmap_mode="r")

    def load(self, symbol: str, names: list[str]) -> np.ndarray:
        """Just these columns, as float32 (rows x len(names))."""
        m = self.matrix(symbol)
        return np.asarray(m[:, self.col_index(names)], dtype=np.float32)

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.dir.rglob("*") if p.is_file())
