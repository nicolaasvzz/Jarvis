"""YAML config loading -> typed engine components."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .broker.base import ExecutionModel
from .data import DataFeed, make_feed
from .risk import RiskConfig
from .strategies import Ensemble, build_ensemble

DEFAULT_UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "TSLA", "SPY"]

DEFAULT_STRATEGY_SPEC = [
    {"name": "sma_cross", "weight": 1.0, "params": {"fast": 20, "slow": 100}},
    {"name": "macd", "weight": 1.0},
    {"name": "breakout", "weight": 1.5, "params": {"entry_window": 55, "exit_window": 20}},
    {"name": "bollinger", "weight": 0.75},
    {"name": "rsi", "weight": 0.75},
]


@dataclass
class BotConfig:
    raw: dict[str, Any] = field(default_factory=dict)

    # ---------------- loading ----------------

    @classmethod
    def load(cls, path: str | Path | None) -> "BotConfig":
        if path is None:
            return cls(raw={})
        text = Path(path).read_text()
        return cls(raw=yaml.safe_load(text) or {})

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name) or {}
        if not isinstance(value, dict):
            raise ValueError(f"Config section {name!r} must be a mapping")
        return value

    # ---------------- typed accessors ----------------

    @property
    def universe(self) -> list[str]:
        return list(self.raw.get("universe") or DEFAULT_UNIVERSE)

    def build_feed(self) -> DataFeed:
        data = self.section("data")
        source = data.get("source", "synthetic")
        kwargs = {k: v for k, v in data.items() if k != "source"}
        return make_feed(source, **kwargs)

    def build_strategy(self) -> Ensemble:
        strat = self.section("strategy")
        spec = strat.get("members") or DEFAULT_STRATEGY_SPEC
        return build_ensemble(
            spec,
            threshold=float(strat.get("threshold", 0.25)),
            max_volatility=strat.get("max_volatility"),
            long_only=bool(strat.get("long_only", False)),
        )

    def build_risk(self) -> RiskConfig:
        risk = self.section("risk")
        return RiskConfig(
            risk_per_trade=float(risk.get("risk_per_trade", 0.01)),
            atr_window=int(risk.get("atr_window", 14)),
            atr_stop_multiple=float(risk.get("atr_stop_multiple", 3.0)),
            take_profit_multiple=(
                float(risk["take_profit_multiple"])
                if risk.get("take_profit_multiple") is not None
                else None
            ),
            max_position_weight=float(risk.get("max_position_weight", 0.20)),
            max_gross_exposure=float(risk.get("max_gross_exposure", 1.0)),
            max_drawdown=float(risk.get("max_drawdown", 0.25)),
            allow_short=bool(risk.get("allow_short", True)),
        )

    def build_execution(self) -> ExecutionModel:
        ex = self.section("execution")
        return ExecutionModel(
            slippage_bps=float(ex.get("slippage_bps", 5.0)),
            commission_per_share=float(ex.get("commission_per_share", 0.0)),
            commission_pct=float(ex.get("commission_pct", 0.0005)),
            min_commission=float(ex.get("min_commission", 0.0)),
        )

    @property
    def backtest_settings(self) -> dict[str, Any]:
        bt = self.section("backtest")
        return {
            "starting_cash": float(bt.get("starting_cash", 100_000.0)),
            "days": int(bt.get("days", 1260)),
            "lookback": int(bt.get("lookback", 300)),
        }

    @property
    def live_settings(self) -> dict[str, Any]:
        live = self.section("live")
        return {
            "broker": str(live.get("broker", "paper")),
            "poll_minutes": float(live.get("poll_minutes", 15)),
            "state_file": str(live.get("state_file", "live_state.json")),
            "starting_cash": float(live.get("starting_cash", 100_000.0)),
            "lookback": int(live.get("lookback", 300)),
        }
