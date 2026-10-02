"""Backtest memory: learn from each backtest's trades, keep what cuts losses.

The online learner (`investment_bot.learning`) adapts ensemble weights *during*
a run, but a backtest used to forget everything when it ended. This module is
the long-term memory across runs. After every backtest it:

1. **Reviews the trades** — where the money was lost (shorts vs longs, stop
   losses, which symbols, trades closed within days) — as plain-English lessons.
2. **Tries small adjustments** to the current settings, one at a time: entry
   threshold, stop distance, risk per trade, the volatility veto, long-only,
   take-profits, and the weights the online learner arrived at.
3. **Keeps a change only if it holds up out of sample.** History is split into
   a training part (first 70%) and a held-out part (the last 30%, which the
   choice never looks at). A change must lower losses on *both* by a margin,
   and must not do it by simply trading far less.
4. **Saves the result** to `learned.json`, which the next backtest — and paper
   trading — start from. One change per backtest, so each run learns one thing
   and you can see exactly what and why.

"Lower losses" is a score that rewards return but punishes drawdown harder:
``score = total_return + loss_aversion * max_drawdown`` (max_drawdown <= 0).
"""
from __future__ import annotations

import copy
import json
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .backtest.engine import BacktestEngine, BacktestResult
from .config import BotConfig

# Numeric knobs the tuner may nudge: (dotted key, step, low, high).
NUDGES: list[tuple[str, float, float, float]] = [
    ("strategy.threshold", 0.05, 0.10, 0.60),
    ("strategy.max_volatility", 0.10, 0.30, 1.50),
    ("risk.atr_stop_multiple", 0.5, 1.5, 6.0),
    ("risk.risk_per_trade", 0.0025, 0.0025, 0.02),
]

LABELS = {
    "strategy.threshold": "entry threshold",
    "strategy.max_volatility": "volatility veto",
    "strategy.long_only": "long-only",
    "strategy.learned_weights": "strategy weights",
    "risk.atr_stop_multiple": "stop distance (ATRs)",
    "risk.risk_per_trade": "risk per trade",
    "risk.take_profit_multiple": "take-profit (ATRs)",
}


@dataclass
class TuneConfig:
    enabled: bool = False
    memory_file: str = "learned.json"
    holdout: float = 0.30        # share of history held out to validate changes
    loss_aversion: float = 1.5   # drawdown penalty in the score
    min_gain: float = 0.002      # score improvement needed on both windows
    min_trade_ratio: float = 0.5  # can't "learn" to stop trading
    workers: int = 0             # 0 = one per CPU core, minus one

    @classmethod
    def from_config(cls, config: BotConfig) -> "TuneConfig":
        learn = config.section("learning")
        d = cls()
        return cls(
            enabled=bool(learn.get("auto_tune", d.enabled)),
            memory_file=str(learn.get("memory_file", d.memory_file)),
            holdout=float(learn.get("holdout", d.holdout)),
            loss_aversion=float(learn.get("loss_aversion", d.loss_aversion)),
            min_gain=float(learn.get("min_gain", d.min_gain)),
            min_trade_ratio=float(learn.get("min_trade_ratio", d.min_trade_ratio)),
            workers=int(learn.get("workers", d.workers)),
        )


# ---------------- scoring & trade review ----------------


def score(metrics: dict[str, float], loss_aversion: float) -> float:
    """Return, minus a heavier penalty for drawdown. Higher is better."""
    return float(metrics.get("total_return", 0.0)) + loss_aversion * float(
        metrics.get("max_drawdown", 0.0)
    )


def review_trades(
    result: BacktestResult, learned_weights: dict[str, float] | None = None
) -> list[str]:
    """Plain-English lessons about where this run made and lost money."""
    trades = result.trades
    if not trades:
        return ["No trades closed — nothing to learn from yet."]
    lessons: list[str] = []

    def total(ts) -> float:
        return sum(t.pnl for t in ts)

    def money(ts) -> str:
        v = total(ts)
        return f"lost ${-v:,.0f}" if v < 0 else f"made ${v:,.0f}"

    longs = [t for t in trades if t.direction > 0]
    shorts = [t for t in trades if t.direction < 0]
    if longs and shorts:
        worse, better = (shorts, longs) if total(shorts) < total(longs) else (longs, shorts)
        w_name, b_name = ("Shorts", "longs") if worse is shorts else ("Longs", "shorts")
        lessons.append(
            f"{w_name} {money(worse)} over {len(worse)} trades; "
            f"{b_name} {money(better)} over {len(better)}."
        )

    by_reason: dict[str, list] = defaultdict(list)
    for t in trades:
        by_reason[t.reason or "other"].append(t)
    for reason in ("stop loss", "circuit breaker"):
        ts = by_reason.get(reason)
        if ts:
            lessons.append(f"{reason.capitalize()} exits: {len(ts)} trades, {money(ts)}.")

    by_symbol: dict[str, list] = defaultdict(list)
    for t in trades:
        by_symbol[t.symbol].append(t)
    ranked = sorted(by_symbol.items(), key=lambda kv: total(kv[1]))
    losers = [(s, ts) for s, ts in ranked if total(ts) < 0][:3]
    if losers:
        lessons.append(
            "Biggest losers: " + ", ".join(f"{s} -${-total(ts):,.0f}" for s, ts in losers) + "."
        )

    quick = [t for t in trades if (t.exit_time - t.entry_time).days <= 3]
    if len(quick) >= len(trades) / 4:
        lessons.append(
            f"{len(quick)} of {len(trades)} trades closed within 3 days "
            f"and {money(quick)}: signals flip-flopping."
        )

    if learned_weights:
        top = sorted(learned_weights.items(), key=lambda kv: kv[1], reverse=True)[:2]
        lessons.append(
            "Online learning leaned on " + " and ".join(f"{n} ({w:.0%})" for n, w in top) + "."
        )
    return lessons


# ---------------- candidates ----------------


def current_values(config: BotConfig) -> dict[str, Any]:
    """The effective value of every tunable knob (config, else built-in default)."""
    strategy = config.build_strategy()
    risk = config.build_risk()
    return {
        "strategy.threshold": strategy.threshold,
        "strategy.max_volatility": strategy.max_volatility,
        "strategy.long_only": strategy.long_only,
        "risk.atr_stop_multiple": risk.atr_stop_multiple,
        "risk.risk_per_trade": risk.risk_per_trade,
        "risk.take_profit_multiple": risk.take_profit_multiple,
        "strategy.learned_weights": strategy.weight_map,
    }


def candidates(
    config: BotConfig, learned_weights: dict[str, float] | None = None
) -> list[dict[str, Any]]:
    """Single-knob changes around the current settings, each as {key: new_value}."""
    now = current_values(config)
    out: list[dict[str, Any]] = []
    for key, step, lo, hi in NUDGES:
        value = now[key]
        if value is None:  # e.g. volatility veto off: offer turning it on
            out.append({key: round(hi * 0.6, 4)})
            continue
        for new in (value - step, value + step):
            if lo - 1e-9 <= new <= hi + 1e-9:
                out.append({key: round(new, 4)})
    out.append({"strategy.long_only": not now["strategy.long_only"]})
    tp = now["risk.take_profit_multiple"]
    if tp is None:
        out.append({"risk.take_profit_multiple": 6.0})
    else:
        out.append({"risk.take_profit_multiple": None})
        out += [{"risk.take_profit_multiple": v} for v in (tp - 1, tp + 1) if 2 <= v <= 10]
    if learned_weights:
        current = now["strategy.learned_weights"]
        if any(abs(learned_weights.get(n, 0) - w) >= 0.02 for n, w in current.items()):
            weights = {n: round(w, 4) for n, w in learned_weights.items()}
            out.append({"strategy.learned_weights": weights})
    return out


def describe(change: dict[str, Any], before: dict[str, Any]) -> str:
    parts = []
    for key, new in change.items():
        label = LABELS.get(key, key)
        old = before.get(key)
        if key == "strategy.learned_weights":
            parts.append(
                f"{label} -> " + ", ".join(f"{n} {w:.0%}" for n, w in new.items())
            )
        elif isinstance(new, bool):
            parts.append(f"{label} {'on' if new else 'off'}")
        else:
            parts.append(f"{label} {_fmt(old)} -> {_fmt(new)}")
    return "; ".join(parts)


def _fmt(value: Any) -> str:
    return "off" if value is None else f"{value:g}"


# ---------------- evaluation ----------------


def split(data: dict[str, pd.DataFrame], holdout: float, warmup: int):
    """Train = the first (1-holdout) of the calendar. Holdout = the rest, plus
    enough earlier bars for the strategies to warm up (no trades happen in
    them, so they don't count toward the holdout's score)."""
    calendar = sorted(set().union(*(df.index for df in data.values())))
    cut = int(len(calendar) * (1 - holdout))
    if cut <= warmup or len(calendar) - cut < 20:
        raise ValueError("Not enough history to hold out a validation window")
    train_end = calendar[cut - 1]
    hold_start = calendar[max(cut - warmup, 0)]
    train = {s: df.loc[:train_end] for s, df in data.items()}
    hold = {s: df.loc[hold_start:] for s, df in data.items()}
    return (
        {s: df for s, df in train.items() if len(df)},
        {s: df for s, df in hold.items() if len(df)},
    )


def run_backtest(config: BotConfig, data: dict[str, pd.DataFrame]) -> BacktestResult:
    settings = config.backtest_settings
    strategy = config.build_strategy()
    engine = BacktestEngine(
        strategy=strategy,
        risk=config.build_risk(),
        execution=config.build_execution(),
        starting_cash=settings["starting_cash"],
        lookback=settings["lookback"],
        learner=config.build_learning(strategy),
    )
    return engine.run(data)


# Worker-process globals: the windows are sent once, not once per job.
_WINDOWS: dict[str, dict[str, pd.DataFrame]] = {}


def _init_worker(windows: dict[str, dict[str, pd.DataFrame]]) -> None:
    _WINDOWS.update(windows)


def _evaluate(job: tuple[dict, str]) -> dict[str, float]:
    raw, window = job
    return run_backtest(BotConfig(raw=raw), _WINDOWS[window]).metrics


Evaluator = Callable[[list[tuple[dict, str]]], list[dict[str, float]]]


def parallel_evaluator(windows: dict[str, dict[str, pd.DataFrame]], workers: int) -> Evaluator:
    def evaluate(jobs: list[tuple[dict, str]]) -> list[dict[str, float]]:
        n = workers or max((os.cpu_count() or 2) - 1, 1)
        n = min(n, len(jobs))
        if n <= 1:
            _init_worker(windows)
            return [_evaluate(j) for j in jobs]
        with ProcessPoolExecutor(n, initializer=_init_worker, initargs=(windows,)) as pool:
            return list(pool.map(_evaluate, jobs))

    return evaluate


# ---------------- the memory itself ----------------


@dataclass
class Round:
    """What one backtest taught the bot."""

    number: int
    at: str
    data_end: str
    full_run: dict[str, float]
    lessons: list[str]
    tested: int = 0
    adopted: dict[str, Any] | None = None  # {"change", "description", "train_gain", "holdout_gain"}
    considered: list[dict[str, Any]] = field(default_factory=list)  # best few, for display


@dataclass
class BacktestMemory:
    path: Path
    overrides: dict[str, Any] = field(default_factory=dict)
    rounds: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "BacktestMemory":
        path = Path(path)
        if not path.exists():
            return cls(path=path)
        state = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            overrides=dict(state.get("overrides") or {}),
            rounds=list(state.get("rounds") or []),
        )

    def save(self) -> None:
        payload = {"version": 1, "overrides": self.overrides, "rounds": self.rounds[-100:]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.path)

    def reset(self) -> None:
        if self.path.exists():
            self.path.unlink()
        self.overrides, self.rounds = {}, []

    def apply(self, config: BotConfig) -> BotConfig:
        return apply_overrides(config, self.overrides)

    # ---------------- one learning round ----------------

    def learn(
        self,
        config: BotConfig,
        data: dict[str, pd.DataFrame],
        result: BacktestResult,
        learned_weights: dict[str, float] | None,
        tune: TuneConfig,
        evaluate: Evaluator | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> Round:
        """Review `result` (a backtest of `config`, which already includes the
        memory's overrides), try changes, adopt at most one, and save."""
        say = progress or (lambda _msg: None)
        m = result.metrics
        rnd = Round(
            number=len(self.rounds) + 1,
            at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            data_end=f"{result.equity.index[-1]:%Y-%m-%d}" if len(result.equity) else "",
            full_run={
                "total_return": round(float(m.get("total_return", 0.0)), 5),
                "max_drawdown": round(float(m.get("max_drawdown", 0.0)), 5),
                "num_trades": int(m.get("num_trades", 0)),
                "win_rate": round(float(m.get("win_rate", 0.0)), 4),
                "gross_loss": round(sum(t.pnl for t in result.trades if t.pnl < 0), 2),
                "score": round(score(m, tune.loss_aversion), 5),
            },
            lessons=review_trades(result, learned_weights),
        )

        before = current_values(config)
        options = candidates(config, learned_weights)
        try:
            train, hold = split(data, tune.holdout, config.build_strategy().warmup)
        except ValueError as exc:
            rnd.lessons.append(f"Skipped tuning: {exc}.")
            return self._record(rnd)

        evaluate = evaluate or parallel_evaluator({"train": train, "hold": hold}, tune.workers)
        configs = [config.raw] + [apply_overrides(config, c).raw for c in options]
        say(f"testing {len(options)} adjustments on train + held-out data")
        pairs = [(i, w) for i in range(len(configs)) for w in ("train", "hold")]
        metrics = dict(zip(pairs, evaluate([(configs[i], w) for i, w in pairs]), strict=True))
        rnd.tested = len(options)

        base = {w: metrics[(0, w)] for w in ("train", "hold")}
        scored = []
        for i, change in enumerate(options, start=1):
            gains, ok = {}, True
            for w in ("train", "hold"):
                m = metrics[(i, w)]
                gains[w] = score(m, tune.loss_aversion) - score(base[w], tune.loss_aversion)
                base_trades = base[w].get("num_trades", 0)
                if base_trades and m.get("num_trades", 0) < tune.min_trade_ratio * base_trades:
                    ok = False
            scored.append((change, gains, ok))

        passing = [
            (c, g) for c, g, ok in scored
            if ok and g["train"] >= tune.min_gain and g["hold"] >= tune.min_gain
        ]
        rnd.considered = [
            {
                "description": describe(c, before),
                "train_gain": round(g["train"], 5),
                "holdout_gain": round(g["hold"], 5),
                "qualified": ok and g["train"] >= tune.min_gain and g["hold"] >= tune.min_gain,
            }
            for c, g, ok in sorted(scored, key=lambda s: s[1]["train"], reverse=True)[:5]
        ]
        if passing:
            # Choose by the training gain; the holdout only vetoes. Picking the
            # best holdout score would quietly fit the holdout instead.
            change, gains = max(passing, key=lambda cg: cg[1]["train"])
            self.overrides.update(change)
            rnd.adopted = {
                "change": change,
                "description": describe(change, before),
                "train_gain": round(gains["train"], 5),
                "holdout_gain": round(gains["hold"], 5),
            }
        return self._record(rnd)

    def _record(self, rnd: Round) -> Round:
        self.rounds.append(rnd.__dict__)
        self.save()
        return rnd


def apply_overrides(config: BotConfig, overrides: dict[str, Any]) -> BotConfig:
    """A copy of `config` with dotted-key overrides like {"risk.risk_per_trade": 0.0075}."""
    raw = copy.deepcopy(config.raw)
    for key, value in overrides.items():
        section, _, name = key.partition(".")
        target = raw.get(section)
        if not isinstance(target, dict):
            target = raw[section] = {}
        target[name] = copy.deepcopy(value)
    return BotConfig(raw=raw)
