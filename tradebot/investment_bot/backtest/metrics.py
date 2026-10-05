"""Performance metrics computed from an equity curve and trade log."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..portfolio import Trade

TRADING_DAYS = 252


def compute_metrics(
    equity: pd.Series, trades: list[Trade], risk_free_rate: float = 0.0
) -> dict[str, float]:
    """Return a flat dict of headline performance statistics."""
    if len(equity) < 2:
        return {"total_return": 0.0, "num_trades": len(trades)}

    rets = equity.pct_change().dropna()
    total_return = equity.iloc[-1] / equity.iloc[0] - 1.0
    years = max(len(equity) / TRADING_DAYS, 1e-9)
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0

    ann_vol = rets.std(ddof=0) * np.sqrt(TRADING_DAYS)
    excess = rets - risk_free_rate / TRADING_DAYS
    sharpe = (
        float(excess.mean() / rets.std(ddof=0) * np.sqrt(TRADING_DAYS))
        if rets.std(ddof=0) > 0
        else 0.0
    )
    downside = rets[rets < 0].std(ddof=0)
    sortino = (
        float(excess.mean() / downside * np.sqrt(TRADING_DAYS)) if downside > 0 else 0.0
    )

    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    max_dd = float(drawdown.min())
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else 0.0

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))

    return {
        "total_return": float(total_return),
        "cagr": float(cagr),
        "volatility": float(ann_vol),
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "num_trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0,
        "avg_trade_pnl": float(np.mean([t.pnl for t in trades])) if trades else 0.0,
        "best_trade": max((t.pnl for t in trades), default=0.0),
        "worst_trade": min((t.pnl for t in trades), default=0.0),
        "final_equity": float(equity.iloc[-1]),
    }


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0
