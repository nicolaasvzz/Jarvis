"""Test mode: check every part of the bot, without trading.

    investment-bot check [-c config.local.yaml] [--quick]

One line per check — PASS, WARN (works, but look at it), FAIL or SKIP — and
a report in ``reports/``. Nothing here places an order or changes the bot's
own state: data is fetched into a throwaway cache, and the package trader's
dry run keeps its book in a throwaway file.

1. the config file, and what backtests have learned (``learned.json``)
2. the test suite (skipped with ``--quick``)
3. a small backtest on made-up prices: the strategies, risk and engine
4. Alpaca: keys, the account, the market clock, positions
5. candles: a stock and a crypto pair from Alpaca, and the backtest feed
6. the champ-set builder: its champion setups and its chart store; the older
   indicator lab: results, champion package, symbol list, indicator store
7. the traders: one dry-run look each (1h setups, and the older package
   trader) on a few symbols
8. the files Jarvis reads: ``jarvis_status.json`` and ``reports/``
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .reports import REPORTS_DIR, Report

STATUSES = ("PASS", "WARN", "FAIL", "SKIP")


@dataclass
class Check:
    name: str
    status: str
    detail: str
    seconds: float


class Skip(Exception):
    """A check that can't run here (no keys, nothing built yet)."""


class Warn(Exception):
    """A check that ran, but whose result needs a look."""


def _brief(exc: BaseException, limit: int = 200) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text if len(text) <= limit else text[: limit - 3] + "..."


def run_checks(config_path: str | None, quick: bool = False,
               say: Callable[[str], None] = print) -> tuple[list[Check], Report]:
    from .config import BotConfig

    results: list[Check] = []
    ctx: dict[str, Any] = {}
    scratch = Path(tempfile.mkdtemp(prefix="bot-check-"))

    def check(name: str, fn: Callable[[], str]) -> None:
        started = time.monotonic()
        try:
            status, detail = "PASS", fn()
        except Skip as exc:
            status, detail = "SKIP", _brief(exc)
        except Warn as exc:
            status, detail = "WARN", _brief(exc)
        except (Exception, SystemExit) as exc:  # a check failing must not stop the others
            status, detail = "FAIL", _brief(exc)
        took = time.monotonic() - started
        results.append(Check(name, status, detail, took))
        say(f"[{status}] {name}: {detail} ({took:.1f}s)")

    # 1. config and memory ---------------------------------------------------
    def config() -> str:
        path = config_path or "config.local.yaml"
        if not Path(path).exists():
            raise FileNotFoundError(f"{path} is missing")
        ctx["config"] = BotConfig.load(path)
        cfg = ctx["config"]
        return (f"{path}: {len(cfg.universe)} symbols for backtests, data from "
                f"{cfg.section('data').get('source', 'synthetic')}")

    def memory() -> str:
        from .memory import BacktestMemory, TuneConfig

        tune = TuneConfig.from_config(ctx["config"])
        mem = BacktestMemory.load(tune.memory_file)
        ctx["memory"] = mem
        if not mem.rounds:
            raise Warn(f"{tune.memory_file}: nothing learned yet (run a backtest)")
        return (f"{tune.memory_file}: {len(mem.rounds)} backtest(s) learned from, "
                f"{len(mem.overrides)} setting(s) changed")

    check("Config", config)
    if "config" not in ctx:
        say("Can't go on without the config.")
        return results, _report(results)
    check("Backtest memory", memory)

    # 2. tests ----------------------------------------------------------------
    def tests() -> str:
        if quick:
            raise Skip("--quick")
        if not Path("tests").is_dir():
            raise Skip("no tests folder")
        command = [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]
        run = subprocess.run(command, capture_output=True, text=True, timeout=1800,
                             encoding="utf-8", errors="replace")
        lines = [ln for ln in (run.stdout + run.stderr).splitlines() if ln.strip()]
        last = lines[-1].strip("= ") if lines else "no output"
        if run.returncode != 0:
            failed = [ln for ln in lines if ln.startswith("FAILED")][:5]
            raise RuntimeError(last + ("; " + "; ".join(failed) if failed else ""))
        return last

    check("Test suite", tests)

    # 3. a backtest on made-up prices ----------------------------------------------
    def backtest() -> str:
        from .backtest.engine import BacktestEngine
        from .data.feed import make_feed

        cfg = ctx["memory"].apply(ctx["config"]) if ctx.get("memory") else ctx["config"]
        feed = make_feed("synthetic")
        data = {s: feed.history(s, 500) for s in ("AAA", "BBB", "CCC")}
        settings = cfg.backtest_settings
        engine = BacktestEngine(strategy=cfg.build_strategy(), risk=cfg.build_risk(),
                                execution=cfg.build_execution(),
                                starting_cash=settings["starting_cash"],
                                lookback=settings["lookback"])
        result = engine.run(data)
        return (f"3 made-up symbols, 500 days: {result.metrics.get('total_return', 0):+.2%}, "
                f"{len(result.trades)} trades (the engine works; the number means nothing)")

    check("Backtest engine", backtest)

    # 4. Alpaca ------------------------------------------------------------------------
    def keys() -> str:
        from .data.alpaca_data import load_env

        load_env()
        if not (os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY")):
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY aren't set in .env")
        url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        ctx["keys"] = True
        if "paper" not in url:
            raise Warn(f"keys set, but ALPACA_BASE_URL is the LIVE endpoint ({url})")
        return "keys set; paper account"

    def account() -> str:
        if not ctx.get("keys"):
            raise Skip("no Alpaca keys")
        from .broker.alpaca import AlpacaBroker

        broker = AlpacaBroker(execution=ctx["config"].build_execution())
        ctx["broker"] = broker
        acct = broker.account()
        equity = float(acct.get("equity") or 0)
        power = float(acct.get("buying_power") or 0)
        line = (f"{acct.get('status', '?')}, equity ${equity:,.2f}, "
                f"buying power ${power:,.2f}")
        blocked = [k for k in ("trading_blocked", "account_blocked") if acct.get(k)]
        if blocked or acct.get("status") != "ACTIVE":
            raise Warn(line + (f"; {', '.join(blocked)}" if blocked else ""))
        return line

    def clock() -> str:
        if "broker" not in ctx:
            raise Skip("no Alpaca account")
        c = ctx["broker"].clock()
        state = "open" if c.get("is_open") else "closed"
        upcoming = c.get("next_close") if c.get("is_open") else c.get("next_open")
        return (f"stock market {state}; next {'close' if c.get('is_open') else 'open'} "
                f"{str(upcoming)[:16].replace('T', ' ')} New York time")

    def positions() -> str:
        if "broker" not in ctx:
            raise Skip("no Alpaca account")
        held = ctx["broker"].positions()
        names = ", ".join(str(p.get("symbol")) for p in held[:8])
        return f"{len(held)} open" + (f": {names}" + ("..." if len(held) > 8 else "")
                                      if held else "")

    check("Alpaca keys", keys)
    check("Alpaca account", account)
    check("Market clock", clock)
    check("Positions", positions)

    # 5. candles -------------------------------------------------------------------------
    def candles(symbol: str) -> Callable[[], str]:
        def run() -> str:
            if not ctx.get("keys"):
                raise Skip("no Alpaca keys")
            import pandas as pd

            from .data.alpaca_data import AlpacaData
            from .lab.prepare import LabConfig

            cfg = LabConfig.from_config(ctx["config"])
            data = AlpacaData(scratch / "alpaca", feed=cfg.feed)
            bars = data.bars(symbol, "10Min", 5)
            if bars.empty:
                raise RuntimeError("no candles came back")
            age = pd.Timestamp.now(tz="UTC") - bars.index[-1]
            hours = age.total_seconds() / 3600
            feed = "crypto" if "/" in symbol else cfg.feed
            line = (f"{len(bars)} 10-minute candles ({feed} feed), last "
                    f"{bars.index[-1]:%Y-%m-%d %H:%M} UTC, ${float(bars['close'].iloc[-1]):,.2f}")
            if "/" in symbol and hours > 1:
                raise Warn(line + f" - {hours:.0f}h old; crypto trades all day")
            return line

        return run

    def backtest_feed() -> str:
        cfg = ctx["config"]
        source = cfg.section("data").get("source", "synthetic")
        symbol = cfg.universe[0]
        df = cfg.build_feed().history(symbol, 30)
        if df is None or df.empty:
            raise RuntimeError(f"{source}: no data for {symbol}")
        return f"{source}: {len(df)} daily candles for {symbol}, last {df.index[-1]:%Y-%m-%d}"

    check("Stock candles (SPY)", candles("SPY"))
    check("Crypto candles (BTC/USD)", candles("BTC/USD"))
    check("Backtest data feed", backtest_feed)

    # 6. the lab ---------------------------------------------------------------------------
    def lab() -> str:
        from .lab.lab import STATE_FILE, running_lab
        from .lab.prepare import LabConfig

        cfg = LabConfig.from_config(ctx["config"])
        path = Path(cfg.results_file)
        if not path.exists():
            raise Warn(f"no {path} yet: run the champ-set builder")
        data = json.loads(path.read_text(encoding="utf-8"))
        champ = data.get("champion")
        rounds = len(data.get("rounds") or [])
        other = running_lab(STATE_FILE)
        running = f"; a lab is running now (process {other})" if other else ""
        if not champ:
            raise Warn(f"{rounds} round(s), but no champion package yet{running}")
        ctx["champion"] = champ
        weights = (champ.get("package") or {}).get("weights") or {}
        hold = ((champ.get("metrics") or {}).get("hold") or {}).get("total_return")
        tested = len(data.get("tested") or {})
        line = (f"{rounds} round(s); champion of {len(weights)} indicators"
                + (f", held-out {hold:+.1%}" if hold is not None else "")
                + (f"; {tested} indicators tried by the builder" if tested else "") + running)
        if not champ.get("proven", True):
            raise Warn(line + "; not yet confirmed on held-out (trades small)")
        return line

    def universe() -> str:
        from .lab.prepare import LabConfig

        cfg = LabConfig.from_config(ctx["config"])
        path = Path(cfg.universe_file)
        if not path.exists():
            raise Warn(f"no {path} yet: the lab writes it")
        symbols = json.loads(path.read_text(encoding="utf-8")).get("symbols") or []
        crypto = sum(1 for s in symbols if s.get("class") == "crypto")
        return f"{len(symbols)} symbols ({len(symbols) - crypto} stocks, {crypto} crypto)"

    def store() -> str:
        from .lab.features import FeatureStore
        from .lab.prepare import LabConfig

        cfg = LabConfig.from_config(ctx["config"])
        fs = FeatureStore(cfg.feature_dir)
        symbols = fs.symbols()
        if not symbols:
            raise Warn(f"{cfg.feature_dir} is empty: the lab builds it on its first run")
        size = sum(p.stat().st_size for p in Path(cfg.feature_dir).glob("*/feat.npy"))
        return f"{len(symbols)} symbols x {len(fs.columns)} indicator columns, {size / 1e9:.1f} GB"

    def champ() -> str:
        from .lab.champ import SESSION_FILE, ChampConfig, load_champions, running_builder
        from .lab.setups import Setup

        cfg = ChampConfig.from_config(ctx["config"])
        champions = load_champions(cfg.results_file)
        other = running_builder(SESSION_FILE)
        running = f"; the builder is running now (process {other})" if other else ""
        if not champions:
            raise Warn(f"no champion setup in {cfg.results_file} yet: run the champ-set "
                       f"builder{running}")
        ctx["setups"] = champions
        parts = []
        for cls, champ in champions.items():
            setup = Setup.from_dict(champ["setup"])
            test = ((champ.get("metrics") or {}).get("test") or {}).get("total_return", 0.0)
            parts.append(f"{cls}: {'proven' if champ.get('proven') else 'NOT proven'}, "
                         f"{len(setup.features)} indicators on 3 charts, test {test:+.1%}")
        line = "; ".join(parts) + running
        if not all(c.get("proven") for c in champions.values()):
            raise Warn(line + " (unproven setups trade small, paper only)")
        return line

    def charts() -> str:
        from .lab.champ import ChampConfig
        from .lab.features import FeatureStore

        cfg = ChampConfig.from_config(ctx["config"])
        fs = FeatureStore(cfg.feature_dir)
        symbols = fs.symbols()
        if not symbols:
            raise Warn(f"{cfg.feature_dir} is empty: the champ-set builder builds it")
        size = sum(p.stat().st_size for p in Path(cfg.feature_dir).glob("*/feat.npy"))
        return (f"{len(symbols)} symbols x {len(fs.columns)} columns (1h, 30m, 4h), "
                f"{size / 1e9:.1f} GB")

    check("Champ-set builder", champ)
    check("Chart store", charts)
    check("Lab results (older lab)", lab)
    check("Lab symbol list", universe)
    check("Indicator store (older lab)", store)

    # 7. the trader, dry run ------------------------------------------------------------------
    def trader() -> str:
        if "broker" not in ctx:
            raise Skip("no Alpaca account")
        if "champion" not in ctx:
            raise Skip("no champion package to trade")
        from .data.alpaca_data import AlpacaData
        from .lab.prepare import LabConfig
        from .lab.trader import PackageTrader

        cfg = LabConfig.from_config(ctx["config"])
        lines: list[str] = []
        data = AlpacaData(scratch / "alpaca", feed=cfg.feed)
        t = PackageTrader(ctx["config"], ctx["broker"], data, say=lines.append, dry_run=True,
                          state_file=scratch / "package_trader.json")
        everything = t.universe()
        stocks = [u for u in everything if u.get("class") != "crypto"][:2]
        crypto = [u for u in everything if u.get("class") == "crypto"][:1]
        sample = stocks + crypto
        if not sample:
            raise Skip("no symbol list yet")
        t.universe = lambda: sample  # type: ignore[method-assign]
        t.cycle()
        failed = [ln.strip() for ln in lines if "failed" in ln or "no candles" in ln]
        names = ", ".join(u["symbol"] for u in sample)
        summary = next((ln for ln in reversed(lines) if "equity" in ln), "").strip()
        if failed:
            raise RuntimeError(f"{names}: " + "; ".join(failed[:3]))
        return f"one dry-run cycle on {names}, no orders placed. {summary}"

    check("Package trader (dry run)", trader)

    def setup_trader() -> str:
        if "broker" not in ctx:
            raise Skip("no Alpaca account")
        if "setups" not in ctx:
            raise Skip("no champion setup to trade")
        from .data.alpaca_data import AlpacaData
        from .lab.prepare import LabConfig
        from .lab.setup_trader import SetupTrader

        cfg = LabConfig.from_config(ctx["config"])
        lines: list[str] = []
        data = AlpacaData(scratch / "alpaca", feed=cfg.feed)
        t = SetupTrader(ctx["config"], ctx["broker"], data, say=lines.append, dry_run=True,
                        state_file=scratch / "setup_trader.json")
        t.load_setups()
        everything = t.universe()
        stocks = [u for u in everything if u.get("class") != "crypto"][:2]
        crypto = [u for u in everything if u.get("class") == "crypto"][:1]
        sample = stocks + crypto
        if not sample:
            raise Skip("no symbol list yet")
        t.universe = lambda: sample  # type: ignore[method-assign]
        t.cycle()
        failed = [ln.strip() for ln in lines if "failed" in ln or "no candles" in ln]
        names = ", ".join(u["symbol"] for u in sample)
        summary = next((ln for ln in reversed(lines) if "equity" in ln), "").strip()
        if failed:
            raise RuntimeError(f"{names}: " + "; ".join(failed[:3]))
        return f"one dry-run look on {names}, no orders placed. {summary}"

    check("1h setup trader (dry run)", setup_trader)

    # 8. what Jarvis reads -----------------------------------------------------------------------
    def status() -> str:
        from .jarvis_status import write_status

        path = write_status(ctx["config"])
        if not path:
            raise RuntimeError("couldn't write jarvis_status.json")
        keys = len(json.loads(Path(path).read_text(encoding="utf-8")))
        return f"{path}: {keys} entries"

    def reports() -> str:
        folder = Path(REPORTS_DIR)
        folder.mkdir(exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        count = len(list(folder.glob("*.html")))
        return f"{folder}/ is writable; {count} report(s) kept"

    check("Jarvis status file", status)
    check("Reports folder", reports)

    shutil.rmtree(scratch, ignore_errors=True)
    counts = {s: sum(1 for r in results if r.status == s) for s in STATUSES}
    say(f"\n{counts['PASS']} passed, {counts['WARN']} to look at, {counts['FAIL']} failed, "
        f"{counts['SKIP']} skipped.")
    return results, _report(results)


def _report(results: list[Check]) -> Report:
    counts = {s: sum(1 for r in results if r.status == s) for s in STATUSES}
    summary = (f"{counts['PASS']} passed, {counts['WARN']} to look at, {counts['FAIL']} failed"
               + (f", {counts['SKIP']} skipped" if counts["SKIP"] else ""))
    tone = "bad" if counts["FAIL"] else "neutral" if counts["WARN"] else "good"
    out = Report("check", summary, tone, title="Test mode")
    out.stat("Passed", counts["PASS"], "good" if counts["PASS"] else "neutral")
    out.stat("To look at", counts["WARN"], "neutral")
    out.stat("Failed", counts["FAIL"], "bad" if counts["FAIL"] else "neutral")
    out.stat("Skipped", counts["SKIP"])
    out.table("Checks", [{"Check": r.name, "Result": r.status, "Detail": r.detail,
                          "Took": f"{r.seconds:.1f}s"} for r in results])
    return out
