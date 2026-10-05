"""Learning sessions: backtest on and on for hours, getting better as it goes.

    investment-bot learn --for 10h --round 30m --goal "learn how to do shorts"

A session repeats *rounds* until its time is up, or until Ctrl+C:

1. **Test** (``--round`` long): backtest the current settings on random
   stretches of history, on every core, one stretch after another. Each
   stretch is split in two: a training part and the later, held-out part.
2. **Analyse**: pool every trade the round made into lessons (where the money
   was made and lost), and find the stretches where it did worst.
3. **Adjust**: try single-setting changes on the worst stretches plus a few
   random ones, and keep at most one: it must improve the goal's score on
   the training parts *and* on the held-out parts. It's saved to
   ``learned.json`` at once, so stopping never loses anything, and the next
   round, the next backtest and paper trading all start from it.

Longer rounds test more stretches, so each adjustment is aimed at weaker spots
found in more history. The **goal** is read for keywords (shorts, longs,
drawdown, profit, win rate, a symbol) and steers the score; the session prints
how it read the goal, and an unrecognised goal just means "cut losses".

``learning_session.json`` (gitignored) holds the session's progress, and
``jarvis_status.json`` shows it on Jarvis's TradeBot page.
"""
from __future__ import annotations

import json
import os
import random
import re
import signal
import sys
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from types import SimpleNamespace
from typing import Any

import pandas as pd

from .config import BotConfig
from .memory import (
    BacktestMemory,
    Round,
    TuneConfig,
    apply_overrides,
    candidates,
    current_values,
    describe,
    review_trades,
    run_backtest,
    score,
    short_candidates,
    split,
)
from .reports import Report, tone_of
from .workers import exit_with_parent

SESSION_FILE = "learning_session.json"
WORST = 3          # stretches where the bot did worst, tuned on each round
RANDOM = 3         # plus this many others, so it can't only fit bad spells
FOCUS_WEIGHT = 1.0  # how much the goal's own P&L counts, on top of the usual score

_UNITS = {"m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> float:
    """'10m', '2h', '1.5h', '10d' (or plain minutes) -> seconds."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([mhd]?)\s*", str(text).lower())
    if not match:
        raise ValueError(f"Can't read the duration {text!r}; use e.g. 30m, 8h or 2d.")
    seconds = float(match.group(1)) * _UNITS[match.group(2) or "m"]
    if seconds <= 0:
        raise ValueError("A duration must be more than zero.")
    return seconds


def human(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 86400:
        return f"{seconds // 86400}d {seconds % 86400 // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    return f"{seconds // 60}m {seconds % 60:02d}s"


# ---------------- the goal ----------------


@dataclass
class Goal:
    """What a session optimises, read from the words the user typed."""

    text: str = ""
    side: int = 0                      # -1 shorts, +1 longs, 0 both
    symbols: tuple[str, ...] = ()
    loss_aversion: float = 1.5
    win_rate: bool = False
    reading: list[str] = field(default_factory=list)  # how it was understood

    @property
    def focused(self) -> bool:
        return bool(self.side or self.symbols)

    def wants(self, trade: Any) -> bool:
        if self.side and trade.direction != self.side:
            return False
        return not self.symbols or trade.symbol in self.symbols

    def score(self, metrics: dict[str, float], focus_pnl: float, cash: float) -> float:
        value = score(metrics, self.loss_aversion)
        if self.focused:
            value += FOCUS_WEIGHT * focus_pnl / cash
        if self.win_rate:
            value += 0.1 * (float(metrics.get("win_rate", 0.0)) - 0.5)
        return value


def read_goal(text: str, universe: list[str], loss_aversion: float) -> Goal:
    goal = Goal(text=(text or "").strip(), loss_aversion=loss_aversion)
    words = goal.text.lower()
    if re.search(r"\bshort", words):
        goal.side = -1
        goal.reading.append("focus on short trades (shorts are switched on while it learns)")
    elif re.search(r"\blongs?\b|\bbuy", words):
        goal.side = 1
        goal.reading.append("focus on long trades")
    named = [s for s in universe if re.search(rf"\b{re.escape(s.lower())}\b", words)]
    if named:
        goal.symbols = tuple(named)
        goal.reading.append("focus on " + ", ".join(named))
    if re.search(r"\b(drawdowns?|risk\w*|safe\w*|careful\w*|loss(es)?|los(e|ing)|protect\w*)\b",
                 words):
        goal.loss_aversion = loss_aversion * 2
        goal.reading.append(f"punish drawdown harder (x{goal.loss_aversion:g})")
    elif re.search(r"\b(profit\w*|returns?|money|gains?|aggressive\w*|earn(ings?)?)\b", words):
        goal.loss_aversion = loss_aversion / 2
        goal.reading.append(f"favour return over drawdown (x{goal.loss_aversion:g})")
    if re.search(r"\b(win ?rate|winning|winners|accura\w*|hit rate)\b", words):
        goal.win_rate = True
        goal.reading.append("reward a higher win rate")
    if not goal.reading:
        goal.reading.append("cut losses overall (no keywords recognised)")
    return goal


# ---------------- backtests on stretches of history, in worker processes ----------------


@dataclass(frozen=True)
class Stretch:
    start: int  # index into the calendar
    end: int    # exclusive

    def label(self, calendar: list[pd.Timestamp]) -> str:
        return f"{calendar[self.start]:%Y-%m-%d}..{calendar[self.end - 1]:%Y-%m-%d}"


_W: dict[str, Any] = {}


def _init_worker(data: dict[str, pd.DataFrame], goal: Goal, holdout: float, warmup: int) -> None:
    # Ctrl+C is the main process's to handle; it ends the workers itself.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    exit_with_parent()
    calendar = sorted(set().union(*(df.index for df in data.values())))
    _W.update(data=data, goal=goal, holdout=holdout, warmup=warmup, calendar=calendar, parts={})


def _part(stretch: Stretch, part: str) -> dict[str, pd.DataFrame]:
    key = (stretch, part)
    if key not in _W["parts"]:
        cal = _W["calendar"]
        lo, hi = cal[stretch.start], cal[stretch.end - 1]
        window = {s: df.loc[lo:hi] for s, df in _W["data"].items()}
        window = {s: df for s, df in window.items() if len(df)}
        train, hold = split(window, _W["holdout"], _W["warmup"])
        _W["parts"][(stretch, "train")], _W["parts"][(stretch, "hold")] = train, hold
    return _W["parts"][key]


def _job(job: tuple[dict, Stretch, str, bool]) -> dict[str, Any]:
    raw, stretch, part, keep_trades = job
    started = time.monotonic()
    config = BotConfig(raw=raw)
    result = run_backtest(config, _part(stretch, part))
    goal: Goal = _W["goal"]
    return {
        "metrics": result.metrics,
        "focus_pnl": sum(t.pnl for t in result.trades if goal.wants(t)),
        "trades": result.trades if keep_trades else None,
        "seconds": time.monotonic() - started,
    }


# ---------------- the session ----------------


@dataclass
class Session:
    config: BotConfig            # the config file, without the memory's overrides
    memory: BacktestMemory
    tune: TuneConfig
    goal: Goal
    seconds: float
    round_seconds: float
    load_data: Callable[[BotConfig], dict[str, pd.DataFrame]]
    say: Callable[[str], None] = print
    workers: int = 0
    state_file: Path = Path(SESSION_FILE)
    on_progress: Callable[[], None] = lambda: None
    seed: int | None = None

    def __post_init__(self) -> None:
        self.workers = self.workers or self.tune.workers or max((os.cpu_count() or 2) - 1, 1)
        self.rng = random.Random(self.seed)
        self.started = datetime.now(timezone.utc)
        self.deadline = time.monotonic() + self.seconds
        self.job_seconds: list[float] = []
        self.rounds_done = 0
        self.kept: list[str] = []
        self.history: list[list[Any]] = []
        self.phase = "starting"
        self.pool: ProcessPoolExecutor | None = None

    # -- settings ---------------------------------------------------------

    def settings(self) -> BotConfig:
        """What this session backtests: the config plus everything learned, and
        for a shorts goal, shorts switched on (else there is nothing to learn)."""
        tuned = self.memory.apply(self.config)
        if self.goal.side < 0 and current_values(tuned)["strategy.long_only"]:
            tuned = apply_overrides(tuned, {"strategy.long_only": False})
        return tuned

    def options(self, config: BotConfig) -> list[dict[str, Any]]:
        if self.goal.side < 0:  # learning shorts: their knobs, not whether to short at all
            opts = [c for c in candidates(config) if "strategy.long_only" not in c]
            have = {repr(c) for c in opts}
            return opts + [c for c in short_candidates(current_values(config)) if repr(c) not in have]
        return candidates(config)

    # -- the loop ---------------------------------------------------------

    def left(self) -> float:
        return self.deadline - time.monotonic()

    def run(self) -> str:
        """Rounds until time is up. Returns 'finished' or 'stopped'."""
        status = "finished"
        self.say(f"Goal: {self.goal.text or '(none)'} -> " + "; ".join(self.goal.reading) + ".")
        self.say(
            f"Learning for {human(self.seconds)}, analysing every {human(self.round_seconds)}, "
            f"on {self.workers} cores. Ctrl+C stops it; everything learned is kept."
        )
        try:
            while self.left() > 0:
                if not self.round():
                    break
        except KeyboardInterrupt:
            status = "stopped"
            self.say("Stopping (Ctrl+C).")
        finally:
            self._close_pool()
            self.phase = status
            self.save_state(status)
            self.on_progress()
        self.say(
            f"{'Stopped' if status == 'stopped' else 'Done'} after {self.rounds_done} round(s) "
            f"in {human((datetime.now(timezone.utc) - self.started).total_seconds())}. "
            + (f"Kept: {'; '.join(self.kept)}." if self.kept else "No setting changes kept.")
        )
        return status

    def round(self) -> bool:
        """One test -> analyse -> adjust round. False when there's no time for one."""
        number = self.rounds_done + 1
        data = self.load_data(self.settings())
        config = self.settings()
        warmup = config.build_strategy().warmup
        calendar = sorted(set().union(*(df.index for df in data.values())))
        shortest = max(250, 3 * warmup)
        if len(calendar) < shortest:
            raise SystemExit(f"Only {len(calendar)} days of history; a session needs {shortest}.")
        options = self.options(config)
        cash = float(config.backtest_settings["starting_cash"])
        tune_jobs = 2 * (WORST + RANDOM) * (len(options) + 1)
        if self.rounds_done and self.left() < self._estimate(tune_jobs + self.workers):
            self.say(f"Only {human(self.left())} left: not enough for another round.")
            return False
        self._open_pool(data, warmup)

        # 1. test
        self.phase = f"round {number}: testing"
        self.save_state("running")
        test_until = min(
            time.monotonic() + self.round_seconds, self.deadline - self._estimate(tune_jobs)
        )
        self.say(f"[round {number}] testing the current settings until "
                 f"{_clock(test_until)} ({len(options)} adjustments to try after)")
        tested = self._test(config, calendar, shortest, test_until)
        if not tested:
            self.say(f"[round {number}] no time left for another round.")
            return False

        # 2. analyse
        self.phase = f"round {number}: analysing"
        trades = [t for s in tested.values() for part in s.values() for t in part["trades"]]
        lessons = review_trades(SimpleNamespace(trades=trades))
        base = {k: {p: self._goal_score(r, cash) for p, r in v.items()} for k, v in tested.items()}
        stretches = sorted(tested, key=lambda k: base[k]["train"] + base[k]["hold"])
        # Fewer stretches if that's what fits in the time left (never fewer than one).
        per_stretch = 2 * len(options) + (4 if self.goal.side < 0 else 0)
        size = WORST + RANDOM
        while size > 1 and self._estimate(size * per_stretch) > self.left():
            size -= 1
        worst_n = min(WORST, size)
        chosen = stretches[:worst_n] + self.rng.sample(
            stretches[worst_n:], min(size - worst_n, max(len(stretches) - worst_n, 0))
        )
        focus = [t for t in trades if self.goal.wants(t)]
        if self.goal.focused:
            lessons.insert(0, f"Goal trades: {len(focus)} of {len(trades)}, "
                              f"P&L ${sum(t.pnl for t in focus):,.0f}.")
        worst = ", ".join(s.label(calendar) for s in stretches[:WORST])
        lessons.append(f"Tested {len(tested)} stretches of history; weakest: {worst}.")
        for line in lessons:
            self.say(f"[round {number}]   - {line}")

        # 3. adjust
        self.phase = f"round {number}: adjusting"
        self.save_state("running")
        self.say(f"[round {number}] trying {len(options)} adjustments on {len(chosen)} stretches")
        rnd = self._adjust(config, options, chosen, tested, base, lessons, trades, cash)
        if rnd.adopted:
            self.kept.append(rnd.adopted["description"])
            self.say(f"[round {number}] KEPT: {rnd.adopted['description']} "
                     f"(goal score {rnd.adopted['train_gain']:+.2%} training, "
                     f"{rnd.adopted['holdout_gain']:+.2%} held-out).")
        else:
            best = (rnd.considered or [None])[0]
            hint = f" Closest: {best['description']}." if best else ""
            self.say(f"[round {number}] nothing kept: no change helped on both parts.{hint}")
        if self.goal.side < 0:
            note = self._shorts_check(chosen, rnd)
            if note:
                self.say(f"[round {number}] {note}")
        self.memory.rounds.append(rnd.__dict__)
        self.memory.save()
        self.rounds_done = number
        self.history.append([number, round(mean(base[k]["train"] + base[k]["hold"]
                                                    for k in tested) / 2, 5)])
        self.save_state("running")
        self.on_progress()
        self.say(f"[round {number}] saved; {human(self.left())} left.")
        return True

    def report(self, status: str) -> Report:
        """This session, for reports/: what it tried, what it kept, how the goal score moved."""
        took = human((datetime.now(timezone.utc) - self.started).total_seconds())
        word = "stopped" if status == "stopped" else "finished"
        kept = len(self.kept)
        scores = [s for _, s in self.history]
        moved = scores[-1] - scores[0] if len(scores) > 1 else 0.0
        summary = (f"{self.rounds_done} round(s) in {took}, {kept} change(s) kept"
                   + (f", goal score {moved:+.2%}" if len(scores) > 1 else "") + f" ({word})")
        out = Report("learn", summary, tone_of(moved) if kept else "neutral",
                     subtitle=f"goal: {self.goal.text or 'cut losses'}")
        out.stat("Rounds", self.rounds_done).stat("Changes kept", kept, "good" if kept else "neutral")
        out.stat("Ran for", took).stat("Analysed every", human(self.round_seconds))
        if len(scores) > 1:
            out.stat("Goal score", f"{scores[0]:+.2%} -> {scores[-1]:+.2%}", tone_of(moved))
        out.bullets("How it read the goal", self.goal.reading, "No goal: cut losses.")
        out.bullets("Kept", self.kept, "No setting change held up on both parts this time.")
        rounds = self.memory.rounds[-self.rounds_done:] if self.rounds_done else []
        out.table("Round by round", [
            {"Round": n + 1,
             "Goal score": f"{self.history[n][1]:+.2%}" if n < len(self.history) else "",
             "Tried": r.get("tested", 0),
             "Kept": (r.get("adopted") or {}).get("description", "-")}
            for n, r in enumerate(rounds)])
        if rounds:
            out.bullets("What the last round saw", rounds[-1].get("lessons") or [])
        return out

    def _test(self, config: BotConfig, calendar: list, shortest: int,
              until: float) -> dict[Stretch, dict[str, dict[str, Any]]]:
        """Backtest random stretches until `until`; every core stays busy."""
        assert self.pool is not None
        raw = config.raw
        done: dict[Stretch, dict[str, dict[str, Any]]] = {}
        running: dict[Future, tuple[Stretch, str]] = {}

        def start() -> None:
            length = self.rng.randint(shortest, len(calendar))
            begin = self.rng.randint(0, len(calendar) - length)
            stretch = Stretch(begin, begin + length)
            for part in ("train", "hold"):
                running[self.pool.submit(_job, (raw, stretch, part, True))] = (stretch, part)

        for _ in range(max(self.workers // 2, 1)):
            start()
        while running:
            finished, _ = wait(list(running), timeout=5, return_when=FIRST_COMPLETED)
            for fut in finished:
                stretch, part = running.pop(fut)
                result = fut.result()
                self.job_seconds.append(result["seconds"])
                done.setdefault(stretch, {})[part] = result
                if len(done[stretch]) == 2:
                    m = {p: r["metrics"] for p, r in done[stretch].items()}
                    self.say(
                        f"    {stretch.label(calendar)}: return "
                        f"{m['train']['total_return']:+.2%} / {m['hold']['total_return']:+.2%} "
                        f"held-out, {int(m['train']['num_trades'] + m['hold']['num_trades'])} trades"
                    )
                    if time.monotonic() < until:
                        start()
        return {s: parts for s, parts in done.items() if len(parts) == 2}

    def _adjust(self, config, options, chosen, tested, base, lessons, trades, cash) -> Round:
        assert self.pool is not None
        before = current_values(config)
        configs = [apply_overrides(config, c).raw for c in options]
        jobs = [(i, s, p) for i in range(len(configs)) for s in chosen for p in ("train", "hold")]
        futures = [self.pool.submit(_job, (configs[i], s, p, False)) for i, s, p in jobs]
        results = [f.result() for f in futures]
        for r in results:
            self.job_seconds.append(r["seconds"])
        by = dict(zip(jobs, results, strict=True))

        scored = []
        for i, change in enumerate(options):
            gains, ok = {}, True
            for p in ("train", "hold"):
                gains[p] = mean(self._goal_score(by[(i, s, p)], cash) - base[s][p] for s in chosen)
                was = sum(tested[s][p]["metrics"].get("num_trades", 0) for s in chosen)
                now = sum(by[(i, s, p)]["metrics"].get("num_trades", 0) for s in chosen)
                if was and now < self.tune.min_trade_ratio * was:
                    ok = False  # can't "learn" to stop trading
            scored.append((change, gains, ok))

        def qualifies(gains: dict[str, float], ok: bool) -> bool:
            return ok and min(gains.values()) >= self.tune.min_gain

        metrics = [r["metrics"] for s in tested.values() for r in s.values()]
        rnd = Round(
            number=len(self.memory.rounds) + 1,
            at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            data_end=f"{len(tested)} stretches",
            full_run={
                "total_return": round(mean(m.get("total_return", 0.0) for m in metrics), 5),
                "max_drawdown": round(mean(m.get("max_drawdown", 0.0) for m in metrics), 5),
                "num_trades": len(trades),
                "win_rate": round(sum(t.pnl > 0 for t in trades) / len(trades), 4) if trades else 0.0,
                "gross_loss": round(sum(t.pnl for t in trades if t.pnl < 0), 2),
                "score": round(mean(base[s][p] for s in tested for p in ("train", "hold")), 5),
            },
            lessons=lessons,
            tested=len(options),
            considered=[
                {
                    "description": describe(c, before),
                    "train_gain": round(g["train"], 5),
                    "holdout_gain": round(g["hold"], 5),
                    "qualified": qualifies(g, ok),
                }
                for c, g, ok in sorted(scored, key=lambda s: s[1]["train"], reverse=True)[:5]
            ],
        )
        rnd.lessons = [*lessons, f"Learning session goal: {self.goal.text or 'cut losses'}."]
        passing = [(c, g) for c, g, ok in scored if qualifies(g, ok)]
        if passing:
            # Choose by the training gain; the held-out parts only veto.
            change, gains = max(passing, key=lambda cg: cg[1]["train"])
            self.memory.overrides.update(change)
            rnd.adopted = {
                "change": change,
                "description": describe(change, before),
                "train_gain": round(gains["train"], 5),
                "holdout_gain": round(gains["hold"], 5),
            }
        return rnd

    def _shorts_check(self, chosen: list[Stretch], rnd: Round) -> str:
        """Learning shorts runs with shorts on. Paper trading gets them back only
        once they beat long-only on the usual score, training and held-out."""
        saved = self.memory.apply(self.config)
        if not current_values(saved)["strategy.long_only"]:
            return ""
        assert self.pool is not None
        trial = apply_overrides(saved, {"strategy.long_only": False})
        jobs = [(c, s, p) for c in ("off", "on") for s in chosen for p in ("train", "hold")]
        raws = {"off": saved.raw, "on": trial.raw}
        futures = [self.pool.submit(_job, (raws[c], s, p, False)) for c, s, p in jobs]
        results = [f.result() for f in futures]
        by = dict(zip(jobs, results, strict=True))
        la = self.tune.loss_aversion
        gains = {
            p: mean(score(by[("on", s, p)]["metrics"], la) - score(by[("off", s, p)]["metrics"], la)
                    for s in chosen)
            for p in ("train", "hold")
        }
        if min(gains.values()) >= self.tune.min_gain:
            self.memory.overrides["strategy.long_only"] = False
            note = (f"Shorts back on for paper trading: they now beat long-only "
                    f"({gains['train']:+.2%} training, {gains['hold']:+.2%} held-out).")
            self.kept.append("shorts back on")
        else:
            note = (f"Shorts still trail long-only ({gains['train']:+.2%} training, "
                    f"{gains['hold']:+.2%} held-out): paper trading stays long-only; "
                    "the short settings learned are kept for when they're on.")
        rnd.lessons.append(note)
        return note

    # -- helpers ----------------------------------------------------------

    def _goal_score(self, result: dict[str, Any], cash: float) -> float:
        return self.goal.score(result["metrics"], result["focus_pnl"], cash)

    def _estimate(self, jobs: int) -> float:
        """Seconds `jobs` backtests should take on the pool."""
        each = mean(self.job_seconds[-50:]) if self.job_seconds else 30.0
        return jobs * each / self.workers

    def _open_pool(self, data: dict[str, pd.DataFrame], warmup: int) -> None:
        self._close_pool()
        self.pool = ProcessPoolExecutor(
            self.workers,
            initializer=_init_worker,
            initargs=(data, self.goal, self.tune.holdout, warmup),
        )

    def _close_pool(self) -> None:
        pool, self.pool = self.pool, None
        if pool is None:
            return
        processes = list((getattr(pool, "_processes", None) or {}).values())
        pool.shutdown(wait=False, cancel_futures=True)
        for process in processes:  # don't wait out a backtest nobody needs
            if process.is_alive():
                process.terminate()

    def save_state(self, status: str) -> None:
        ends = self.started + timedelta(seconds=self.seconds)
        payload = {
            "status": status,
            "phase": self.phase,
            "goal": self.goal.text,
            "reading": self.goal.reading,
            "started": self.started.isoformat(timespec="seconds"),
            "ends": ends.isoformat(timespec="seconds"),
            "round_minutes": round(self.round_seconds / 60, 2),
            "rounds": self.rounds_done,
            "kept": self.kept,
            "score_by_round": self.history,
            "pid": os.getpid(),
        }
        try:
            tmp = self.state_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.state_file)
        except OSError:
            pass


def _clock(monotonic_at: float) -> str:
    at = datetime.now(timezone.utc).astimezone() + timedelta(seconds=monotonic_at - time.monotonic())
    return f"{at:%H:%M}"


def keep_awake() -> None:
    """Ask Windows not to sleep while this process runs (released when it exits)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        es_continuous, es_system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)
    except (AttributeError, OSError):
        pass
