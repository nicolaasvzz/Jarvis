"""Parameter optimization: grid search and walk-forward analysis.

Grid search brute-forces every combination of the supplied parameter grid
and ranks by a chosen metric. Walk-forward splits history into rolling
train/test windows, picks the best params on each train slice, and evaluates
them out-of-sample on the following test slice — the honest way to check a
strategy isn't just curve-fit.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from .engine import BacktestEngine
from ..risk import RiskConfig
from ..broker.base import ExecutionModel

StrategyFactory = Callable[[dict[str, Any]], Any]  # params -> Strategy


@dataclass
class GridResult:
    params: dict[str, Any]
    metrics: dict[str, float]


@dataclass
class Optimizer:
    strategy_factory: StrategyFactory
    param_grid: dict[str, list[Any]]
    metric: str = "sharpe"
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionModel = field(default_factory=ExecutionModel)
    starting_cash: float = 100_000.0

    def combos(self) -> list[dict[str, Any]]:
        keys = list(self.param_grid)
        return [
            dict(zip(keys, values))
            for values in itertools.product(*(self.param_grid[k] for k in keys))
        ]

    def _run_one(self, params: dict[str, Any], data: dict[str, pd.DataFrame]) -> GridResult | None:
        try:
            strategy = self.strategy_factory(params)
        except (ValueError, TypeError):
            return None  # invalid combo (e.g. fast >= slow)
        engine = BacktestEngine(
            strategy=strategy,
            risk=self.risk,
            execution=self.execution,
            starting_cash=self.starting_cash,
        )
        result = engine.run(data)
        return GridResult(params=params, metrics=result.metrics)

    def grid_search(
        self,
        data: dict[str, pd.DataFrame],
        progress: Callable[[int, int], None] | None = None,
    ) -> list[GridResult]:
        """Run every combo; returns results sorted best-first by the metric."""
        combos = self.combos()
        results: list[GridResult] = []
        for i, params in enumerate(combos):
            r = self._run_one(params, data)
            if r is not None:
                results.append(r)
            if progress:
                progress(i + 1, len(combos))
        results.sort(key=lambda r: r.metrics.get(self.metric, float("-inf")), reverse=True)
        return results

    def walk_forward(
        self,
        data: dict[str, pd.DataFrame],
        train_bars: int = 504,
        test_bars: int = 126,
        progress: Callable[[int, int], None] | None = None,
    ) -> pd.DataFrame:
        """Rolling train/test evaluation.

        Returns one row per fold with the chosen params and both in-sample
        and out-of-sample scores.
        """
        calendar = sorted(set().union(*(df.index for df in data.values())))
        folds = []
        start = 0
        while start + train_bars + test_bars <= len(calendar):
            folds.append(
                (
                    calendar[start],
                    calendar[start + train_bars - 1],
                    calendar[start + train_bars + test_bars - 1],
                )
            )
            start += test_bars

        rows = []
        for fold_num, (t0, t1, t2) in enumerate(folds, 1):
            train = {s: df.loc[t0:t1] for s, df in data.items()}
            test = {s: df.loc[t1:t2] for s, df in data.items()}
            train = {s: df for s, df in train.items() if len(df) > 0}
            test = {s: df for s, df in test.items() if len(df) > 0}

            ranked = self.grid_search(train)
            if not ranked:
                continue
            best = ranked[0]
            oos = self._run_one(best.params, test)
            rows.append(
                {
                    "fold": fold_num,
                    "train_start": t0,
                    "train_end": t1,
                    "test_end": t2,
                    "params": str(best.params),
                    f"train_{self.metric}": best.metrics.get(self.metric),
                    f"test_{self.metric}": oos.metrics.get(self.metric) if oos else None,
                    "test_return": oos.metrics.get("total_return") if oos else None,
                    "test_max_dd": oos.metrics.get("max_drawdown") if oos else None,
                }
            )
            if progress:
                progress(fold_num, len(folds))
        return pd.DataFrame(rows)
