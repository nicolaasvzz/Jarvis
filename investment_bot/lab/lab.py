"""The indicator lab: rounds that find the clearest-signalling package of indicators.

    investment-bot lab --for 8h --round 1h

History is cut by time into three parts: the first 60% (**training**) is
where the bot looks and chooses; the next 20% (**held-out**) only confirms
or vetoes a choice; the last 20% (**final check**) is never used to decide
anything and is only reported, so you can see how a package does on candles
it has never been tuned on.

Rounds, each at most ``--round`` long:

1. **Scout** - every indicator on every timeframe, scored on what the price
   did after it voted. No trades. The scoreboard.
2. **Patterns** - re-scout the two halves of training to keep only
   indicators whose reading held up in both; then every pair (and the best
   trios) of those: which agree in a way that is followed by a clear move.
3. **Build** - packages assembled from the scoreboard and the patterns (at
   least one indicator per family, up to 50), backtested; the best on
   training that also holds up held-out becomes the **champion**. The old
   five-strategy setup, as a package, is the bar to beat.
4. **Challenge** (and every round after) - look at the indicators again on
   a fresh stretch, then try changes to the champion: add, drop or swap an
   indicator, reweight one, and tune entry, exit, stops, size and pace
   (hold longer, wait between trades, confirm signals). A change is kept
   only if it beats the champion on training *and* held-out.

Everything is saved to ``lab.json`` after each round (and each kept change),
so stopping loses nothing and the next session carries on from the champion.

**Until done** (``--until-done``, the champ-set builder): challenge rounds
also try every indicator the champion hasn't had yet - added, or swapped in
for its weakest member of the same family once it's full - and the session
ends by itself when all of them have been tried (``--for`` is then only the
longest it may take). What has been tried is kept in ``lab.json``, so a
stopped builder carries on where it left off; a finished one starts a new
pass next time.
"""
from __future__ import annotations

import json
import os
import random
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ..reports import Report, tone_of
from .catalog import CATALOG, FAMILIES, family_of
from .features import FeatureStore, ns
from .package import EXIT_REASONS, Package, Trades, portfolio, score_of
from .prepare import LabConfig
from .research import (
    init_worker,
    pairs_job,
    sample_job,
    scoreboard,
    scout_job,
    trades_job,
    triples_job,
)

STATE_FILE = "lab_session.json"
POOL_SIZE = 60          # best indicators carried into the pattern search
PER_FAMILY = 6          # ...with at least this many from each family
MAX_FEATURES = 50
SIZES = (5, 8, 12, 16, 24, 32, 40, 50)
MIN_LEFT = 60          # seconds; less than this left and no new round starts
REF_SIZE = 0.10        # every package is judged at this trade size, so that betting
                       # less on a losing package can't pass for an improvement

# The bot's original five strategies, as a package on daily candles.
BASELINE = {
    "sma_cross_20_100@1d": 1.0, "macd_hist_12_26@1d": 1.0, "donchian_breakout_20@1d": 1.5,
    "bb_pctb_20@1d": -0.75, "rsi_14@1d": -0.75,
}


def running_lab(state_file: str | Path = STATE_FILE) -> int | None:
    """The process id of a lab that is running right now, if there is one."""
    from ..jarvis_status import _alive

    try:
        data = json.loads(Path(state_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = data.get("pid")
    if data.get("status") == "running" and pid != os.getpid() and _alive(pid):
        return int(pid)
    return None


def human(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    return f"{seconds // 60}m {seconds % 60:02d}s"


@dataclass
class Result:
    package: Package
    metrics: dict[str, dict[str, float]] = field(default_factory=dict)  # part -> metrics
    scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"package": self.package.to_dict(),
                "metrics": {p: _round(m) for p, m in self.metrics.items()},
                "scores": {p: round(v, 5) for p, v in self.scores.items()}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Result:
        return cls(Package.from_dict(data["package"]), data.get("metrics", {}),
                   data.get("scores", {}))


def _round(m: dict[str, Any]) -> dict[str, Any]:
    return {k: (round(v, 5) if isinstance(v, float) and np.isfinite(v) else v)
            for k, v in m.items()}


class Lab:
    def __init__(
        self,
        cfg: LabConfig,
        store: FeatureStore,
        universe: list[dict[str, Any]],
        seconds: float,
        round_seconds: float,
        say: Callable[[str], None] = print,
        on_progress: Callable[[], None] = lambda: None,
        seed: int | None = None,
        state_file: str | Path = STATE_FILE,
        until_done: bool = False,
    ):
        self.cfg, self.store, self.say, self.on_progress = cfg, store, say, on_progress
        self.universe = universe
        self.symbols = [u["symbol"] for u in universe]
        self.meta = {u["symbol"]: u for u in universe}
        self.seconds, self.round_seconds = seconds, round_seconds
        self.rng = random.Random(seed)
        self.started = datetime.now(timezone.utc)
        self.deadline = time.monotonic() + seconds
        self.round_deadline = self.deadline
        self.state_file = Path(state_file)
        self.results_file = Path(cfg.results_file)
        self.state = self._load()
        self.pool: ProcessPoolExecutor | None = None
        self.phase = "starting"
        self.rounds_done = 0
        self.kept: list[str] = []
        self.job_seconds: list[float] = []
        self.tried: set[str] = set()  # changes already tested against the current champion
        self.windows = self._windows()
        self.until_done = until_done
        self.done = False  # until_done: every indicator has been tried

    # ------------------------------------------------------------ persistence

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.results_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("version", 1)
        data.setdefault("rounds", [])
        data.setdefault("scoreboard", [])
        data.setdefault("patterns", {})
        data.setdefault("champion", None)
        data.setdefault("hall_of_fame", [])
        data.setdefault("indicator_history", {})
        data.setdefault("tested", {})   # indicator -> times tried in the champion (until done)
        data.setdefault("passes", 0)    # times the builder tried every indicator
        return data

    def save(self) -> None:
        self.state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.state["symbols"] = len(self.symbols)
        self.state["windows"] = {k: [_iso(a), _iso(b)] for k, (a, b) in self.windows.items()}
        _write_json(self.results_file, self.state)

    def save_session(self, status: str) -> None:
        champ = self.state.get("champion")
        tested, total = self.coverage()
        _write_json(self.state_file, {
            "status": status,
            "phase": self.phase,
            "started": self.started.isoformat(timespec="seconds"),
            "ends": (self.started + timedelta(seconds=self.seconds)).isoformat(timespec="seconds"),
            "round_minutes": round(self.round_seconds / 60, 2),
            "rounds": self.rounds_done,
            "kept": self.kept[-20:],
            "symbols": len(self.symbols),
            "champion": Package.from_dict(champ["package"]).describe() if champ else None,
            "score_by_round": [[r["number"], r.get("champion_score")] for r in
                               self.state["rounds"] if r.get("champion_score") is not None][-50:],
            "until_done": self.until_done,
            "tested": tested,
            "to_test": total,
            "pid": os.getpid(),
        })

    # ------------------------------------------------------------ time

    def _windows(self) -> dict[str, tuple[int, int]]:
        firsts, lasts = [], []
        for s in self.symbols:
            idx = ns(self.store.bars(s).index)
            if len(idx):
                firsts.append(idx[0])
                lasts.append(idx[-1])
        if not firsts:
            raise SystemExit("No symbols with data in the feature store.")
        t0, t1 = int(np.median(firsts)), int(max(lasts)) + 1
        a = t0 + int((t1 - t0) * self.cfg.train)
        b = t0 + int((t1 - t0) * (self.cfg.train + self.cfg.holdout))
        return {"train": (t0, a), "hold": (a, b), "final": (b, t1)}

    def left(self) -> float:
        return self.deadline - time.monotonic()

    def round_left(self) -> float:
        return min(self.round_deadline, self.deadline) - time.monotonic()

    # ------------------------------------------------------------ pool

    def _open_pool(self) -> None:
        if self.pool is None:
            costs = {"stock": self.cfg.stock_cost_bps / 1e4,
                     "crypto": self.cfg.crypto_cost_bps / 1e4}
            self.pool = ProcessPoolExecutor(
                self.cfg.workers, initializer=init_worker,
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

    def _chunks(self, symbols: list[str] | None = None, per_worker: int = 2) -> list[list[str]]:
        symbols = list(symbols or self.symbols)
        n = max(1, min(len(symbols), self.cfg.workers * per_worker))
        return [symbols[i::n] for i in range(n)]

    def _map(self, fn: Callable[[Any], Any], jobs: list[Any]) -> list[Any]:
        self._open_pool()
        assert self.pool is not None
        started = time.monotonic()
        out = list(self.pool.map(fn, jobs))
        self.job_seconds.append(time.monotonic() - started)
        return out

    # ------------------------------------------------------------ until done

    def testable(self) -> list[dict[str, Any]]:
        """Scoreboard entries that can be tried: every column that had readings."""
        return [e for e in self.state["scoreboard"] if e.get("signals") or e.get("t")]

    def untested(self) -> list[dict[str, Any]]:
        champ = self.state.get("champion") or {}
        have = set((champ.get("package") or {}).get("weights") or {})
        tested = self.state["tested"]
        return [e for e in self.testable()
                if e["feature"] not in tested and e["feature"] not in have]

    def coverage(self) -> tuple[int, int]:
        """(indicators tried in the champion set, all there are to try)."""
        total = len(self.testable())
        return total - len(self.untested()), total

    def complete(self) -> bool:
        """Until done: has every indicator been tried in the champion set?"""
        if (self.until_done and not self.done and self.state.get("champion")
                and self.testable() and not self.untested()):
            self.done = True
        return self.done

    def coverage_changes(self, pkg: Package, count: int) -> list[tuple[Package, str]]:
        """Indicators the champion hasn't tried yet, clearest first: added, or once
        it's full, swapped in for the weakest member of the same family."""
        scale = float(np.mean(np.abs(list(pkg.weights.values())))) if pkg.weights else 1.0
        out: list[tuple[Package, str]] = []
        for e in self.untested():
            if len(out) >= count:
                break
            f = e["feature"]
            w = dict(pkg.weights)
            if len(w) < MAX_FEATURES:
                w[f] = e["sign"] * scale
                label = f"try {f}"
            else:
                same = [m for m in w if family_of(m) == e["family"]] or list(w)
                weakest = min(same, key=lambda m: abs(w[m]))
                w[f] = e["sign"] * abs(w.pop(weakest))
                label = f"try {f} (for {weakest})"
            cand = pkg.with_(weights=w)
            key = json.dumps(cand.to_dict(), sort_keys=True)
            if key in self.tried:
                continue
            self.tried.add(key)
            out.append((cand, label))
        return out

    def _mark_tested(self, champion: Package, packages: list[Package]) -> None:
        tested = self.state["tested"]
        for p in packages:
            for f in set(p.weights) - set(champion.weights):
                tested[f] = tested.get(f, 0) + 1

    # ------------------------------------------------------------ the loop

    def run(self) -> str:
        status = "finished"
        self.say(f"Lab: {len(self.symbols)} symbols, {len(self.store.columns)} indicator columns "
                 f"({len(CATALOG)} indicators x {len(self.cfg.timeframes)} timeframes), "
                 f"{self.cfg.workers} cores.")
        w = self.windows
        self.say(f"Training {_day(w['train'][0])}..{_day(w['train'][1])}, held-out "
                 f"{_day(w['hold'][0])}..{_day(w['hold'][1])}, final check "
                 f"{_day(w['final'][0])}..{_day(w['final'][1])}.")
        if self.until_done:
            tested, total = self.coverage()
            if total and tested >= total:
                self.state["tested"] = {}
                self.say(f"Every indicator was tried last time; starting pass "
                         f"{int(self.state['passes']) + 1}.")
                tested, total = self.coverage()
            self.say(f"Champ-set builder: tries every indicator in the champion set, then stops "
                     f"(at most {human(self.seconds)}, rounds of up to "
                     f"{human(self.round_seconds)})."
                     + (f" {tested} of {total} tried so far." if total else ""))
        else:
            self.say(f"Running for {human(self.seconds)}, rounds of up to "
                     f"{human(self.round_seconds)}.")
        self.say("Ctrl+C stops it; everything found so far is kept.")
        try:
            while self.left() > MIN_LEFT and not self.complete():
                self.round()
        except KeyboardInterrupt:
            status = "stopped"
            self.say("Stopping (Ctrl+C).")
        finally:
            self.close()
            self.phase = status
            if self.done:
                self.phase = "finished: every indicator tried"
                self.state["passes"] = int(self.state.get("passes", 0)) + 1
            self.save()
            self.save_session(status)
            self.on_progress()
        champ = self.state.get("champion")
        word = "Stopped" if status == "stopped" else "Done"
        self.say(f"{word} after {self.rounds_done} round(s)."
                 + (" Every indicator has been tried in the champion set." if self.done else ""))
        if champ:
            self.say("Champion: " + self.report(Result.from_dict(champ)))
        return status

    def round(self) -> None:
        number = len(self.state["rounds"]) + 1
        self.round_deadline = time.monotonic() + self.round_seconds
        if not self.state["scoreboard"]:
            kind = "scout"
        elif not self.state["patterns"]:
            kind = "patterns"
        elif not self.state["champion"]:
            kind = "build"
        else:
            kind = "challenge"
        self.phase = f"round {number}: {kind}"
        self.save_session("running")
        budget = human(min(self.round_left(), self.left()))
        self.say(f"\n[round {number}] {kind.upper()} (up to {budget})")
        record: dict[str, Any] = {"number": number, "kind": kind,
                                  "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        lessons = getattr(self, f"_round_{kind}")(record)
        record["lessons"] = lessons
        champ = self.state.get("champion")
        if champ:
            record["champion_score"] = round(champ["scores"].get("hold", 0.0), 5)
        self.state["rounds"].append(record)
        self.rounds_done += 1
        self.save()
        self.save_session("running")
        self.on_progress()
        for line in lessons:
            self.say(f"[round {number}]   - {line}")
        self.say(f"[round {number}] saved; {human(self.left())} left.")

    # ------------------------------------------------------------ 1. scout

    def _scout(self, window: tuple[int, int], symbols: list[str] | None = None) -> list[dict]:
        results = self._map(scout_job, [(c, window, self.cfg.horizons)
                                        for c in self._chunks(symbols)])
        return scoreboard(results, self.store.columns, self.cfg.horizons)

    def _round_scout(self, record: dict[str, Any]) -> list[str]:
        self.say("Looking at what every indicator did on the training candles (no trading)...")
        board = self._scout(self.windows["train"])
        self.state["scoreboard"] = [_compact(e) for e in board]
        self._remember(board)
        record["top"] = [_compact(e) for e in board[:20]]
        return describe_board(board)

    # ------------------------------------------------------------ 2. patterns

    def _round_patterns(self, record: dict[str, Any]) -> list[str]:
        lessons = []
        a, b = self.windows["train"]
        mid = (a + b) // 2
        self.say("Re-scouting each half of training to see which readings held up...")
        first, second = self._scout((a, mid)), self._scout((mid, b))
        by1 = {e["feature"]: e for e in first}
        by2 = {e["feature"]: e for e in second}
        board = self.state["scoreboard"]
        stable = []
        for e in board:
            e1, e2 = by1.get(e["feature"]), by2.get(e["feature"])
            if e1 and e2 and e1["sign"] == e2["sign"] == e["sign"] and min(
                    abs(e1["t"]), abs(e2["t"])) >= 1.0:
                stable.append(e)
        top50 = board[:50]
        held = sum(1 for e in top50 if e in stable)
        lessons.append(f"{held} of the top 50 indicators read the same way in both halves of "
                       f"training; {len(stable)} in all. Only those go forward.")
        self.state["stable"] = [e["feature"] for e in stable]

        pool = choose_pool(stable or board, POOL_SIZE, PER_FAMILY)
        cols = [e["feature"] for e in pool]
        signs = [e["sign"] for e in pool]
        h = Counter(e["horizon"] for e in pool).most_common(1)[0][0]
        self.say(f"Checking all {len(cols) * (len(cols) - 1) // 2} pairs of the best {len(cols)} "
                 f"({h} candles ahead)...")
        parts = self._map(pairs_job, [(c, self.windows["train"], cols, signs, h)
                                      for c in self._chunks()])
        tot = {k: sum(p[k] for p in parts) for k in parts[0]}
        n = tot["nl"] + tot["ns"]
        with np.errstate(all="ignore"):
            edge = (tot["sl"] - tot["ss"]) / n
        single = np.diag(edge)
        pairs = []
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                if n[i, j] < 300 or not np.isfinite(edge[i, j]):
                    continue
                if cols[i].split("@")[0] == cols[j].split("@")[0]:
                    continue  # the same indicator on two timeframes isn't a pattern
                lift = edge[i, j] - max(single[i], single[j])
                pairs.append({"features": [cols[i], cols[j]], "edge_bps": float(edge[i, j] * 1e4),
                              "lift_bps": float(lift * 1e4), "count": int(n[i, j]),
                              "strength": float(edge[i, j] * np.sqrt(n[i, j]) * 1e4)})
        pairs.sort(key=lambda p: -p["strength"])
        good_pairs = [p for p in pairs if p["lift_bps"] > 0][:15]
        triples = []
        if good_pairs:
            idx = {c: i for i, c in enumerate(cols)}
            pair_idx = [(idx[p["features"][0]], idx[p["features"][1]]) for p in good_pairs]
            self.say(f"Extending the best {len(pair_idx)} pairs into trios...")
            parts = self._map(triples_job, [(c, self.windows["train"], cols, signs, h, pair_idx)
                                            for c in self._chunks()])
            tn = sum(p["n"] for p in parts)
            te = sum(p["edge"] for p in parts)
            for p_i, (i, j) in enumerate(pair_idx):
                for k in range(len(cols)):
                    if k in (i, j) or tn[p_i, k] < 200:
                        continue
                    e3 = te[p_i, k] / tn[p_i, k]
                    if e3 * 1e4 <= good_pairs[p_i]["edge_bps"]:
                        continue
                    trio = sorted([cols[i], cols[j], cols[k]])
                    triples.append({"features": trio, "edge_bps": float(e3 * 1e4),
                                    "count": int(tn[p_i, k]),
                                    "strength": float(e3 * np.sqrt(tn[p_i, k]) * 1e4)})
            seen, unique = set(), []
            for t in sorted(triples, key=lambda t: -t["strength"]):
                if tuple(t["features"]) not in seen:
                    seen.add(tuple(t["features"]))
                    unique.append(t)
            triples = unique[:15]
        self.state["patterns"] = {"horizon": h, "pool": cols, "signs": signs,
                                  "pairs": pairs[:30], "triples": triples}
        record["pairs"] = pairs[:10]
        record["triples"] = triples[:5]
        for p in pairs[:5]:
            lessons.append(f"Pair: {' + '.join(p['features'])} agree -> {p['edge_bps']:+.1f} bps "
                           f"per signal over {p['count']} signals (lift {p['lift_bps']:+.1f}).")
        for t in triples[:3]:
            lessons.append(f"Trio: {' + '.join(t['features'])} -> {t['edge_bps']:+.1f} bps "
                           f"over {t['count']}.")
        if not pairs:
            lessons.append("No pair agreed often enough to judge; packages will lean on singles.")
        return lessons

    # ------------------------------------------------------------ 3. build

    def evaluate(self, packages: list[Package], part: str) -> list[Result]:
        """Backtest packages on one part of history (all symbols, all cores)."""
        if not packages:
            return []
        dicts = [p.with_(size=REF_SIZE).to_dict() for p in packages]
        parts = self._map(trades_job, [(dicts, c, self.windows[part]) for c in self._chunks()])
        results = []
        for i, pkg in enumerate(packages):
            trades = Trades.concat([chunk[i] for chunk in parts])
            _, m = portfolio(trades)
            r = Result(pkg)
            r.metrics[part] = m
            r.scores[part] = score_of(m, self.cfg.loss_aversion, self._min_trades(part))
            results.append(r)
        return results

    def _min_trades(self, part: str) -> int:
        """Trades a part of history needs before its result counts: in proportion to its length."""
        a, b = self.windows[part]
        ta, tb = self.windows["train"]
        return max(5, round(self.cfg.min_trades * (b - a) / max(tb - ta, 1)))

    def _fill(self, results: list[Result], part: str) -> None:
        todo = [r for r in results if part not in r.metrics]
        for r, done in zip(todo, self.evaluate([r.package for r in todo], part), strict=True):
            r.metrics[part], r.scores[part] = done.metrics[part], done.scores[part]

    def _round_build(self, record: dict[str, Any]) -> list[str]:
        patterns = self.state["patterns"]
        board = {e["feature"]: e for e in self.state["scoreboard"]}
        pool = list(dict.fromkeys(
            patterns["pool"] + [f for t in patterns["triples"] for f in t["features"]]
            + [f for p in patterns["pairs"] for f in p["features"]]))
        signs = np.array([board[f]["sign"] for f in pool], dtype=np.float32)
        h = patterns["horizon"]
        per_symbol = max(200, 200_000 // max(len(self.symbols), 1))
        self.say(f"Sampling {per_symbol} candles per symbol to assemble packages...")
        parts = self._map(sample_job, [(c, self.windows["train"], pool, h, per_symbol,
                                        self.rng.randrange(1 << 30)) for c in self._chunks()])
        x = np.concatenate([p["x"] for p in parts]) * signs
        f = np.concatenate([p["f"] for p in parts])
        cost = np.concatenate([p["cost"] for p in parts])
        self.say(f"Assembling packages from {len(pool)} indicators over {len(f):,} candles...")
        candidates: list[Package] = []
        for weighting in ("equal", "evidence"):
            weight = (np.ones(len(pool)) if weighting == "equal"
                      else np.array([min(abs(board[c]["t"]), 10.0) for c in pool]))
            for size, chosen, theta in greedy(x, f, cost, pool, weight, SIZES):
                w = {pool[i]: float(signs[i] * weight[i]) for i in chosen}
                candidates.append(Package(w, threshold=theta,
                                          name=f"greedy-{weighting}-{size}"))
        for t in patterns["triples"][:5]:
            w = {c: float(board[c]["sign"]) for c in t["features"]}
            for fam in FAMILIES:  # top up to at least one per family
                if fam not in {family_of(c) for c in w}:
                    best = next((e for e in self.state["scoreboard"] if e["family"] == fam), None)
                    if best:
                        w[best["feature"]] = float(best["sign"]) * 0.5
            candidates.append(Package(w, threshold=0.3, name="trio+families"))
        baseline = Package(dict(BASELINE), threshold=0.25, name="original strategy")
        self.say(f"Backtesting {len(candidates)} packages and the original strategy on training...")
        results = self.evaluate(candidates + [baseline], "train")
        base_result = results.pop()
        results.sort(key=lambda r: -r.scores["train"])
        top = results[:6]
        self._fill(top + [base_result], "hold")
        lessons = [f"The original strategy (as a package): {self.brief(base_result)}."]
        for r in top:
            lessons.append(f"{r.package.name}: {r.package.describe()} -> {self.brief(r)}")
        proven = [r for r in top if r.scores["hold"] > max(base_result.scores["hold"], 0.0)]
        champ = max(proven or top, key=lambda r: (r.scores["train"] + r.scores["hold"]))
        self._fill([champ, base_result], "final")
        self.state["baseline"] = base_result.to_dict()
        self._crown(champ)
        self.state["hall_of_fame"] = [r.to_dict() for r in top]
        lessons.append(("Champion" if self.state["champion"]["proven"] else
                        "Best so far (not yet confirmed on held-out, so it trades small)")
                       + f": {self.report(champ)}")
        return lessons

    def fit_size(self, pkg: Package) -> tuple[float, dict[str, float]]:
        """How much to bet: the largest trade size whose training drawdown stays inside
        the budget. A package that loses on training gets the smallest size."""
        parts = self._map(trades_job, [([pkg.with_(size=REF_SIZE).to_dict()], c,
                                         self.windows["train"]) for c in self._chunks()])
        trades = Trades.concat([p[0] for p in parts])
        best, best_m = 0.02, portfolio(_scaled(trades, 0.02 / REF_SIZE))[1]
        if best_m["total_return"] <= 0:
            return best, best_m
        for size in np.arange(0.03, self.cfg.max_size + 1e-9, 0.01):
            _, m = portfolio(_scaled(trades, size / REF_SIZE))
            if m["max_drawdown"] < -self.cfg.drawdown_budget or m["total_return"] <= 0:
                break
            best, best_m = round(float(size), 2), m
        return best, best_m

    def _crown(self, result: Result) -> None:
        """Make this the champion. Its trade size comes from its training drawdown, but only
        once held-out confirms it (made money, and beat the original strategy there);
        until then it trades at the smallest size."""
        base = (self.state.get("baseline") or {}).get("scores", {}).get("hold", 0.0)
        hold = result.metrics.get("hold") or {}
        proven = hold.get("total_return", 0.0) > 0 and result.scores.get("hold", 0.0) >= base
        size, at_size = self.fit_size(result.package) if proven else (0.02, {})
        result.package = result.package.with_(size=size)
        data = result.to_dict()
        data["at_size"] = _round(at_size)
        data["proven"] = proven
        data["since"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.state["champion"] = data

    # ------------------------------------------------------------ 4. challenge

    def _round_challenge(self, record: dict[str, Any]) -> list[str]:
        lessons = []
        champ = Result.from_dict(self.state["champion"])
        # Look at the indicators again, on a fresh stretch and some of the symbols.
        a, b = self.windows["train"]
        length = (b - a) // 3
        start = self.rng.randint(a, b - length)
        sample = self.rng.sample(self.symbols, max(len(self.symbols) // 3,
                                                   min(len(self.symbols), 12)))
        self.say(f"Looking at the indicators again: {len(sample)} symbols, "
                 f"{_day(start)}..{_day(start + length)}...")
        fresh = self._scout((start, start + length), sample)
        lessons += self._compare_boards(fresh)
        self._remember(fresh)

        champ.metrics, champ.scores = {}, {}  # re-test: the data may have grown since
        self._fill([champ], "train")
        self._fill([champ], "hold")
        self.say(f"Champion: {self.report(champ)}")
        tried = adopted = 0
        batch = max(8, self.cfg.workers * 2)
        explored = False
        while self.round_left() > 0 and self.left() > MIN_LEFT and not self.complete():
            # Mostly single changes; a quarter bigger jumps, and all jumps once the
            # single changes are used up, so a round never just idles.
            options = (self.coverage_changes(champ.package, batch // 2)
                       if self.until_done else [])
            options += self.mutations(champ.package, batch - batch // 4 - len(options))
            options += self.explorations(champ.package, batch - len(options))
            if len(options) < batch // 2 and not explored:
                explored = True
                lessons.append("Single changes to the champion are used up; exploring bigger "
                               "jumps (random packages, mixes, two changes at once).")
            if not options:
                break
            # leave room: a batch on training plus a few on held-out
            est = (np.mean(self.job_seconds[-10:]) if self.job_seconds else 30.0) * 2
            if tried and self.round_left() < est:
                break
            results = self.evaluate([p for p, _ in options], "train")
            tried += len(results)
            self._mark_tested(champ.package, [p for p, _ in options])
            gains = [(r, label, r.scores["train"] - champ.scores["train"])
                     for r, (_, label) in zip(results, options, strict=True)]
            gains.sort(key=lambda g: -g[2])
            hopeful = [g for g in gains if g[2] >= self.cfg.min_gain][:4]
            best_line = (f"best {gains[0][1]} ({gains[0][2]:+.2%} training)" if gains else "")
            self.say(f"  tried {len(results)} changes; {best_line}")
            if not hopeful:
                continue
            self._fill([g[0] for g in hopeful], "hold")
            winners = [g for g in hopeful
                       if g[0].scores["hold"] - champ.scores["hold"] >= self.cfg.min_gain
                       and g[0].metrics["train"]["num_trades"] >= self.cfg.min_trades]
            if not winners:
                self.say(f"  {hopeful[0][1]} didn't hold up held-out "
                         f"({hopeful[0][0].scores['hold'] - champ.scores['hold']:+.2%}).")
                continue
            r, label, gain = winners[0]
            hold_gain = r.scores["hold"] - champ.scores["hold"]
            self._fill([r], "final")
            champ = r
            self.tried.clear()
            self._crown(r)
            self._hall(r)
            adopted += 1
            line = f"KEPT {label}: {gain:+.2%} training, {hold_gain:+.2%} held-out"
            self.kept.append(label)
            lessons.append(line + ".")
            self.say(f"  {line}. Now: {self.report(r)}")
            self.save()
            self.save_session("running")
            self.on_progress()
        lessons.append(f"Tried {tried} changes, kept {adopted}. Champion: {self.report(champ)}")
        if self.until_done:
            done, total = self.coverage()
            lessons.append(f"Indicators tried in the champion set: {done} of {total}.")
        lessons += self.trade_lessons(champ)
        return lessons

    def mutations(self, pkg: Package, count: int,
                  remember: bool = True) -> list[tuple[Package, str]]:
        """Up to `count` single changes to the champion, mixed across kinds."""
        board = self.state["scoreboard"]
        stable = set(self.state.get("stable") or [e["feature"] for e in board])
        ranked = [e for e in board if e["feature"] in stable and e["feature"] not in pkg.weights]
        partners = [f for p in self.state["patterns"].get("pairs", []) for f in p["features"]
                    if any(m in pkg.weights for m in p["features"])]
        families = pkg.families
        scale = float(np.mean(np.abs(list(pkg.weights.values()))))
        out: list[tuple[Package, str]] = []

        adds = list(dict.fromkeys([f for f in partners if f not in pkg.weights]
                                  + [e["feature"] for e in ranked[:40]]))
        if len(pkg.weights) < MAX_FEATURES:
            for f in self.rng.sample(adds, min(len(adds), count // 3 + 1)):
                sign = next((e["sign"] for e in board if e["feature"] == f), 1)
                out.append((pkg.with_(weights={**pkg.weights, f: sign * scale}), f"add {f}"))
        members = list(pkg.weights)
        for f in self.rng.sample(members, min(len(members), count // 5 + 1)):
            if families[family_of(f)] > 1 and len(members) > 1:
                w = dict(pkg.weights)
                w.pop(f)
                out.append((pkg.with_(weights=w), f"drop {f}"))
            same = [e["feature"] for e in ranked[:120] if e["family"] == family_of(f)]
            if same:
                g = self.rng.choice(same[:10])
                sign = next(e["sign"] for e in board if e["feature"] == g)
                w = dict(pkg.weights)
                w[g] = sign * abs(w.pop(f))
                out.append((pkg.with_(weights=w), f"swap {f} -> {g}"))
        for f in self.rng.sample(members, min(len(members), 2)):
            for factor, word in ((2.0, "double"), (0.5, "halve")):
                w = dict(pkg.weights)
                w[f] *= factor
                out.append((pkg.with_(weights=w), f"{word} {f}"))
        out += knob_changes(pkg)
        self.rng.shuffle(out)
        unique = []
        for p, label in out:
            key = json.dumps(p.to_dict(), sort_keys=True)
            if key not in self.tried:
                unique.append((p, label))
        unique = unique[:count]
        if remember:
            self.tried.update(json.dumps(p.to_dict(), sort_keys=True) for p, _ in unique)
        return unique

    def explorations(self, pkg: Package, count: int) -> list[tuple[Package, str]]:
        """Bigger jumps: random packages, mixes with the hall of fame, double changes."""
        board = self.state["scoreboard"]
        stable = set(self.state.get("stable") or [e["feature"] for e in board])
        usable = [e for e in board if e["feature"] in stable][:200]
        hall = [Package.from_dict(d["package"]) for d in self.state["hall_of_fame"]]
        out: list[tuple[Package, str]] = []
        for _ in range(count * 4):
            if len(out) >= count:
                break
            kind = self.rng.random()
            if kind < 0.4 and usable:
                size = int(round(np.exp(self.rng.uniform(np.log(5), np.log(MAX_FEATURES)))))
                picks = {}
                for fam in FAMILIES:
                    options = [e for e in usable if e["family"] == fam][:15]
                    if options:
                        e = self.rng.choice(options)
                        picks[e["feature"]] = e
                rest = [e for e in usable if e["feature"] not in picks]
                for e in self.rng.sample(rest, min(len(rest), max(size - len(picks), 0))):
                    picks[e["feature"]] = e
                by_evidence = self.rng.random() < 0.5
                w = {f: e["sign"] * (min(abs(e["t"]), 10.0) if by_evidence else 1.0)
                     for f, e in picks.items()}
                cand = pkg.with_(weights=w, threshold=round(self.rng.uniform(0.2, 0.6), 2))
                label = f"explore: random {len(w)}-indicator package"
            elif kind < 0.7 and hall:
                other = self.rng.choice(hall)
                union = {**other.weights, **pkg.weights}
                keep = self.rng.sample(list(union), max(len(FAMILIES), len(union) // 2))
                w = {f: union[f] for f in keep}
                for fam in FAMILIES:  # keep every family
                    if fam not in {family_of(f) for f in w}:
                        extra = [f for f in union if family_of(f) == fam]
                        if extra:
                            w[extra[0]] = union[extra[0]]
                cand = pkg.with_(weights=w)
                label = f"explore: mix with a top-10 package ({len(w)} indicators)"
            else:
                singles = self.mutations(pkg, 40, remember=False)
                if len(singles) < 2:
                    continue
                (p1, l1), (p2, l2) = self.rng.sample(singles, 2)
                merged = p1.to_dict()
                weights = dict(p1.weights)
                for f in set(pkg.weights) - set(p2.weights):
                    weights.pop(f, None)
                weights.update({f: v for f, v in p2.weights.items() if pkg.weights.get(f) != v})
                merged["weights"] = weights
                for k, v in p2.to_dict().items():
                    if k not in ("weights", "name") and v != getattr(pkg, k):
                        merged[k] = v
                cand = Package.from_dict(merged)
                label = f"two changes: {l1} + {l2}"
            key = json.dumps(cand.to_dict(), sort_keys=True)
            if key not in self.tried:
                self.tried.add(key)
                out.append((cand, label))
        return out

    def _hall(self, result: Result) -> None:
        hall = [Result.from_dict(d) for d in self.state["hall_of_fame"]] + [result]
        hall.sort(key=lambda r: -(r.scores.get("train", 0) + r.scores.get("hold", 0)))
        self.state["hall_of_fame"] = [r.to_dict() for r in hall[:10]]

    # ------------------------------------------------------------ reporting

    def session_report(self, status: str) -> Report:
        """This session, for reports/: the champion, what was kept, what was tried."""
        kind = "champ" if self.until_done else "lab"
        took = human((datetime.now(timezone.utc) - self.started).total_seconds())
        rounds = self.state["rounds"][-self.rounds_done:] if self.rounds_done else []
        champ = self.state.get("champion")
        hold = ((champ or {}).get("metrics") or {}).get("hold") or {}
        tested, total = self.coverage()
        word = ("every indicator tried" if self.done else
                "stopped" if status == "stopped" else "time up")
        summary = (f"{self.rounds_done} round(s) in {took}, {len(self.kept)} change(s) kept"
                   + (f", {tested}/{total} indicators tried" if self.until_done else "")
                   + (f"; champion held-out {hold['total_return']:+.1%}" if hold else "")
                   + f" ({word})")
        out = Report(kind, summary, tone_of(hold.get("total_return", 0.0)) if hold else "neutral",
                     title="Champ-set builder" if self.until_done else "Indicator lab",
                     subtitle=f"{len(self.symbols)} symbols")
        out.stat("Rounds", self.rounds_done).stat("Changes kept", len(self.kept),
                                                   "good" if self.kept else "neutral")
        if self.until_done:
            out.stat("Indicators tried", f"{tested} / {total}", "good" if self.done else "neutral")
        out.stat("Ran for", took)
        if champ:
            result = Result.from_dict(champ)
            for part in ("train", "hold", "final"):
                m = result.metrics.get(part)
                if m:
                    out.stat(f"Champion {_PART[part]}", f"{m['total_return']:+.1%}",
                             tone_of(m["total_return"]))
            out.text("Champion", self.report(result)
                     + ("" if champ.get("proven", True) else
                        " Not yet confirmed on held-out, so it trades at the smallest size."))
            out.table("Champion indicators", [
                {"Indicator": f, "Family": family_of(f), "Use": "follow" if v > 0 else "fade",
                 "Weight": round(abs(v), 3)}
                for f, v in sorted(result.package.weights.items(), key=lambda kv: -abs(kv[1]))])
        out.bullets("Kept this session", self.kept, "No change beat the champion this time.")
        out.table("Rounds", [
            {"Round": r.get("number"), "Kind": r.get("kind"),
             "Kept": sum(1 for line in r.get("lessons") or [] if line.startswith("KEPT")),
             "Note": next((line for line in r.get("lessons") or []
                           if line.startswith(("Tried", "Champion", "Pair"))), "")}
            for r in rounds])
        if rounds:
            out.bullets("What the last round saw", rounds[-1].get("lessons") or [])
        out.table("Clearest indicators", [
            {"Indicator": e["feature"], "Family": e["family"],
             "Use": "follow" if e["sign"] > 0 else "fade", "t": round(abs(e["t"]), 2),
             "Right": f"{e['hit']:.0%}", "Signals": e["signals"]}
            for e in self.state["scoreboard"][:15]])
        return out

    def _remember(self, board: list[dict[str, Any]]) -> None:
        hist = self.state["indicator_history"]
        signs = {b["feature"]: b["sign"] for b in self.state["scoreboard"]}
        for rank, e in enumerate(board[:100]):
            h = hist.setdefault(e["feature"], {"seen": 0, "same_sign": 0, "best_rank": 999})
            h["seen"] += 1
            h["same_sign"] += int(e["sign"] == signs.get(e["feature"], e["sign"]))
            h["best_rank"] = min(h["best_rank"], rank + 1)

    def _compare_boards(self, fresh: list[dict[str, Any]]) -> list[str]:
        old = {e["feature"]: e["sign"] for e in self.state["scoreboard"]}
        new = {e["feature"]: e["sign"] for e in fresh}
        top_old = [e["feature"] for e in self.state["scoreboard"][:30]]
        top_new = [e["feature"] for e in fresh[:30]]
        flipped = [f for f in top_old if new.get(f, old[f]) != old[f]]
        risers = [f for f in top_new if f not in top_old][:3]
        lines = [f"Fresh look: {len(set(top_old) & set(top_new))} of the top 30 indicators are "
                 f"still top 30" + (f"; {len(flipped)} flipped direction" if flipped else "") + "."]
        if risers:
            lines.append("Rising: " + ", ".join(risers) + ".")
        return lines

    def brief(self, r: Result) -> str:
        parts = []
        for p in ("train", "hold", "final"):
            if p in r.metrics:
                m = r.metrics[p]
                parts.append(f"{_PART[p]} {m['total_return']:+.1%} (DD {m['max_drawdown']:.1%}, "
                             f"{int(m['num_trades'])} trades, {m['win_rate']:.0%} won)")
        return "; ".join(parts)

    def report(self, r: Result) -> str:
        return (f"{r.package.describe()}, {r.package.size:.0%} a trade. {self.brief(r)} "
                f"(results at {REF_SIZE:.0%} a trade)")

    def trade_lessons(self, r: Result) -> list[str]:
        parts = self._map(trades_job, [([r.package.with_(size=REF_SIZE).to_dict()], c,
                                         self.windows["train"])
                                       for c in self._chunks()])
        trades, m = portfolio(Trades.concat([p[0] for p in parts]))
        if not len(trades):
            return ["The champion made no trades on training."]
        pnl = trades.fraction * trades.ret
        out = [f"Longs {m['long_pnl']:+.1%}, shorts {m['short_pnl']:+.1%}; average hold "
               f"{m['avg_bars']:.0f} candles."]
        reasons = Counter()
        for code, value in zip(trades.reason, pnl, strict=True):
            reasons[EXIT_REASONS[code]] += value
        out.append("Exits: " + ", ".join(f"{k} {v:+.1%}" for k, v in reasons.most_common()) + ".")
        by_symbol: dict[str, float] = {}
        for s, v in zip(trades.symbol, pnl, strict=True):
            by_symbol[s] = by_symbol.get(s, 0.0) + v
        ranked = sorted(by_symbol.items(), key=lambda kv: kv[1])
        out.append("Best: " + ", ".join(f"{s} {v:+.1%}" for s, v in ranked[::-1][:3])
                   + "; worst: " + ", ".join(f"{s} {v:+.1%}" for s, v in ranked[:3]) + ".")
        return out


_PART = {"train": "training", "hold": "held-out", "final": "final check"}


# ------------------------------------------------------------------ building blocks


def _scaled(trades: Trades, factor: float) -> Trades:
    return Trades(trades.entry_time, trades.exit_time, trades.direction,
                  trades.fraction * factor, trades.ret, trades.bars, trades.reason, trades.symbol)


def choose_pool(board: list[dict[str, Any]], size: int, per_family: int) -> list[dict[str, Any]]:
    pool: list[dict[str, Any]] = []
    for fam in FAMILIES:
        pool += [e for e in board if e["family"] == fam][:per_family]
    for e in board:
        if len(pool) >= size:
            break
        if e not in pool:
            pool.append(e)
    return sorted(pool, key=lambda e: -abs(e["t"]))[:max(size, per_family * len(FAMILIES))]


def proxy(score: np.ndarray, f: np.ndarray, cost: np.ndarray) -> tuple[float, float]:
    """How clearly a composite score predicts the move, after trading costs:
    the best t-statistic over a few entry thresholds."""
    best, best_theta = -np.inf, 0.3
    for theta in (0.15, 0.25, 0.35, 0.45, 0.6):
        long_ = score >= theta
        short = score <= -theta
        r = np.concatenate([f[long_], -f[short]]) - np.concatenate([cost[long_], cost[short]])
        if len(r) < 200:
            continue
        sd = r.std()
        if sd <= 0:
            continue
        t = r.mean() / sd * np.sqrt(len(r))
        if t > best:
            best, best_theta = t, theta
    return float(best), best_theta


def greedy(x: np.ndarray, f: np.ndarray, cost: np.ndarray, pool: list[str], weight: np.ndarray,
           sizes: tuple[int, ...]) -> list[tuple[int, list[int], float]]:
    """Start with the best indicator of each family, then add whichever helps most."""
    fams = [family_of(c) for c in pool]
    chosen: list[int] = []
    for fam in FAMILIES:
        idx = [i for i, fm in enumerate(fams) if fm == fam]
        if idx:
            chosen.append(max(idx, key=lambda i: proxy(x[:, i], f, cost)[0]))
    num = x[:, chosen] @ weight[chosen]
    den = weight[chosen].sum()
    out = []
    current, theta = proxy(num / den, f, cost)
    stale = 0
    while len(chosen) < min(MAX_FEATURES, len(pool)):
        if len(chosen) in sizes or not out:
            out.append((len(chosen), list(chosen), theta))
        best_i, best_val, best_theta = -1, -np.inf, theta
        for i in range(len(pool)):
            if i in chosen:
                continue
            val, th = proxy((num + weight[i] * x[:, i]) / (den + weight[i]), f, cost)
            if val > best_val:
                best_i, best_val, best_theta = i, val, th
        if best_i < 0:
            break
        stale = stale + 1 if best_val <= current else 0
        if stale > 6:
            break
        chosen.append(best_i)
        num = num + weight[best_i] * x[:, best_i]
        den += weight[best_i]
        current, theta = max(current, best_val), best_theta
    if not out or out[-1][0] != len(chosen):
        out.append((len(chosen), list(chosen), theta))
    return out


def knob_changes(pkg: Package) -> list[tuple[Package, str]]:
    out: list[tuple[Package, str]] = []

    def add(label: str, **changes: Any) -> None:
        if any(getattr(pkg, k) != v for k, v in changes.items()):
            out.append((pkg.with_(**changes), label))

    for d in (-0.05, 0.05):
        t = round(pkg.threshold + d, 3)
        if 0.05 <= t <= 0.9:
            add(f"entry bar {pkg.threshold:.2f} -> {t:.2f}", threshold=t)
    for d in (-0.1, 0.1):
        e = round(pkg.exit_threshold + d, 3)
        if -0.5 <= e < pkg.threshold:
            add(f"exit bar {pkg.exit_threshold:+.2f} -> {e:+.2f}", exit_threshold=e)
    for d in (-0.5, 1.0):
        s = round(pkg.stop_atr + d, 2)
        if 1.0 <= s <= 8.0:
            add(f"stop {pkg.stop_atr:g} -> {s:g} ATR", stop_atr=s)
    if pkg.take_atr is None:
        add("take profit at 6 ATR", take_atr=6.0)
    else:
        add("no take profit", take_atr=None)
        for v in (pkg.take_atr - 1, pkg.take_atr + 2):
            if 2 <= v <= 15:
                add(f"take profit {pkg.take_atr:g} -> {v:g} ATR", take_atr=v)
    for v in sorted({0, 3, 6, 12, 24, pkg.min_hold * 2}):
        if v != pkg.min_hold and abs(v - pkg.min_hold) <= max(12, pkg.min_hold):
            add(f"hold at least {v} candles", min_hold=v)
    for v in sorted({0, 3, 6, 12, 36, pkg.cooldown * 2}):
        if v != pkg.cooldown and abs(v - pkg.cooldown) <= max(24, pkg.cooldown):
            add(f"wait {v} candles between trades", cooldown=v)
    for v in (pkg.confirm - 1, pkg.confirm + 1):
        if 1 <= v <= 6:
            add(f"signal must hold {v} candles", confirm=v)
    for v in (0, 39, 156) if pkg.max_hold == 0 else (0, pkg.max_hold // 2, pkg.max_hold * 2):
        if v != pkg.max_hold and v >= 0:
            add(f"max hold {v or 'none'} candles", max_hold=v)
    add("shorts " + ("off" if pkg.shorts else "on"), shorts=not pkg.shorts)
    return out


def describe_board(board: list[dict[str, Any]]) -> list[str]:
    lines = ["Clearest indicators (follow = do what it says, fade = do the opposite):"]
    for e in board[:10]:
        lines.append(
            f"  {e['feature']:<28} {'follow' if e['sign'] > 0 else 'fade':<6} "
            f"t={e['t']:+.1f} "
            f"right {e['hit']:.0%} of {e['signals']} strong signals, {e['edge_bps']:+.1f} bps "
            f"after {e['horizon']} candles")
    for fam in FAMILIES:
        best = next((e for e in board if e["family"] == fam), None)
        if best:
            lines.append(f"Best {fam}: {best['feature']} (t={best['t']:+.1f})")
    tfs = Counter(e["feature"].split("@")[1] for e in board[:50])
    lines.append("Timeframes in the top 50: "
                 + ", ".join(f"{tf} {n}" for tf, n in tfs.most_common()))
    return lines


def _compact(e: dict[str, Any]) -> dict[str, Any]:
    return {"feature": e["feature"], "family": e["family"], "sign": e["sign"],
            "t": round(e["t"], 2), "ic": round(e["ic"], 4), "hit": round(e["hit"], 3),
            "edge_bps": round(e["edge_bps"], 2), "signals": e["signals"], "horizon": e["horizon"]}


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat(timespec="minutes")


def _day(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")


def _write_json(path: Path, data: Any) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=float), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
