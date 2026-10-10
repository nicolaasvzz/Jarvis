"""The champ-set builder: master the 4h, 1h and 30m charts, then one setup that trades the 1h.

    investment-bot champ --for 3d            (run-local.ps1 -Mode champ)

Stocks and crypto are built apart: their trading costs differ about 8x, and
so do their hours. For each:

1. **Master each chart**, one after another (4h, 1h, 30m), each judged on its
   own candles:
   - *scout*: every indicator on the chart, scored on how the price moved
     after it voted (30m: 1-2 hours ahead; 1h: 2-4; 4h: 4-8);
   - keep only readings that held up in both halves of training;
   - *look-alikes*: indicators that move together (|correlation| at least
     ``look_alike``) count as one, and only the clearest of each group stays,
     at most ``per_chart`` of them, every family represented;
   - *the set*: the best of each family, then whichever adds most, each one
     followed or faded with an equal vote (tuned weights don't hold up on new
     data). The set size that predicts best on the last third of training wins.
   The scouting is one pass over the data for all three charts (it's the slow
   part); the rest is done chart by chart.
2. **Combine.** Every 1h candle where the three sets agree is a possible
   trade. For each way of gating them (how far the 1h must lean, how firmly
   the 4h and 30m must agree) the builder fits, on training only: the stop,
   target and time limit (from how far winners dipped and ran: MAE/MFE), then
   the confidence model, then the confidence bar (a little above break-even).
3. **Judge.** Every candidate is tested on ``test_windows`` stretches of time
   after training. To be **proven** it must make money on training, in most
   test windows (3 of 4) and in all of them together, and its daily results
   there must beat the **luck bar**: the t-statistic that the best of that
   many tries would reach by luck alone. Every try ever made counts (kept in
   ``champ.json``), so re-running doesn't make luck easier. The last part of
   history (the **final check**) is never used to choose, only reported. The
   previous champion is re-tested and competes too.

Bet size: the largest whose training drawdown stays inside
``lab.drawdown_budget``. A champion that isn't proven bets the smallest size
(2%), and never trades real money.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import BotConfig
from ..reports import Report, tone_of
from ..style import Style
from ..workers import exit_with_parent
from .catalog import FAMILIES, family_of
from .charts import CHARTS, all_columns, chart_of, decision_features
from .charts import VERSION as CHARTS_VERSION
from .features import FeatureStore, ns
from .lab import greedy, proxy
from .package import Trades, metrics, portfolio, score_of
from .prepare import LabConfig, scan_universe, synthetic_bars, synthetic_crypto, synthetic_symbols
from .research import _W, _bars, init_worker, sample_job, scoreboard, scout_job
from .setups import CLOSE, END, TIME, Setup, barrier, daily_t, fit_model, luck_bar

SESSION_FILE = "champ_session.json"
CLASSES = ("stock", "crypto")
CLASS_WORDS = {"stock": "stocks", "crypto": "crypto"}
ORDER = ("4h", "1h", "30m")                     # the order the charts are mastered in
HORIZONS = {"30m": [1, 2], "1h": [2, 4], "4h": [4, 8]}  # in 1h candles
ALL_HORIZONS = [1, 2, 4, 8]
SET_SIZES = (3, 5, 8, 12, 16, 24, 35)
GATE = 0.2                                      # the loosest 1h lean a candidate needs
ENTRIES = (0.25, 0.35, 0.5)
AGREE = (0.0, 0.15)
MARGINS = (0.02, 0.05, 0.10)                    # confidence bar: break-even plus this
HOURS = (2, 3, 4, 6)
PATH = 6                                        # candles of each trade's path kept
REF_SIZE = 0.10                                 # every candidate is judged at this size
SMALL_SIZE = 0.02
HOUR_NS = 3_600_000_000_000
DAY_NS = 24 * HOUR_NS


@dataclass
class ChampConfig:
    feature_dir: str = "data_cache/charts"
    results_file: str = "champ.json"
    classes: list[str] = field(default_factory=lambda: ["stock", "crypto"])
    per_chart: int = 35            # indicators kept per chart (the clearest of each look-alike)
    look_alike: float = 0.7        # |correlation| at which two indicators count as one
    max_hours: int = 6             # the longest normal trade (time limits tried: 2, 3, 4, 6 h)
    test_windows: int = 4
    gap_hours: float = 24          # left out between training, test and final check
    min_session_left: float = 1.0  # stocks: no new trade with less than this many hours left
    luck_margin: float = 0.0       # extra t on top of the luck bar
    news_extension: bool = True    # trader: let agreeing news stretch a trade
    news_cap_hours: float = 72
    news_judge_after: int = 50     # extended trades before the trader judges extensions
    pdt_equity: float = 25000      # stock accounts below this get 3 day trades per 5 days
    trade: list[str] = field(default_factory=lambda: ["stock", "crypto"])

    @classmethod
    def from_config(cls, config: BotConfig) -> ChampConfig:
        raw = config.section("champ")
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unknown champ settings: {', '.join(sorted(unknown))}")
        cfg = cls(**raw)
        for name in ("classes", "trade"):
            values = [str(v).lower().rstrip("s") for v in getattr(cfg, name)]
            bad = [v for v in values if v not in CLASSES]
            if bad:
                raise ValueError(f"champ.{name}: use stock and/or crypto, not {bad}")
            setattr(cfg, name, values)
        cfg.max_hours = int(min(max(cfg.max_hours, 2), PATH))
        return cfg


def class_of(item: dict[str, Any]) -> str:
    return "crypto" if item.get("class") == "crypto" else "stock"


def running_builder(state_file: str | Path = SESSION_FILE) -> int | None:
    from ..jarvis_status import _alive

    try:
        data = json.loads(Path(state_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = data.get("pid")
    if data.get("status") == "running" and pid != os.getpid() and _alive(pid):
        return int(pid)
    return None


# ------------------------------------------------------------------ data


def _chart_job(job: tuple[str, pd.DataFrame, bool, str, str]) -> str:
    symbol, raw, stock, feature_dir, stamp = job
    bars, features = decision_features(raw, stock)
    FeatureStore(feature_dir).save(symbol, bars, features, stamp)
    return symbol


def prepare_charts(config: BotConfig, say: Callable[[str], None] = print,
                   symbols: list[str] | None = None) -> tuple[FeatureStore, list[dict[str, Any]]]:
    """The symbols, their 10-minute candles, and every indicator on the 1h, 30m
    and 4h charts, lined up on the 1h candles, into ``champ.feature_dir``."""
    lab = LabConfig.from_config(config)
    cfg = ChampConfig.from_config(config)
    store = FeatureStore(cfg.feature_dir)
    expected = all_columns()
    if store.columns and store.columns != expected:
        say("The indicator list changed since the charts were built: rebuilding them all.")
        for path in store.dir.glob("*/feat.npy"):
            path.unlink()
        (store.dir / "columns.json").unlink(missing_ok=True)
        store = FeatureStore(cfg.feature_dir)

    if lab.source == "synthetic":
        universe = ([{"symbol": s, "class": "stock", "shortable": True, "fractionable": True,
                      "dollar_volume": 0.0} for s in synthetic_symbols(lab)]
                    + [{"symbol": s, "class": "crypto", "shortable": False, "fractionable": True,
                        "dollar_volume": 0.0} for s in synthetic_crypto(lab)])

        def fetch(sym: str) -> pd.DataFrame:
            return synthetic_bars(sym, lab, around_the_clock="/" in sym)
    else:
        from ..data.alpaca_data import AlpacaData, load_env

        load_env()
        data = AlpacaData(lab.cache_dir, feed=lab.feed, say=say)
        universe = scan_universe(data, lab, say)

        def fetch(sym: str) -> pd.DataFrame:
            return data.bars(sym, "10Min", lab.intraday_days)

    universe = [u for u in universe if class_of(u) in cfg.classes]
    if symbols:
        wanted = set(symbols)
        universe = [u for u in universe if u["symbol"] in wanted] + [
            {"symbol": s, "class": "crypto" if "/" in s else "stock", "shortable": False,
             "fractionable": "/" in s, "dollar_volume": 0.0}
            for s in symbols if s not in {u["symbol"] for u in universe}]

    cap = lab.max_feature_gb * 1e9
    used = 0.0
    kept: list[dict[str, Any]] = []
    jobs = []
    say(f"Getting 10-minute candles for {len(universe)} symbols...")
    for n, item in enumerate(universe, 1):
        symbol = item["symbol"]
        try:
            raw = fetch(symbol)
        except Exception as exc:  # one bad symbol must not stop the rest
            say(f"  skipping {symbol}: {' '.join(str(exc).split())[:120]}")
            continue
        if len(raw) < 500:
            say(f"  skipping {symbol}: only {len(raw)} candles")
            continue
        size = len(raw) / 6 * len(expected) * 2
        if used + size > cap:
            say(f"  chart store would pass {lab.max_feature_gb:g} GB: stopping at "
                f"{len(kept)} symbols (raise lab.max_feature_gb for more).")
            break
        used += size
        kept.append(item)
        stamp = (f"charts{CHARTS_VERSION}|{raw.index[0].isoformat()}|"
                 f"{raw.index[-1].isoformat()}|{len(raw)}")
        built = (store.dir / store.key(symbol) / "feat.npy").exists()
        if store.stamp(symbol) != stamp or not built:
            jobs.append((symbol, raw, class_of(item) == "stock", str(store.dir), stamp))
        if n % 25 == 0:
            say(f"  {n}/{len(universe)} downloaded")
    if jobs:
        say(f"Building the 1h, 30m and 4h charts ({len(expected)} indicator columns) for "
            f"{len(jobs)} symbols on {lab.workers} cores...")
        with ProcessPoolExecutor(min(lab.workers, len(jobs)), initializer=exit_with_parent) as pool:
            futures = [pool.submit(_chart_job, job) for job in jobs]
            for done, fut in enumerate(as_completed(futures), 1):
                try:
                    fut.result()
                except Exception as exc:
                    say(f"  a symbol failed: {' '.join(str(exc).split())[:160]}")
                if done % 20 == 0 or done == len(jobs):
                    say(f"  {done}/{len(jobs)} built")
    store = FeatureStore(cfg.feature_dir)
    present = set(store.symbols())
    kept = [k for k in kept if k["symbol"] in present]
    say(f"Ready: {len(kept)} symbols, {store.size_bytes() / 1e9:.1f} GB of charts on disk.")
    return store, kept


# ------------------------------------------------------------------ possible trades


@dataclass
class Candidates:
    """Every 1h candle where the three charts' sets agree (loosely): where a trade
    could start, and the path the price took for the next ``PATH`` candles, in
    ATRs from the entry, signed so that + is in the trade's favour."""

    symbols: list[str]
    sym: np.ndarray        # index into symbols
    row: np.ndarray        # decision candle's row in that symbol's charts
    t: np.ndarray          # when the decision candle closed (ns)
    d: np.ndarray          # +1 long, -1 short
    a1: np.ndarray         # how far the 1h leans that way (|score|)
    a4: np.ndarray         # how far the 4h agrees (score x direction)
    a30: np.ndarray
    entry: np.ndarray      # next candle's open
    scale: np.ndarray      # one ATR as a share of the entry price
    cost: np.ndarray       # trading cost, each way
    tpcap: np.ndarray      # the 4h ATR, in 1h ATRs
    opn: np.ndarray        # (n, PATH)
    fav: np.ndarray
    adv: np.ndarray
    cls: np.ndarray
    times: np.ndarray      # (n, PATH) each candle's start
    k_last: np.ndarray     # last candle of the trading day (stocks); PATH for crypto
    valid: np.ndarray      # candles of data after the entry (at most PATH)

    KEYS = ("sym", "row", "t", "d", "a1", "a4", "a30", "entry", "scale", "cost", "tpcap",
            "opn", "fav", "adv", "cls", "times", "k_last", "valid")

    def __len__(self) -> int:
        return len(self.t)

    @classmethod
    def join(cls, parts: list[dict[str, Any]]) -> Candidates:
        symbols = [s for p in parts for s in p["symbols"]]
        arrays: dict[str, list[np.ndarray]] = {k: [] for k in cls.KEYS}
        base = 0
        for p in parts:
            for k in cls.KEYS:
                arrays[k].append(p[k] + base if k == "sym" else p[k])
            base += len(p["symbols"])
        joined = {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in arrays.items()}
        if len(joined["t"]):
            order = np.lexsort((joined["row"], joined["sym"]))
            joined = {k: v[order] for k, v in joined.items()}
        else:
            joined["opn"] = joined["fav"] = joined["adv"] = joined["cls"] = np.zeros((0, PATH))
            joined["times"] = np.zeros((0, PATH), dtype="int64")
        return cls(symbols, **joined)

    def limits(self, idx: np.ndarray, hours: int) -> tuple[np.ndarray, np.ndarray]:
        """How many candles each trade may last, and what ends it then."""
        lim = np.full(len(idx), hours, dtype=np.int32)
        why = np.full(len(idx), TIME, dtype=np.int8)
        day = self.k_last[idx] + 1
        cut = day < lim
        lim[cut], why[cut] = day[cut], CLOSE
        data = self.valid[idx]
        cut = data < lim
        lim[cut], why[cut] = data[cut], END
        return np.maximum(lim, 1), why


def candidate_job(job: tuple[list[str], dict[str, dict[str, float]], int, bool]) -> dict[str, Any]:
    """The candidates on a chunk of symbols, for one setup's three sets."""
    symbols, sets, min_left_ns, shorts = job
    store: FeatureStore = _W["store"]
    feats = [f for tf in CHARTS for f in sets[tf]]
    probe = Setup(sets)
    out: dict[str, list[np.ndarray]] = {k: [] for k in Candidates.KEYS}
    names: list[str] = []
    for symbol in symbols:
        bars = _bars(symbol)
        n = len(bars)
        if n < 60:
            continue
        item = _W["meta"].get(symbol, {})
        crypto = class_of(item) == "crypto"
        scores = probe.chart_scores(store.load(symbol, feats), feats)
        s1 = scores["1h"]
        d = np.where(s1 >= GATE, 1, np.where(s1 <= -GATE, -1, 0)).astype(np.int8)
        if not (shorts and not crypto and item.get("shortable", False)):
            d[d < 0] = 0
        a4, a30 = d * scores["4h"], d * scores["30m"]
        atr = bars["atr"].to_numpy(dtype=float)
        ok = (d != 0) & (a4 >= 0) & (a30 >= 0) & np.isfinite(atr) & (atr > 0)
        ok[-1] = False
        r = np.flatnonzero(ok)
        start = ns(bars.index)
        session_end = bars["session_end"].to_numpy(dtype="int64")
        if not crypto:  # enter the same trading day, with time left
            r = r[session_end[r] - start[r + 1] >= min_left_ns]
        if not len(r):
            continue
        e = r + 1
        idx = np.minimum(e[:, None] + np.arange(PATH), n - 1)
        valid = np.minimum(PATH, n - e).astype(np.int16)
        beyond = np.arange(PATH)[None, :] >= valid[:, None]
        o, h, lo, c = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        entry = o[e]
        unit = atr[r][:, None]
        dd = d[r].astype(float)[:, None]
        up = dd > 0
        opn = dd * (o[idx] - entry[:, None]) / unit
        fav = np.where(up, h[idx] - entry[:, None], entry[:, None] - lo[idx]) / unit
        adv = np.where(up, lo[idx] - entry[:, None], entry[:, None] - h[idx]) / unit
        cls = dd * (c[idx] - entry[:, None]) / unit
        for arr in (opn, fav, adv, cls):
            arr[beyond] = np.nan
        if crypto:
            k_last = np.full(len(r), PATH, dtype=np.int16)
        else:
            same = (session_end[idx] == session_end[r][:, None]) & ~beyond
            k_last = (same.sum(1) - 1).astype(np.int16)
        cost = _W["costs"].get(class_of(item), 0.0)
        atr4 = bars["atr4h"].to_numpy(dtype=float)[r]
        out["sym"].append(np.full(len(r), len(names), dtype=np.int32))
        out["row"].append(r.astype(np.int64))
        out["t"].append(bars["closes"].to_numpy(dtype="int64")[r])
        out["d"].append(d[r])
        out["a1"].append(np.abs(s1[r]).astype(np.float32))
        out["a4"].append(a4[r].astype(np.float32))
        out["a30"].append(a30[r].astype(np.float32))
        out["entry"].append(entry)
        out["scale"].append(atr[r] / entry)
        out["cost"].append(np.full(len(r), cost))
        out["tpcap"].append(np.where(np.isfinite(atr4) & (atr4 > 0), atr4 / atr[r], np.nan))
        out["opn"].append(opn.astype(np.float32))
        out["fav"].append(fav.astype(np.float32))
        out["adv"].append(adv.astype(np.float32))
        out["cls"].append(cls.astype(np.float32))
        out["times"].append(start[idx])
        out["k_last"].append(k_last)
        out["valid"].append(valid)
        names.append(symbol)
    result: dict[str, Any] = {"symbols": names}
    for k, v in out.items():
        if v:
            result[k] = np.concatenate(v)
        else:
            result[k] = (np.zeros((0, PATH), dtype=np.float32) if k in ("opn", "fav", "adv", "cls")
                         else np.zeros((0, PATH), dtype="int64") if k == "times"
                         else np.zeros(0))
    return result


def run_setup(setup: Setup, c: Candidates, mask: np.ndarray) -> tuple[Trades, np.ndarray]:
    """The trades ``setup`` takes among the candidates in ``mask`` (one at a time
    per symbol), and the chance of winning it gave each."""
    gate = (mask & (c.a1 >= setup.entry) & (c.a4 >= setup.agree_4h)
            & (c.a30 >= setup.agree_30m))
    if not setup.shorts:
        gate &= c.d > 0
    p = setup.p_win(c.a1, c.a4, c.a30)
    gate &= p >= setup.confidence
    idx = np.flatnonzero(gate)
    if not len(idx):
        return Trades(), np.zeros(0)
    lim, why = c.limits(idx, setup.hours)
    cap = c.tpcap[idx] * math.sqrt(setup.hours / 4)
    tp = np.where(np.isfinite(cap), np.minimum(setup.tp_atr, cap), setup.tp_atr)
    result, ended, why = barrier(c.opn[idx], c.fav[idx], c.adv[idx], c.cls[idx],
                                 setup.sl_atr, tp, lim, why)
    net = result * c.scale[idx] - 2 * c.cost[idx]
    take = []
    last_sym, free = -1, -1
    sym, row = c.sym[idx], c.row[idx]
    for j in range(len(idx)):
        if sym[j] != last_sym:
            last_sym, free = sym[j], -1
        if row[j] >= free:
            take.append(j)
            free = row[j] + 1 + ended[j]  # the candle it ended in can decide the next one
    take_ = np.array(take, dtype=np.int64)
    chosen = idx[take_]
    ended_t = ended[take_]
    trades = Trades(
        entry_time=c.times[chosen, 0],
        exit_time=c.times[chosen, ended_t] + HOUR_NS,
        direction=c.d[chosen].astype(np.int8),
        fraction=np.asarray(setup.fraction(p[chosen]), dtype=float),
        ret=net[take_].astype(float),
        bars=(ended_t + 1).astype(np.int32),
        reason=why[take_].astype(np.int8),
        symbol=[c.symbols[s] for s in c.sym[chosen]],
    )
    return trades, p[chosen]


def fit_barriers(c: Candidates, mask: np.ndarray, max_hours: int,
                 min_wins: int = 20) -> tuple[float, float, int, float] | None:
    """Stop, target and time limit for these candidates, from how far winning
    trades dipped first (MAE) and how far they ran (MFE), on training only.
    Returns (stop ATRs, target ATRs, hours, average net return) or None."""
    idx = np.flatnonzero(mask)
    if len(idx) < min_wins * 2:
        return None
    best: tuple[float, float, int, float] | None = None
    k = np.arange(PATH)[None, :]
    for hours in (h for h in HOURS if h <= max_hours):
        lim, why = c.limits(idx, hours)
        inside = k < lim[:, None]
        final = c.cls[idx, lim - 1]
        net_final = final * c.scale[idx] - 2 * c.cost[idx]
        wins = np.isfinite(net_final) & (net_final > 0)
        if wins.sum() < min_wins:
            continue
        mae = np.where(inside, c.adv[idx], np.inf).min(1)[wins]
        mfe = np.where(inside, c.fav[idx], -np.inf).max(1)[wins]
        stops = np.unique(np.round(np.clip(np.nanquantile(-mae, [0.75, 0.85, 0.95]), 0.5, 4), 2))
        targets = np.unique(np.round(np.clip(np.nanquantile(mfe, [0.4, 0.6, 0.8]), 0.5, 6), 2))
        cap = c.tpcap[idx] * math.sqrt(hours / 4)
        for sl in stops:
            for tp in targets:
                tp_eff = np.where(np.isfinite(cap), np.minimum(tp, cap), tp)
                result, _, _ = barrier(c.opn[idx], c.fav[idx], c.adv[idx], c.cls[idx],
                                       sl, tp_eff, lim, why)
                ev = float(np.nanmean(result * c.scale[idx] - 2 * c.cost[idx]))
                if best is None or ev > best[3]:
                    best = (float(sl), float(tp), int(hours), ev)
    return best


def outcome(c: Candidates, idx: np.ndarray, sl: float, tp: float, hours: int) -> np.ndarray:
    """Net return of each candidate in ``idx`` under these exits."""
    lim, why = c.limits(idx, hours)
    cap = c.tpcap[idx] * math.sqrt(hours / 4)
    tp_eff = np.where(np.isfinite(cap), np.minimum(tp, cap), tp)
    result, _, _ = barrier(c.opn[idx], c.fav[idx], c.adv[idx], c.cls[idx], sl, tp_eff, lim, why)
    return result * c.scale[idx] - 2 * c.cost[idx]


def look_alike_pool(x: np.ndarray, entries: list[dict[str, Any]], size: int, threshold: float,
                    per_family: int = 3) -> tuple[list[int], int]:
    """The clearest indicator of each look-alike group (|correlation| >= threshold
    with a clearer one): at most ``size``, every family represented where it can be.
    ``entries`` are in order of clarity. Returns (positions, number of groups)."""
    usable = np.flatnonzero(x.std(0) > 1e-6) if len(x) else np.array([], dtype=int)
    if not len(usable):
        return [], 0
    with np.errstate(all="ignore"):
        corr = np.nan_to_num(np.corrcoef(x[:, usable].T))
    corr = np.atleast_2d(corr)
    leaders: list[int] = []
    for i in range(len(usable)):
        if all(abs(corr[i, j]) < threshold for j in leaders):
            leaders.append(i)
    picks = [int(usable[i]) for i in leaders]
    pool: list[int] = []
    for fam in FAMILIES:
        pool += [p for p in picks if entries[p]["family"] == fam][:per_family]
    for p in picks:
        if len(pool) >= size:
            break
        if p not in pool:
            pool.append(p)
    pool = sorted(pool[:max(size, per_family * len(FAMILIES))])
    return pool[:size], len(picks)


# ------------------------------------------------------------------ the builder


def _iso(t: int) -> str:
    return datetime.fromtimestamp(t / 1e9, tz=timezone.utc).isoformat(timespec="minutes")


def _day(t: int) -> str:
    return datetime.fromtimestamp(t / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")


def _human(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    return f"{seconds // 60}m {seconds % 60:02d}s"


def _round(m: dict[str, Any]) -> dict[str, Any]:
    return {k: (round(v, 5) if isinstance(v, float) and math.isfinite(v) else v)
            for k, v in m.items()}


def _write_json(path: Path, data: Any) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=float), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _scaled(trades: Trades, factor: float) -> Trades:
    return Trades(trades.entry_time, trades.exit_time, trades.direction,
                  trades.fraction * factor, trades.ret, trades.bars, trades.reason, trades.symbol)


def _subset(trades: Trades, keep: np.ndarray) -> Trades:
    return Trades(*(getattr(trades, k)[keep] for k in
                    ("entry_time", "exit_time", "direction", "fraction", "ret", "bars", "reason")),
                  symbol=[s for s, k in zip(trades.symbol, keep, strict=True) if k])


class ChampBuilder:
    def __init__(
        self,
        config: BotConfig,
        store: FeatureStore,
        universe: list[dict[str, Any]],
        seconds: float,
        say: Callable[[str], None] = print,
        on_progress: Callable[[], None] = lambda: None,
        seed: int | None = None,
        state_file: str | Path = SESSION_FILE,
        style: Style | None = None,
        money: float = 0.0,
    ):
        self.lab = LabConfig.from_config(config)
        self.cfg = ChampConfig.from_config(config)
        self.store, self.say, self.on_progress = store, say, on_progress
        self.universe = universe
        self.meta = {u["symbol"]: u for u in universe}
        self.seconds = seconds
        self.rng = random.Random(seed)
        self.started = datetime.now(timezone.utc)
        self.deadline = time.monotonic() + seconds
        self.state_file = Path(state_file)
        self.results_file = Path(self.cfg.results_file)
        self.state = self._load()
        self.pool: ProcessPoolExecutor | None = None
        self.phase = "starting"
        self.style = style or Style()
        self.pins = self.style.package_pins()   # threshold here means the confidence bar
        self.money = money
        self.lessons: dict[str, list[str]] = {}
        self.done: list[str] = []

    # ------------------------------------------------------------ persistence

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.results_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("version", 1)
        data.setdefault("classes", {})
        data.setdefault("trials", {})
        data.setdefault("runs", [])
        return data

    def save(self) -> None:
        self.state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _write_json(self.results_file, self.state)

    def save_session(self, status: str) -> None:
        champs = {}
        for cls, data in self.state["classes"].items():
            champ = data.get("champion") or {}
            if champ:
                champs[cls] = (("proven: " if champ.get("proven") else "not proven: ")
                               + Setup.from_dict(champ["setup"]).describe())
        _write_json(self.state_file, {
            "status": status,
            "phase": self.phase,
            "started": self.started.isoformat(timespec="seconds"),
            "ends": (self.started + timedelta(seconds=self.seconds)).isoformat(timespec="seconds"),
            "done": self.done,
            "champions": champs,
            "symbols": len(self.universe),
            "pid": os.getpid(),
        })

    def set_phase(self, text: str) -> None:
        self.phase = text
        self.say(f"\n[{text}]")
        self.save_session("running")
        self.on_progress()

    def left(self) -> float:
        return self.deadline - time.monotonic()

    # ------------------------------------------------------------ the worker pool

    def _open_pool(self) -> None:
        if self.pool is None:
            costs = {"stock": self.lab.stock_cost_bps / 1e4,
                     "crypto": self.lab.crypto_cost_bps / 1e4}
            self.pool = ProcessPoolExecutor(self.lab.workers, initializer=init_worker,
                                            initargs=(str(self.store.dir), self.meta, costs))

    def close(self) -> None:
        pool, self.pool = self.pool, None
        if pool is None:
            return
        processes = list((getattr(pool, "_processes", None) or {}).values())
        pool.shutdown(wait=False, cancel_futures=True)
        for process in processes:
            if process.is_alive():
                process.terminate()

    def _chunks(self, symbols: list[str], per_worker: int = 2) -> list[list[str]]:
        n = max(1, min(len(symbols), self.lab.workers * per_worker))
        return [symbols[i::n] for i in range(n)]

    def _map(self, fn: Callable[[Any], Any], jobs: list[Any]) -> list[Any]:
        self._open_pool()
        assert self.pool is not None
        return list(self.pool.map(fn, jobs))

    # ------------------------------------------------------------ the run

    def run(self) -> str:
        status = "finished"
        classes = [c for c in CLASSES if c in self.cfg.classes]
        self.say(f"Champ-set builder: {len(self.universe)} symbols; charts 4h, 1h and 30m, "
                 f"trading on the 1h; at most {_human(self.seconds)}.")
        self.say(f"Strategy: {self.style.describe()}"
                 + (f" (read from: {'; '.join(self.style.reading)})" if self.style.text else "")
                 + ". Confidence here means the chance of reaching the target before the stop.")
        self.say("Ctrl+C stops it; whatever is finished is kept.")
        try:
            for cls in classes:
                symbols = [u["symbol"] for u in self.universe if class_of(u) == cls]
                if not symbols:
                    self.say(f"No {CLASS_WORDS[cls]} in the symbol list; skipping them.")
                    continue
                if self.left() <= 0:
                    status = "time up"
                    break
                self.build_class(cls, symbols)
                self.done.append(cls)
                self.save()
                self.save_session("running")
                self.on_progress()
        except KeyboardInterrupt:
            status = "stopped"
            self.say("Stopping (Ctrl+C).")
        finally:
            self.close()
            self.phase = status
            self.state["runs"] = (self.state["runs"] + [{
                "at": self.started.isoformat(timespec="seconds"), "status": status,
                "classes": self.done, "style": self.style.describe()}])[-50:]
            self.save()
            self.save_session(status)
            self.on_progress()
        return status

    def windows(self, symbols: list[str]) -> dict[str, Any]:
        firsts, lasts = [], []
        for s in symbols:
            idx = ns(self.store.bars(s).index)
            if len(idx):
                firsts.append(idx[0])
                lasts.append(idx[-1])
        if not firsts:
            raise SystemExit("No symbols with data in the chart store.")
        t0, t1 = int(np.median(firsts)), int(max(lasts)) + HOUR_NS
        gap = int(self.cfg.gap_hours * HOUR_NS)
        a = t0 + int((t1 - t0) * self.lab.train)
        b = t0 + int((t1 - t0) * (self.lab.train + self.lab.holdout))
        test = (a + gap, b)
        k = max(int(self.cfg.test_windows), 1)
        cuts = np.linspace(test[0], test[1], k + 1).astype("int64")
        return {"train": (t0, a), "test": test, "final": (b + gap, t1),
                "parts": [(int(cuts[i]), int(cuts[i + 1])) for i in range(k)]}

    def build_class(self, cls: str, symbols: list[str]) -> None:
        word = CLASS_WORDS[cls]
        lessons: list[str] = []
        self.lessons[cls] = lessons
        w = self.windows(symbols)
        self.say(f"\n=== {word.capitalize()}: {len(symbols)} symbols. Training "
                 f"{_day(w['train'][0])}..{_day(w['train'][1])}, test "
                 f"{_day(w['test'][0])}..{_day(w['test'][1])} in {len(w['parts'])} windows, "
                 f"final check {_day(w['final'][0])}..{_day(w['final'][1])}. ===")
        charts = self.master_charts(cls, symbols, w, lessons)
        if self.left() <= 0:
            lessons.append("Out of time before combining the charts.")
            return
        sets = {tf: charts[tf]["set"] for tf in CHARTS}
        self.set_phase(f"{word}: combining the three charts")
        cands = self.gather(symbols, sets)
        previous = (self.state["classes"].get(cls) or {}).get("champion")
        prev_setup = None
        prev_cands = None
        if previous:
            try:
                prev_setup = Setup.from_dict(previous["setup"])
                self.store.col_index(prev_setup.features)
            except (KeyError, ValueError, TypeError):
                prev_setup = None
            if prev_setup is not None:
                prev_cands = (cands if prev_setup.sets == sets
                              else self.gather(symbols, prev_setup.sets))
        self.set_phase(f"{word}: judging the setups")
        result = self.judge(cls, cands, w, sets, prev_setup, prev_cands, lessons)
        self.state["classes"][cls] = {
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "symbols": len(symbols),
            "windows": {"train": [_iso(w["train"][0]), _iso(w["train"][1])],
                        "test": [_iso(w["test"][0]), _iso(w["test"][1])],
                        "final": [_iso(w["final"][0]), _iso(w["final"][1])]},
            "charts": charts,
            "champion": result.get("champion"),
            "tried": result.get("tried", []),
            "lessons": lessons,
        }
        for line in lessons:
            self.say(f"  - {line}")

    # ------------------------------------------------------------ 1. master each chart

    def _scout(self, symbols: list[str], window: tuple[int, int]) -> list[dict[str, Any]]:
        return self._map(scout_job, [(c, window, ALL_HORIZONS) for c in self._chunks(symbols)])

    def master_charts(self, cls: str, symbols: list[str], w: dict[str, Any],
                      lessons: list[str]) -> dict[str, dict[str, Any]]:
        word = CLASS_WORDS[cls]
        a, b = w["train"]
        mid = (a + b) // 2
        self.set_phase(f"{word}: scouting the 4h, 1h and 30m charts (one pass for all three)")
        columns = self.store.columns
        full = self._scout(symbols, (a, b))
        halves = (self._scout(symbols, (a, mid)), self._scout(symbols, (mid, b)))
        charts: dict[str, dict[str, Any]] = {}
        for n, tf in enumerate(ORDER, 1):
            self.set_phase(f"{word}: mastering the {tf} chart ({n} of 3)")
            charts[tf] = self.master_chart(tf, symbols, columns, full, halves, (a, b))
            c = charts[tf]
            fams = Counter(family_of(f) for f in c["set"])
            lessons.append(
                f"{tf} chart: {c['stable']} of {c['indicators']} indicators held up in both "
                f"halves of training; {c['groups']} look-alike groups; pool of {len(c['pool'])}; "
                f"set of {len(c['set'])} (" + ", ".join(f"{k} {v}" for k, v in sorted(fams.items()))
                + f"), {c['proxy']['hit']:.0%} right {c['horizon']}h ahead on the last third "
                f"of training (t={c['proxy']['t']:.1f}).")
        return charts

    def master_chart(self, tf: str, symbols: list[str], columns: list[str],
                     full: list[dict[str, Any]], halves: tuple[list[Any], list[Any]],
                     window: tuple[int, int]) -> dict[str, Any]:
        def board_of(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
            board = scoreboard(results, columns, HORIZONS[tf])
            return [e for e in board if chart_of(e["feature"]) == tf]

        board = board_of(full)
        by1 = {e["feature"]: e for e in board_of(halves[0])}
        by2 = {e["feature"]: e for e in board_of(halves[1])}
        stable = [e for e in board if e["feature"] in by1 and e["feature"] in by2
                  and by1[e["feature"]]["sign"] == by2[e["feature"]]["sign"] == e["sign"]
                  and min(abs(by1[e["feature"]]["t"]), abs(by2[e["feature"]]["t"])) >= 1.0
                  and e["signals"] > 0]
        use = stable if len(stable) >= 10 else [e for e in board if e["signals"] > 0][:40]
        h = Counter(e["horizon"] for e in use[:20]).most_common(1)[0][0] if use else HORIZONS[tf][0]
        cols = [e["feature"] for e in use]
        per_symbol = max(200, 150_000 // max(len(symbols), 1))
        parts = self._map(sample_job, [(c, window, cols, h, per_symbol,
                                        self.rng.randrange(1 << 30))
                                       for c in self._chunks(symbols)])
        x = np.concatenate([p["x"] for p in parts])
        f = np.concatenate([p["f"] for p in parts])
        t = np.concatenate([p["t"] for p in parts])
        signs = np.array([e["sign"] for e in use], dtype=np.float32)
        x = x * signs
        pool_idx, groups = look_alike_pool(x, use, self.cfg.per_chart, self.cfg.look_alike)
        pool = [cols[i] for i in pool_idx]
        empty = {"set": {}, "pool": pool, "stable": len(stable), "indicators": len(board),
                 "groups": groups, "horizon": h, "proxy": {"t": 0.0, "hit": 0.0},
                 "top": [_compact(e) for e in board[:10]]}
        if not pool:
            fallback = board[0]
            empty["set"] = {fallback["feature"]: float(fallback["sign"])}
            return empty
        xp = x[:, pool_idx]
        cut = np.quantile(t, 2 / 3) if len(t) else 0
        early, late = t < cut, t >= cut
        zero = np.zeros(int(early.sum()))
        options = greedy(xp[early], f[early], zero, pool, np.ones(len(pool)), SET_SIZES)
        best: tuple[float, list[int], float] | None = None
        for _, chosen, _ in options:
            score = xp[late][:, chosen].mean(1)
            t_late, theta = proxy(score, f[late], np.zeros(int(late.sum())))
            if best is None or t_late > best[0] + 1e-9:
                best = (t_late, chosen, theta)
        assert best is not None
        t_late, chosen, theta = best
        score = xp[late][:, chosen].mean(1)
        voted = np.abs(score) >= theta
        hit = float((np.sign(score[voted]) * f[late][voted] > 0).mean()) if voted.any() else 0.0
        signs_by = {e["feature"]: e["sign"] for e in use}
        empty.update(
            set={pool[i]: float(signs_by[pool[i]]) for i in chosen},
            proxy={"t": round(float(t_late) if math.isfinite(t_late) else 0.0, 2),
                   "hit": round(hit, 3), "threshold": theta},
        )
        return empty

    # ------------------------------------------------------------ 2. combine

    def gather(self, symbols: list[str], sets: dict[str, dict[str, float]]) -> Candidates:
        min_left = int(self.cfg.min_session_left * HOUR_NS)
        shorts = self.pins.get("shorts", True)
        parts = self._map(candidate_job, [(c, sets, min_left, shorts)
                                          for c in self._chunks(symbols)])
        return Candidates.join(parts)

    def _mask(self, c: Candidates, window: tuple[int, int]) -> np.ndarray:
        return (c.t >= window[0]) & (c.t < window[1])

    def _days(self, cls: str, window: tuple[int, int]) -> float:
        days = (window[1] - window[0]) / DAY_NS
        return days * 5 / 7 if cls == "stock" else days

    def _min_trades(self, w: dict[str, Any], part: str) -> int:
        a, b = w[part]
        ta, tb = w["train"]
        return max(5, round(self.lab.min_trades * (b - a) / max(tb - ta, 1)))

    def candidates_for(self, cls: str, c: Candidates, w: dict[str, Any],
                       sets: dict[str, dict[str, float]]) -> list[Setup]:
        """Every setup to judge: for each gate, exits and model fitted on training."""
        train = self._mask(c, w["train"])
        out: list[Setup] = []
        pinned = self.pins.get("threshold")
        shorts = bool(self.pins.get("shorts", True))
        for entry in ENTRIES:
            for agree_4h in AGREE:
                for agree_30m in AGREE:
                    gate = train & (c.a1 >= entry) & (c.a4 >= agree_4h) & (c.a30 >= agree_30m)
                    if not shorts:
                        gate &= c.d > 0
                    fitted = fit_barriers(c, gate, self.cfg.max_hours)
                    if fitted is None:
                        continue
                    sl, tp, hours, _ = fitted
                    idx = np.flatnonzero(gate)
                    net = outcome(c, idx, sl, tp, hours)
                    ok = np.isfinite(net)
                    idx, net = idx[ok], net[ok]
                    won = (net > 0).astype(float)
                    x = np.column_stack([c.a1[idx], c.a4[idx], c.a30[idx]]).astype(float)
                    model = fit_model(x, won)
                    gain = net[net > 0].mean() if (net > 0).any() else 0.0
                    loss = -net[net <= 0].mean() if (net <= 0).any() else 0.0
                    even = loss / (gain + loss) if gain + loss > 0 else 0.5
                    bars = ([float(pinned)] if pinned is not None
                            else [round(min(even + m, 0.95), 3) for m in MARGINS])
                    for bar in bars:
                        out.append(Setup(
                            sets, entry=entry, agree_4h=agree_4h, agree_30m=agree_30m,
                            model=model, confidence=bar, sl_atr=sl, tp_atr=tp, hours=hours,
                            size=REF_SIZE, shorts=shorts,
                            name=(f"1h>={entry:g}, 4h>={agree_4h:g}, 30m>={agree_30m:g}, "
                                  f"{bar:.0%} sure (break-even {even:.0%})")))
        return out

    # ------------------------------------------------------------ 3. judge

    def evaluate(self, cls: str, setup: Setup, c: Candidates, w: dict[str, Any],
                 parts: tuple[str, ...] = ("train", "test")) -> dict[str, Any]:
        out: dict[str, Any] = {"metrics": {}, "scores": {}}
        for part in parts:
            trades, p = run_setup(setup, c, self._mask(c, w[part]))
            taken, m = portfolio(trades)
            m["daily_t"] = daily_t(taken.exit_time, taken.fraction * taken.ret,
                                   self._days(cls, w[part]))
            out["metrics"][part] = m
            out["scores"][part] = score_of(m, self.lab.loss_aversion, self._min_trades(w, part))
            if part == "test":
                out["windows"] = []
                for a, b in w["parts"]:
                    inside = (taken.entry_time >= a) & (taken.entry_time < b)
                    out["windows"].append(metrics(_subset(taken, inside)))
                # the model's honesty: every trade it said yes to (before the exposure cap)
                out["calibration"] = calibration(p, trades.ret, setup.confidence)
        return out

    def judge(self, cls: str, c: Candidates, w: dict[str, Any], sets: dict[str, Any],
              prev: Setup | None, prev_c: Candidates | None,
              lessons: list[str]) -> dict[str, Any]:
        word = CLASS_WORDS[cls]
        setups = self.candidates_for(cls, c, w, sets)
        runs = [(s, c) for s in setups]
        if prev is not None and prev_c is not None:
            runs.append((prev.with_(size=REF_SIZE, name="previous champion"), prev_c))
        if not runs:
            lessons.append("Too few possible trades to fit any setup; nothing to judge.")
            return {}
        tries = int(self.state["trials"].get(cls, 0)) + len(runs)
        self.state["trials"][cls] = tries
        bar = max(luck_bar(tries), 1.65) + self.cfg.luck_margin
        self.say(f"Judging {len(runs)} setups on training and {len(w['parts'])} test windows "
                 f"({tries} {word} setups tried in all: the luck bar is t={bar:.2f})...")
        judged = []
        for setup, cands in runs:
            r = self.evaluate(cls, setup, cands, w)
            r["setup"] = setup
            r["cands"] = cands
            train, test = r["metrics"]["train"], r["metrics"]["test"]
            windows = [m for m in r["windows"] if m["num_trades"]]
            won = sum(1 for m in windows if m["total_return"] > 0)
            need = math.ceil(0.75 * len(windows)) if windows else 1
            enough = (train["num_trades"] >= self._min_trades(w, "train")
                      and test["num_trades"] >= self._min_trades(w, "test"))
            r["checks"] = {
                "enough trades": enough,
                "made money on training": train["total_return"] > 0,
                "made money on test": test["total_return"] > 0,
                f"won {won} of {len(windows)} test windows (needs {need})": won >= need,
                f"test t={test['daily_t']:.2f} beats the luck bar {bar:.2f}":
                    test["daily_t"] >= bar,
            }
            r["proven"] = all(r["checks"].values())
            r["eligible"] = enough
            judged.append(r)
        proven = [r for r in judged if r["proven"]]
        eligible = [r for r in judged if r["eligible"]]
        trading = [r for r in judged if r["metrics"]["train"]["num_trades"]] or judged
        if proven:
            best = max(proven, key=lambda r: r["scores"]["test"])
        elif eligible:
            best = max(eligible, key=lambda r: r["scores"]["train"] + r["scores"]["test"])
        else:
            best = max(trading, key=lambda r: r["scores"]["train"])
        setup = best["setup"]
        if "size" in self.pins:
            size = float(self.pins["size"])
        elif best["proven"]:
            size = self.fit_size(setup, best["cands"], w)
        else:
            size = SMALL_SIZE
        final = self.evaluate(cls, setup, best["cands"], w, parts=("final",))
        setup = setup.with_(size=size)
        champion = {
            "setup": setup.to_dict(),
            "proven": bool(best["proven"]),
            "checks": best["checks"],
            "metrics": {**{p: _round(m) for p, m in best["metrics"].items()},
                        "final": _round(final["metrics"]["final"])},
            "windows": [_round(m) for m in best["windows"]],
            "calibration": best["calibration"],
            "luck_bar": round(bar, 3),
            "trials": tries,
            "since": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        m = champion["metrics"]
        lessons.append(f"{len(runs)} setups judged; {len(proven)} proven "
                       f"(luck bar t={bar:.2f} after {tries} tries).")
        lessons.append(("Champion" if best["proven"] else "Best so far, NOT proven (trades at "
                        f"{SMALL_SIZE:.0%}, never with real money)") + f": {setup.describe()}.")
        lessons.append(f"Confidence model: {model_words(setup.model)}.")
        lessons.append(f"Results at {REF_SIZE:.0%} a trade: {brief(m)}.")
        failed = [k for k, ok in best["checks"].items() if not ok]
        if failed:
            lessons.append("Not proven because it didn't pass: " + "; ".join(failed) + ".")
        if not any(r["metrics"]["train"]["num_trades"] for r in judged):
            pinned = self.pins.get("threshold")
            lessons.append("No setup made a single trade" + (
                f": none is ever {pinned:.0%} sure of its target before its stop. Ask for "
                "less confidence (or let the builder pick it)." if pinned is not None else "."))
        tried = sorted(judged, key=lambda r: -(r["scores"]["train"] + r["scores"]["test"]))[:10]
        return {"champion": champion, "tried": [
            {"name": r["setup"].name, "proven": r["proven"],
             "train %": round(r["metrics"]["train"]["total_return"] * 100, 2),
             "test %": round(r["metrics"]["test"]["total_return"] * 100, 2),
             "test t": round(r["metrics"]["test"]["daily_t"], 2),
             "trades": int(r["metrics"]["train"]["num_trades"]
                           + r["metrics"]["test"]["num_trades"])}
            for r in tried]}

    def fit_size(self, setup: Setup, c: Candidates, w: dict[str, Any]) -> float:
        """The largest bet whose training drawdown stays inside the budget."""
        trades, _ = run_setup(setup.with_(size=REF_SIZE), c, self._mask(c, w["train"]))
        best = SMALL_SIZE
        for size in np.arange(0.03, self.lab.max_size + 1e-9, 0.01):
            _, m = portfolio(_scaled(trades, size / REF_SIZE))
            if m["max_drawdown"] < -self.lab.drawdown_budget or m["total_return"] <= 0:
                break
            best = round(float(size), 2)
        return best

    # ------------------------------------------------------------ the report

    def report(self, status: str) -> Report:
        took = _human((datetime.now(timezone.utc) - self.started).total_seconds())
        parts, tone = [], "neutral"
        for cls in self.done:
            champ = (self.state["classes"].get(cls) or {}).get("champion") or {}
            if champ:
                test = champ["metrics"]["test"]["total_return"]
                parts.append(f"{CLASS_WORDS[cls]} {'proven' if champ['proven'] else 'not proven'}"
                             f" (test {test:+.1%})")
                if champ["proven"]:
                    tone = "good" if tone != "bad" else tone
        summary = (f"{status} in {took}: " + ("; ".join(parts) if parts else "no champion yet"))
        out = Report("champ", summary, tone, title="Champ-set builder",
                     subtitle=f"{len(self.universe)} symbols; charts 4h, 1h, 30m; trades on the "
                              f"1h; strategy: {self.style.describe()}")
        out.stat("Ran for", took)
        for cls in self.done:
            data = self.state["classes"].get(cls) or {}
            champ = data.get("champion") or {}
            word = CLASS_WORDS[cls].capitalize()
            if not champ:
                out.bullets(f"{word}", data.get("lessons") or [], "Nothing built.")
                continue
            setup = Setup.from_dict(champ["setup"])
            m = champ["metrics"]
            out.stat(f"{word}", "proven" if champ["proven"] else "not proven",
                     "good" if champ["proven"] else "bad")
            for part, label in (("test", "test"), ("final", "final check")):
                out.stat(f"{word} {label}", f"{m[part]['total_return']:+.1%}",
                         tone_of(m[part]["total_return"]))
            out.text(f"{word}: the champion", f"{setup.describe()}. Bet {setup.size:.0%} of the "
                     f"money a trade. Confidence model: {model_words(setup.model)}.")
            out.table(f"{word}: how it did (at {REF_SIZE:.0%} a trade)", [
                {"Part": label, "Return %": round(m[p]["total_return"] * 100, 2),
                 "Max DD %": round(m[p]["max_drawdown"] * 100, 2),
                 "Trades": int(m[p]["num_trades"]), "Won %": round(m[p]["win_rate"] * 100, 1),
                 "Daily t": round(m[p].get("daily_t", 0.0), 2)}
                for p, label in (("train", "training"), ("test", "test (all windows)"),
                                 ("final", "final check"))])
            out.table(f"{word}: test windows", [
                {"Window": i + 1, "Return %": round(x["total_return"] * 100, 2),
                 "Trades": int(x["num_trades"]), "Won %": round(x["win_rate"] * 100, 1)}
                for i, x in enumerate(champ.get("windows") or [])])
            out.table(f"{word}: proven?", [
                {"Check": k, "Passed": "yes" if ok else "NO"}
                for k, ok in (champ.get("checks") or {}).items()])
            out.table(f"{word}: is its confidence honest? (test)", champ.get("calibration") or [],
                      "Too few trades to tell.")
            out.table(f"{word}: indicators on each chart", [
                {"Chart": tf, "Indicator": f.split("@", 1)[0], "Family": family_of(f),
                 "Use": "follow" if v > 0 else "fade"}
                for tf in ORDER for f, v in setup.sets[tf].items()])
            out.table(f"{word}: setups tried (best 10)", data.get("tried") or [])
            out.bullets(f"{word}: what the builder saw", data.get("lessons") or [])
        return out


def _compact(e: dict[str, Any]) -> dict[str, Any]:
    return {"feature": e["feature"], "family": e["family"], "sign": e["sign"],
            "t": round(e["t"], 2), "hit": round(e["hit"], 3), "horizon": e["horizon"]}


def calibration(p: np.ndarray, ret: np.ndarray, bar: float) -> list[dict[str, Any]]:
    """Predicted chance against how often trades really won, in bands."""
    if len(p) < 10:
        return []
    out = []
    edges = [bar, bar + 0.05, bar + 0.10, bar + 0.20, 1.01]
    for lo, hi in zip(edges, edges[1:], strict=False):
        inside = (p >= lo) & (p < hi)
        if inside.sum() >= 5:
            out.append({"Predicted": f"{lo:.0%}-{min(hi, 1):.0%}", "Trades": int(inside.sum()),
                        "Said %": round(float(p[inside].mean()) * 100, 1),
                        "Won %": round(float((ret[inside] > 0).mean()) * 100, 1)})
    return out


def model_words(model: list[float]) -> str:
    """What the confidence model learned, in words."""
    _, w1, w4, w30 = model
    parts = []
    for name, value in (("1h", w1), ("4h", w4), ("30m", w30)):
        odds = math.exp(value * 0.5)  # agreement rising by half the scale
        parts.append(f"{name} x{odds:.2f}")
    return "odds of winning when a chart leans half a step further its way: " + ", ".join(parts)


def brief(m: dict[str, dict[str, float]]) -> str:
    words = {"train": "training", "test": "test", "final": "final check"}
    return "; ".join(f"{words[p]} {m[p]['total_return']:+.1%} ({int(m[p]['num_trades'])} trades, "
                     f"{m[p]['win_rate']:.0%} won)" for p in ("train", "test", "final") if p in m)


def load_champions(path: str | Path) -> dict[str, dict[str, Any]]:
    """{class: champion} from ``champ.json`` (empty if there is none yet)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {cls: v["champion"] for cls, v in (data.get("classes") or {}).items()
            if isinstance(v, dict) and v.get("champion")}

