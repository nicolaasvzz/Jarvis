"""The workbench for improving the sweep setup by hand (by Claude, every 30 minutes).

It measures; it never decides. History is split three ways, by time, for each
profile:

* **learn** (first 50%): look at it as much as you like;
* **check** (next 30%): a change is only kept if it's better here too;
* **final** (last 20%): locked. ``bench`` never shows it. ``final`` tests the
  chosen versions on it once, and every look raises the bar for the next one.

The versions being worked on live in ``sweep_champions.json`` (one per profile),
and the reasoning behind every change in ``SWEEP-JOURNAL.md``.

    python -m investment_bot sweep-bench                      # the champions, with breakdowns
    python -m investment_bot sweep-bench --try '{"swing": {"sides": 1}}'   # a change, side by side
    python -m investment_bot sweep-final                      # the locked test; writes sweep.json
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from ..lab.setups import luck_bar
from ..reports import Report
from . import data
from .engine import DEFAULT, PROFILES, Engine, Params
from .prove import STATE_FILE as PROOF_FILE
from .prove import quarters, stats

CHAMPS_FILE = "sweep_champions.json"
BENCH_FILE = "sweep_bench.json"
LEARN_END, CHECK_END = 0.5, 0.8
MIN_TRADES = 30

_FRAMES: dict[tuple[str, str], pd.DataFrame] = {}


def _frame(symbol: str, tf: str) -> pd.DataFrame | None:
    if (symbol, tf) not in _FRAMES:
        path = data.CACHE / f"{symbol}_{tf}.pkl"
        if not path.exists():
            return None
        _FRAMES[(symbol, tf)] = pd.read_pickle(path)
    return _FRAMES[(symbol, tf)]


def _job(job: tuple[str, str, float, list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    symbol, profile, point, plist = job
    pf = PROFILES[profile]
    fr = [_frame(symbol, tf) for tf in (pf.htf, pf.mtf, pf.ltf)]
    if any(f is None or not len(f) for f in fr):
        return [[] for _ in plist]
    return [[t.to_dict() for t in Engine(symbol, pf, fr[0], fr[1], fr[2], Params(**p), point).run()]
            for p in plist]


def load_champions() -> dict[str, Params]:
    try:
        raw = json.loads(Path(CHAMPS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    names = {f.name for f in fields(Params)}
    return {p: replace(DEFAULT, **{k: v for k, v in (raw.get(p) or {}).items() if k in names})
            for p in PROFILES}


def save_champions(champs: dict[str, Params]) -> None:
    Path(CHAMPS_FILE).write_text(json.dumps({p: asdict(v) for p, v in champs.items()}, indent=1),
                                 encoding="utf-8")


def symbols_for(profile: str, universe: list[str] | None = None) -> list[str]:
    pf = PROFILES[profile]
    return [s for s in (universe or data.UNIVERSE)
            if all((data.CACHE / f"{s}_{tf}.pkl").exists() for tf in (pf.htf, pf.mtf, pf.ltf))]


def run_versions(versions: dict[str, list[Params]], workers: int | None = None,
                 universe: list[str] | None = None) -> dict[str, list[list[dict[str, Any]]]]:
    """Every symbol's trades for each version: {profile: [trades of version 0, ...]}."""
    jobs, keys = [], []
    for p, plist in versions.items():
        for s in symbols_for(p, universe):
            jobs.append((s, p, data.offline_point(s), [asdict(v) for v in plist]))
            keys.append(p)
    out: dict[str, list[list[dict[str, Any]]]] = {p: [[] for _ in v] for p, v in versions.items()}
    with ProcessPoolExecutor(workers or max(1, (os.cpu_count() or 2) - 1)) as pool:
        for p, res in zip(keys, pool.map(_job, jobs)):
            for i, tr in enumerate(res):
                out[p][i].extend(tr)
    return out


def windows(trades: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    """Fixed per profile by its data: the same for every version."""
    t0 = min(t["exit_time"] for t in trades)
    t1 = max(t["exit_time"] for t in trades) + 1
    return t0, int(t0 + LEARN_END * (t1 - t0)), int(t0 + CHECK_END * (t1 - t0)), t1


def _windows_for(profile: str, trades: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    """The profile's windows, fixed on first use so every later version is cut the same way."""
    path = Path("sweep_windows.json")
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if profile not in saved:
        saved[profile] = list(windows(trades))
        path.write_text(json.dumps(saved), encoding="utf-8")
    return tuple(saved[profile])  # type: ignore[return-value]


def breakdown(trades: list[dict[str, Any]], key: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    groups: dict[Any, list[float]] = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t["r"])
    rows = []
    for k, rs in groups.items():
        rows.append({"group": k, "trades": len(rs), "win%": round(100 * sum(r > 0 for r in rs) / len(rs), 1),
                     "avg_r": round(sum(rs) / len(rs), 3), "sum_r": round(sum(rs), 1)})
    return sorted(rows, key=lambda r: r["sum_r"])


def group_of(symbol: str) -> str:
    """forex, metals or indices: a change should hold up in more than one of them."""
    if symbol.startswith(("XAU", "XAG", "XPT", "XPD")):
        return "metals"
    if len(symbol) == 6 and symbol.isalpha():
        return "forex"
    return "indices"


def _rr(t: dict[str, Any]) -> float:
    risk = abs(t["entry"] - t["stop"])
    return abs(t["target"] - t["entry"]) / risk if risk else 0.0


def bench(trial: dict[str, dict[str, Any]] | None = None, profiles: list[str] | None = None,
          detail: bool = True, say: Callable[[str], None] = print) -> dict[str, Any]:
    """Measure the champions (and a trial change per profile) on learn and check only."""
    champs = load_champions()
    profiles = profiles or list(trial or PROFILES)
    def trials(p: str) -> list[Params]:
        t = (trial or {}).get(p) or []
        return [replace(champs[p], **x) for x in ([t] if isinstance(t, dict) else t)]

    versions = {p: [champs[p]] + trials(p) for p in profiles}
    res = run_versions(versions)
    out: dict[str, Any] = {"at": datetime.now().isoformat(timespec="seconds"), "profiles": {}}
    for p in profiles:
        rows = []
        for i, v in enumerate(versions[p]):
            tr = res[p][i]
            if not tr:
                rows.append({"version": v.label(), "learn": stats([], 0, 1), "check": stats([], 0, 1)})
                continue
            t0, a, b, _ = _windows_for(p, res[p][0] or tr)
            seen = [t for t in tr if t["exit_time"] < b]          # the final part never leaves here
            row = {"version": (f"TRIAL {i} " if i else "CHAMP ") + v.label(),
                   "learn": stats(seen, t0, a), "check": stats(seen, a, b)}
            row["check_groups"] = {g: stats([t for t in seen if group_of(t["symbol"]) == g], a, b)
                                   for g in ("forex", "metals", "indices")}
            if detail and i == 0:
                learn_part = [t for t in seen if t["exit_time"] < a]
                row["by_symbol"] = breakdown(learn_part, lambda t: t["symbol"])
                row["by_side"] = breakdown(learn_part, lambda t: "long" if t["side"] == 1 else "short")
                row["by_exit"] = breakdown(learn_part, lambda t: t["reason"])
                row["by_hour"] = breakdown(learn_part, lambda t: (t["signal_time"] // 3600) % 24 // 3 * 3)
                row["by_rr"] = breakdown(learn_part, lambda t: min(int(_rr(t)), 10))
                row["by_hold_h"] = breakdown(learn_part, lambda t: min(int((t["exit_time"] - t["fill_time"]) / 3600), 48) // 6 * 6)
            rows.append(row)
        out["profiles"][p] = rows
        for row in rows:
            L, C = row["learn"], row["check"]
            say(f"{p:<8} {row['version'][:110]}")
            say(f"         learn: {L['trades']} tr, win {L['win_pct']}%, avg {L['avg_r']:+.3f}R, t {L['t']:+.2f} | "
                f"check: {C['trades']} tr, win {C['win_pct']}%, avg {C['avg_r']:+.3f}R, t {C['t']:+.2f}")
            if row.get("check_groups"):
                say("         check by group: " + ", ".join(
                    f"{g} {x['avg_r']:+.3f}R ({x['trades']})" for g, x in row["check_groups"].items()))
    Path(BENCH_FILE).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


def final(say: Callable[[str], None] = print) -> dict[str, Any]:
    """The locked test, once: each champion on the last 20%. Writes sweep.json for the trader."""
    champs = load_champions()
    state = json.loads(Path(CHAMPS_FILE + ".looks").read_text()) if Path(CHAMPS_FILE + ".looks").exists() else {"looks": 0}
    state["looks"] += 1
    Path(CHAMPS_FILE + ".looks").write_text(json.dumps(state))
    bar = max(1.65, luck_bar(state["looks"] * len(PROFILES)))
    res = run_versions({p: [champs[p]] for p in PROFILES})
    proof: dict[str, Any] = {"updated": datetime.now().isoformat(timespec="seconds"),
                             "source": "Claude's improvement sessions (sweep-final)",
                             "luck_bar": round(bar, 2), "tries": state["looks"], "profiles": {}}
    for p in PROFILES:
        tr = res[p][0]
        if not tr:
            continue
        t0, a, b, t1 = _windows_for(p, tr)
        fin, q = stats(tr, b, t1), quarters(tr, b, t1)
        why = []
        if fin["trades"] < MIN_TRADES:
            why.append(f"only {fin['trades']} final trades (needs {MIN_TRADES})")
        if fin["t"] < bar:
            why.append(f"final t={fin['t']:.2f} is under the bar {bar:.2f}")
        if sum(x > 0 for x in q) < 3:
            why.append(f"won {sum(x > 0 for x in q)} of 4 final quarters (needs 3)")
        if fin["pf"] < 1.2:
            why.append(f"profit factor {fin['pf']:.2f} (needs 1.2)")
        proof["profiles"][p] = {
            "words": PROFILES[p].words, "train_from": _day(t0), "test_from": _day(b), "test_to": _day(t1),
            "best": {"label": champs[p].label(), "params": asdict(champs[p]), "train": stats(tr, t0, a),
                     "check": stats(tr, a, b), "test": fin, "quarters": q},
            "video_rules": {"label": DEFAULT.label(), "test": {}}, "proven": not why, "why_not": why,
            "by_symbol": breakdown([t for t in tr if t["exit_time"] >= b], lambda t: t["symbol"]),
            "versions": [], "recent_trades": sorted(tr, key=lambda t: t["exit_time"])[-40:],
        }
        say(f"{p} FINAL: {champs[p].label()}: {fin['trades']} trades, win {fin['win_pct']}%, avg {fin['avg_r']:+.3f}R, "
            f"t {fin['t']:+.2f}, quarters {q}. " + ("PROVEN" if not why else "not proven: " + "; ".join(why)))
    Path(PROOF_FILE).write_text(json.dumps(proof, indent=1, default=str), encoding="utf-8")
    rep = Report("sweep", ("Proven: " + ", ".join(p for p, x in proof["profiles"].items() if x["proven"]))
                 if any(x["proven"] for x in proof["profiles"].values()) else "Nothing proven on the locked test",
                 "good" if any(x["proven"] for x in proof["profiles"].values()) else "bad",
                 title="Sweep setup: the locked final test", subtitle=f"look #{state['looks']}, bar t={bar:.2f}")
    for p, x in proof["profiles"].items():
        b_ = x["best"]
        rep.bullets(f"{p}: {x['words']}", [
            f"Version: {b_['label']}.",
            f"Learn: {b_['train']['trades']} trades, avg {b_['train']['avg_r']}R, t={b_['train']['t']}.",
            f"Check: {b_['check']['trades']} trades, avg {b_['check']['avg_r']}R, t={b_['check']['t']}.",
            f"FINAL ({x['test_from']} to {x['test_to']}): {b_['test']['trades']} trades, win {b_['test']['win_pct']}%, "
            f"avg {b_['test']['avg_r']}R, t={b_['test']['t']}, quarters {b_['quarters']}, at 1% a trade "
            f"{b_['test']['return_pct']}% (worst drop {b_['test']['max_dd_pct']}%).",
            "PROVEN" if x["proven"] else "Not proven: " + "; ".join(x["why_not"]) + ".",
        ])
        rep.table(f"{p}: final part by symbol", x["by_symbol"])
    path = rep.save()
    if path:
        say(f"Report: {path}")
    return proof


def _day(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def fresh_final(profiles: list[str] | None = None, say: Callable[[str], None] = print) -> dict[str, Any]:
    """The final test on markets never used for tuning (data.FRESH), over their whole history."""
    champs = load_champions()
    profiles = profiles or ["swing", "intraday"]
    res = run_versions({p: [champs[p]] for p in profiles}, universe=data.FRESH)
    proof: dict[str, Any] = {"updated": datetime.now().isoformat(timespec="seconds"),
                             "source": "final test on fresh markets (sweep-fresh)", "luck_bar": 1.65,
                             "profiles": {}}
    for p in profiles:
        tr = res[p][0]
        if not tr:
            say(f"{p}: no fresh-market candles cached")
            continue
        t0 = min(t["exit_time"] for t in tr)
        t1 = max(t["exit_time"] for t in tr) + 1
        fin, q = stats(tr, t0, t1), quarters(tr, t0, t1)
        groups = {g: stats([t for t in tr if group_of(t["symbol"]) == g], t0, t1)
                  for g in ("forex", "metals", "indices")}
        why = []
        if fin["trades"] < MIN_TRADES:
            why.append(f"only {fin['trades']} trades (needs {MIN_TRADES})")
        if fin["t"] < 1.65:
            why.append(f"t={fin['t']:.2f} is under 1.65")
        if sum(x > 0 for x in q) < 3:
            why.append(f"won {sum(x > 0 for x in q)} of 4 quarters (needs 3)")
        if fin["pf"] < 1.2:
            why.append(f"profit factor {fin['pf']:.2f} (needs 1.2)")
        proof["profiles"][p] = {
            "words": PROFILES[p].words, "train_from": _day(t0), "test_from": _day(t0), "test_to": _day(t1),
            "best": {"label": champs[p].label(), "params": asdict(champs[p]), "train": {}, "test": fin,
                     "quarters": q},
            "video_rules": {"label": DEFAULT.label(), "test": {}}, "proven": not why, "why_not": why,
            "by_symbol": breakdown(tr, lambda t: t["symbol"]), "versions": [],
            "recent_trades": sorted(tr, key=lambda t: t["exit_time"])[-40:],
        }
        say(f"{p} FRESH MARKETS: {fin['trades']} trades, win {fin['win_pct']}%, avg {fin['avg_r']:+.3f}R, "
            f"t {fin['t']:+.2f}, quarters {q}; by group "
            + ", ".join(f"{g} {x['avg_r']:+.3f}R ({x['trades']})" for g, x in groups.items()) + ". "
            + ("PROVEN" if not why else "not proven: " + "; ".join(why)))
    Path(PROOF_FILE).write_text(json.dumps(proof, indent=1, default=str), encoding="utf-8")
    proven = [p for p, x in proof["profiles"].items() if x["proven"]]
    rep_ = Report("sweep", ("Proven on fresh markets: " + ", ".join(proven)) if proven else
                  "Nothing proven on fresh markets", "good" if proven else "bad",
                  title="Sweep setup: final test on fresh markets",
                  subtitle=f"{len(data.FRESH)} markets never used for tuning, whole history")
    for p, x in proof["profiles"].items():
        b_ = x["best"]
        rep_.bullets(f"{p}: {x['words']}", [
            f"Version: {b_['label']}.",
            f"{x['train_from']} to {x['test_to']}: {b_['test']['trades']} trades, win {b_['test']['win_pct']}%, "
            f"avg {b_['test']['avg_r']}R, t={b_['test']['t']}, quarters {b_['quarters']}, at 1% a trade "
            f"{b_['test']['return_pct']}% (worst drop {b_['test']['max_dd_pct']}%).",
            "PROVEN" if x["proven"] else "Not proven: " + "; ".join(x["why_not"]) + ".",
        ])
        rep_.table(f"{p}: by market", x["by_symbol"])
    path = rep_.save()
    if path:
        say(f"Report: {path}")
    return proof
