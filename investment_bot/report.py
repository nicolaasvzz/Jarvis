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

    total_return = result.metrics.get("total_return", 0.0)
    headline_color = "var(--green)" if total_return >= 0 else "var(--red)"

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Investment Bot — Backtest Report</title>
<style>
 :root {{ --bg:#050505; --green:#4ade80; --red:#f87171; --purple:#c084fc; }}
 * {{ box-sizing:border-box; margin:0; padding:0; }}
 body {{ background:var(--bg); color:#fff; padding:1.5rem 1rem 4rem;
   font-family:'Montserrat',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; font-size:15px; }}
 .wrap {{ max-width:1100px; margin:0 auto; }}
 h1 {{ font-size:1.5rem; font-weight:700; letter-spacing:-.02em; display:flex; align-items:center; gap:.5rem; }}
 .sub {{ color:rgba(255,255,255,.6); font-size:.875rem; margin-top:.35rem; }}
 h2 {{ font-size:.7rem; font-weight:700; text-transform:uppercase; letter-spacing:.12em;
   color:rgba(255,255,255,.6); margin:2rem 0 .75rem; }}
 .card {{ background:rgba(0,0,0,.7); border:1px solid rgba(255,255,255,.2); border-radius:1.5rem;
   padding:1.25rem; box-shadow:0 10px 30px rgba(0,0,0,.5); }}
 .grid {{ display:grid; gap:1rem; grid-template-columns:repeat(2,1fr); }}
 @media(min-width:900px) {{ .grid {{ grid-template-columns:repeat(4,1fr); }} }}
 .label {{ font-size:.7rem; font-weight:700; text-transform:uppercase; letter-spacing:.12em;
   color:rgba(255,255,255,.6); margin-bottom:.5rem; }}
 .stat {{ font-size:1.5rem; font-weight:700; font-variant-numeric:tabular-nums; }}
 table {{ border-collapse:collapse; width:100%; font-size:.85rem; }}
 th {{ font-size:.65rem; font-weight:700; text-transform:uppercase; letter-spacing:.1em;
   color:rgba(255,255,255,.5); text-align:left; padding:.5rem .7rem; }}
 td {{ padding:.5rem .7rem; border-top:1px solid rgba(255,255,255,.07); }}
 tbody tr:hover {{ background:rgba(255,255,255,.04); }}
 .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
 .pos {{ color:var(--green); }} .neg {{ color:var(--red); }}
 .halted {{ background:rgba(69,10,10,.4); border:1px solid rgba(239,68,68,.4); border-radius:1rem;
   padding:.8rem 1rem; color:#fca5a5; font-weight:600; margin:1rem 0; }}
 .scroll {{ overflow-x:auto; }}
 .tall {{ max-height:520px; overflow-y:auto; }}
 ::-webkit-scrollbar {{ width:6px; height:6px; }}
 ::-webkit-scrollbar-track {{ background:#080808; }}
 ::-webkit-scrollbar-thumb {{ background:#333; border-radius:3px; }}
</style></head><body><div class="wrap">
<h1>
<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>
Backtest Report</h1>
<p class="sub">{period} &nbsp;·&nbsp; {len(result.trades)} closed trades</p>
{halted_banner}

<div class="grid" style="margin-top:1.5rem">
  <div class="card"><div class="label">Total Return</div>
    <p class="stat" style="color:{headline_color}">{total_return:.2%}</p></div>
  <div class="card"><div class="label">Sharpe</div>
    <p class="stat">{result.metrics.get('sharpe', 0):.2f}</p></div>
  <div class="card"><div class="label">Max Drawdown</div>
    <p class="stat neg">{result.metrics.get('max_drawdown', 0):.2%}</p></div>
  <div class="card"><div class="label">Win Rate</div>
    <p class="stat" style="color:var(--purple)">{result.metrics.get('win_rate', 0):.2%}</p></div>
</div>

<h2>Equity Curve</h2>
<div class="card">{_svg_line(equity, 900, 220, "#4ade80" if total_return >= 0 else "#f87171", "#4ade8022" if total_return >= 0 else "#f8717122") if len(equity) else "no data"}</div>
<h2>Drawdown</h2>
<div class="card">{_svg_line(dd, 900, 120, "#f87171", "#f8717122") if len(dd) else "no data"}</div>

<h2>All Metrics</h2>
<div class="card scroll"><table>{metric_rows}</table></div>

<h2>Closed Trades</h2>
<div class="card scroll tall"><table>
<thead><tr><th>Exit date</th><th>Symbol</th><th>Side</th><th class="num">Qty</th>
<th class="num">Entry</th><th class="num">Exit</th><th class="num">P&amp;L</th><th>Reason</th></tr></thead>
<tbody>{trade_rows}</tbody></table></div>
</div></body></html>"""
    path.write_text(html)
    return path
