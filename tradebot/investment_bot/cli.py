"""Command-line interface.

    investment-bot backtest  [-c config.yaml] [--html report.html]
    investment-bot optimize  --strategy sma_cross --grid "fast=10,20 slow=50,100"
    investment-bot trade     [-c config.yaml] [--once]
    investment-bot learn     --for 8h [--round 30m] [--goal "learn shorts"]
    investment-bot learned   [-c config.yaml] [--reset]
    investment-bot lab       --for 8h [--round 1h] [--until-done] [--prepare-only] [--fresh]
    investment-bot package   [-c config.yaml]
    investment-bot trade-package [--once] [--dry-run]
    investment-bot champ     --for 3d [--only stock|crypto] [--fresh] [--prepare-only]
    investment-bot trade-setup [--once] [--dry-run] [--only stock|crypto]
    investment-bot research  [--once] [--trade [--dry-run]]
    investment-bot check     [--quick]
    investment-bot strategies

Any command also takes ``--research watch|trade``: news research runs
alongside it in the background (see ``investment_bot.research``).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import reports
from .backtest.engine import BacktestEngine
from .backtest.optimizer import Optimizer
from .config import BotConfig
from .jarvis_status import write_status
from .memory import BacktestMemory, TuneConfig
from .report import print_terminal_report, write_html_report
from .strategies import REGISTRY, build_strategy
from .style import Style, money_text, pick

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
            console.print(f"[yellow]skipping {symbol}: {_brief(exc)}[/yellow]")
    if not data:
        console.print("[red]No data loaded for any symbol.[/red]")
        if config.section("data").get("source", "synthetic").lower() == "yahoo":
            console.print(
                "[dim]Yahoo Finance was unreachable. Check your connection, or set "
                "data.source to 'synthetic' in your config to run offline.[/dim]"
            )
        sys.exit(1)
    return data


def _brief(exc: Exception, limit: int = 140) -> str:
    """One-line error text — network stack traces are unreadable in bulk."""
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _load_with_memory(path: str | None) -> tuple[BotConfig, BacktestMemory | None]:
    """The config, with whatever past backtests learned laid over it."""
    config = BotConfig.load(path)
    tune = TuneConfig.from_config(config)
    if not tune.enabled:
        return config, None
    memory = BacktestMemory.load(tune.memory_file)
    if memory.overrides:
        console.print(
            f"[dim]Using what {len(memory.rounds)} past backtest(s) learned "
            f"({tune.memory_file}).[/dim]"
        )
    return memory.apply(config), memory


def _style(args: argparse.Namespace) -> Style:
    style = pick(args.style, args.custom, args.confidence, args.bet)
    console.print(f"Strategy: {style.describe()} ({'; '.join(style.reading)})"
                  + f". Money: {money_text(args.money)}.", markup=False, highlight=False)
    return style


def _with_money(config: BotConfig, money: float) -> BotConfig:
    """Backtests start with this much cash, if a sum was picked."""
    from .memory import apply_overrides

    return apply_overrides(config, {"backtest.starting_cash": money}) if money > 0 else config


def cmd_backtest(args: argparse.Namespace) -> None:
    style = _style(args)
    config, memory = _load_with_memory(args.config)
    if style.pinned:  # the picked strategy becomes the current one
        from .memory import apply_overrides

        if memory is not None:
            memory.overrides.update(style.config_overrides())
            memory.save()
        config = apply_overrides(config, style.config_overrides())
    config = _with_money(config, args.money)
    settings = config.backtest_settings
    days = args.days or settings["days"]
    symbols = args.symbols.split(",") if args.symbols else None

    console.print(f"[bold]Loading data[/bold] ({days} days)...")
    data = _load_data(config, days, symbols)

    strategy = config.build_strategy()
    learner = config.build_learning(strategy)
    engine = BacktestEngine(
        strategy=strategy,
        risk=config.build_risk(),
        execution=config.build_execution(),
        starting_cash=settings["starting_cash"],
        lookback=settings["lookback"],
        learner=learner,
    )
    console.print(
        f"[bold]Backtesting[/bold] {len(data)} symbols: {', '.join(sorted(data))}"
    )
    result = engine.run(data)
    print_terminal_report(result, console)
    if learner is not None:
        weights = ", ".join(f"{n} {w:.0%}" for n, w in learner.weights.items())
        console.print(
            f"[bold]Learned weights[/bold] ({learner.trades_seen} trade / "
            f"{learner.bars_seen} bar lessons): {weights}"
        )
    learned = False
    if memory is not None and not args.no_tune:
        console.print("[bold]Learning from this backtest[/bold]...")
        with console.status("reviewing trades...") as status:
            memory.learn(
                config,
                data,
                result,
                dict(learner.weights) if learner is not None else None,
                TuneConfig.from_config(config),
                progress=status.update,
                pinned=style.config_overrides(),
            )
        learned = True
        print_learning(memory, console)
    if args.html:
        path = write_html_report(result, args.html, memory=memory if learned else None)
        console.print(f"HTML report written to [bold]{path}[/bold]")
        m = result.metrics
        kept = memory.rounds[-1].get("adopted") if learned and memory and memory.rounds else None
        summary = (f"{m.get('total_return', 0.0):+.2%} return, max drawdown "
                   f"{m.get('max_drawdown', 0.0):.1%}, {len(result.trades)} trades, "
                   f"{m.get('win_rate', 0.0):.0%} won"
                   + (f"; learned: {kept['description']}" if kept else ""))
        saved = reports.archive(path, "backtest", summary,
                                reports.tone_of(m.get("total_return", 0.0)))
        if saved:
            console.print(f"Kept in [bold]{saved}[/bold]")
    write_status(config)
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

    config, _ = _load_with_memory(args.config)
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


def cmd_serve(args: argparse.Namespace) -> None:
    from .api import serve
    from .broker.paper import PaperBroker

    config, _ = _load_with_memory(args.config)
    broker_name = args.broker or config.live_settings["broker"]
    if broker_name == "alpaca":
        from .broker.alpaca import AlpacaBroker

        broker = AlpacaBroker(execution=config.build_execution())
        console.print("[bold yellow]API serving with the Alpaca broker.[/bold yellow]")
    else:
        broker = PaperBroker(execution=config.build_execution())
        console.print("[bold]API serving with the paper broker[/bold] (simulated fills).")
    console.print(DISCLAIMER)
    serve(config, broker, host=args.host, port=args.port)


def print_learning(memory: BacktestMemory, console: Console) -> None:
    """What the latest backtest taught the bot, and how it has improved."""
    rnd = memory.rounds[-1]
    console.print(f"[bold]What backtest #{rnd['number']} taught the bot[/bold]")
    for lesson in rnd["lessons"]:
        console.print(f"  - {lesson}")
    adopted = rnd.get("adopted")
    if adopted:
        console.print(
            f"[green]Learned:[/green] {adopted['description']}. It lowers losses on both the "
            f"training data ({adopted['train_gain']:+.2%}) and the held-out data "
            f"({adopted['holdout_gain']:+.2%}); the next backtest and paper trading use it."
        )
    elif rnd.get("tested"):
        best = (rnd.get("considered") or [None])[0]
        hint = (
            f" Closest: {best['description']} ({best['train_gain']:+.2%} train, "
            f"{best['holdout_gain']:+.2%} held-out)."
            if best
            else ""
        )
        console.print(
            f"[yellow]No change kept[/yellow]: none of {rnd['tested']} adjustments cut losses "
            f"on both the training and the held-out data.{hint}"
        )
    if len(memory.rounds) > 1:
        print_progress(memory, console)


def print_progress(memory: BacktestMemory, console: Console) -> None:
    table = Table(title="Backtest by backtest")
    for col in ("#", "Data to", "Return", "Max DD", "Losses", "Trades"):
        table.add_column(col, justify="right")
    table.add_column("Learned")
    for rnd in memory.rounds[-12:]:
        fr = rnd["full_run"]
        adopted = rnd.get("adopted")
        table.add_row(
            str(rnd["number"]),
            rnd.get("data_end", ""),
            f"{fr['total_return']:.2%}",
            f"{fr['max_drawdown']:.2%}",
            f"${-fr['gross_loss']:,.0f}",
            str(fr["num_trades"]),
            adopted["description"] if adopted else "[dim]-[/dim]",
        )
    console.print(table)


def cmd_learned(args: argparse.Namespace) -> None:
    config = BotConfig.load(args.config)
    tune = TuneConfig.from_config(config)
    memory = BacktestMemory.load(tune.memory_file)
    if args.reset:
        memory.reset()
        write_status(config)
        console.print(f"Forgot everything in {tune.memory_file}; back to the config's settings.")
        return
    if not tune.enabled:
        console.print("[yellow]learning.auto_tune is off in this config.[/yellow]")
    if not memory.rounds:
        console.print("Nothing learned yet. Run a backtest.")
        return
    console.print("[bold]Settings learned so far[/bold] (laid over the config):")
    for key, value in memory.overrides.items():
        console.print(f"  {key}: {value}")
    print_learning(memory, console)


def cmd_learn(args: argparse.Namespace) -> None:
    from .session import Session, keep_awake, parse_duration, read_goal

    try:
        seconds, round_seconds = parse_duration(args.duration), parse_duration(args.round)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    style = _style(args)
    config = _with_money(BotConfig.load(args.config), args.money)
    tune = TuneConfig.from_config(config)
    if not tune.enabled:
        console.print("[yellow]learning.auto_tune is off in this config; learning anyway.[/yellow]")
    memory = BacktestMemory.load(tune.memory_file)
    days = args.days or config.backtest_settings["days"]
    symbols = args.symbols.split(",") if args.symbols else None
    # Custom words are the session's goal too (shorts, a symbol, win rate...).
    goal_text = args.goal or (args.custom if args.style == "custom" else "")
    goal = read_goal(goal_text, symbols or config.universe, tune.loss_aversion)
    keep_awake()
    session = Session(
        config=config,
        memory=memory,
        tune=tune,
        goal=goal,
        seconds=seconds,
        round_seconds=round_seconds,
        load_data=lambda cfg: _load_data(cfg, days, symbols),
        say=lambda line: console.print(line, markup=False, highlight=False, soft_wrap=True),
        on_progress=lambda: write_status(config),
        style=style,
    )
    status = session.run()
    saved = session.report(status).save()
    if saved:
        console.print(f"Report kept in [bold]{saved}[/bold]")
    if memory.rounds:
        print_progress(memory, console)
    console.print(DISCLAIMER)


def cmd_lab(args: argparse.Namespace) -> None:
    from .lab.lab import STATE_FILE, Lab, running_lab
    from .lab.prepare import LabConfig, prepare
    from .session import keep_awake, parse_duration

    try:
        seconds, round_seconds = parse_duration(args.duration), parse_duration(args.round)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    style = _style(args)
    config = BotConfig.load(args.config)
    cfg = LabConfig.from_config(config)
    other = running_lab()
    if other:
        raise SystemExit(f"A lab is already running (process {other}). Stop it first, or let "
                         "it finish: two at once would overwrite each other's work.")
    session = Path(STATE_FILE)
    session.write_text(json.dumps({"status": "running", "phase": "getting data ready",
                                   "pid": os.getpid()}), encoding="utf-8")
    if args.fresh:
        Path(cfg.results_file).unlink(missing_ok=True)
        console.print(f"Starting over: forgot {cfg.results_file}.")
    say = lambda line: console.print(line, markup=False, highlight=False, soft_wrap=True)
    keep_awake()
    symbols = args.symbols.split(",") if args.symbols else None
    try:
        store, universe = prepare(config, say, symbols)
    finally:
        session.write_text(json.dumps({"status": "finished", "phase": "data ready"}),
                           encoding="utf-8")
    if args.prepare_only or not universe:
        return
    lab = Lab(cfg, store, universe, seconds, round_seconds, say=say,
              on_progress=lambda: write_status(config), until_done=args.until_done,
              style=style, money=args.money)
    status = lab.run()
    saved = lab.session_report(status).save()
    if saved:
        console.print(f"Report kept in [bold]{saved}[/bold]")
    console.print(DISCLAIMER)


def cmd_package(args: argparse.Namespace) -> None:
    from .lab.prepare import LabConfig

    cfg = LabConfig.from_config(BotConfig.load(args.config))
    path = Path(cfg.results_file)
    if not path.exists():
        console.print("Nothing found yet. Run [bold]investment-bot lab --for 1h[/bold].")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    champ = data.get("champion")
    if champ:
        pkg = champ["package"]
        console.print(f"[bold]Champion package[/bold] ({len(pkg['weights'])} indicators"
                      + ("" if champ.get("proven", True) else ", not yet better than the original")
                      + ")")
        table = Table()
        for col in ("Indicator", "Weight"):
            table.add_column(col)
        for name, weight in sorted(pkg["weights"].items(), key=lambda kv: -abs(kv[1])):
            table.add_row(name, f"{weight:+.2f}")
        console.print(table)
        knobs = {k: v for k, v in pkg.items() if k not in ("weights", "name")}
        console.print("Rules: " + ", ".join(f"{k}={v}" for k, v in knobs.items()))
        table = Table(title="How it did")
        for col in ("Part", "Return", "Max DD", "Trades", "Won"):
            table.add_column(col, justify="right")
        for part, label in (("train", "training"), ("hold", "held-out"), ("final", "final check")):
            m = champ["metrics"].get(part)
            if m:
                table.add_row(label, f"{m['total_return']:+.2%}", f"{m['max_drawdown']:.2%}",
                              str(m["num_trades"]), f"{m['win_rate']:.0%}")
        console.print(table)
    board = data.get("scoreboard") or []
    if board:
        table = Table(title="Indicator scoreboard (top 15)")
        for col in ("Indicator", "Family", "Use", "t", "Right", "Signals"):
            table.add_column(col)
        for e in board[:15]:
            table.add_row(e["feature"], e["family"], "follow" if e["sign"] > 0 else "fade",
                          f"{e['t']:+.1f}", f"{e['hit']:.0%}", str(e["signals"]))
        console.print(table)
    console.print(f"{len(data.get('rounds', []))} lab round(s) so far.")


def cmd_trade_package(args: argparse.Namespace) -> None:
    from .broker.alpaca import AlpacaBroker, AlpacaCredentialsError
    from .data.alpaca_data import AlpacaData
    from .lab.prepare import LabConfig
    from .lab.trader import PackageTrader

    style = _style(args)
    config = BotConfig.load(args.config)
    try:
        broker = AlpacaBroker(execution=config.build_execution())
    except AlpacaCredentialsError as exc:
        raise SystemExit(str(exc)) from exc
    say = lambda line: console.print(line, markup=False, highlight=False, soft_wrap=True)
    lab_cfg = LabConfig.from_config(config)
    data = AlpacaData(lab_cfg.cache_dir, feed=lab_cfg.feed, say=say)
    trader = PackageTrader(config, broker, data, say=say, dry_run=args.dry_run, style=style,
                           money=args.money)
    where = "PAPER" if "paper" in broker.base_url else "LIVE (real money)"
    console.print(f"[bold]Package trader[/bold] on Alpaca {where}"
                  + (" - dry run, no orders" if args.dry_run else "") + ".")
    console.print(DISCLAIMER)
    if args.once:
        trader.cycle()
        return
    try:
        trader.run_forever()
    finally:
        saved = trader.session_report().save()
        if saved:
            console.print(f"Report kept in [bold]{saved}[/bold]")


def cmd_champ(args: argparse.Namespace) -> None:
    """The champ-set builder: master the 4h, 1h and 30m charts, one setup per class."""
    from .lab.champ import SESSION_FILE, ChampBuilder, ChampConfig, prepare_charts, running_builder
    from .memory import apply_overrides
    from .session import keep_awake, parse_duration

    try:
        seconds = parse_duration(args.duration)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    style = _style(args)
    config = BotConfig.load(args.config)
    if args.only:
        config = apply_overrides(config, {"champ.classes": [args.only.rstrip("s")]})
    cfg = ChampConfig.from_config(config)
    other = running_builder()
    if other:
        raise SystemExit(f"The champ-set builder is already running (process {other}). Stop it "
                         "first, or let it finish.")
    session = Path(SESSION_FILE)
    session.write_text(json.dumps({"status": "running", "phase": "getting the charts ready",
                                   "pid": os.getpid()}), encoding="utf-8")
    if args.fresh:
        Path(cfg.results_file).unlink(missing_ok=True)
        console.print(f"Starting over: forgot {cfg.results_file} (and every try it counted).")
    say = lambda line: console.print(line, markup=False, highlight=False, soft_wrap=True)
    keep_awake()
    symbols = args.symbols.split(",") if args.symbols else None
    try:
        store, universe = prepare_charts(config, say, symbols)
    finally:
        session.write_text(json.dumps({"status": "finished", "phase": "charts ready"}),
                           encoding="utf-8")
    if args.prepare_only or not universe:
        return
    builder = ChampBuilder(config, store, universe, seconds, say=say,
                           on_progress=lambda: write_status(config), style=style, money=args.money)
    status = builder.run()
    saved = builder.report(status).save()
    write_status(config)
    if saved:
        console.print(f"Report kept in [bold]{saved}[/bold]")
    console.print(DISCLAIMER)


def cmd_trade_setup(args: argparse.Namespace) -> None:
    from .broker.alpaca import AlpacaBroker, AlpacaCredentialsError
    from .data.alpaca_data import AlpacaData
    from .lab.prepare import LabConfig
    from .lab.setup_trader import SetupTrader

    style = _style(args)
    config = BotConfig.load(args.config)
    try:
        broker = AlpacaBroker(execution=config.build_execution())
    except AlpacaCredentialsError as exc:
        raise SystemExit(str(exc)) from exc
    say = lambda line: console.print(line, markup=False, highlight=False, soft_wrap=True)
    lab_cfg = LabConfig.from_config(config)
    data = AlpacaData(lab_cfg.cache_dir, feed=lab_cfg.feed, say=say)
    trader = SetupTrader(config, broker, data, say=say, dry_run=args.dry_run, style=style,
                         money=args.money, only=args.only)
    where = "PAPER" if "paper" in broker.base_url else "LIVE (real money)"
    console.print(f"[bold]1h setup trader[/bold] on Alpaca {where}"
                  + (" - dry run, no orders" if args.dry_run else "") + ".")
    console.print(DISCLAIMER)
    if args.once:
        trader.cycle()
        write_status(config)
        return
    try:
        trader.run_forever()
    finally:
        saved = trader.session_report().save()
        write_status(config)
        if saved:
            console.print(f"Report kept in [bold]{saved}[/bold]")


def cmd_check(args: argparse.Namespace) -> None:
    from .check import run_checks

    config_path = args.config
    results, report = run_checks(config_path, quick=args.quick,
                                 say=lambda line: console.print(line, markup=False,
                                                                highlight=False))
    saved = report.save()
    failed = [r for r in results if r.status == "FAIL"]
    if saved:
        console.print(f"Report kept in [bold]{saved}[/bold]")
    if failed:
        raise SystemExit(f"{len(failed)} check(s) failed.")


def cmd_research(args: argparse.Namespace) -> None:
    from .research.runner import Researcher

    config = BotConfig.load(args.config)
    say = lambda line: console.print(line, markup=False, highlight=False, soft_wrap=True)
    researcher = Researcher(config, say=say)
    cfg = researcher.cfg
    if args.trade:
        from .broker.alpaca import AlpacaBroker, AlpacaCredentialsError

        try:
            broker = AlpacaBroker(execution=config.build_execution())
        except AlpacaCredentialsError as exc:
            raise SystemExit(str(exc)) from exc
        researcher.trade_with(broker, dry_run=args.dry_run)
        where = "PAPER" if "paper" in broker.base_url else "LIVE (real money)"
        t = researcher.trader.cfg
        console.print(f"[bold]Trading the news[/bold] on Alpaca {where}"
                      + (" - dry run, no orders" if args.dry_run else "")
                      + f": bets {t.min_size:.0%}-{t.max_size:.0%} of equity for articles "
                        f"{t.min_probability:.0%}-{t.full_probability:.0%} likely.")
        console.print(DISCLAIMER)
    x = (f"X on, cap ${cfg.x_monthly_cap:.2f}/month" if researcher.x.ready
         else "X off (set X_BEARER_TOKEN in .env to turn it on)")
    console.print(f"[bold]Research[/bold] on {len(researcher.symbols)} symbols, scored by "
                  f"{cfg.model}; {x}."
                  + ("" if args.trade else " Reads and judges only, places no orders."))
    if args.once:
        state = researcher.cycle()
        write_status(config)
        for mood in state["moods"][:15]:
            flag = f"  -> {mood['signal'].upper()}" if mood["signal"] else ""
            line = (f"{mood['symbol']:>9} {mood['mood']:+.2f}  {mood['stories']} stories"
                    f"  {mood['event']}: {mood['headline'][:80]}{flag}")
            # ASCII only: Jarvis's terminal is cp1252, and headlines have curly quotes.
            console.print(line.encode("ascii", "replace").decode(), markup=False,
                          highlight=False)
    else:
        researcher.run_forever()


def cmd_status(args: argparse.Namespace) -> None:
    config, _ = _load_with_memory(args.config)
    path = write_status(config)
    console.print(f"Wrote {path}." if path else "[red]Couldn't write the status file.[/red]")


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
    common.add_argument(
        "--research", choices=["off", "watch", "trade"], default="off",
        help="Also run news research alongside this command: watch = collect and score, "
             "trade = also trade the news on Alpaca paper")
    common.add_argument("--style", default="refine", choices=["refine", "fewer", "more", "custom"],
                        help="Strategy to try: refine the current one, fewer trades + more "
                             "risk, less risk + more trades, or custom (--custom)")
    common.add_argument("--custom", default="", help='Your own strategy in words, e.g. '
                        '"80%% confidence, bet 10%% a trade, no shorts"')
    common.add_argument("--confidence", type=float, default=0.0,
                        help="Confidence needed, in %% (0 = the strategy's own)")
    common.add_argument("--bet", type=float, default=0.0,
                        help="Share of the money a trade, in %% (0 = the strategy's own)")
    common.add_argument("--money", type=float, default=0.0,
                        help="Money to trade with (0 = the whole account / the config's cash)")

    bt = sub.add_parser("backtest", parents=[common], help="Run a backtest")
    bt.add_argument("--html", default=None, help="Write an HTML report to this path")
    bt.add_argument(
        "--no-tune", action="store_true", help="Don't learn from this run (memory still applies)"
    )
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

    srv = sub.add_parser("serve", parents=[common], help="Run the dashboard HTTP API")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    srv.add_argument("--broker", choices=["paper", "alpaca"], default=None)
    srv.set_defaults(func=cmd_serve)

    lrn = sub.add_parser("learned", parents=[common], help="What backtests have taught the bot")
    lrn.add_argument("--reset", action="store_true", help="Forget everything learned")
    lrn.set_defaults(func=cmd_learned)

    ln = sub.add_parser(
        "learn", parents=[common], help="Backtest over and over for a while, learning as it goes"
    )
    ln.add_argument("--for", dest="duration", required=True, help="How long: 10m, 8h, 2d...")
    ln.add_argument("--round", default="30m", help="Test this long before each adjustment")
    ln.add_argument("--goal", default="", help='What to get better at, e.g. "learn shorts"')
    ln.set_defaults(func=cmd_learn)

    lab = sub.add_parser(
        "lab", parents=[common],
        help="Find the clearest package of indicators, round after round")
    lab.add_argument("--for", dest="duration", required=True, help="How long: 30m, 8h, 2d...")
    lab.add_argument("--round", default="1h", help="Longest a round may take (default 1h)")
    lab.add_argument("--prepare-only", action="store_true",
                     help="Download candles and compute indicators, then stop")
    lab.add_argument("--fresh", action="store_true", help="Forget earlier lab results first")
    lab.add_argument("--until-done", action="store_true",
                     help="Champ-set builder: stop once every indicator has been tried "
                          "in the champion set (--for is then the longest it may take)")
    lab.set_defaults(func=cmd_lab)

    pk = sub.add_parser("package", parents=[common], help="Show the lab's champion package")
    pk.set_defaults(func=cmd_package)

    tp = sub.add_parser("trade-package", parents=[common],
                        help="Trade the lab's champion package on Alpaca")
    tp.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    tp.add_argument("--dry-run", action="store_true", help="Decide and log, place no orders")
    tp.set_defaults(func=cmd_trade_package)

    ch = sub.add_parser(
        "champ", parents=[common],
        help="Champ-set builder: master the 4h, 1h and 30m charts, then one setup that "
             "trades the 1h (stocks and crypto apart)")
    ch.add_argument("--for", dest="duration", default="3d",
                    help="The longest it may take (it usually needs minutes)")
    ch.add_argument("--round", default=None, help=argparse.SUPPRESS)  # older Jarvis buttons
    ch.add_argument("--only", choices=["stock", "stocks", "crypto"], default=None,
                    help="Build one class only")
    ch.add_argument("--fresh", action="store_true",
                    help="Forget earlier results, and the count of setups tried")
    ch.add_argument("--prepare-only", action="store_true",
                    help="Download candles and build the charts, then stop")
    ch.set_defaults(func=cmd_champ)

    ts = sub.add_parser("trade-setup", parents=[common],
                        help="Trade the champ-set builder's setups on Alpaca (1h decisions)")
    ts.add_argument("--once", action="store_true", help="Run a single look and exit")
    ts.add_argument("--dry-run", action="store_true", help="Decide and log, place no orders")
    ts.add_argument("--only", choices=["stock", "stocks", "crypto"], default=None,
                    help="Trade one class only")
    ts.set_defaults(func=cmd_trade_setup)

    rs = sub.add_parser("research", parents=[common],
                        help="Watch news (and X) about the bot's symbols, scored by Gemini")
    rs.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    rs.add_argument("--trade", action="store_true",
                    help="Also trade the news on Alpaca (paper), sized by probability")
    rs.add_argument("--dry-run", action="store_true", help="With --trade: decide, place no orders")
    rs.set_defaults(func=cmd_research)

    ck =sub.add_parser("check", parents=[common],
                        help="Test mode: check every part of the bot, without trading")
    ck.add_argument("--quick", action="store_true", help="Skip the test suite")
    ck.set_defaults(func=cmd_check)

    st = sub.add_parser("status", parents=[common], help="Rewrite jarvis_status.json for Jarvis")
    st.set_defaults(func=cmd_status)

    ls = sub.add_parser("strategies", help="List available strategies")
    ls.set_defaults(func=cmd_strategies)

    return parser


def main(argv: list[str] | None = None) -> None:
    from .data.alpaca_data import load_env

    load_env()  # Alpaca keys from .env
    args = build_parser().parse_args(argv)
    if getattr(args, "research", "off") != "off" and args.command != "research":
        start_research_alongside(args.config, args.research)
    args.func(args)


def start_research_alongside(config_path: str | None, mode: str) -> threading.Thread:
    """Run the research loop (and with mode "trade", the news trader) in a
    background thread next to another command. It ends with that command."""

    def say(line: str) -> None:
        # ASCII only: Jarvis's terminal is cp1252.
        console.print(f"[news] {line}".encode("ascii", "replace").decode(), markup=False,
                      highlight=False, soft_wrap=True)

    def run() -> None:
        from .research.runner import Researcher

        config = BotConfig.load(config_path)
        while True:
            try:
                researcher = Researcher(config, say=say)
                if mode == "trade":
                    from .broker.alpaca import AlpacaBroker

                    researcher.trade_with(AlpacaBroker(execution=config.build_execution()))
                say(f"research running alongside ({mode}), every "
                    f"{researcher.cfg.poll_minutes:g} min")
                researcher.run_forever()
            except (SystemExit, RuntimeError) as exc:  # no keys, or real money refused
                say(f"research stopped: {' '.join(str(exc).split())[:200]}")
                return
            except Exception as exc:  # noqa: BLE001 - research must never take the command down
                say(f"research failed ({' '.join(str(exc).split())[:160]}); restarting in 60 s")
                time.sleep(60)

    thread = threading.Thread(target=run, name="research", daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    main()
