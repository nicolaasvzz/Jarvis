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

Both predict what the chart votes predict: the chance a trade reaches its
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


def fit_trees(f_fit: np.ndarray, y_fit: np.ndarray, f_check: np.ndarray, y_check: np.ndarray,
              f_all: np.ndarray, y_all: np.ndarray, seed: int = 0) -> dict[str, Any]:
    """Decision trees: as many as predict the held-back part of training best,
    refitted on all of training. Returns the model and how many trees."""
    from sklearn.ensemble import HistGradientBoostingClassifier

    leaf = int(min(MIN_LEAF, max(20, len(y_fit) // 20)))

    def make(trees: int) -> Any:
        return HistGradientBoostingClassifier(
            learning_rate=RATE, max_iter=trees, max_depth=3, max_leaf_nodes=8,
            min_samples_leaf=leaf, l2_regularization=1.0, early_stopping=False,
            random_state=seed)

    probe = make(MAX_TREES).fit(f_fit, y_fit)
    losses = [_log_loss(y_check, p[:, 1]) for p in probe.staged_predict_proba(f_check)]
    best = int(np.argmin(losses)) + 1
    model = make(max(best, 10)).fit(f_all, y_all)
    return {"model": model, "trees": best, "check_loss": float(losses[best - 1]),
            "base_loss": _log_loss(y_check, np.full(len(y_check), y_fit.mean()))}


def leaning_on(kind: str, fitted: dict[str, Any], names: list[str], f_check: np.ndarray,
               y_check: np.ndarray, top: int = 10) -> list[dict[str, Any]]:
    """What a fitted formula leans on most, for the report and Jarvis's page.

    weights: the biggest weights (per spread of the input: comparable). trees:
    how much worse the held-back predictions get when an input is shuffled."""
    if kind == "weights":
        w = np.array(fitted["coef"][1:])
        order = np.argsort(-np.abs(w))[:top]
        return [{"Input": names[i], "Effect": "toward a win" if w[i] > 0 else "against a win",
                 "Strength": round(float(abs(w[i])), 3)} for i in order]
    from sklearn.inspection import permutation_importance

    keep = min(len(y_check), 20000)
    result = permutation_importance(fitted["model"], f_check[:keep], y_check[:keep],
                                    scoring="neg_log_loss", n_repeats=2, random_state=0)
    gain = result.importances_mean
    order = np.argsort(-gain)[:top]
    return [{"Input": names[i], "Effect": "matters" if gain[i] > 0 else "hardly matters",
             "Strength": round(float(gain[i]) * 1000, 3)} for i in order]


def input_names(inputs: list[str]) -> list[str]:
    """Names for a formula's features, in ``Setup.formula_inputs`` order."""
    return list(inputs) + ["1h chart score", "4h chart agreement", "30m chart agreement",
                           "long (not short)"]
