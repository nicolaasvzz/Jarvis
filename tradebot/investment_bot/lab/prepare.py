"""Get the lab's data ready: which symbols, their candles, every indicator.

1. **Scan** (once a day): every stock and crypto pair Alpaca can trade,
   ranked by yesterday's dollar volume; keep the top ``lab.stocks`` stocks
   (price >= ``min_price``) and ``lab.crypto`` crypto pairs. Saved to
   ``universe.json`` so the scan isn't repeated within the day.
2. **Download**: 10-minute candles for ``intraday_days`` and daily candles
   for a year longer (the 200-day indicators need the warm-up).
3. **Compute**: every indicator on every timeframe, on all cores, into the
   feature store. A symbol whose candles haven't changed isn't recomputed.

The store stops growing at ``max_feature_gb``: symbols past that are left
out, with a message, rather than filling the disk.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import BotConfig
from ..workers import exit_with_parent
from .catalog import CATALOG
from .features import ALPACA_TF, FeatureStore, base_bars, duration, multi_timeframe

STABLECOINS = {"USDC", "USDT", "DAI", "PYUSD", "USDG"}
EXCHANGES = {"NYSE", "NASDAQ", "ARCA", "AMEX", "BATS"}


@dataclass
class LabConfig:
    source: str = "alpaca"                 # alpaca | synthetic
    timeframes: list[str] = field(default_factory=lambda: ["10m", "30m", "1h", "4h", "1d"])
    intraday_days: int = 730
    stocks: int = 200
    crypto: int = 20
    min_price: float = 5.0
    min_dollar_volume: float = 5e6
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    feed: str = "iex"                      # stock candles: iex (free, real time) or sip
    cache_dir: str = "data_cache/alpaca"
    feature_dir: str = "data_cache/features"
    universe_file: str = "universe.json"
    results_file: str = "lab.json"
    max_feature_gb: float = 8.0
    workers: int = 0
    # Moves scored by the scout, in base candles ahead: 1h, 4h, ~a trading day. Shorter
    # moves are too small to beat trading costs.
    horizons: list[int] = field(default_factory=lambda: [6, 24, 78])
    train: float = 0.6                     # first 60% of time: choose
    holdout: float = 0.2                   # next 20%: confirm; the rest: final check only
    stock_cost_bps: float = 3.0            # slippage per side (Alpaca stocks: no commission)
    crypto_cost_bps: float = 25.0          # Alpaca crypto taker fee, per side
    loss_aversion: float = 1.5
    drawdown_budget: float = 0.15          # size trades so training drawdown stays inside this
    max_size: float = 0.25                 # most of equity any one trade may use
    min_gain: float = 0.002
    min_trades: int = 30
    synthetic_symbols: int = 12
    synthetic_crypto: int = 0              # made-up pairs trading around the clock (tests)
    seed: int = 7

    @classmethod
    def from_config(cls, config: BotConfig) -> LabConfig:
        raw = config.section("lab")
        known = set(cls.__dataclass_fields__)
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"Unknown lab settings: {', '.join(sorted(unknown))}")
        cfg = cls(**raw)
        cfg.workers = cfg.workers or max((os.cpu_count() or 2) - 1, 1)
        for tf in cfg.timeframes:
            duration(tf)
        if sorted(cfg.timeframes, key=duration) != cfg.timeframes:
            raise ValueError("lab.timeframes must go from smallest to largest")
        return cfg

    @property
    def base_tf(self) -> str:
        return self.timeframes[0]


# ------------------------------------------------------------------ universe


def scan_universe(data: Any, cfg: LabConfig, say: Callable[[str], None]) -> list[dict[str, Any]]:
    """Everything Alpaca trades, ranked by dollar volume, cut to the configured size."""
    path = Path(cfg.universe_file)
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        fresh = saved.get("date") == date.today().isoformat()
        if fresh and saved.get("settings") == _scan_key(cfg):
            return saved["symbols"]

    chosen: list[dict[str, Any]] = []
    if data.has_keys and cfg.stocks > 0:
        assets = [
            a for a in data.stock_assets()
            if a.get("tradable") and a.get("exchange") in EXCHANGES
            and "." not in a["symbol"] and "/" not in a["symbol"]
        ]
        say(f"Alpaca lists {len(assets)} tradable US stocks and ETFs; ranking by volume...")
        snaps = data.snapshots([a["symbol"] for a in assets])
        ranked = []
        for a in assets:
            bar = (snaps.get(a["symbol"]) or {}).get("prevDailyBar") or {}
            price, volume = float(bar.get("c") or 0), float(bar.get("v") or 0)
            if price >= cfg.min_price and price * volume >= cfg.min_dollar_volume:
                ranked.append({
                    "symbol": a["symbol"], "class": "stock", "dollar_volume": price * volume,
                    "shortable": bool(a.get("shortable") and a.get("easy_to_borrow")),
                    "fractionable": bool(a.get("fractionable")),
                })
        ranked.sort(key=lambda r: -r["dollar_volume"])
        chosen += ranked[: cfg.stocks]
        say(f"  {len(ranked)} pass price >= ${cfg.min_price:g} and enough volume; "
            f"keeping the top {min(cfg.stocks, len(ranked))}.")
    elif cfg.stocks > 0:
        say("No Alpaca secret key yet: stocks skipped, crypto only "
            "(put ALPACA_API_KEY and ALPACA_SECRET_KEY in .env).")

    if cfg.crypto > 0:
        pairs = [
            a["symbol"] for a in data.crypto_assets()
            if a.get("tradable", True) and a["symbol"].endswith("/USD")
            and a["symbol"].split("/")[0] not in STABLECOINS
        ]
        snaps = data.snapshots(pairs)
        ranked = []
        for pair in pairs:
            bar = (snaps.get(pair) or {}).get("prevDailyBar") or (snaps.get(pair) or {}).get(
                "dailyBar") or {}
            ranked.append({"symbol": pair, "class": "crypto", "shortable": False,
                           "fractionable": True,
                           "dollar_volume": float(bar.get("c") or 0) * float(bar.get("v") or 0)})
        ranked.sort(key=lambda r: -r["dollar_volume"])
        chosen += ranked[: cfg.crypto]
        say(f"Crypto: {len(pairs)} USD pairs; keeping {min(cfg.crypto, len(pairs))}.")

    have = {c["symbol"] for c in chosen}
    for symbol in cfg.include:
        if symbol not in have:
            chosen.append({"symbol": symbol, "class": "crypto" if "/" in symbol else "stock",
                           "shortable": False, "fractionable": "/" in symbol,
                           "dollar_volume": 0.0})
    chosen = [c for c in chosen if c["symbol"] not in set(cfg.exclude)]
    try:
        path.write_text(json.dumps({"date": date.today().isoformat(), "settings": _scan_key(cfg),
                                    "symbols": chosen}, indent=1), encoding="utf-8")
    except OSError:
        pass
    return chosen


def _scan_key(cfg: LabConfig) -> list[Any]:
    return [cfg.stocks, cfg.crypto, cfg.min_price, cfg.min_dollar_volume,
            sorted(cfg.include), sorted(cfg.exclude)]


# ------------------------------------------------------------------ features


def _compute_job(job: tuple[str, pd.DataFrame, pd.DataFrame | None, list[str], str, str]) -> str:
    symbol, base, daily, timeframes, feature_dir, stamp = job
    features = multi_timeframe(base, timeframes, daily)
    FeatureStore(feature_dir).save(symbol, base_bars(base), features, stamp)
    return symbol


def prepare(
    config: BotConfig,
    say: Callable[[str], None] = print,
    symbols: list[str] | None = None,
) -> tuple[FeatureStore, list[dict[str, Any]]]:
    """Scan, download and compute; returns the store and the symbols in it."""
    cfg = LabConfig.from_config(config)
    store = FeatureStore(cfg.feature_dir)
    expected = [f"{n}@{tf}" for tf in cfg.timeframes for n in CATALOG]
    if store.columns and store.columns != expected:
        say("The indicator list changed since the features were built: rebuilding them all.")
        for path in store.dir.glob("*/feat.npy"):
            path.unlink()
        (store.dir / "columns.json").unlink(missing_ok=True)
        store = FeatureStore(cfg.feature_dir)

    if cfg.source == "synthetic":
        universe = [{"symbol": s, "class": "stock", "shortable": True, "fractionable": True,
                     "dollar_volume": 0.0} for s in synthetic_symbols(cfg)]
        fetch = lambda sym: (synthetic_bars(sym, cfg), None)  # noqa: E731
    else:
        from ..data.alpaca_data import AlpacaData, load_env

        load_env()
        data = AlpacaData(cfg.cache_dir, feed=cfg.feed, say=say)
        universe = scan_universe(data, cfg, say)

        def fetch(sym: str) -> tuple[pd.DataFrame, pd.DataFrame | None]:
            base = data.bars(sym, ALPACA_TF[cfg.base_tf], cfg.intraday_days)
            daily = (data.bars(sym, "1Day", cfg.intraday_days + 400)
                     if "1d" in cfg.timeframes else None)
            return base, daily

    if symbols:
        wanted = set(symbols)
        universe = [u for u in universe if u["symbol"] in wanted] + [
            {"symbol": s, "class": "crypto" if "/" in s else "stock", "shortable": False,
             "fractionable": "/" in s, "dollar_volume": 0.0}
            for s in symbols if s not in {u["symbol"] for u in universe}]

    cap = cfg.max_feature_gb * 1e9
    used = 0.0
    kept: list[dict[str, Any]] = []
    jobs = []
    say(f"Getting candles for {len(universe)} symbols ({', '.join(cfg.timeframes)})...")
    for n, item in enumerate(universe, 1):
        symbol = item["symbol"]
        try:
            base, daily = fetch(symbol)
        except Exception as exc:  # one bad symbol must not stop the rest
            say(f"  skipping {symbol}: {' '.join(str(exc).split())[:120]}")
            continue
        if len(base) < 500:
            say(f"  skipping {symbol}: only {len(base)} candles")
            continue
        size = len(base) * len(expected) * 2
        if used + size > cap:
            say(f"  feature store would pass {cfg.max_feature_gb:g} GB: stopping at "
                f"{len(kept)} symbols (raise lab.max_feature_gb for more).")
            break
        used += size
        kept.append(item)
        stamp = f"{base.index[0].isoformat()}|{base.index[-1].isoformat()}|{len(base)}"
        built = (store.dir / store.key(symbol) / "feat.npy").exists()
        if store.stamp(symbol) != stamp or not built:
            jobs.append((symbol, base, daily, cfg.timeframes, str(store.dir), stamp))
        if n % 25 == 0:
            say(f"  {n}/{len(universe)} downloaded")

    if jobs:
        say(f"Computing {len(expected)} indicator columns for {len(jobs)} symbols "
            f"on {cfg.workers} cores...")
        with ProcessPoolExecutor(min(cfg.workers, len(jobs)), initializer=exit_with_parent) as pool:
            futures = [pool.submit(_compute_job, job) for job in jobs]
            for done, fut in enumerate(as_completed(futures), 1):
                try:
                    fut.result()
                except Exception as exc:
                    say(f"  a symbol failed: {' '.join(str(exc).split())[:160]}")
                if done % 10 == 0 or done == len(jobs):
                    say(f"  {done}/{len(jobs)} computed")
    store = FeatureStore(cfg.feature_dir)
    present = set(store.symbols())
    kept = [k for k in kept if k["symbol"] in present]
    say(f"Ready: {len(kept)} symbols, {len(expected)} indicator columns, "
        f"{store.size_bytes() / 1e9:.1f} GB on disk.")
    return store, kept


# ------------------------------------------------------------------ offline data


def synthetic_symbols(cfg: LabConfig) -> list[str]:
    return [f"SYN{i:02d}" for i in range(cfg.synthetic_symbols)]


def synthetic_crypto(cfg: LabConfig) -> list[str]:
    return [f"SYN{i:02d}/USD" for i in range(cfg.synthetic_crypto)]


def synthetic_bars(symbol: str, cfg: LabConfig, around_the_clock: bool = False) -> pd.DataFrame:
    """Regime-switching 10-minute candles in market hours (or around the clock,
    like crypto), for offline runs and tests."""
    seed = (cfg.seed * 1000 + sum(map(ord, symbol))) % 2**32
    rng = np.random.default_rng(seed)
    step = duration(cfg.base_tf)
    if around_the_clock:
        days = pd.date_range(end=pd.Timestamp("2026-09-30"), periods=max(cfg.intraday_days, 60))
        per_day = int(pd.Timedelta(days=1) / step)
        start = pd.Timedelta(0)
    else:
        days = pd.bdate_range(end=pd.Timestamp("2026-09-30"),
                              periods=max(cfg.intraday_days * 5 // 7, 60))
        per_day = int(pd.Timedelta(hours=6.5) / step)
        start = pd.Timedelta(hours=9, minutes=30)  # New York time, put in UTC below
    stamps = (days.repeat(per_day) + start
              + pd.to_timedelta(np.tile(np.arange(per_day), len(days)) * step))
    stamps = (pd.DatetimeIndex(stamps).tz_localize("UTC") if around_the_clock else
              pd.DatetimeIndex(stamps).tz_localize("America/New_York").tz_convert("UTC"))
    n = len(stamps)
    regime = np.cumsum(rng.random(n) < 1 / 400) % 3  # drift up / sideways / down
    drift = np.array([4e-5, 0.0, -4e-5])[regime]
    vol = np.array([0.002, 0.0015, 0.003])[regime]
    ret = drift + vol * rng.standard_normal(n)
    close = 100 * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[100.0], close[:-1]]) * (1 + 0.0003 * rng.standard_normal(n))
    wick = np.abs(rng.standard_normal((2, n))) * vol * close * 0.6
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    volume = rng.lognormal(10, 0.5, n) * (1 + 50 * np.abs(ret))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": volume}, index=stamps.as_unit("ns"))
