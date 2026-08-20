"""HTTP API for driving the bot from a dashboard.

Endpoints:
    GET  /         -> the built-in dashboard (HTML)
    GET  /health   -> {ok: true}
    GET  /stats    -> account/positions/trades snapshot for a dashboard
    GET  /equity   -> {points: [[iso8601, equity], ...]} for the chart
    GET  /logs     -> {logs: [...last 200 lines...]}
    POST /logs     -> {lines: [...]} append external log lines
    POST /control  -> {command: START | STOP | ABORT}

The response shapes match the AgencyOS trading tab (`/api/bybit/*` proxy):
stats returns balance, pnl_today, winRate, positions, recent_trades, running,
plus max_drawdown / sharpe / best_pair extras.

Trading runs in a background thread using the same LiveTrader as the CLI
`trade` command (paper broker unless configured otherwise), so dashboard
state and `trade` state are interchangeable.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd
from rich.console import Console

from .backtest.metrics import compute_metrics
from .broker.base import Broker
from .config import BotConfig
from .live import LiveTrader


DASHBOARD_HTML = Path(__file__).parent / "static" / "dashboard.html"


class _LogBuffer:
    """File-like sink for rich Console that keeps the last N plain-text lines."""

    def __init__(self, maxlen: int = 200):
        self.lines: deque[str] = deque(maxlen=maxlen)
        self._partial = ""
        self.lock = threading.Lock()

    def write(self, text: str) -> int:
        with self.lock:
            self._partial += text
            while "\n" in self._partial:
                line, self._partial = self._partial.split("\n", 1)
                if line.strip():
                    self.lines.append(f"[{pd.Timestamp.now():%H:%M:%S}] {line.rstrip()}")
        return len(text)

    def flush(self) -> None:
        pass

    def append(self, line: str) -> None:
        with self.lock:
            self.lines.append(f"[{pd.Timestamp.now():%H:%M:%S}] {line}")

    def snapshot(self) -> list[str]:
        with self.lock:
            return list(self.lines)


def _money(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.2f}"


class BotService:
    """Owns the trader, the background loop, and stats formatting."""

    def __init__(self, config: BotConfig, broker: Broker):
        self.config = config
        self.logs = _LogBuffer()
        console = Console(file=self.logs, force_terminal=False, width=200, highlight=False)
        self.trader = LiveTrader(
            config=config,
            feed=config.build_feed(),
            strategy=config.build_strategy(),
            broker=broker,
            console=console,
            show_dashboard=False,
        )
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.poll_seconds = max(config.live_settings["poll_minutes"] * 60, 5)

    # ---------------- lifecycle ----------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def start(self) -> str:
        if self.running:
            return "Bot is already running."
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="trading-loop", daemon=True)
        self._thread.start()
        self.logs.append("SYSTEM: trading loop started")
        return "Bot started."

    def stop(self) -> str:
        if not self.running:
            return "Bot is not running."
        self._stop.set()
        self.logs.append("SYSTEM: trading loop stopping (positions kept)")
        return "Bot stopping; open positions kept."

    def abort(self) -> str:
        was_running = self.running
        self._stop.set()
        with self.lock:
            self.trader.liquidate_all("dashboard abort")
        self.logs.append("SYSTEM: ABORT — all positions liquidated")
        return ("Bot stopped and " if was_running else "") + "all positions liquidated."

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with self.lock:
                    self.trader.run_cycle()
            except Exception as exc:
                self.logs.append(f"ERROR: cycle failed: {exc}")
            self._stop.wait(self.poll_seconds)

    # ---------------- stats ----------------

    def stats(self) -> dict:
        with self.lock:
            portfolio = self.trader.portfolio
            equity = portfolio.equity
            trades = list(portfolio.trades)
            positions = [self._format_position(p) for p in portfolio.positions.values()]
            curve = portfolio.equity_series()
            learning = self.trader.learning_summary()

        midnight = pd.Timestamp.now().normalize()
        baseline = equity
        if len(curve) > 0:
            prior = curve[curve.index < midnight]
            baseline = float(prior.iloc[-1]) if len(prior) else float(curve.iloc[0])

        wins = sum(1 for t in trades if t.pnl > 0)
        win_rate = f"{wins / len(trades) * 100:.1f}%" if trades else "0.0%"

        todays = [t for t in trades if t.exit_time >= midnight]
        recent = [self._format_trade(t, i) for i, t in enumerate(reversed(todays[-20:]))]
        # Every closed trade, newest first — the dashboard's history panels use
        # this; `recent_trades` stays today-only for the "trades today" counter.
        history = [self._format_trade(t, i) for i, t in enumerate(reversed(trades[-200:]))]

        extras: dict = {"max_drawdown": None, "sharpe": None, "best_pair": None}
        if len(curve) >= 2:
            metrics = compute_metrics(curve, trades)
            extras["max_drawdown"] = f"{metrics['max_drawdown']:.2%}"
            extras["sharpe"] = f"{metrics['sharpe']:.2f}"
        if trades:
            by_symbol: dict[str, float] = {}
            for t in trades:
                by_symbol[t.symbol] = by_symbol.get(t.symbol, 0.0) + t.pnl
            extras["best_pair"] = max(by_symbol, key=by_symbol.get)

        return {
            "success": True,
            "running": self.running,
            "universe": self.config.universe,
            "balance": round(equity, 2),
            "pnl_today": round(equity - baseline, 2),
            "winRate": win_rate,
            "positions": positions,
            "recent_trades": recent,
            "all_trades": history,
            "trades_today": len(todays),
            "learning": learning,
            **extras,
        }

    def equity_points(self) -> list[list]:
        """The equity curve as [iso8601, value] pairs for charting."""
        with self.lock:
            curve = self.trader.portfolio.equity_series()
        return [[ts.isoformat(), round(float(v), 2)] for ts, v in curve.items()]

    @staticmethod
    def _format_position(pos) -> dict:
        pnl = pos.unrealized_pnl
        basis = abs(pos.qty) * pos.avg_price
        pct = pnl / basis * 100 if basis else 0.0
        return {
            "id": pos.symbol,
            "asset": pos.symbol,
            "type": "LONG" if pos.qty > 0 else "SHORT",
            "entry": f"{pos.avg_price:,.2f}",
            "current": f"{pos.last_price:,.2f}",
            "pnl": _money(pnl),
            "pnlPercent": f"{pct:+.2f}%",
            "isPositive": pnl >= 0,
        }

    @staticmethod
    def _format_trade(trade, index: int) -> dict:
        return {
            "id": f"{trade.symbol}-{trade.exit_time:%Y%m%d%H%M%S}-{index}",
            "asset": trade.symbol,
            "type": "LONG" if trade.direction > 0 else "SHORT",
            "time": f"{trade.exit_time:%b %d %H:%M}",
            "profit": _money(trade.pnl),
            "isWin": trade.pnl > 0,
        }


def make_handler(service: BotService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence default per-request stderr noise
            pass

        def _send_html(self, html: str, status: int = 200) -> None:
            body = html.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return {}

        def do_OPTIONS(self):
            self._send({})

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                try:
                    self._send_html(DASHBOARD_HTML.read_text(encoding="utf-8"))
                except OSError:
                    self._send({"error": "dashboard.html missing"}, 500)
            elif path == "/health":
                self._send({"ok": True})
            elif path == "/stats":
                try:
                    self._send(service.stats())
                except Exception as exc:
                    self._send({"success": False, "error": str(exc)}, 500)
            elif path == "/equity":
                self._send({"points": service.equity_points()})
            elif path == "/logs":
                self._send({"logs": service.logs.snapshot()[-40:]})
            else:
                self._send({"error": "not found"}, 404)

        def do_POST(self):
            if self.path == "/logs":
                body = self._read_body()
                for line in body.get("lines") or []:
                    service.logs.append(str(line))
                self._send({"success": True})
            elif self.path == "/control":
                command = str(self._read_body().get("command", "")).upper()
                if command == "START":
                    self._send({"success": True, "message": service.start()})
                elif command == "STOP":
                    self._send({"success": True, "message": service.stop()})
                elif command == "ABORT":
                    self._send({"success": True, "message": service.abort()})
                else:
                    self._send({"success": False, "error": f"unknown command {command!r}"}, 400)
            else:
                self._send({"error": "not found"}, 404)

    return Handler


def serve(config: BotConfig, broker: Broker, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the API server (blocking). Ctrl-C stops it."""
    service = BotService(config, broker)
    server = ThreadingHTTPServer((host, port), make_handler(service))
    display_host = "localhost" if host in ("0.0.0.0", "127.0.0.1") else host
    print(f"\n  Dashboard:  http://{display_host}:{port}\n")
    print(f"  API listening on http://{host}:{port} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        service.stop()
        server.shutdown()
