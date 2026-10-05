"""The work done on every core: scouting indicators, finding patterns, backtesting packages.

Everything here runs in worker processes, over one chunk of symbols at a
time, and returns plain sums/arrays that the main process adds up.

- ``scout_job``: for every indicator column, how its vote lined up with what
  the price did next (1, 6 and 24 candles ahead): correlation, and for
  strong votes (|vote| >= 0.5) how often the direction was right and the
  average move that followed. Nothing is traded.
- ``pairs_job``: for the best indicators, every pair agreeing strongly: how
  often, and the move that followed (long agreements and short agreements
  separately), as matrices, so 60 indicators -> 1,770 pairs is two matrix
  products per symbol.
- ``triples_job``: the strongest pairs, extended by every third indicator.
- ``sample_job``: a random sample of candles (indicator votes + the move that
  followed), for building packages quickly in the main process.
- ``trades_job``: backtest packages on a chunk of symbols.
"""
from __future__ import annotations

import signal
from typing import Any

import numpy as np
import pandas as pd

from ..workers import exit_with_parent
from .catalog import family_of
from .features import FeatureStore, duration, ns
from .package import Package, Trades, simulate_symbol

STRONG = 0.5   # |vote| that counts as a clear signal
AGREE = 0.3    # both votes past this, same direction: "they agree"
BLOCK = 128    # indicator columns scouted at once

_W: dict[str, Any] = {}


def init_worker(feature_dir: str, meta: dict[str, dict[str, Any]], costs: dict[str, float]) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is the main process's to handle
    exit_with_parent()
    _W.update(store=FeatureStore(feature_dir), meta=meta, costs=costs, bars={})


def _bars(symbol: str) -> pd.DataFrame:
    cache = _W["bars"]
    if symbol not in cache:
        if len(cache) > 400:
            cache.clear()
        cache[symbol] = _W["store"].bars(symbol)
    return cache[symbol]


def _rows(symbol: str, window: tuple[int, int]) -> slice:
    times = ns(_bars(symbol).index)
    return slice(int(np.searchsorted(times, window[0])), int(np.searchsorted(times, window[1])))


def _forward(bars: pd.DataFrame, rows: slice, h: int) -> np.ndarray:
    """Return from the next candle's open to the close h candles later."""
    open_ = bars["open"].to_numpy()
    close = bars["close"].to_numpy()
    n = len(close)
    idx = np.arange(rows.start, rows.stop)
    entry = idx + 1
    exit_ = idx + h
    ok = exit_ < n
    out = np.full(len(idx), np.nan)
    out[ok] = close[exit_[ok]] / open_[entry[ok]] - 1.0
    return out


# ------------------------------------------------------------------ scout


def scout_job(job: tuple[list[str], tuple[int, int], list[int]]) -> dict[str, Any]:
    symbols, window, horizons = job
    store: FeatureStore = _W["store"]
    n_cols = len(store.columns)
    acc = {
        h: {k: np.zeros(n_cols) for k in ("ic_sum", "z_sum", "ic_n", "strong_n", "strong_edge",
                                          "strong_wins")}
        for h in horizons
    }
    for symbol in symbols:
        rows = _rows(symbol, window)
        if rows.stop - rows.start < 200:
            continue
        bars = _bars(symbol)
        moves = {}
        for h in horizons:
            f = _forward(bars, rows, h)
            ok = np.isfinite(f)
            # clip extreme moves so one news spike doesn't decide an indicator's fate
            lim = np.percentile(np.abs(f[ok]), 99) if ok.any() else 0.0
            moves[h] = (np.clip(np.where(ok, f, 0.0), -lim, lim), ok)
        matrix = store.matrix(symbol)
        for lo in range(0, n_cols, BLOCK):  # a block of columns at a time keeps memory small
            hi = min(lo + BLOCK, n_cols)
            x = np.asarray(matrix[rows, lo:hi], dtype=np.float32)
            valid_x = np.isfinite(x)
            x0 = np.where(valid_x, x, 0.0)
            for h in horizons:
                f, ok_f = moves[h]
                m = valid_x & ok_f[:, None]
                cnt = m.sum(0)
                xm = np.where(m, x0, 0.0)
                fm = m * f[:, None]
                sx, sf = xm.sum(0), fm.sum(0)
                sxx, sff = (xm * xm).sum(0), (fm * fm).sum(0)
                sxf = (xm * fm).sum(0)
                with np.errstate(all="ignore"):
                    cov = sxf / cnt - sx / cnt * sf / cnt
                    vx = sxx / cnt - (sx / cnt) ** 2
                    vf = sff / cnt - (sf / cnt) ** 2
                    ic = cov / np.sqrt(vx * vf)
                good = np.isfinite(ic) & (cnt >= 100) & (vx > 1e-8)
                a = acc[h]
                a["ic_sum"][lo:hi][good] += ic[good]
                a["z_sum"][lo:hi][good] += ic[good] * np.sqrt(cnt[good])
                a["ic_n"][lo:hi][good] += 1
                strong = (np.abs(x0) >= STRONG) & m
                signed = np.sign(x0) * strong * f[:, None]
                a["strong_n"][lo:hi] += strong.sum(0)
                a["strong_edge"][lo:hi] += signed.sum(0)
                a["strong_wins"][lo:hi] += (signed > 0).sum(0)
    return acc


def scoreboard(results: list[dict[str, Any]], columns: list[str],
               horizons: list[int]) -> list[dict[str, Any]]:
    """Add up the scouts: per indicator column, its best horizon's evidence."""
    total = {h: {k: sum(r[h][k] for r in results) for k in results[0][h]} for h in horizons}
    base = duration(columns[0].split("@")[1])
    # Neighbouring candles overlap: a 24-candle move counted on every candle, or a daily
    # value repeated on every 10-minute candle, isn't that many independent observations.
    span = np.array([duration(c.split("@")[1]) / base for c in columns])
    best: dict[int, dict[str, Any]] = {}
    for h in horizons:
        a = total[h]
        n = np.maximum(a["ic_n"], 1)
        mean_ic = a["ic_sum"] / n
        # Stouffer: each symbol's correlation as a z-score, combined over the symbols.
        t = a["z_sum"] / np.sqrt(n) / np.sqrt(np.maximum(span, h))
        t[a["ic_n"] < 1] = 0.0
        with np.errstate(all="ignore"):
            edge = a["strong_edge"] / np.maximum(a["strong_n"], 1)
            hit = a["strong_wins"] / np.maximum(a["strong_n"], 1)
        for i, col in enumerate(columns):
            entry = {
                "feature": col, "t": float(t[i]), "ic": float(mean_ic[i]), "horizon": h,
                "edge_bps": float(edge[i] * 1e4), "hit": float(hit[i]),
                "signals": int(a["strong_n"][i]), "symbols": int(a["ic_n"][i]),
            }
            if i not in best or abs(entry["t"]) > abs(best[i]["t"]):
                best[i] = entry
    board = []
    for i in range(len(columns)):
        e = best[i]
        e["sign"] = 1 if e["t"] >= 0 else -1  # follow it (+1) or fade it (-1)
        if e["sign"] < 0:  # report how it does used the way the bot will use it
            e["hit"], e["edge_bps"] = 1.0 - e["hit"], -e["edge_bps"]
        e["family"] = family_of(e["feature"])
        board.append(e)
    board.sort(key=lambda e: -abs(e["t"]))
    return board


# ------------------------------------------------------------------ patterns


def pairs_job(job: tuple[list[str], tuple[int, int], list[str], list[int], int]) -> dict[str, Any]:
    symbols, window, cols, signs, h = job
    store: FeatureStore = _W["store"]
    k = len(cols)
    sign = np.array(signs, dtype=np.float32)
    out = {name: np.zeros((k, k)) for name in ("nl", "sl", "ns", "ss")}
    for symbol in symbols:
        rows = _rows(symbol, window)
        if rows.stop - rows.start < 200:
            continue
        s = np.nan_to_num(store.load(symbol, cols)[rows] * sign)
        f = _forward(_bars(symbol), rows, h)
        ok = np.isfinite(f)
        s, f = s[ok], f[ok]
        lim = np.percentile(np.abs(f), 99) if len(f) else 0.0
        f = np.clip(f, -lim, lim)
        long_ = (s >= AGREE).astype(np.float64)
        short = (s <= -AGREE).astype(np.float64)
        out["nl"] += long_.T @ long_
        out["sl"] += long_.T @ (long_ * f[:, None])
        out["ns"] += short.T @ short
        out["ss"] += short.T @ (short * f[:, None])
    return out


def triples_job(job: tuple[list[str], tuple[int, int], list[str], list[int], int,
                           list[tuple[int, int]]]) -> dict[str, Any]:
    symbols, window, cols, signs, h, pairs = job
    store: FeatureStore = _W["store"]
    k = len(cols)
    sign = np.array(signs, dtype=np.float32)
    n = np.zeros((len(pairs), k))
    edge = np.zeros((len(pairs), k))
    for symbol in symbols:
        rows = _rows(symbol, window)
        if rows.stop - rows.start < 200:
            continue
        s = np.nan_to_num(store.load(symbol, cols)[rows] * sign)
        f = _forward(_bars(symbol), rows, h)
        ok = np.isfinite(f)
        s, f = s[ok], f[ok]
        lim = np.percentile(np.abs(f), 99) if len(f) else 0.0
        f = np.clip(f, -lim, lim)
        long_ = s >= AGREE
        short = s <= -AGREE
        for p, (i, j) in enumerate(pairs):
            both_long = long_[:, i] & long_[:, j]
            both_short = short[:, i] & short[:, j]
            if both_long.any():
                third = long_[both_long]
                n[p] += third.sum(0)
                edge[p] += third.T.astype(np.float64) @ f[both_long]
            if both_short.any():
                third = short[both_short]
                n[p] += third.sum(0)
                edge[p] -= third.T.astype(np.float64) @ f[both_short]
    return {"n": n, "edge": edge}


def sample_job(job: tuple[list[str], tuple[int, int], list[str], int, int, int]) -> dict[str, Any]:
    """Random candles: indicator votes and the move after (for building packages)."""
    symbols, window, cols, h, per_symbol, seed = job
    store: FeatureStore = _W["store"]
    rng = np.random.default_rng(seed)
    xs, fs, cs = [], [], []
    for symbol in symbols:
        rows = _rows(symbol, window)
        if rows.stop - rows.start < 200:
            continue
        f = _forward(_bars(symbol), rows, h)
        idx = np.flatnonzero(np.isfinite(f))
        if not len(idx):
            continue
        pick = np.sort(rng.choice(idx, size=min(per_symbol, len(idx)), replace=False))
        x = store.load(symbol, cols)[rows][pick]
        xs.append(np.nan_to_num(x).astype(np.float32))
        fs.append(f[pick])
        cost = _W["costs"].get(_W["meta"].get(symbol, {}).get("class", "stock"), 0.0)
        cs.append(np.full(len(pick), 2 * cost))  # a round trip: in and out
    if not xs:
        return {"x": np.zeros((0, len(cols)), dtype=np.float32), "f": np.zeros(0),
                "cost": np.zeros(0)}
    return {"x": np.concatenate(xs), "f": np.concatenate(fs), "cost": np.concatenate(cs)}


# ------------------------------------------------------------------ backtests


def trades_job(job: tuple[list[dict[str, Any]], list[str], tuple[int, int]]) -> list[Trades]:
    """Each package's trades on this chunk of symbols, in this window."""
    packages, symbols, window = job
    store: FeatureStore = _W["store"]
    pkgs = [Package.from_dict(p) for p in packages]
    out: list[list[Trades]] = [[] for _ in pkgs]
    for symbol in symbols:
        rows = _rows(symbol, window)
        if rows.stop - rows.start < 50:
            continue
        bars = _bars(symbol).iloc[rows]
        meta = _W["meta"].get(symbol, {})
        cost = _W["costs"].get(meta.get("class", "stock"), 0.0)
        arrays = (bars["open"].to_numpy(), bars["high"].to_numpy(), bars["low"].to_numpy(),
                  bars["close"].to_numpy(), bars["atr"].to_numpy(), ns(bars.index))
        needed = sorted({f for p in pkgs for f in p.features})
        matrix = store.load(symbol, needed)[rows]
        where = {f: i for i, f in enumerate(needed)}
        for i, pkg in enumerate(pkgs):
            cols = [where[f] for f in pkg.features]
            score = pkg.score(matrix[:, cols])
            out[i].append(simulate_symbol(pkg, score, *arrays,
                                          can_short=bool(meta.get("shortable", True)),
                                          cost=cost, symbol=symbol))
    return [Trades.concat(parts) for parts in out]
