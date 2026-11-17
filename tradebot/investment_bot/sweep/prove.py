"""Prove the sweep setup, or don't: an honest backtest on MT5 history.

For each profile (swing, intraday, scalp):

1. Every version in ``engine.variants()`` runs over every symbol's full history.
2. Only the first 60% of the history (the *training* part) picks the best
   version, by the t-statistic of its daily results.
3. The last 40% (the *test* part, never looked at for the choice) has to prove
   it. It must beat the **luck bar** (the t that the best of all versions ever
   tried would reach by luck; every try counts, kept in ``sweep.json``), win in
   at least 3 of the test part's 4 quarters, have a profit factor of 1.2 or
   more, and show at least 30 trades.

Only a proven profile may trade, and on the demo first. The exact rules from
the videos (``engine.DEFAULT``) are always reported too, whatever won.
"""
from __future__ import annotations

import json
import math
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..lab.setups import luck_bar
from ..reports import Report
from . import data
from .engine import DEFAULT, PROFILES, Engine, Params, Trade, variants

STATE_FILE = "sweep.json"
TRAIN_SHARE = 0.6
RISK = 0.01        # share of the account risked per trade in the simulation
MAX_OPEN = 3       # trades open at once
MIN_TRADES = 30


def _run(job: tuple[str, str, dict[str, Any], float, list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    """One symbol and profile, every version. Runs in a worker process."""
    symbol, profile, _spec, point, plist = job
    pf = PROFILES[profile]
    frames = {}
    for tf in (pf.htf, pf.mtf, pf.ltf):
        path = data.CACHE / f"{symbol}_{tf}.pkl"
        if not path.exists():
            return [[] for _ in plist]
        frames[tf] = pd.read_pickle(path)
    out = []
    for pd_ in plist:
        eng = Engine(symbol, pf, frames[pf.htf], frames[pf.mtf], frames[pf.ltf], Params(**pd_), point)
        out.append([t.to_dict() for t in eng.run()])
    return out


def stats(trades: list[dict[str, Any]], t0: int, t1: int) -> dict[str, Any]:
    """Results of the trades that closed between ``t0`` and ``t1`` (epoch seconds)."""
    tr = [t for t in trades if t0 <= t["exit_time"] < t1]
    days = max((t1 - t0) / 86400, 2)
    r = np.array([t["r"] for t in tr]) if tr else np.zeros(0)
    if not len(r):
        return {"trades": 0, "win_pct": 0.0, "avg_r": 0.0, "sum_r": 0.0, "pf": 0.0, "t": 0.0,
                "per_week": 0.0, "return_pct": 0.0, "max_dd_pct": 0.0}
    per_day: dict[int, float] = {}
    for t in tr:
        d = t["exit_time"] // 86400
        per_day[d] = per_day.get(d, 0.0) + t["r"]
    full = np.zeros(int(round(days)))
    full[:len(per_day)] = list(per_day.values())
    sd = full.std(ddof=1)
    tstat = float(full.mean() / sd * math.sqrt(len(full))) if sd > 0 else 0.0
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    ret, dd = simulate(tr)
    return {
        "trades": int(len(r)), "win_pct": round(float((r > 0).mean() * 100), 1),
        "avg_r": round(float(r.mean()), 3), "sum_r": round(float(r.sum()), 1),
        "pf": round(float(gains / losses), 2) if losses > 0 else 99.0,
        "t": round(tstat, 2), "per_week": round(len(r) / days * 7, 2),
        "return_pct": round(ret * 100, 1), "max_dd_pct": round(dd * 100, 1),
    }


def simulate(trades: list[dict[str, Any]], risk: float = RISK, max_open: int = MAX_OPEN) -> tuple[float, float]:
    """Compound the trades risking ``risk`` of the account each, at most
    ``max_open`` at a time (later ones are skipped). Returns (return, max drawdown)."""
    eq = peak = 1.0
    dd = 0.0
    open_: list[tuple[int, float]] = []   # (exit_time, r)
    for t in sorted(trades, key=lambda t: t["fill_time"]):
        for e in sorted(x for x in open_ if x[0] <= t["fill_time"]):
            open_.remove(e)
        if len(open_) >= max_open:
            continue
        open_.append((t["exit_time"], t["r"]))
        eq *= 1 + risk * t["r"]
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    return eq - 1, dd


def quarters(trades: list[dict[str, Any]], t0: int, t1: int) -> list[float]:
    edges = np.linspace(t0, t1, 5).astype(int)
    return [round(sum(t["r"] for t in trades if a <= t["exit_time"] < b), 1)
            for a, b in zip(edges[:-1], edges[1:])]


def _load() -> dict[str, Any]:
    try:
        return json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def prove(symbols: list[str] | None = None, profiles: list[str] | None = None,
          say: Callable[[str], None] = print, refresh: bool = True) -> dict[str, Any]:
    symbols = symbols or data.UNIVERSE
    profiles = profiles or list(PROFILES)
    plist = variants()
    if DEFAULT not in plist:
        plist.insert(0, DEFAULT)
    state = _load()
    tries = int(state.get("tries", 0)) + len(plist) * len(profiles)
    bar = max(luck_bar(tries), 1.65)

    say("Connecting to MT5 and topping up the candle cache...")
    mt5 = data.connect()
    specs: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        sp = data.spec(mt5, sym)
        if sp is None:
            say(f"  {sym}: not offered by this broker, skipped")
            continue
        specs[sym] = sp.to_dict()
        for tf in {tf for p in profiles for tf in (PROFILES[p].htf, PROFILES[p].mtf, PROFILES[p].ltf)}:
            data.cached(mt5, sym, tf, refresh=refresh)
    say(f"{len(specs)} symbols. {len(plist)} versions x {len(profiles)} profiles; "
        f"{tries} tries in all, so the luck bar is t={bar:.2f}.")

    jobs = [(s, p, specs[s], specs[s]["point"], [asdict(v) for v in plist]) for p in profiles for s in specs]
    results: dict[str, dict[str, list[list[dict[str, Any]]]]] = {p: {} for p in profiles}
    with ProcessPoolExecutor(max(1, (os.cpu_count() or 2) - 1)) as pool:
        for (s, p, *_), res in zip(jobs, pool.map(_run, jobs)):
            results[p][s] = res
            say(f"  {p:<8} {s:<7} {sum(len(r) for r in res) // len(res)} trades per version")

    out: dict[str, Any] = {"updated": datetime.now().isoformat(timespec="seconds"),
                           "tries": tries, "luck_bar": round(bar, 2), "profiles": {}}
    for p in profiles:
        per_sym = results[p]
        times = [t["exit_time"] for res in per_sym.values() for r in res for t in r]
        if not times:
            continue
        t0, t1 = min(times), max(times) + 1
        split = int(t0 + TRAIN_SHARE * (t1 - t0))
        versions = []
        for vi, v in enumerate(plist):
            trades = [t for res in per_sym.values() for t in res[vi]]
            versions.append({"params": asdict(v), "label": v.label(), "trades": trades,
                             "train": stats(trades, t0, split), "test": stats(trades, split, t1)})
        ok = [x for x in versions if x["train"]["trades"] >= MIN_TRADES] or versions
        best = max(ok, key=lambda x: x["train"]["t"])
        video = versions[plist.index(DEFAULT)]
        test = best["test"]
        q = quarters(best["trades"], split, t1)
        reasons = []
        if test["trades"] < MIN_TRADES:
            reasons.append(f"only {test['trades']} test trades (needs {MIN_TRADES})")
        if test["t"] < bar:
            reasons.append(f"test t={test['t']:.2f} is under the luck bar {bar:.2f}")
        if sum(x > 0 for x in q) < 3:
            reasons.append(f"won {sum(x > 0 for x in q)} of 4 test quarters (needs 3)")
        if test["pf"] < 1.2:
            reasons.append(f"profit factor {test['pf']:.2f} (needs 1.2)")
        by_symbol = []
        for s, res in per_sym.items():
            tr = res[versions.index(best)]
            st = stats(tr, split, t1)
            by_symbol.append({"Symbol": s, "Test trades": st["trades"], "Win %": st["win_pct"],
                              "Sum R": st["sum_r"], "Avg R": st["avg_r"]})
        out["profiles"][p] = {
            "words": PROFILES[p].words,
            "train_from": _day(t0), "test_from": _day(split), "test_to": _day(t1),
            "best": {"label": best["label"], "params": best["params"],
                     "train": best["train"], "test": test, "quarters": q},
            "video_rules": {"label": video["label"], "train": video["train"], "test": video["test"]},
            "proven": not reasons, "why_not": reasons,
            "by_symbol": sorted(by_symbol, key=lambda r: -r["Sum R"]),
            "versions": [{"label": x["label"], "train": x["train"], "test": x["test"]} for x in versions],
            "recent_trades": sorted(best["trades"], key=lambda t: t["exit_time"])[-40:],
        }
        verdict = "PROVEN" if not reasons else "not proven: " + "; ".join(reasons)
        say(f"{p}: best on training = {best['label']}. Test: {test['trades']} trades, "
            f"win {test['win_pct']}%, avg {test['avg_r']}R, t={test['t']}, quarters {q}. {verdict}")

    Path(STATE_FILE).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    path = report(out)
    if path:
        say(f"Report: {path}")
    return out


def _day(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def report(out: dict[str, Any]) -> Path | None:
    profs = out["profiles"]
    proven = [p for p, x in profs.items() if x["proven"]]
    summary = (f"Proven: {', '.join(proven)}" if proven else "Nothing proven yet") + \
        f" (luck bar t={out['luck_bar']})"
    rep = Report("sweep", summary, "good" if proven else "bad", title="Sweep setup backtest",
                 subtitle="liquidity sweep + zone + break of structure, on MT5 history")
    for p, x in profs.items():
        b = x["best"]
        rep.stat(f"{p} test t", b["test"]["t"], "good" if x["proven"] else "bad")
        rep.stat(f"{p} test avg R", b["test"]["avg_r"], "good" if b["test"]["avg_r"] > 0 else "bad")
    rep.text("How it was judged",
             "The first 60% of each profile's history chose the best version; the last 40% had to "
             "prove it: beat the luck bar, win 3 of 4 quarters, profit factor 1.2+, 30+ trades. "
             "Results are in R: 1R is what a trade risks. Costs: at least 1 basis point each way.")
    for p, x in profs.items():
        b, v = x["best"], x["video_rules"]
        rep.bullets(f"{p}: {x['words']}", [
            f"Training {x['train_from']} to {x['test_from']}, test {x['test_from']} to {x['test_to']}.",
            f"Best version on training: {b['label']}.",
            f"Test: {b['test']['trades']} trades ({b['test']['per_week']}/week), win {b['test']['win_pct']}%, "
            f"avg {b['test']['avg_r']}R, profit factor {b['test']['pf']}, t={b['test']['t']}, "
            f"quarters {b['quarters']}, at 1% a trade {b['test']['return_pct']}% "
            f"(worst drop {b['test']['max_dd_pct']}%).",
            f"The videos' exact rules ({v['label']}): test avg {v['test']['avg_r']}R over "
            f"{v['test']['trades']} trades, t={v['test']['t']}.",
            "PROVEN" if x["proven"] else "Not proven: " + "; ".join(x["why_not"]) + ".",
        ])
        rep.table(f"{p}: every version", [
            {"Version": y["label"], "Train trades": y["train"]["trades"], "Train t": y["train"]["t"],
             "Train avg R": y["train"]["avg_r"], "Test trades": y["test"]["trades"],
             "Test t": y["test"]["t"], "Test avg R": y["test"]["avg_r"]} for y in x["versions"]])
        rep.table(f"{p}: by symbol (test part)", x["by_symbol"])
    return rep.save()


def load_champions(path: str | Path = STATE_FILE) -> dict[str, dict[str, Any]]:
    """The proven profiles and the version each trades."""
    try:
        out = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {p: x for p, x in (out.get("profiles") or {}).items() if x.get("proven")}


def params_of(champ: dict[str, Any]) -> Params:
    return replace(DEFAULT, **champ["best"]["params"])


__all__ = ["prove", "stats", "simulate", "load_champions", "params_of", "Trade"]
