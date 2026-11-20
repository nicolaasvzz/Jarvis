"""Part 6: one strict judge for every new strategy.

Strategies hand over POSITIONS, never results. A strategy is a pure function

    positions = strategy(frames, params)

where ``frames`` maps a market to its candles (columns time, open, high, low, close,
tick_volume, spread; ``time`` = bar start, epoch seconds, broker server time) and the result
maps a market to a ``pd.Series`` of target positions indexed by that market's ``time`` values.
A position is the signed share of the account held in that market's notional (1.0 = the
account's whole value long; -0.5 = half of it short), decided at the CLOSE of the bar it's
indexed by. The judge does everything else, the same way for every strategy:

* **No lookahead.** A position decided at the close of bar i is filled at the OPEN of bar
  i+1 and earns from there to the open of bar i+2 (open-to-open P&L).
* **Costs.** Every change in position pays the bigger of ``COST_BP`` and the candle's own
  spread, as a share of price.
* **Financing**, per calendar night held (a night = a server-date boundary crossed):
  currency pairs earn or pay their interest gap (base rate - quote rate, ``financing.RATES``)
  minus ``FX_MARKUP`` a year; indices and metals pay the US rate + ``CFD_MARKUP`` on longs
  and ``CFD_MARKUP`` - US rate on shorts.
* **Locked years.** ``frames(dev=True)`` cuts every market at ``DEV_END`` (1 Jan 2019), so a
  strategy developed with it cannot have seen 2019-2026. Only ``locked_look`` / ``locked_look_portfolio`` read
  the locked years, at a size chosen on the development years (the multiple whose development
  worst drop is 25%, capped so total positions never exceed ``max_gross`` = 10x the account on
  any development day), once per name, and record it.

Scoring: the development years are cut into four cells, market half A or B (balanced by
group, ``HALF_A``/``HALF_B``) x first or second half of that market half's time span. A
strategy is a *candidate* only if it makes money in all four cells and its whole-development
t-statistic is at least 2.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import data
from .bench import group_of
from .financing import POLICY, RATES

CACHE = Path(os.environ.get("SWEEP_CACHE", str(data.CACHE)))
SCORE_START = int(datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp())   # older candles = warm-up only
DEV_END = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp())
END = int(datetime(2026, 11, 18, tzinfo=timezone.utc).timestamp())
COST_BP = 1.0
FX_MARKUP = 0.01
CFD_MARKUP = 0.025
DD_CAP = 0.25
DAY = 86400
MARKETS = list(data.UNIVERSE) + list(data.FRESH)

Strategy = Callable[[dict[str, pd.DataFrame], dict[str, Any]], dict[str, pd.Series]]


def _halves() -> tuple[list[str], list[str]]:
    a: list[str] = []
    b: list[str] = []
    for g in ("forex", "metals", "indices"):
        for i, m in enumerate(sorted(m for m in MARKETS if group_of(m) == g)):
            (a if i % 2 == 0 else b).append(m)
    return a, b


HALF_A, HALF_B = _halves()


def frames(symbols: list[str] | None = None, tf: str = "D1", dev: bool = True,
           cache: Path | None = None) -> dict[str, pd.DataFrame]:
    """Candles per market. ``dev=True`` (the default) cuts each market before ``DEV_END``."""
    out: dict[str, pd.DataFrame] = {}
    for s in symbols or MARKETS:
        path = (cache or CACHE) / f"{s}_{tf}.pkl"
        if not path.exists():
            continue
        df = pd.read_pickle(path)
        if dev:
            df = df[df["time"] < DEV_END]
        df = df.reset_index(drop=True)
        if len(df) > 2:
            out[s] = df
    return out


# ------------------------------------------------------------------ costs ----

def is_fx(symbol: str) -> bool:
    return len(symbol) == 6 and symbol.isalpha() and symbol[:3] in RATES and symbol[3:] in RATES


def _year_rate(table: dict[int, float], years: np.ndarray) -> np.ndarray:
    lo, hi = min(table), max(table)
    return np.array([table[int(min(max(y, lo), hi))] for y in years], dtype=float)


def financing(symbol: str, held: np.ndarray, t: np.ndarray, nights: np.ndarray) -> np.ndarray:
    """Financing as a share of the account (+ = earned) for holding ``held`` from time ``t``
    for ``nights`` nights."""
    held = np.asarray(held, dtype=float)
    years = pd.to_datetime(np.asarray(t), unit="s").year.to_numpy()
    if is_fx(symbol):
        gap = _year_rate(RATES[symbol[:3]], years) - _year_rate(RATES[symbol[3:]], years)
        yearly = held * gap - np.abs(held) * FX_MARKUP
    else:
        us = _year_rate(POLICY, years)
        yearly = -np.where(held > 0, held * (us + CFD_MARKUP),
                           np.where(held < 0, -held * (CFD_MARKUP - us), 0.0))
    return yearly / 365.0 * np.asarray(nights, dtype=float)


def market_pnl(symbol: str, df: pd.DataFrame, pos: pd.Series | None) -> pd.DataFrame:
    """Per bar: the position held, gross P&L, cost, financing and net (shares of the account)."""
    t = df["time"].to_numpy("int64")
    o = df["open"].to_numpy(float)
    c = df["close"].to_numpy(float)
    n = len(df)
    if pos is None or not len(pos):
        target = np.zeros(n)
    else:
        ps = pd.Series(pos, dtype=float)
        if not ps.index.isin(t).any():
            raise ValueError(f"{symbol}: positions must be indexed by the frame's 'time' values (epoch seconds)")
        target = ps.reindex(t).ffill().fillna(0.0).to_numpy()
    held = np.r_[0.0, target[:-1]]                      # decided at bar i-1's close, held through bar i
    oo = np.r_[o[1:] / o[:-1] - 1.0, 0.0]               # bar i's open to bar i+1's open
    point = data.offline_point(symbol)
    spread = df["spread"].to_numpy(float) * point / np.where(c > 0, c, np.nan) if "spread" in df else 0.0
    cost_frac = np.maximum(COST_BP * 1e-4, np.nan_to_num(spread, nan=COST_BP * 1e-4))
    change = np.abs(held - np.r_[0.0, held[:-1]])        # changes happen at bar i's open
    cost = change * cost_frac
    day = t // DAY
    nights = np.r_[day[1:] - day[:-1], 0]
    fin = financing(symbol, held, t, nights)
    gross = held * oo
    return pd.DataFrame({"time": t, "day": day, "held": held, "gross": gross, "cost": cost,
                         "fin": fin, "net": gross - cost + fin})


def daily_table(pnl: dict[str, pd.DataFrame], col: str = "net") -> pd.DataFrame:
    """Date x market table of P&L (0 where a market had no bar that date)."""
    cols = {s: p.groupby("day")[col].sum() for s, p in pnl.items()}
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index().fillna(0.0)


# ------------------------------------------------------------------ stats ----

def curve(r: np.ndarray, k: float = 1.0) -> tuple[float, float]:
    """(final equity multiple, worst drop) compounding k x the daily returns."""
    eq = peak = 1.0
    dd = 0.0
    for x in r:
        eq *= max(1.0 + k * x, 0.0)
        if eq <= 0:
            return 0.0, 1.0
        peak = max(peak, eq)
        dd = max(dd, 1.0 - eq / peak)
    return eq, dd


def scale_for_cap(r: np.ndarray, cap: float = DD_CAP, hi: float = 50.0) -> float:
    """The biggest multiple of these returns whose worst drop stays within ``cap``."""
    if curve(r, hi)[1] <= cap:
        return hi
    lo = 0.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if curve(r, mid)[1] <= cap:
            lo = mid
        else:
            hi = mid
    return lo


def stats(daily: pd.Series) -> dict[str, Any]:
    r = daily.to_numpy(float)
    n = len(r)
    if n < 20 or not np.any(r):
        return {"days": n, "sharpe": 0.0, "t": 0.0, "annual_pct": 0.0, "maxdd_1x_pct": 0.0, "years": 0.0}
    sd = r.std(ddof=1)
    days = daily.index.to_numpy()
    years = max((days[-1] - days[0]) / 365.25, 1 / 365.25)
    eq, dd = curve(r)
    return {"days": n, "years": round(float(years), 2),
            "sharpe": round(float(r.mean() / sd * math.sqrt(252)), 3) if sd > 0 else 0.0,
            "t": round(float(r.mean() / sd * math.sqrt(n)), 2) if sd > 0 else 0.0,
            "annual_pct": round(float(r.sum() / years * 100), 2),
            "maxdd_1x_pct": round(float(dd * 100), 1)}


def _split(index: pd.Index) -> int:
    days = index.to_numpy()
    return int(days[0] + (days[-1] - days[0]) // 2)


def cells(table: pd.DataFrame) -> dict[str, Any]:
    """Stats for market half A/B x first/second half of time (development years)."""
    out: dict[str, Any] = {"all": stats(table.sum(axis=1)) if len(table) else stats(pd.Series(dtype=float))}
    for name, half in (("A", HALF_A), ("B", HALF_B)):
        cols = [c for c in table.columns if c in half]
        if not cols:
            for k in ("", "1", "2"):
                out[name + k] = stats(pd.Series(dtype=float))
            continue
        series = table[cols].sum(axis=1)
        mid = _split(series.index)
        out[name] = stats(series)
        out[name + "1"] = stats(series[series.index <= mid])
        out[name + "2"] = stats(series[series.index > mid])
    return out


def gate(c: dict[str, Any]) -> dict[str, bool]:
    four = all(c[k]["sharpe"] > 0 for k in ("A1", "A2", "B1", "B2"))
    return {"all_four_cells_positive": four,
            "candidate": bool(four and c["all"]["t"] >= 2.0),
            "promising": bool(four and c["all"]["t"] >= 2.0 and c["all"]["sharpe"] >= 1.0)}


def summary(pnl: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Scores the development years from ``SCORE_START`` (earlier candles only warm a strategy up)."""
    pnl = {s: p[p["time"] >= SCORE_START] for s, p in pnl.items()}
    pnl = {s: p for s, p in pnl.items() if len(p)}
    net = daily_table(pnl, "net")
    c = cells(net)
    tot = {k: float(sum(p[k].sum() for p in pnl.values())) for k in ("gross", "cost", "fin", "net")}
    years = c["all"].get("years") or 1.0
    turnover = 0.0
    for p in pnl.values():
        h = p["held"].to_numpy()
        turnover += float(np.abs(h - np.r_[0.0, h[:-1]]).sum())
    by_group: dict[str, Any] = {}
    for g in ("forex", "metals", "indices"):
        cols = [s for s in net.columns if group_of(s) == g]
        by_group[g] = stats(net[cols].sum(axis=1)) if cols else None
    by_year = net.sum(axis=1).groupby(pd.to_datetime(net.index * DAY, unit="s").year).sum()
    return {"cells": c, "gate": gate(c),
            "totals_per_year_pct": {k: round(v / years * 100, 2) for k, v in tot.items()},
            "turnover_per_year": round(turnover / years, 1), "by_group": by_group,
            "by_year_pct": {int(y): round(float(v * 100), 2) for y, v in by_year.items()}}


def evaluate(strategy: Strategy, params: dict[str, Any], symbols: list[str] | None = None,
             tf: str = "D1", fr: dict[str, pd.DataFrame] | None = None) -> dict[str, Any]:
    """Development-years report for one version of a strategy."""
    fr = fr if fr is not None else frames(symbols, tf, dev=True)
    if any(int(df["time"].iat[-1]) >= DEV_END for df in fr.values()):
        raise ValueError("evaluate() takes development frames only (cut before DEV_END)")
    pos = strategy(fr, params)
    pnl = {s: market_pnl(s, fr[s], pos.get(s)) for s in fr}
    return summary(pnl)


def assert_no_peek(strategy: Strategy, params: dict[str, Any], fr: dict[str, pd.DataFrame],
                   fractions: tuple[float, ...] = (0.5, 0.75, 0.9)) -> None:
    """Positions decided up to time T must not change when the data after T is removed."""
    full = strategy(fr, params)
    allt = np.sort(np.unique(np.concatenate([df["time"].to_numpy("int64") for df in fr.values()])))
    for f in fractions:
        cut = int(allt[int(len(allt) * f)])
        part_fr = {s: df[df["time"] <= cut].reset_index(drop=True) for s, df in fr.items()}
        part_fr = {s: df for s, df in part_fr.items() if len(df) > 2}
        part = strategy(part_fr, params)
        for s, p in part.items():
            a = pd.Series(p, dtype=float)
            b = pd.Series(full.get(s), dtype=float).reindex(a.index)
            a, b = a[a.index <= cut], b[b.index <= cut]
            same = np.isclose(a.to_numpy(), b.to_numpy(), equal_nan=True, atol=1e-9)
            if not same.all():
                bad = a.index[~same][0]
                raise AssertionError(f"{s}: position at {bad} changes when data after {cut} is removed "
                                     f"({b.loc[bad]} with the future, {a.loc[bad]} without)")


def _entries(held: np.ndarray) -> int:
    prev = np.r_[0.0, held[:-1]]
    return int(np.sum((held != 0) & (np.sign(held) != np.sign(prev))))


def gross_table(pnl: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Date x market table of the largest |position| held that date (1x)."""
    cols = {s: p.assign(a=p["held"].abs()).groupby("day")["a"].max() for s, p in pnl.items()}
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index().fillna(0.0)


def _add(tables: list[pd.DataFrame]) -> pd.DataFrame:
    out = pd.DataFrame()
    for f in tables:
        out = f.copy() if out.empty else out.add(f, fill_value=0.0)
    return out.sort_index().fillna(0.0)


def component_pnl(comp: dict[str, Any], dev: bool = True) -> dict[str, pd.DataFrame]:
    """One portfolio component = {"name", "strategy", "params", "symbols", "tf", "weight"}."""
    fr = frames(comp.get("symbols"), comp.get("tf", "D1"), dev=dev)
    pos = comp["strategy"](fr, comp.get("params") or {})
    return {s: market_pnl(s, fr[s], pos.get(s)) for s in fr}


def portfolio_tables(components: list[dict[str, Any]], dev: bool = True
                     ) -> tuple[pd.DataFrame, pd.Series, dict[str, pd.Series], int]:
    """Weighted date x market net P&L summed over components, the daily gross leverage (1x),
    each component's own weighted daily net, and the number of locked-period entries."""
    nets, grosses, per, entries = [], [], {}, 0
    for c in components:
        pnl = component_pnl(c, dev)
        w = float(c.get("weight", 1.0))
        net = daily_table(pnl) * w
        nets.append(net)
        grosses.append(gross_table(pnl) * abs(w))
        per[c["name"]] = net.sum(axis=1)
        entries += sum(_entries(q["held"].to_numpy()[q["time"].to_numpy() >= DEV_END]) for q in pnl.values())
    net = _add(nets)
    gross = _add(grosses).sum(axis=1) if grosses else pd.Series(dtype=float)
    return net, gross, per, entries


def evaluate_portfolio(components: list[dict[str, Any]]) -> dict[str, Any]:
    """Development-years report for a weighted mix of strategies (any timeframes)."""
    net, gross, per, _ = portfolio_tables(components, dev=True)
    start = SCORE_START // DAY
    net = net[net.index >= start]
    gross = gross[gross.index >= start]
    c = cells(net)
    comp = pd.DataFrame({k: v[v.index >= start] for k, v in per.items()}).fillna(0.0)
    return {"cells": c, "gate": gate(c),
            "components": {k: stats(comp[k]) for k in comp.columns},
            "correlation": comp.corr().round(2).to_dict() if comp.shape[1] > 1 else {},
            "gross_1x": {"mean": round(float(gross.mean()), 3) if len(gross) else 0.0,
                         "max": round(float(gross.max()), 3) if len(gross) else 0.0}}


def _locked_report(name: str, net: pd.Series, gross: pd.Series, entries: int, record: dict[str, Any],
                   looks_file: str | Path, max_gross: float) -> dict[str, Any]:
    path = Path(looks_file)
    looks = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if any(x["name"] == name for x in looks):
        raise RuntimeError(f"{name!r} already had its locked look")
    dev_day = DEV_END // DAY
    in_dev = (net.index >= SCORE_START // DAY) & (net.index < dev_day)
    dev, lock = net[in_dev], net[net.index >= dev_day]
    k_dd = scale_for_cap(dev.to_numpy())
    dev_gross = gross[(gross.index >= SCORE_START // DAY) & (gross.index < dev_day)]
    peak = float(dev_gross.max()) if len(dev_gross) else 0.0
    k_lev = max_gross / peak if peak > 0 else float("inf")
    k = min(k_dd, k_lev)
    years = max((lock.index[-1] - lock.index[0]) / 365.25, 1e-9)
    eq, dd = curve(lock.to_numpy(), k)
    yearly = eq ** (1 / years) - 1 if eq > 0 else -1.0
    st = stats(lock)
    edges = np.linspace(lock.index[0], lock.index[-1] + 1, 5)
    quarters = [round(float(lock[(lock.index >= a) & (lock.index < b)].sum() * k * 100), 2)
                for a, b in zip(edges[:-1], edges[1:])]
    pos_days, neg_days = lock[lock > 0].sum(), -lock[lock < 0].sum()
    pf = float(pos_days / neg_days) if neg_days > 0 else 99.0
    lock_gross = gross[gross.index >= dev_day] * k
    passed = {"yearly_18pct": yearly >= 0.18, "drop_25pct": dd <= DD_CAP + 1e-9, "t_1.65": st["t"] >= 1.65,
              "quarters_3of4": sum(q > 0 for q in quarters) >= 3, "pf_1.2": pf >= 1.2, "trades_30": entries >= 30}
    res = {"name": name, "at": datetime.now().isoformat(timespec="minutes"), **record,
           "size": round(k, 3), "size_for_25pct_dev_drop": round(k_dd, 3), "size_for_leverage_cap": round(k_lev, 3),
           "max_gross_cap": max_gross,
           "locked_yearly_pct": round(yearly * 100, 2), "locked_maxdd_pct": round(dd * 100, 1),
           "locked_t": st["t"], "locked_sharpe": st["sharpe"], "quarters_pct": quarters, "pf": round(pf, 2),
           "entries": entries,
           "locked_gross_mean": round(float(lock_gross.mean()), 2) if len(lock_gross) else 0.0,
           "locked_gross_max": round(float(lock_gross.max()), 2) if len(lock_gross) else 0.0,
           "passed": passed, "clears_bar": all(passed.values())}
    looks.append(res)
    path.write_text(json.dumps(looks, indent=1, default=str), encoding="utf-8")
    return res


def locked_look(name: str, strategy: Strategy, params: dict[str, Any], symbols: list[str] | None = None,
                tf: str = "D1", looks_file: str | Path = "locked_looks_p6.json",
                max_gross: float = 10.0) -> dict[str, Any]:
    """The ONE look at 2019-2026 for a version decided on the development years. Recorded; a
    name can be looked at only once. Size = the multiple whose development-years worst drop is
    25%, but never more than ``max_gross`` times the account in total positions on any
    development day."""
    comp = {"name": name, "strategy": strategy, "params": params, "symbols": symbols, "tf": tf, "weight": 1.0}
    net, gross, _, entries = portfolio_tables([comp], dev=False)
    return _locked_report(name, net.sum(axis=1), gross, entries, {"params": params, "tf": tf},
                          looks_file, max_gross)


def locked_look_portfolio(name: str, components: list[dict[str, Any]],
                          looks_file: str | Path = "locked_looks_p6.json", max_gross: float = 10.0) -> dict[str, Any]:
    """The ONE locked look for a weighted mix decided on the development years."""
    net, gross, _, entries = portfolio_tables(components, dev=False)
    record = {"components": [{k: v for k, v in c.items() if k != "strategy"} for c in components]}
    return _locked_report(name, net.sum(axis=1), gross, entries, record, looks_file, max_gross)


__all__ = ["SCORE_START", "DEV_END", "END", "MARKETS", "HALF_A", "HALF_B", "frames", "is_fx", "financing", "market_pnl",
           "daily_table", "curve", "scale_for_cap", "stats", "cells", "gate", "summary", "evaluate",
           "assert_no_peek", "gross_table", "component_pnl", "portfolio_tables", "evaluate_portfolio",
           "locked_look", "locked_look_portfolio"]
