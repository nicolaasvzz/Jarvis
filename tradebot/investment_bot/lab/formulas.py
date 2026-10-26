"""The learned formulas: how all of a setup's indicators work together, fitted on training.

The chart votes average each chart's set and let a four-number model weigh
the three charts. The learned formulas read every indicator the builder kept
on the three charts instead (the clearest of each look-alike group, up to 35 a
chart). Each one is turned toward the trade: an indicator that says "up"
counts + for a long and - for a short. Alongside them come the three chart
scores and whether the trade is a long:

- **weights** (learned weights): a logistic regression with its own weight
  for every indicator, pulled toward zero unless the data earns it (an L2
  penalty, its strength picked on the last part of training).
- **trees** (decision trees): gradient-boosted trees, three levels deep, so
  they can learn "this only matters when that" (combinations of up to three
  indicators). Every leaf needs at least ``MIN_LEAF`` past trades (fewer only
  when there's little data: a twentieth of it). How many
  trees to keep is picked on the last part of training (early stopping), and
  then they're refitted on all of it.
- **bagged** (averaged trees): ``MEMBERS`` sets of those trees, each fitted on
  a different random ``SHARE`` of training with its own random seed, their
  chances averaged (bagging, Breiman 1996: the idea behind random forests).
  One set of trees swings from run to run on the same data; the average of
  several swings much less. A fixed recipe, so it counts as one formula.

All of them predict what the chart votes predict: the chance a trade reaches its
target before its stop, under the same exits. The builder judges them exactly
like the votes, on the test windows against the luck bar, and every one tried
counts as a try. Nothing here ever sees the test windows or the final check.

Needs scikit-learn (in requirements.txt; ``Update Jarvis and the bot`` installs it).
"""
from __future__ import annotations

from typing import Any

import numpy as np

STRENGTHS = (0.001, 0.01, 0.1, 1.0)   # weights: inverse penalty strengths tried (sklearn's C)
MIN_LEAF = 300                        # trees: past trades a leaf must hold
MAX_TREES = 400
RATE = 0.05
MEMBERS = 5                           # bagged: sets of trees averaged
SHARE = 0.7                           # ...each on this share of training


class Averaged:
    """Several fitted classifiers whose chances are averaged."""

    def __init__(self, models: list[Any]):
        self.models = models

    def predict_proba(self, f: np.ndarray) -> np.ndarray:
        return np.mean([m.predict_proba(f) for m in self.models], axis=0)


def _log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def fit_weights(f_fit: np.ndarray, y_fit: np.ndarray, f_check: np.ndarray, y_check: np.ndarray,
                f_all: np.ndarray, y_all: np.ndarray) -> dict[str, Any]:
    """Learned weights: the penalty that predicts the held-back part of training
    best, refitted on all of training. Returns coef (bias first), centre, spread."""
    from sklearn.linear_model import LogisticRegression

    centre = f_all.mean(0)
    spread = f_all.std(0)
    spread[spread < 1e-6] = 1.0

    def scale(f: np.ndarray) -> np.ndarray:
        return (f - centre) / spread

    best_c, best_loss = STRENGTHS[0], np.inf
    for c in STRENGTHS:
        model = LogisticRegression(C=c, max_iter=500)
        model.fit(scale(f_fit), y_fit)
        loss = _log_loss(y_check, model.predict_proba(scale(f_check))[:, 1])
        if loss < best_loss:
            best_c, best_loss = c, loss
    model = LogisticRegression(C=best_c, max_iter=500)
    model.fit(scale(f_all), y_all)
    return {"coef": [float(model.intercept_[0])] + [float(v) for v in model.coef_[0]],
            "centre": [float(v) for v in centre], "spread": [float(v) for v in spread],
            "strength": best_c, "check_loss": best_loss,
            "base_loss": _log_loss(y_check, np.full(len(y_check), y_fit.mean()))}


def _trees(leaf: int, trees: int, seed: int) -> Any:
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(
        learning_rate=RATE, max_iter=trees, max_depth=3, max_leaf_nodes=8,
        min_samples_leaf=leaf, l2_regularization=1.0, early_stopping=False,
        random_state=seed)


def _probe(f_fit: np.ndarray, y_fit: np.ndarray, f_check: np.ndarray, y_check: np.ndarray,
           leaf: int, seed: int) -> tuple[int, np.ndarray]:
    """Fit up to MAX_TREES trees; how many predict the held-back part best, and
    the held-back chances with that many."""
    probe = _trees(leaf, MAX_TREES, seed).fit(f_fit, y_fit)
    staged = [p[:, 1] for p in probe.staged_predict_proba(f_check)]
    best = int(np.argmin([_log_loss(y_check, p) for p in staged])) + 1
    return best, staged[best - 1]


def fit_trees(f_fit: np.ndarray, y_fit: np.ndarray, f_check: np.ndarray, y_check: np.ndarray,
              f_all: np.ndarray, y_all: np.ndarray, seed: int = 0) -> dict[str, Any]:
    """Decision trees: as many as predict the held-back part of training best,
    refitted on all of training. Returns the model and how many trees."""
    leaf = int(min(MIN_LEAF, max(20, len(y_fit) // 20)))
    best, held = _probe(f_fit, y_fit, f_check, y_check, leaf, seed)
    model = _trees(leaf, max(best, 10), seed).fit(f_all, y_all)
    return {"model": model, "trees": best, "check_loss": _log_loss(y_check, held),
            "base_loss": _log_loss(y_check, np.full(len(y_check), y_fit.mean()))}


def fit_bagged(f_fit: np.ndarray, y_fit: np.ndarray, f_check: np.ndarray, y_check: np.ndarray,
               f_all: np.ndarray, y_all: np.ndarray, seed: int = 0,
               members: int = MEMBERS) -> dict[str, Any]:
    """Averaged trees: ``members`` sets of trees, each on its own random share of
    training with its own seed, averaged. Returns the model, each member's tree
    count, and the held-back loss of the average."""
    rng = np.random.default_rng(seed)
    leaf = int(min(MIN_LEAF, max(20, int(len(y_fit) * SHARE) // 20)))
    models, counts, held = [], [], []
    for _ in range(members):
        member_seed = int(rng.integers(1 << 30))
        fit_rows = rng.choice(len(y_fit), int(len(y_fit) * SHARE), replace=False)
        best, chances = _probe(f_fit[fit_rows], y_fit[fit_rows], f_check, y_check, leaf,
                               member_seed)
        all_rows = rng.choice(len(y_all), int(len(y_all) * SHARE), replace=False)
        models.append(_trees(leaf, max(best, 10), member_seed).fit(f_all[all_rows],
                                                                    y_all[all_rows]))
        counts.append(best)
        held.append(chances)
    return {"model": Averaged(models), "trees": counts,
            "check_loss": _log_loss(y_check, np.mean(held, axis=0)),
            "base_loss": _log_loss(y_check, np.full(len(y_check), y_fit.mean()))}


def leaning_on(kind: str, fitted: dict[str, Any], names: list[str], f_check: np.ndarray,
               y_check: np.ndarray, top: int = 10) -> list[dict[str, Any]]:
    """What a fitted formula leans on most, for the report and Jarvis's page.

    weights: the biggest weights (per spread of the input: comparable). trees
    and averaged trees: how much worse the held-back predictions get when an
    input is shuffled (permutation importance)."""
    if kind == "weights":
        w = np.array(fitted["coef"][1:])
        order = np.argsort(-np.abs(w))[:top]
        return [{"Input": names[i], "Effect": "toward a win" if w[i] > 0 else "against a win",
                 "Strength": round(float(abs(w[i])), 3)} for i in order]
    model = fitted["model"]
    keep = min(len(y_check), 20000 if kind == "trees" else 8000)
    f, y = np.array(f_check[:keep], dtype=np.float32), y_check[:keep]
    base = _log_loss(y, model.predict_proba(f)[:, 1])
    rng = np.random.default_rng(0)
    gain = np.zeros(f.shape[1])
    for j in range(f.shape[1]):
        column = f[:, j].copy()
        worse = []
        for _ in range(2):
            f[:, j] = rng.permutation(column)
            worse.append(_log_loss(y, model.predict_proba(f)[:, 1]) - base)
        f[:, j] = column
        gain[j] = float(np.mean(worse))
    order = np.argsort(-gain)[:top]
    return [{"Input": names[i], "Effect": "matters" if gain[i] > 0 else "hardly matters",
             "Strength": round(float(gain[i]) * 1000, 3)} for i in order]


def input_names(inputs: list[str]) -> list[str]:
    """Names for a formula's features, in ``Setup.formula_inputs`` order."""
    return list(inputs) + ["1h chart score", "4h chart agreement", "30m chart agreement",
                           "long (not short)"]
