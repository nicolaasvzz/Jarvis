"""Backtest reporting: rich terminal output and a self-contained HTML report.

The HTML report embeds hand-rolled SVG equity/drawdown charts — no plotting
library, no external assets, opens anywhere.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

from .backtest.engine import BacktestResult
from .backtest.metrics import drawdown_series

PCT_METRICS = {"total_return", "cagr", "volatility", "max_drawdown", "win_rate"}

METRIC_LABELS = {
    "total_return": "Total return",
    "cagr": "CAGR",
    "volatility": "Volatility (ann.)",
    "sharpe": "Sharpe",
    "sortino": "Sortino",
    "max_drawdown": "Max drawdown",
    "calmar": "Calmar",
    "num_trades": "Trades",
    "win_rate": "Win rate",
    "profit_factor": "Profit factor",
    "avg_trade_pnl": "Avg trade P&L",
    "best_trade": "Best trade",
    "worst_trade": "Worst trade",
    "final_equity": "Final equity",
}


def _fmt(key: str, value: float) -> str:
    if key in PCT_METRICS:
        return f"{value:.2%}"
    if key == "num_trades":
        return f"{value:,.0f}"
    if key in ("final_equity", "avg_trade_pnl", "best_trade", "worst_trade"):
        return f"${value:,.2f}"
    return f"{value:.2f}"


def print_terminal_report(result: BacktestResult, console: Console | None = None) -> None:
    console = console or Console()
    table = Table(title="Backtest results", show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    for key, label in METRIC_LABELS.items():
        if key in result.metrics:
            value = result.metrics[key]
            style = ""
            if key in ("total_return", "cagr", "sharpe"):
                style = "green" if value > 0 else "red"
            table.add_row(label, f"[{style}]{_fmt(key, value)}[/{style}]" if style else _fmt(key, value))
    console.print(table)
    if result.halted:
        console.print("[bold red]NOTE: the max-drawdown circuit breaker halted this run.[/bold red]")

    if result.trades:
        recent = Table(title=f"Last {min(len(result.trades), 10)} closed trades")
        for col in ("Exit date", "Symbol", "Side", "Qty", "Entry", "Exit", "P&L", "Reason"):
            recent.add_column(col, justify="right")
        for t in result.trades[-10:]:
            color = "green" if t.pnl >= 0 else "red"
            recent.add_row(
                f"{t.exit_time:%Y-%m-%d}",
                t.symbol,
                "LONG" if t.direction > 0 else "SHORT",
                f"{t.qty:.0f}",
                f"{t.entry_price:.2f}",
                f"{t.exit_price:.2f}",
                f"[{color}]{t.pnl:+,.2f}[/{color}]",
                t.reason,
            )
        console.print(recent)


# ---------------- HTML ----------------


def _svg_line(series: pd.Series, width: int, height: int, color: str, fill: str | None = None) -> str:
    values = series.to_numpy(dtype=float)
    lo, hi = float(values.min()), float(values.max())
    span = (hi - lo) or 1.0
    n = len(values)
    points = [
        (round(i / max(n - 1, 1) * width, 2), round(height - (v - lo) / span * (height - 8) - 4, 2))
        for i, v in enumerate(values)
    ]
    path = "M" + " L".join(f"{x},{y}" for x, y in points)
    fill_el = ""
    if fill:
        area = path + f" L{width},{height} L0,{height} Z"
        fill_el = f'<path d="{area}" fill="{fill}" stroke="none"/>'
    return (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'style="width:100%;height:{height}px;display:block">'
        f'{fill_el}<path d="{path}" fill="none" stroke="{color}" stroke-width="1.5"/></svg>'
    )


def write_html_report(result: BacktestResult, path: str | Path) -> Path:
    path = Path(path)
    equity = result.equity
    dd = drawdown_series(equity)

    metric_rows = "".join(
        f"<tr><td>{METRIC_LABELS.get(k, k)}</td><td class='num'>{_fmt(k, v)}</td></tr>"
        for k, v in result.metrics.items()
        if k in METRIC_LABELS
    )
    trade_rows = "".join(
        f"<tr><td>{t.exit_time:%Y-%m-%d}</td><td>{t.symbol}</td>"
        f"<td>{'LONG' if t.direction > 0 else 'SHORT'}</td><td class='num'>{t.qty:.0f}</td>"
        f"<td class='num'>{t.entry_price:.2f}</td><td class='num'>{t.exit_price:.2f}</td>"
        f"<td class='num {'pos' if t.pnl >= 0 else 'neg'}'>{t.pnl:+,.2f}</td><td>{t.reason}</td></tr>"
        for t in result.trades
    )
    period = (
        f"{equity.index[0]:%Y-%m-%d} → {equity.index[-1]:%Y-%m-%d}" if len(equity) else "n/a"
    )
    halted_banner = (
        "<p class='halted'>⚠ Max-drawdown circuit breaker halted this run.</p>"
        if result.halted
        else ""
    )

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Investment Bot — Backtest Report</title>
<style>
 body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 2rem auto; max-width: 960px; color: #1a1a2e; padding: 0 1rem; }}
 h1 {{ font-size: 1.5rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
 .sub {{ color: #666; }}
 table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
 td, th {{ padding: 0.35rem 0.6rem; border-bottom: 1px solid #eee; text-align: left; }}
 .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
 .pos {{ color: #0a7a3d; }} .neg {{ color: #c0392b; }}
 .halted {{ color: #c0392b; font-weight: 600; }}
 .chart {{ border: 1px solid #eee; border-radius: 8px; padding: 0.5rem; margin: 0.5rem 0 1.5rem; }}
</style></head><body>
<h1>Investment Bot — Backtest Report</h1>
<p class="sub">Period: {period} &nbsp;|&nbsp; {len(result.trades)} closed trades</p>
{halted_banner}
<h2>Equity curve</h2>
<div class="chart">{_svg_line(equity, 900, 220, "#2563eb", "#2563eb18") if len(equity) else "no data"}</div>
<h2>Drawdown</h2>
<div class="chart">{_svg_line(dd, 900, 120, "#c0392b", "#c0392b18") if len(dd) else "no data"}</div>
<h2>Metrics</h2>
<table>{metric_rows}</table>
<h2>Closed trades</h2>
<table><tr><th>Exit date</th><th>Symbol</th><th>Side</th><th class="num">Qty</th>
<th class="num">Entry</th><th class="num">Exit</th><th class="num">P&amp;L</th><th>Reason</th></tr>
{trade_rows}</table>
</body></html>"""
    path.write_text(html)
    return path
