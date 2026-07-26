"""Command-line interface.

    investment-bot backtest  [-c config.yaml] [--html report.html]
    investment-bot optimize  --strategy sma_cross --grid "fast=10,20 slow=50,100"
    investment-bot trade     [-c config.yaml] [--once]
    investment-bot strategies
"""
from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.table import Table

from .backtest.engine import BacktestEngine
from .backtest.optimizer import Optimizer
from .config import BotConfig
from .report import print_terminal_report, write_html_report
from .strategies import REGISTRY, build_strategy

console = Console()

DISCLAIMER = (
    "[dim]Not financial advice. Backtests overstate live results. "
    "Trade real money only after paper trading, and only money you can afford to lose.[/dim]"
)


def _load_data(config: BotConfig, days: int, symbols: list[str] | None = None):
    feed = config.build_feed()
    universe = symbols or config.universe
    data = {}
    for symbol in universe:
        try:
            data[symbol] = feed.history(symbol, days)
        except Exception as exc:
            console.print(f"[yellow]skipping {symbol}: {exc}[/yellow]")
    if not data:
        console.print("[red]No data loaded for any symbol.[/red]")
        sys.exit(1)
    return data


def cmd_backtest(args: argparse.Namespace) -> None:
    config = BotConfig.load(args.config)
    settings = config.backtest_settings
    days = args.days or settings["days"]
    symbols = args.symbols.split(",") if args.symbols else None

    console.print(f"[bold]Loading data[/bold] ({days} days)...")
    data = _load_data(config, days, symbols)

    engine = BacktestEngine(
        strategy=config.build_strategy(),
        risk=config.build_risk(),
        execution=config.build_execution(),
        starting_cash=settings["starting_cash"],
        lookback=settings["lookback"],
    )
    console.print(
        f"[bold]Backtesting[/bold] {len(data)} symbols: {', '.join(sorted(data))}"
    )
    result = engine.run(data)
    print_terminal_report(result, console)
    if args.html:
        path = write_html_report(result, args.html)
        console.print(f"HTML report written to [bold]{path}[/bold]")
    console.print(DISCLAIMER)


def _parse_grid(spec: str) -> dict[str, list]:
    grid: dict[str, list] = {}
    for chunk in spec.replace(";", " ").split():
        if "=" not in chunk:
            raise SystemExit(f"Bad grid chunk {chunk!r}; expected name=v1,v2,...")
        key, values = chunk.split("=", 1)
        parsed = []
        for v in values.split(","):
            try:
                parsed.append(int(v))
            except ValueError:
                try:
                    parsed.append(float(v))
                except ValueError:
                    parsed.append(v)
        grid[key] = parsed
    return grid


def cmd_optimize(args: argparse.Namespace) -> None:
    config = BotConfig.load(args.config)
    settings = config.backtest_settings
    days = args.days or settings["days"]
    if args.strategy not in REGISTRY:
        raise SystemExit(f"Unknown strategy {args.strategy!r}. Available: {sorted(REGISTRY)}")
    grid = _parse_grid(args.grid)
    data = _load_data(config, days, args.symbols.split(",") if args.symbols else None)

    optimizer = Optimizer(
        strategy_factory=lambda params: build_strategy(args.strategy, params),
        param_grid=grid,
        metric=args.metric,
        risk=config.build_risk(),
        execution=config.build_execution(),
        starting_cash=settings["starting_cash"],
    )

    if args.walk_forward:
        console.print(
            f"[bold]Walk-forward[/bold]: {args.strategy}, train {args.train_bars} / test {args.test_bars} bars"
        )
        with console.status("running folds..."):
            wf = optimizer.walk_forward(data, args.train_bars, args.test_bars)
        if wf.empty:
            console.print("[red]Not enough history for a single train/test fold.[/red]")
            return
        table = Table(title="Walk-forward folds (out-of-sample)")
        for col in wf.columns:
            table.add_column(str(col), justify="right")
        for _, row in wf.iterrows():
            table.add_row(*[
                f"{v:.3f}" if isinstance(v, float) else (f"{v:%Y-%m-%d}" if hasattr(v, "strftime") else str(v))
                for v in row
            ])
        console.print(table)
        oos = wf[f"test_{args.metric}"].dropna()
        console.print(
            f"Mean out-of-sample {args.metric}: [bold]{oos.mean():.3f}[/bold] over {len(oos)} folds"
        )
    else:
        combos = optimizer.combos()
        console.print(f"[bold]Grid search[/bold]: {args.strategy}, {len(combos)} combos, ranked by {args.metric}")
        with console.status("running grid...") as status:
            results = optimizer.grid_search(
                data, progress=lambda done, total: status.update(f"combo {done}/{total}")
            )
        table = Table(title=f"Top {min(len(results), args.top)} parameter sets")
        table.add_column("Params")
        for col in (args.metric, "total_return", "max_drawdown", "num_trades"):
            table.add_column(col, justify="right")
        for r in results[: args.top]:
            table.add_row(
                str(r.params),
                f"{r.metrics.get(args.metric, float('nan')):.3f}",
                f"{r.metrics.get('total_return', 0):.2%}",
                f"{r.metrics.get('max_drawdown', 0):.2%}",
                f"{r.metrics.get('num_trades', 0):.0f}",
            )
        console.print(table)
        console.print("[dim]Careful: the top row is also the most curve-fit. Validate with --walk-forward.[/dim]")
    console.print(DISCLAIMER)


def cmd_trade(args: argparse.Namespace) -> None:
    from .broker.paper import PaperBroker
    from .live import LiveTrader

    config = BotConfig.load(args.config)
    settings = config.live_settings
    broker_name = args.broker or settings["broker"]

    if broker_name == "alpaca":
        from .broker.alpaca import AlpacaBroker

        broker = AlpacaBroker(execution=config.build_execution())
        console.print("[bold yellow]Using the Alpaca broker.[/bold yellow]")
    else:
        broker = PaperBroker(execution=config.build_execution())
        console.print("[bold]Paper trading[/bold] (simulated fills, state on disk).")

    trader = LiveTrader(
        config=config,
        feed=config.build_feed(),
        strategy=config.build_strategy(),
        broker=broker,
        console=console,
    )
    console.print(DISCLAIMER)
    if args.once:
        trader.run_cycle()
    else:
        trader.run_forever(settings["poll_minutes"])


def cmd_strategies(_args: argparse.Namespace) -> None:
    table = Table(title="Available strategies")
    table.add_column("Name", style="bold")
    table.add_column("Class")
    table.add_column("Default params")
    for name, cls in sorted(REGISTRY.items()):
        instance = cls()
        params = {
            k: v for k, v in vars(instance).items() if k != "name"
        }
        table.add_row(name, cls.__name__, str(params))
    console.print(table)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="investment-bot",
        description="Multi-strategy automated trading bot: backtest, optimize, trade.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", default=None, help="Path to YAML config")
    common.add_argument("--days", type=int, default=None, help="History length in trading days")
    common.add_argument("--symbols", default=None, help="Comma-separated symbol override")

    bt = sub.add_parser("backtest", parents=[common], help="Run a backtest")
    bt.add_argument("--html", default=None, help="Write an HTML report to this path")
    bt.set_defaults(func=cmd_backtest)

    opt = sub.add_parser("optimize", parents=[common], help="Grid search / walk-forward")
    opt.add_argument("--strategy", required=True, help="Strategy name (see `strategies`)")
    opt.add_argument("--grid", required=True, help='e.g. "fast=10,20,50 slow=100,200"')
    opt.add_argument("--metric", default="sharpe", help="Ranking metric (default sharpe)")
    opt.add_argument("--top", type=int, default=10, help="Rows to display")
    opt.add_argument("--walk-forward", action="store_true", help="Rolling out-of-sample validation")
    opt.add_argument("--train-bars", type=int, default=504)
    opt.add_argument("--test-bars", type=int, default=126)
    opt.set_defaults(func=cmd_optimize)

    trade = sub.add_parser("trade", parents=[common], help="Run the live/paper trading loop")
    trade.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    trade.add_argument("--broker", choices=["paper", "alpaca"], default=None)
    trade.set_defaults(func=cmd_trade)

    ls = sub.add_parser("strategies", help="List available strategies")
    ls.set_defaults(func=cmd_strategies)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
