# Investment Bot 🤖📈

A multi-strategy automated trading bot with an event-driven backtester,
portfolio-level risk management, parameter optimization with walk-forward
validation, and a live paper/real trading loop — all driven from one YAML
config and a single CLI.

> ⚠️ **Not financial advice.** Backtests systematically overstate live
> results. This project defaults to simulated data and simulated money, and
> that is where you should stay until you have long, boring proof otherwise.
> Never trade money you can't afford to lose.

## What's inside

- **5 strategies, one ensemble.** Trend (SMA cross, MACD momentum, Donchian
  breakout) and mean-reversion (Bollinger z-score fade, RSI extremes)
  strategies vote with conviction-weighted scores; a configurable threshold,
  long-only switch, and volatility veto gate the net signal.
- **Bias-controlled backtester.** Signals are computed on bar *t*'s close and
  filled at bar *t+1*'s open with slippage + commission — the bot never sees
  a price before it trades on it. Stops are checked intrabar against
  high/low, with gap-through-stop fills at the open.
- **Real risk management.** Volatility-targeted position sizing (fixed
  fraction of equity at risk per trade, stop distance in ATR multiples),
  ATR trailing stops, optional take-profits, per-position weight caps, gross
  exposure caps, and a max-drawdown circuit breaker that liquidates
  everything and halts.
- **Optimization that keeps you honest.** Grid search any strategy's
  parameters, then validate with rolling walk-forward analysis
  (train in-sample → test out-of-sample → roll forward).
- **Live loop with persistent state.** Paper broker by default (simulated
  fills), with an optional [Alpaca](https://alpaca.markets) adapter that
  points at Alpaca's *paper* endpoint unless you explicitly configure the
  live one. Portfolio state (cash, positions, stops, high-water mark)
  survives restarts, so it also runs fine as a cron job with `--once`.
- **Reports.** Rich terminal output plus a self-contained HTML report with
  SVG equity/drawdown charts — no plotting libraries, opens anywhere.
- **Three data sources.** Deterministic regime-switching synthetic markets
  (works offline — the default), Yahoo Finance daily bars (free, cached on
  disk), or your own CSVs.

## Quickstart — run it locally, free

One command. First run builds a virtualenv and installs dependencies; after
that it starts immediately. Costs nothing to run — no hosting, no API keys,
no data fees.

**Windows (PowerShell):**

```powershell
.\run-local.ps1                 # live paper trading, terminal dashboard
.\run-local.ps1 -Mode backtest  # backtest + HTML report
.\run-local.ps1 -Mode api       # HTTP API on :8000, for a web dashboard
.\run-local.ps1 -Mode once      # one cycle, then exit (Task Scheduler)
```

**macOS / Linux:**

```bash
./run-local.sh                  # live paper trading, terminal dashboard
./run-local.sh backtest
./run-local.sh api
./run-local.sh once             # one cycle, then exit (cron)
```

Settings live in [`config.local.yaml`](config.local.yaml): real Yahoo prices,
simulated money, state saved next to the script so positions survive
restarts. No internet? Change `data.source` to `synthetic` and it runs
entirely offline.

### The built-in dashboard

`api` mode also serves a full dashboard at **http://localhost:8000** — same
dark glass styling as the AgencyOS trading tab, no build step, no CDN, works
offline:

- **Dashboard** — balance, P&L today, trades today, win rate; win-rate donut
  with drawdown / Sharpe / best symbol; live open positions with LONG/SHORT
  badges and running P&L; order history
- **Equity** — equity curve and drawdown charts with 1D/1W/1M/ALL ranges and
  hover inspection
- **Trades** — every closed trade
- **Controls** — start / halt / abort, the active risk parameters, watched
  symbols, and a live system log showing each strategy's vote per trade

It polls every 2 seconds and warns if the bot goes away.

### Reaching it from your phone

Run `api` mode, then expose it with a free [Cloudflare
Tunnel](https://developers.cloudflare.com/cloudflare-tunnel/) in a second
window:

```bash
cloudflared tunnel --url http://localhost:8000
```

Point your dashboard's `INVESTMENT_BOT_URL` at the URL it prints. Quick
tunnels get a new random URL each restart; a named tunnel (free Cloudflare
account) gives you a permanent one.

## Manual usage

```bash
pip install -r requirements.txt

# Backtest the default ensemble on synthetic data (no network, no keys):
python -m investment_bot backtest

# Same, with an HTML report:
python -m investment_bot backtest --html report.html

# Real market data instead (free, no API key):
cp config.example.yaml config.yaml   # then set data.source: yahoo
python -m investment_bot backtest -c config.yaml

# Optimize a strategy's parameters...
python -m investment_bot optimize --strategy sma_cross \
    --grid "fast=10,20,50 slow=100,200"

# ...then check it survives out-of-sample:
python -m investment_bot optimize --strategy sma_cross \
    --grid "fast=10,20,50 slow=100,200" --walk-forward

# Paper-trade the ensemble (simulated fills, state saved to disk):
python -m investment_bot trade            # loop, Ctrl-C to stop
python -m investment_bot trade --once     # single cycle (cron-friendly)

# List available strategies and their parameters:
python -m investment_bot strategies

# Serve the dashboard HTTP API (see "Dashboard integration"):
python -m investment_bot serve --port 8000
```

## Dashboard integration

`investment-bot serve` exposes the trading loop over HTTP for a web UI:

| Endpoint | Method | Purpose |
|---|---|---|
| `/stats` | GET | balance, P&L today, win rate, open positions, today's trades, max drawdown, Sharpe, best pair |
| `/logs` | GET/POST | last log lines / append external lines |
| `/control` | POST | `{"command": "START" \| "STOP" \| "ABORT"}` (ABORT liquidates everything) |
| `/health` | GET | liveness check |

The response shapes match the AgencyOS (Web-Design-Auto) trading tab: set
`INVESTMENT_BOT_URL=http://localhost:8000` for that dashboard's server and
its `/api/bybit/*` routes proxy straight to this bot. State is shared with
the `trade` command via the same `live_state.json`.

## Configuration

Everything lives in one YAML file (see [`config.example.yaml`](config.example.yaml)
for the full annotated reference). Every key has a sane default; an empty
config is valid.

```yaml
data:
  source: synthetic          # synthetic | yahoo | csv

universe: [AAPL, MSFT, NVDA, AMZN, GOOG, META, TSLA, SPY]

strategy:
  threshold: 0.25            # |weighted vote| needed to take a position
  long_only: false
  max_volatility: 0.80       # veto entries when 20d annualized vol exceeds this
  members:
    - {name: sma_cross, weight: 1.0, params: {fast: 20, slow: 100}}
    - {name: macd,      weight: 1.0}
    - {name: breakout,  weight: 1.5, params: {entry_window: 55, exit_window: 20}}
    - {name: bollinger, weight: 0.75}
    - {name: rsi,       weight: 0.75}

risk:
  risk_per_trade: 0.01       # 1% of equity at risk between entry and stop
  atr_stop_multiple: 3.0     # stop (and trail) distance in ATRs
  max_position_weight: 0.20  # any one position ≤ 20% of equity
  max_gross_exposure: 1.0    # no leverage
  max_drawdown: 0.25         # kill switch: liquidate + halt at −25% from peak
```

## Architecture

```
investment_bot/
├── data/            # feeds: synthetic (regime-switching GBM), Yahoo, CSV
├── indicators.py    # SMA, EMA, RSI, MACD, Bollinger, ATR, Donchian, z-score
├── strategies/      # Strategy ABC, 5 implementations, weighted Ensemble
├── risk.py          # sizing, stops, exposure caps, drawdown circuit breaker
├── portfolio.py     # cash/position ledger, trade log, equity curve
├── broker/          # ExecutionModel (slippage+commission), Paper, Alpaca
├── backtest/        # event-driven engine, metrics, grid/walk-forward optimizer
├── live.py          # persistent live/paper loop with rich dashboard
├── report.py        # terminal + self-contained HTML/SVG reports
└── cli.py           # backtest / optimize / trade / strategies
```

The same `Strategy` and `RiskEngine` code paths drive both the backtester
and the live loop, so what you backtest is what you trade. Strategies are
stateless (all state lives in the price history window), which keeps the two
modes behaviorally identical and trivially testable.

### Adding a strategy

Subclass `Strategy`, declare parameters as dataclass fields, implement
`warmup` and `signal()`, and register it:

```python
from dataclasses import dataclass
from investment_bot.strategies import REGISTRY
from investment_bot.strategies.base import Signal, Strategy, FLAT

@dataclass
class MyEdge(Strategy):
    lookback: int = 10

    @property
    def warmup(self) -> int:
        return self.lookback + 1

    def signal(self, history) -> Signal:
        if history["close"].iloc[-1] > history["close"].iloc[-self.lookback]:
            return Signal(1, 0.8, "price up over lookback")
        return FLAT

REGISTRY["my_edge"] = MyEdge
```

It's now available in ensemble configs and `optimize --strategy my_edge`.

## Going live (read this first)

The `trade` command uses the simulated paper broker unless you opt into
Alpaca (`live.broker: alpaca` plus `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`),
and even then it targets Alpaca's **paper-trading** endpoint until you
explicitly set `ALPACA_BASE_URL` to the live API. That's three deliberate
steps between you and real money — keep them in that order:

1. Backtest, then walk-forward validate (in-sample results alone are noise).
2. Paper trade for months, through at least one ugly market stretch.
3. Only then consider small real size, with the circuit breaker configured.

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest tests/ -q     # 49 tests: indicators, ledger, risk,
                               # strategies, engine, optimizer
```
