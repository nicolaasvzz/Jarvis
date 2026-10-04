import json
from datetime import datetime

from investment_bot.config import BotConfig
from investment_bot.jarvis_status import build_status, write_status


def config(tmp_path):
    return BotConfig(raw={
        "live": {"state_file": str(tmp_path / "live_state.json"), "starting_cash": 1000},
        "learning": {"auto_tune": True, "memory_file": str(tmp_path / "learned.json")},
    })


def test_nothing_yet_is_just_a_timestamp(tmp_path):
    assert list(build_status(config(tmp_path))) == ["Updated"]


def test_summarises_paper_trading_and_backtest_memory(tmp_path):
    (tmp_path / "live_state.json").write_text(json.dumps({
        "cash": 500.0,
        "positions": [{"symbol": "AAPL", "qty": 2, "avg_price": 200, "last_price": 260,
                       "stop_price": 190, "opened_at": "2026-10-01 10:00:00"}],
        "equity_curve": [["2026-10-01 10:00:00", 1000], ["2026-10-04 10:00:00", 1020]],
        "trades": [
            {"symbol": "MSFT", "direction": -1, "qty": 1, "entry_price": 10, "exit_price": 12,
             "exit_time": "2026-10-03 15:00:00", "pnl": -2, "reason": "stop loss"},
            {"symbol": "SPY", "direction": 1, "qty": 1, "entry_price": 10, "exit_price": 15,
             "exit_time": "2026-10-04 15:00:00", "pnl": 5, "reason": "signal flat"},
        ],
        "halted": False,
        "learning": {"weights": {"SmaCross": 0.6, "RsiReversion": 0.4}},
    }), encoding="utf-8")
    (tmp_path / "learned.json").write_text(json.dumps({
        "overrides": {"strategy.long_only": True, "strategy.threshold": 0.3},
        "rounds": [{"number": 1, "data_end": "2026-10-01", "lessons": ["Shorts lost $7."],
                    "tested": 11, "adopted": {"description": "long-only on"},
                    "full_run": {"total_return": -0.0173, "max_drawdown": -0.0867,
                                 "gross_loss": -38168.3, "num_trades": 520}}],
    }), encoding="utf-8")
    s = build_status(config(tmp_path), now=datetime(2026, 10, 4, 18, 0))
    assert s["Equity"] == 1020.0 and s["Return %"] == 2.0
    assert (s["Open positions"], s["Trades today"], s["P&L today"]) == (1, 1, 5.0)
    assert s["Positions"][0]["P&L"] == 120.0 and s["Positions"][0]["Side"] == "LONG"
    assert s["Recent trades"][0]["Side"] == "SHORT"
    assert s["Equity curve"][-1] == ["2026-10-04 10:00", 1020.0]
    assert s["Learned from backtests"] == {"Long-only": "on", "Entry threshold": 0.3}
    assert s["Backtests"][0]["Losses"] == 38168.3
    assert s["What the last backtest taught it"][-1] == {"Lesson": "Kept: long-only on."}
    # Plain values first: the page turns those into tiles at the top.
    keys = list(s)
    assert keys.index("Halted") < keys.index("Equity curve")


def test_write_never_raises(tmp_path):
    path = write_status(config(tmp_path), tmp_path / "out.json")
    assert json.loads(path.read_text(encoding="utf-8"))["Updated"]
    assert write_status(config(tmp_path), tmp_path / "missing" / "dir" / "out.json") is None
