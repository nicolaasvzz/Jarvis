"""A one-file summary of the bot for Jarvis's TradeBot page.

Jarvis's Mothership draws any project's status JSON without knowing the
project, deciding the picture from the shape: plain values become tiles,
``[time, value]`` pairs a chart, lists of objects tables, ``{name: number}``
bars. This module writes ``jarvis_status.json`` in exactly those shapes, with
human-readable keys, from the files the bot already keeps:

- the paper-trading state (``live_state.json``): equity, positions, trades
- backtest memory (``learned.json``): learned settings, run-by-run history
- a learning session (``learning_session.json``): goal, time left, rounds
- news research (``research.json``): mood per symbol, signals, X spend

It is rewritten after every trading cycle, every backtest, and on
``investment-bot status``. Reading only, never trading: if a file is missing,
that part is simply left out.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import BotConfig
from .memory import LABELS, TuneConfig

STATUS_FILE = "jarvis_status.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _alive(pid: Any) -> bool:
    """Is process `pid` still running? (A session ended by force can't say so.)"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _session(path: Path, now: datetime) -> dict[str, Any]:
    """Tiles and a chart for the learning session, if there is or was one."""
    session = _read(path)
    if not session:
        return {}
    status = str(session.get("status", ""))
    if status == "running" and not _alive(session.get("pid")):
        status = "stopped"
    out: dict[str, Any] = {"Learning": session.get("phase") if status == "running" else status}
    out["Learning goal"] = session.get("goal") or "cut losses"
    if status == "running":
        try:
            ends = datetime.fromisoformat(str(session["ends"]))
            left = (ends - now.astimezone(timezone.utc)).total_seconds()
            out["Learning time left"] = (
                f"{int(left // 3600)}h {int(left % 3600 // 60):02d}m" if left > 0 else "finishing"
            )
        except (KeyError, ValueError):
            pass
    out["Learning rounds"] = session.get("rounds", 0)
    out["Changes kept this session"] = len(session.get("kept") or [])
    history = session.get("score_by_round") or []
    if len(history) > 1:
        out["Goal score by round"] = [[f"round {n}", value] for n, value in history]
    return out


def _lab(config: BotConfig, now: datetime) -> dict[str, Any]:
    """The indicator lab: session progress, the champion package, the scoreboard,
    and the package trader on Alpaca."""
    from .lab.lab import STATE_FILE as LAB_SESSION
    from .lab.prepare import LabConfig
    from .lab.trader import STATE_FILE as TRADER_FILE

    try:
        cfg = LabConfig.from_config(config)
    except (ValueError, TypeError):
        return {}
    out: dict[str, Any] = {}
    session = _read(Path(LAB_SESSION))
    if session:
        status = str(session.get("status", ""))
        if status == "running" and not _alive(session.get("pid")):
            status = "stopped"
        out["Lab"] = session.get("phase") if status == "running" else status
        if status == "running" and session.get("ends"):
            try:
                ends = datetime.fromisoformat(str(session["ends"]))
                left = (ends - now.astimezone(timezone.utc)).total_seconds()
                out["Lab time left"] = (f"{int(left // 3600)}h {int(left % 3600 // 60):02d}m"
                                        if left > 0 else "finishing")
            except (KeyError, ValueError):
                pass
        if session.get("symbols"):
            out["Lab symbols"] = session["symbols"]
        if session.get("until_done") and session.get("to_test"):
            out["Champ-set indicators tried"] = f"{session.get('tested', 0)} / {session['to_test']}"
    results = _read(Path(cfg.results_file))
    champ = results.get("champion") or {}
    if champ:
        pkg = champ.get("package") or {}
        weights = pkg.get("weights") or {}
        out["Champion package"] = (f"{len(weights)} indicators, enter at "
                                   f"{pkg.get('threshold')}, "
                                   f"{float(pkg.get('size', 0)):.0%} a trade")
        for part, label in (("hold", "held-out"), ("final", "final check")):
            m = (champ.get("metrics") or {}).get(part)
            if m:
                out[f"Champion {label} %"] = round(float(m["total_return"]) * 100, 2)
        from .lab.catalog import family_of

        out["Champion indicators"] = [
            {"Indicator": name, "Family": family_of(name), "Use": "follow" if w > 0 else "fade",
             "Weight": round(abs(float(w)), 3)}
            for name, w in sorted(weights.items(), key=lambda kv: -abs(kv[1]))
        ]
    board = results.get("scoreboard") or []
    if board:
        out["Clearest indicators (t)"] = {e["feature"]: round(abs(float(e["t"])), 2)
                                          for e in board[:15]}
    rounds = results.get("rounds") or []
    if rounds:
        out["Lab rounds done"] = len(rounds)
        scores = [[f"round {r['number']}", r["champion_score"]] for r in rounds
                  if r.get("champion_score") is not None]
        if len(scores) > 1:
            out["Champion held-out score by round"] = scores[-100:]
        out["Lab rounds"] = [
            {"#": r.get("number"), "Kind": r.get("kind"),
             "Kept": sum(1 for line in r.get("lessons") or [] if line.startswith("KEPT")),
             "Note": next((line for line in r.get("lessons") or []
                           if line.startswith(("Tried", "Champion", "Pair"))), "")}
            for r in rounds[-50:]
        ]
    trader = _read(Path(TRADER_FILE))
    if trader:
        curve = trader.get("equity") or []
        if curve:
            out["Alpaca equity"] = _money(curve[-1][1])
        if len(curve) > 1:
            out["Alpaca equity curve"] = [[str(t)[:16], _money(e)] for t, e in curve[-300:]]
        holdings = trader.get("holdings") or {}
        out["Alpaca positions (package)"] = [
            {"Symbol": s, "Side": "LONG" if h.get("direction", 1) > 0 else "SHORT",
             "Entry": _money(h.get("entry_price", 0)), "Stop": _money(h.get("stop", 0)),
             "Candles held": h.get("bars", 0), "Since": str(h.get("opened", ""))[:16]}
            for s, h in holdings.items()
        ]
        out["Alpaca orders"] = [
            {"At": str(e.get("at", ""))[:16], "Symbol": e.get("symbol"), "What": e.get("what")}
            for e in (trader.get("log") or [])[-50:]
        ]
    return out


def _news_trades(path: Path) -> dict[str, Any]:
    """The news trader: open bets, closed ones, and whether its probabilities hold up."""
    state = _read(path)
    if not state:
        return {}
    closed = state.get("closed") or []
    out: dict[str, Any] = {
        "News trades open": len(state.get("holdings") or {}),
        "News trades closed": len(closed),
        "News trades P&L": _money(sum(float(t.get("pnl", 0)) for t in closed)),
    }
    if state.get("dry_run"):
        out["News trading"] = "dry run (no orders)"
    elif state.get("paused_today"):
        out["News trading"] = "paused today (daily loss limit)"
    out["News bet size by probability"] = {p: round(float(s) * 100, 1)
                                           for p, s in (state.get("sizing") or {}).items()}
    out["News positions"] = [
        {"Symbol": s, "Side": "LONG" if h.get("direction", 1) > 0 else "SHORT",
         "Probability %": round(float(h.get("probability", 0)) * 100),
         "Bet % of equity": round(float(h.get("size", 0)) * 100, 1),
         "Entry": _money(h.get("entry", 0)), "Stop": _money(h.get("stop", 0)),
         "Target": _money(h["take"]) if h.get("take") else None,
         "Until": str(h.get("until", ""))[:16].replace("T", " "),
         "Article": str(h.get("headline", ""))[:140]}
        for s, h in (state.get("holdings") or {}).items()
    ]
    if closed:
        out["Closed news trades"] = [
            {"Closed": str(t.get("closed", ""))[:16].replace("T", " "), "Symbol": t.get("symbol"),
             "Side": str(t.get("side", "")).upper(),
             "Probability %": round(float(t.get("probability", 0)) * 100),
             "Bet %": round(float(t.get("size", 0)) * 100, 1), "P&L": _money(t.get("pnl", 0)),
             "P&L %": t.get("pnl_pct"), "Why": t.get("why"),
             "Article": str(t.get("headline", ""))[:120]}
            for t in closed[-50:]
        ]
    calib = state.get("calibration") or []
    if calib:
        out["News win rate by probability %"] = {c["band"]: round(c["win_rate"] * 100, 1)
                                                for c in calib}
    return out


def _research(path: Path, trade_file: str | Path = "news_trader.json") -> dict[str, Any]:
    """News research: what it's reading, what X has cost, the mood per symbol."""
    state = _read(path)
    if not state:
        return {}
    counts = state.get("counts") or {}
    hours = state.get("window_hours", 24)
    out: dict[str, Any] = {
        "Research updated": str(state.get("updated", ""))[:16],
        f"News stories ({hours:g}h)": counts.get("alpaca", 0),
        "X spend this month": (f"${float(state.get('x_spent', 0)):.2f} / "
                               f"${float(state.get('x_cap', 0)):.2f}"
                               if state.get("x_on") else "off"),
        "News signals": len(state.get("signals") or []),
    }
    problems = [f"{name}: {text}" for name, text in (state.get("steps") or {}).items()
                if str(text).startswith("error")]
    if problems:
        out["Research problems"] = "; ".join(problems)[:300]
    moods = state.get("moods") or []
    if moods:
        out["News mood by symbol"] = {m["symbol"]: m["mood"] for m in moods[:15]}
        out["News by symbol"] = [
            {"Symbol": m["symbol"], "Mood": m["mood"], "Signal": m.get("signal") or "-",
             "Stories": m.get("stories", 0), "X posts": m.get("posts", 0),
             "Event": m.get("event", ""), "Top story": m.get("headline", "")[:140]}
            for m in moods[:25]
        ]
    out.update(_news_trades(Path(trade_file)))
    recent = state.get("recent") or []
    if recent:
        # Oldest first: the page shows the newest at the top.
        out["Latest scored news"] = [
            {"At": str(r.get("published", ""))[:16].replace("T", " "), "Symbol": r.get("symbol"),
             "Direction": round(float(r.get("direction", 0)), 2),
             "Strength": round(float(r.get("strength", 0)), 2),
             "Probability %": round(float(r.get("probability", 0)) * 100),
             "Move %": r.get("move_pct"), "Target": r.get("target"), "Event": r.get("event"),
             "Headline": str(r.get("headline", ""))[:140]}
            for r in recent[::-1]
        ]
    return out


def _money(value: float) -> float:
    return round(float(value), 2) + 0.0  # + 0.0 turns -0.0 into 0.0


def build_status(config: BotConfig, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    live = config.live_settings
    state = _read(Path(live["state_file"]))
    tune = TuneConfig.from_config(config)
    memory = _read(Path(tune.memory_file))
    out: dict[str, Any] = {"Updated": now.isoformat(timespec="seconds")}

    # ---- tiles: the numbers at a glance ----
    positions = state.get("positions") or []
    trades = state.get("trades") or []
    if state:
        start = float(live["starting_cash"])
        held = sum(float(p["qty"]) * float(p.get("last_price") or p["avg_price"]) for p in positions)
        equity = float(state.get("cash", start)) + held
        today = now.date().isoformat()
        todays = [t for t in trades if str(t.get("exit_time", "")).startswith(today)]
        out["Equity"] = _money(equity)
        out["Return %"] = round((equity / start - 1) * 100, 2) if start else 0.0
        out["Cash"] = _money(state.get("cash", start))
        out["Open positions"] = len(positions)
        out["Trades today"] = len(todays)
        out["P&L today"] = _money(sum(float(t.get("pnl", 0)) for t in todays))
        out["Halted"] = bool(state.get("halted", False))
    rounds = memory.get("rounds") or []
    if rounds:
        out["Backtests learned from"] = len(rounds)
    out.update(_session(Path("learning_session.json"), now))
    try:
        out.update(_lab(config, now))
    except (OSError, ValueError, KeyError, TypeError):
        pass  # the lab's files are optional; never let them break the page
    try:
        from .research.runner import ResearchConfig
        from .research.trader import TradeConfig

        out.update(_research(Path(ResearchConfig.from_config(config).state_file),
                             TradeConfig.from_config(config).state_file))
    except (OSError, ValueError, KeyError, TypeError):
        pass  # research is optional too

    # ---- blocks ----
    curve = state.get("equity_curve") or []
    if len(curve) > 1:
        out["Equity curve"] = [[str(t)[:16], _money(e)] for t, e in curve[-300:]]
    if state:
        out["Positions"] = [
            {
                "Symbol": p["symbol"],
                "Side": "LONG" if float(p["qty"]) > 0 else "SHORT",
                "Qty": abs(float(p["qty"])),
                "Entry": _money(p["avg_price"]),
                "Last": _money(p.get("last_price") or p["avg_price"]),
                "Stop": _money(p["stop_price"]) if p.get("stop_price") else None,
                "P&L": _money(
                    float(p["qty"]) * (float(p.get("last_price") or p["avg_price"]) - float(p["avg_price"]))
                ),
                "Since": str(p.get("opened_at", ""))[:16],
            }
            for p in positions
        ]
        # Oldest first: the page shows the newest dozen at the top.
        out["Recent trades"] = [
            {
                "Closed": str(t.get("exit_time", ""))[:16],
                "Symbol": t.get("symbol"),
                "Side": "LONG" if int(t.get("direction", 1)) > 0 else "SHORT",
                "Qty": t.get("qty"),
                "Entry": _money(t.get("entry_price", 0)),
                "Exit": _money(t.get("exit_price", 0)),
                "P&L": _money(t.get("pnl", 0)),
                "Why": t.get("reason", ""),
            }
            for t in trades[-50:]
        ]
    weights = ((state.get("learning") or {}).get("weights")) or {}
    if weights:
        out["Strategy weights (paper trading)"] = {n: round(float(w), 3) for n, w in weights.items()}

    overrides = memory.get("overrides") or {}
    if overrides:
        learned: dict[str, Any] = {}
        for key, value in overrides.items():
            label = LABELS.get(key, key).capitalize()
            if isinstance(value, dict):
                value = ", ".join(f"{n} {float(w):.0%}" for n, w in value.items())
            elif isinstance(value, bool):
                value = "on" if value else "off"
            elif value is None:
                value = "off"
            learned[label] = value
        out["Learned from backtests"] = learned
    if rounds:
        latest = rounds[-1]
        lessons = [{"Lesson": text} for text in latest.get("lessons") or []] + (
            [{"Lesson": f"Kept: {latest['adopted']['description']}."}]
            if latest.get("adopted")
            else [{"Lesson": f"No change kept ({latest.get('tested', 0)} tried)."}]
        )
        # The page lists newest first; reversed, they read top to bottom in order.
        out["What the last backtest taught it"] = lessons[::-1]
        out["Backtests"] = [
            {
                "#": r.get("number"),
                "Data to": r.get("data_end", ""),
                "Return %": round(float(r["full_run"]["total_return"]) * 100, 2),
                "Max drawdown %": round(float(r["full_run"]["max_drawdown"]) * 100, 2),
                "Losses": _money(-float(r["full_run"]["gross_loss"])),
                "Trades": r["full_run"]["num_trades"],
                "Learned": (r.get("adopted") or {}).get("description", "-"),
            }
            for r in rounds[-50:]
        ]
    return out


def write_status(config: BotConfig, path: str | Path = STATUS_FILE) -> Path | None:
    """Write the summary next to the bot; never let it break trading."""
    path = Path(path)
    try:
        payload = json.dumps(build_status(config), indent=2, default=str)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return path
