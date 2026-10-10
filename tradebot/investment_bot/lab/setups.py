"""A setup: three charts' indicator sets, and the rules for one trade on the 1-hour chart.

Each chart has its own set of indicators, every one followed (+1) or faded
(-1), and the chart's score is their average vote, from -1 to +1. When a
1-hour candle closes, a trade is taken only if:

1. the **1h** score leans one way by at least ``entry``: that's the direction;
2. the **4h** score leans the same way by at least ``agree_4h``, and the
   **30m** score by at least ``agree_30m``;
3. the **confidence model** gives it at least ``confidence`` chance of
   reaching the take-profit before the stop. It's a logistic model, fitted on
   training history: how often trades like this one, with this much agreement
   on each chart, won. Its weights show how much each chart really adds, so
   three charts agreeing isn't simply counted as three votes.

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
from dataclasses import asdict, dataclass, field, fields
from statistics import NormalDist
from typing import Any

import numpy as np

from .catalog import family_of
from .charts import CHARTS, chart_of

EXITS = ("stop", "take profit", "time limit", "day's close", "end of data")
STOP, TAKE, TIME, CLOSE, END = range(5)
FULL_SIZE_ABOVE = 0.15     # full size once the chance is this far above the bar


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

    def __post_init__(self) -> None:
        self.sets = {tf: {f: float(w) for f, w in (self.sets.get(tf) or {}).items() if w}
                     for tf in CHARTS}
        missing = [tf for tf in CHARTS if not self.sets[tf]]
        if missing:
            raise ValueError(f"a setup needs indicators on every chart (none on {missing})")
        self.model = [float(v) for v in self.model]
        self.hours = int(self.hours)

    @property
    def features(self) -> list[str]:
        return [f for tf in CHARTS for f in self.sets[tf]]

    def names(self) -> dict[str, list[str]]:
        """Indicator names per chart, for ``charts.decision_features``."""
        return {tf: [f.split("@", 1)[0] for f in self.sets[tf]] for tf in CHARTS}

    def with_(self, **changes: Any) -> Setup:
        data = asdict(self)
        data.update(changes)
        return Setup(**data)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["model"] = [round(v, 4) for v in self.model]
        for key in ("entry", "agree_4h", "agree_30m", "confidence", "sl_atr", "tp_atr", "size"):
            out[key] = round(float(out[key]), 4)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Setup:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    def describe(self) -> str:
        counts = ", ".join(f"{len(self.sets[tf])} on {tf}" for tf in ("4h", "1h", "30m"))
        return (f"{counts}; trade when 1h >= {self.entry:.2f}, 4h and 30m agree, "
                f"{self.confidence:.0%} sure; stop {self.sl_atr:.2g} ATR, target "
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
        b, w1, w4, w30 = self.model
        z = b + w1 * a1 + w4 * a4 + w30 * a30
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))

    def decide(self, scores: dict[str, np.ndarray], can_short: bool = True
               ) -> tuple[np.ndarray, np.ndarray]:
        """Direction (+1, -1 or 0) and chance of winning, per row."""
        s1, s4, s30 = scores["1h"], scores["4h"], scores["30m"]
        d = np.where(s1 >= self.entry, 1, np.where(s1 <= -self.entry, -1, 0)).astype(np.int8)
        if not (self.shorts and can_short):
            d[d < 0] = 0
        a1, a4, a30 = np.abs(s1), d * s4, d * s30
        p = self.p_win(a1, a4, a30)
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
