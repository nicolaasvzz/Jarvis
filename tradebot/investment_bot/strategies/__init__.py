"""Strategy registry and config-driven construction."""
from __future__ import annotations

from typing import Any

from .base import FLAT, Signal, Strategy
from .ensemble import Ensemble
from .reversion import BollingerReversion, RsiReversion
from .trend import DonchianBreakout, MacdMomentum, SmaCross

REGISTRY: dict[str, type[Strategy]] = {
    "sma_cross": SmaCross,
    "macd": MacdMomentum,
    "breakout": DonchianBreakout,
    "bollinger": BollingerReversion,
    "rsi": RsiReversion,
}

__all__ = [
    "Strategy",
    "Signal",
    "FLAT",
    "Ensemble",
    "SmaCross",
    "MacdMomentum",
    "DonchianBreakout",
    "BollingerReversion",
    "RsiReversion",
    "REGISTRY",
    "build_ensemble",
]


def build_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    if name not in REGISTRY:
        raise ValueError(f"Unknown strategy {name!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name](**(params or {}))


def build_ensemble(spec: list[dict[str, Any]], **ensemble_kwargs) -> Ensemble:
    """Build an Ensemble from config entries like
    {name: sma_cross, weight: 1.0, params: {fast: 10, slow: 50}}.
    """
    members, weights = [], []
    for entry in spec:
        members.append(build_strategy(entry["name"], entry.get("params")))
        weights.append(float(entry.get("weight", 1.0)))
    return Ensemble(members=members, weights=weights, **ensemble_kwargs)
