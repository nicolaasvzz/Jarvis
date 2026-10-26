"""A setup: three charts' indicator sets, and the rules for one trade on the 1-hour chart.

Each chart has its own set of indicators, every one followed (+1) or faded
(-1), and the chart's score is their average vote, from -1 to +1. When a
1-hour candle closes, a trade is taken only if:

1. the **1h** score leans one way by at least ``entry``: that's the direction;
2. the **4h** score leans the same way by at least ``agree_4h``, and the
   **30m** score by at least ``agree_30m``;
3. the setup's **formula** gives it at least ``confidence`` chance of
   reaching the take-profit before the stop. There are three kinds:

   - ``vote`` (chart votes): a logistic model on the three chart scores alone,
     fitted on training history: how often trades like this one, with this
     much agreement on each chart, won. Its weights show how much each chart
     really adds, so three charts agreeing isn't simply three votes.
   - ``weights`` (learned weights), ``trees`` (decision trees) and ``bagged``
     (averaged trees: several sets of trees, averaged) read every indicator in
     ``inputs`` (the clearest of each look-alike group on all three charts)
     and learn how they work together. See ``formulas.py``.

Then three exits (the "triple barrier"), whichever comes first:

- **stop**: ``sl_atr`` 1-hour ATRs against the entry;
- **take-profit**: ``tp_atr`` 1-hour ATRs in favour, but never further than
  the 4-hour chart says a move usually goes in ``hours`` hours
  (4h ATR x sqrt(hours / 4));
- **time limit**: ``hours`` 1-hour candles; for stocks, the day's close too.

The builder puts the stop and target where past trades say (MAE/MFE): the
stop just beyond the dip most winning trades survived, the target near where
winning trades typically got to.

Entries fill at the next candle's open. Stops and targets are checked inside
each candle against its high and low, always the cautious way: if one candle
touches both, the stop counts; a candle that opens past the stop exits at that
(worse) open, but one that opens past the target only gets the target.
"""
from __future__ import annotations

import math
import pickle
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

from .catalog import family_of
from .charts import CHARTS, chart_of

EXITS = ("stop", "take profit", "time limit", "day's close", "end of data")
STOP, TAKE, TIME, CLOSE, END = range(5)
FULL_SIZE_ABOVE = 0.15     # full size once the chance is this far above the bar
FORMULAS = ("vote", "weights", "trees", "bagged")
FORMULA_WORDS = {"vote": "chart votes", "weights": "learned weights", "trees": "decision trees",
                 "bagged": "averaged trees"}
TREE_FORMULAS = ("trees", "bagged")   # kept as fitted models in champ_models/


@dataclass
class Setup:
    sets: dict[str, dict[str, float]]          # chart -> {feature column: +1 follow / -1 fade}
    entry: float = 0.3
    agree_4h: float = 0.0
    agree_30m: float = 0.0
    model: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])  # bias, 1h, 4h, 30m
    confidence: float = 0.5
    sl_atr: float = 1.5
    tp_atr: float = 2.0
    hours: int = 4
    size: float = 0.10
    shorts: bool = True
    name: str = ""
    formula: str = "vote"                       # vote | weights | trees
    inputs: dict[str, float] = field(default_factory=dict)  # weights/trees: column -> +1/-1
    coef: list[float] = field(default_factory=list)    # weights: bias, then one per feature
    centre: list[float] = field(default_factory=list)  # weights: each feature's mean...
    spread: list[float] = field(default_factory=list)  # ...and spread on training
    model_file: str = ""                        # trees/bagged: where the fitted trees are kept

    def __post_init__(self) -> None:
        self.sets = {tf: {f: float(w) for f, w in (self.sets.get(tf) or {}).items() if w}
                     for tf in CHARTS}
        missing = [tf for tf in CHARTS if not self.sets[tf]]
        if missing:
            raise ValueError(f"a setup needs indicators on every chart (none on {missing})")
        if self.formula not in FORMULAS:
            raise ValueError(f"unknown formula {self.formula!r}; use one of {FORMULAS}")
        if self.formula != "vote" and not self.inputs:
            raise ValueError(f"the {self.formula} formula needs inputs")
        self.model = [float(v) for v in self.model]
        self.inputs = {f: float(w) for f, w in self.inputs.items()}
        self.hours = int(self.hours)
        self._trees: Any = None  # the fitted trees, loaded from model_file when first needed

    @property
    def features(self) -> list[str]:
        """Every indicator column the setup reads: its chart sets, then its inputs."""
        out = [f for tf in CHARTS for f in self.sets[tf]]
        seen = set(out)
        return out + [f for f in self.inputs if f not in seen]

    def names(self) -> dict[str, list[str]]:
        """Indicator names per chart, for ``charts.decision_features``."""
        return {tf: [f.split("@", 1)[0] for f in self.features if chart_of(f) == tf]
                for tf in CHARTS}

    def with_(self, **changes: Any) -> Setup:
        data = asdict(self)
        data.update(changes)
        out = Setup(**data)
        if out.formula == self.formula and out.model_file == self.model_file:
            out._trees = self._trees
        return out

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["model"] = [round(v, 4) for v in self.model]
        out["coef"] = [round(v, 6) for v in self.coef]
        out["centre"] = [round(v, 6) for v in self.centre]
        out["spread"] = [round(v, 6) for v in self.spread]
        for key in ("entry", "agree_4h", "agree_30m", "confidence", "sl_atr", "tp_atr", "size"):
            out[key] = round(float(out[key]), 4)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Setup:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    def describe(self) -> str:
        counts = ", ".join(f"{len(self.sets[tf])} on {tf}" for tf in ("4h", "1h", "30m"))
        reads = ("" if self.formula == "vote" else
                 f"{FORMULA_WORDS[self.formula]} on {len(self.inputs)} indicators; ")
        return (f"{reads}votes from {counts}; trade when 1h >= {self.entry:.2f}, 4h and 30m "
                f"agree, {self.confidence:.0%} sure; stop {self.sl_atr:.2g} ATR, target "
                f"{self.tp_atr:.2g} ATR, at most {self.hours}h")

    # ------------------------------------------------------------ reading the charts

    def chart_scores(self, matrix: np.ndarray, columns: list[str]) -> dict[str, np.ndarray]:
        """Each chart's score per row, from a matrix whose columns are ``columns``."""
        where = {c: i for i, c in enumerate(columns)}
        out = {}
        for tf in CHARTS:
            w = np.array(list(self.sets[tf].values()), dtype=np.float32)
            x = np.nan_to_num(matrix[:, [where[f] for f in self.sets[tf]]].astype(np.float32))
            out[tf] = (x @ w) / float(np.abs(w).sum())
        return out

    def p_win(self, a1: np.ndarray, a4: np.ndarray, a30: np.ndarray) -> np.ndarray:
        """The chart-vote model: chance of a win from the three charts' agreement."""
        b, w1, w4, w30 = self.model
        z = b + w1 * a1 + w4 * a4 + w30 * a30
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))

    def formula_inputs(self, x: np.ndarray, d: np.ndarray, a1: np.ndarray, a4: np.ndarray,
                       a30: np.ndarray) -> np.ndarray:
        return formula_features(x, np.array(list(self.inputs.values())), d, a1, a4, a30)

    def chance(self, d: np.ndarray, a1: np.ndarray, a4: np.ndarray, a30: np.ndarray,
               x: np.ndarray | None = None) -> np.ndarray:
        """Chance of reaching the target before the stop, per row. ``x`` holds the
        raw values of ``inputs`` (rows x inputs), for the learned formulas."""
        if self.formula == "vote":
            return self.p_win(a1, a4, a30)
        if x is None:
            raise ValueError(f"the {self.formula} formula needs its inputs' values")
        f = self.formula_inputs(x, d, a1, a4, a30)
        if self.formula == "weights":
            scaled = (f - np.array(self.centre, dtype=np.float32)) / np.array(
                self.spread, dtype=np.float32)
            z = self.coef[0] + scaled @ np.array(self.coef[1:], dtype=np.float32)
            return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        return np.asarray(self.trees().predict_proba(f)[:, 1], dtype=float)

    def trees(self) -> Any:
        """The fitted trees (loaded from ``model_file`` the first time)."""
        if self._trees is None:
            self._trees = load_trees(self.model_file)
        return self._trees

    def decide(self, scores: dict[str, np.ndarray], can_short: bool = True,
               x: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Direction (+1, -1 or 0) and chance of winning, per row. ``x``: the raw
        values of ``inputs`` on those rows (learned formulas only)."""
        s1, s4, s30 = scores["1h"], scores["4h"], scores["30m"]
        d = np.where(s1 >= self.entry, 1, np.where(s1 <= -self.entry, -1, 0)).astype(np.int8)
        if not (self.shorts and can_short):
            d[d < 0] = 0
        a1, a4, a30 = np.abs(s1), d * s4, d * s30
        p = self.chance(d, a1, a4, a30, x)
        ok = (d != 0) & (a4 >= self.agree_4h) & (a30 >= self.agree_30m) & (p >= self.confidence)
        return np.where(ok, d, 0).astype(np.int8), p

    def fraction(self, p: np.ndarray | float) -> np.ndarray | float:
        """Share of the money: half ``size`` at the confidence bar, all of it well above."""
        lift = np.clip((np.asarray(p) - self.confidence) / FULL_SIZE_ABOVE, 0.0, 1.0)
        out = self.size * (0.5 + 0.5 * lift)
        return float(out) if np.ndim(out) == 0 else out

    def take_distance(self, atr: float, atr4h: float) -> float:
        """How far the take-profit sits, in price."""
        tp = self.tp_atr * atr
        if math.isfinite(atr4h) and atr4h > 0:
            tp = min(tp, atr4h * math.sqrt(self.hours / 4))
        return tp


def formula_features(x: np.ndarray, signs: np.ndarray, d: np.ndarray, a1: np.ndarray,
                     a4: np.ndarray, a30: np.ndarray) -> np.ndarray:
    """What a learned formula reads, per row: each input turned toward the trade
    (its usual reading x the direction), the three chart scores, and +1 for a long."""
    d = np.asarray(d, dtype=np.float32)
    toward = (np.nan_to_num(np.asarray(x, dtype=np.float32)) * np.asarray(signs, np.float32)
              * d[:, None])
    return np.column_stack([toward, a1, a4, a30, d]).astype(np.float32)


def save_trees(model: Any, path: str | Path) -> None:
    import sklearn

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as fh:
        pickle.dump({"model": model, "sklearn": sklearn.__version__}, fh)
    tmp.replace(path)


def load_trees(path: str | Path) -> Any:
    """Fitted trees saved by the builder. Only ever this bot's own file."""
    if not path:
        raise ValueError("this setup's trees were never saved: re-run the champ-set builder")
    try:
        with Path(path).open("rb") as fh:
            saved = pickle.load(fh)  # noqa: S301 - the builder's own file, not downloaded
    except (OSError, pickle.UnpicklingError, AttributeError, ImportError, EOFError) as exc:
        raise ValueError(f"couldn't load the trees in {path} ({exc}): re-run the champ-set "
                         "builder") from exc
    return saved["model"]


# ------------------------------------------------------------------ the triple barrier


def barrier(opn: np.ndarray, fav: np.ndarray, adv: np.ndarray, cls: np.ndarray,
            sl: np.ndarray | float, tp: np.ndarray | float, limit: np.ndarray,
            limit_why: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Where each trade ends, with every price in ATRs from the entry, signed so
    that + is in the trade's favour.

    ``opn``/``fav``/``adv``/``cls`` are (trades x candles): each candle's open,
    best point, worst point and close. ``limit`` is how many candles the trade
    may last, and ``limit_why`` what ends it then (TIME, CLOSE or END).
    Returns (result in ATRs, candle it ended in, why).
    """
    n, h = opn.shape
    sl = np.broadcast_to(np.asarray(sl, dtype=float), (n,))
    tp = np.broadcast_to(np.asarray(tp, dtype=float), (n,))
    result = np.full(n, np.nan)
    ended = np.full(n, -1, dtype=np.int32)
    why = np.full(n, -1, dtype=np.int8)
    done = np.zeros(n, dtype=bool)
    with np.errstate(invalid="ignore"):
        for k in range(h):
            live = ~done & (k < limit)
            if not live.any():
                break
            o, hi, lo = opn[:, k], fav[:, k], adv[:, k]
            gap_stop = live & (o <= -sl)
            gap_take = live & ~gap_stop & (o >= tp)
            stop = live & ~gap_stop & ~gap_take & (lo <= -sl)
            take = live & ~gap_stop & ~gap_take & ~stop & (hi >= tp)
            for mask, value, code in ((gap_stop, o, STOP), (gap_take, tp, TAKE),
                                      (stop, -sl, STOP), (take, tp, TAKE)):
                result[mask] = value[mask]
                why[mask] = code
                ended[mask] = k
                done |= mask
    rest = np.flatnonzero(~done)
    last = np.maximum(limit[rest], 1) - 1
    result[rest] = cls[rest, last]
    ended[rest] = last
    why[rest] = limit_why[rest]
    return result, ended, why


# ------------------------------------------------------------------ the confidence model


def fit_model(x: np.ndarray, y: np.ndarray, ridge: float = 1.0, steps: int = 30) -> list[float]:
    """Logistic regression (Newton steps, a ridge penalty on the slopes):
    chance of a win from (1h, 4h, 30m) agreement. Returns [bias, w1h, w4h, w30m]."""
    if len(y) < 20 or y.min() == y.max():
        rate = float(np.clip(y.mean() if len(y) else 0.5, 0.01, 0.99))
        return [math.log(rate / (1 - rate)), 0.0, 0.0, 0.0]
    a = np.column_stack([np.ones(len(x)), x]).astype(float)
    w = np.zeros(a.shape[1])
    penalty = np.diag([0.0] + [ridge] * x.shape[1])
    for _ in range(steps):
        p = 1.0 / (1.0 + np.exp(-np.clip(a @ w, -30, 30)))
        grad = a.T @ (p - y) + penalty @ w
        hess = (a * (p * (1 - p))[:, None]).T @ a + penalty + 1e-9 * np.eye(len(w))
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-7:
            break
    return [float(v) for v in w]


def luck_bar(trials: int) -> float:
    """The t-statistic the best of ``trials`` tries would reach by luck alone, on
    average (the expected maximum of that many standard normals, as in Bailey and
    Lopez de Prado's deflated Sharpe ratio). A setup must beat it."""
    if trials < 2:
        return 0.0
    gamma = 0.5772156649  # Euler-Mascheroni
    z = NormalDist().inv_cdf
    return float((1 - gamma) * z(1 - 1 / trials) + gamma * z(1 - 1 / (trials * math.e)))


def daily_t(exit_time: np.ndarray, pnl: np.ndarray, days: float) -> float:
    """t-statistic of the daily profit and loss over ``days`` days (days without a
    closed trade count as zero): trades on the same day aren't independent."""
    days = max(int(round(days)), 2)
    if not len(pnl):
        return 0.0
    _, per_day = np.unique(exit_time // 86_400_000_000_000, return_inverse=True)
    sums = np.bincount(per_day, weights=pnl)
    full = np.zeros(max(days, len(sums)))
    full[:len(sums)] = sums
    sd = full.std(ddof=1)
    return float(full.mean() / sd * math.sqrt(len(full))) if sd > 0 else 0.0


def describe_sets(setup: Setup) -> list[dict[str, Any]]:
    return [{"Chart": chart_of(f), "Indicator": f.split("@", 1)[0], "Family": family_of(f),
             "Use": "follow" if w > 0 else "fade"}
            for tf in ("4h", "1h", "30m") for f, w in setup.sets[tf].items()]
