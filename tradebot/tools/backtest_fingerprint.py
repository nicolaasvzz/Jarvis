"""Fingerprint backtest behaviour so refactors and speed-ups can be proven safe.

Runs deterministic synthetic-market backtests and prints one hash per run,
covering every trade, the whole equity curve, the signal log and (when
learning is on) the learned ensemble weights. Run it on the old code and the
new code; if any line differs, behaviour changed.

    python tools/backtest_fingerprint.py > before.txt   # on main
    python tools/backtest_fingerprint.py > after.txt    # on your branch
    diff before.txt after.txt                           # must be empty

To fingerprint another checkout without switching branches:

    PYTHONPATH=/path/to/other/checkout python tools/backtest_fingerprint.py
"""
from __future__ import annotations

import dataclasses
import hashlib
import sys
from pathlib import Path

import investment_bot
from investment_bot.backtest.engine import BacktestEngine
from investment_bot.config import BotConfig
from investment_bot.data.feed import SyntheticFeed

UNIVERSE = ["AAPL", "MSFT", "NVDA", "TSLA", "SPY"]
DAYS = 900
SEEDS = (42, 7, 123)
ROOT = Path(__file__).resolve().parent.parent


def run(seed: int, variant: str, config: BotConfig) -> tuple[int, str]:
    feed = SyntheticFeed(seed=seed)
    data = {symbol: feed.history(symbol, DAYS) for symbol in UNIVERSE}
    strategy = config.build_strategy()
    risk = config.build_risk()
    learner = config.build_learning(strategy)
    if variant == "long-only-take-profit":
        strategy.long_only = True
        risk = dataclasses.replace(risk, take_profit_multiple=4.0)

    result = BacktestEngine(
        strategy=strategy,
        risk=risk,
        execution=config.build_execution(),
        learner=learner,
    ).run(data)

    digest = hashlib.sha256()
    for t in result.trades:
        digest.update(
            repr(
                (t.symbol, t.direction, t.qty, t.entry_price, t.exit_price,
                 str(t.entry_time), str(t.exit_time), t.pnl, t.reason)
            ).encode()
        )
    digest.update(repr(result.equity.tolist()).encode())
    digest.update(result.signals_log.to_csv().encode())
    digest.update(repr(result.halted).encode())
    if learner is not None:
        digest.update(repr(sorted(learner.weights.items())).encode())
    return len(result.trades), digest.hexdigest()[:16]


def main() -> None:
    print(f"# code under test: {Path(investment_bot.__file__).parent}", file=sys.stderr)
    configs = {
        "defaults": (BotConfig.load(None), ("default", "long-only-take-profit")),
        # config.local.yaml has learning enabled, which exercises that path.
        "learning": (BotConfig.load(str(ROOT / "config.local.yaml")), ("default",)),
    }
    for label, (config, variants) in configs.items():
        for seed in SEEDS:
            for variant in variants:
                trades, sha = run(seed, variant, config)
                print(f"{label:<9} seed={seed:<4} {variant:<22} trades={trades:<4} sha={sha}")


if __name__ == "__main__":
    main()
