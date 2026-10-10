# Investment Bot 🤖📈

[![TradeBot](https://github.com/nicolaasvzz/Jarvis/actions/workflows/tradebot.yml/badge.svg)](https://github.com/nicolaasvzz/Jarvis/actions/workflows/tradebot.yml)

**Part of [Jarvis](../README.md).** The bot lives in Jarvis's `tradebot/` folder and comes set up as a project on Jarvis's Mothership, with buttons for every mode. It also runs on its own, as below. (It used to be its own repo, `nicolaasvzz/Investment_Bot`, now archived.)

A multi-strategy automated trading bot with an event-driven backtester,
portfolio-level risk management, parameter optimization with walk-forward
validation, and a live paper/real trading loop — all driven from one YAML
config and a single CLI. Comes in two parts: the full-featured **Python bot**
(this README) and a **C# / .NET rewrite** in [`TradeBot/`](#c-tradebot-net-rewrite).

> ⚠️ **Not financial advice.** Backtests systematically overstate live
> results. This project defaults to simulated data and simulated money, and
> that is where you should stay until you have long, boring proof otherwise.
> Never trade money you can't afford to lose.

## Get it

```bash
git clone https://github.com/nicolaasvzz/Jarvis.git
cd Jarvis/tradebot
```

…or use **Code → Download ZIP** on the GitHub page. You need Python 3.10+
(and the [.NET 10 SDK](https://dotnet.microsoft.com/download) only if you want
the C# bot). No API keys are needed to try it — it defaults to simulated data
and simulated money.

**Using and editing it.** You're free to download, read and run this code.
Changing or redistributing it needs the owner's permission — see
[LICENSE](LICENSE) and [CONTRIBUTING.md](CONTRIBUTING.md). Ask in an issue
first; once approved, changes come in through a reviewed pull request.

## What's inside

- **5 strategies, one ensemble.** Trend (SMA cross, MACD momentum, Donchian
  breakout) and mean-reversion (Bollinger z-score fade, RSI extremes)
  strategies vote with conviction-weighted scores; a configurable threshold,
  long-only switch, and volatility veto gate the net signal.
- **Learns from its mistakes.** The ensemble's weights are not fixed. Every
  closed trade scores the votes that argued for it, and every new daily bar
  scores each strategy's prior vote against the return that actually followed
  (multiplicative-weights / Hedge update). Strategies that keep being right
  gain influence; ones that keep being wrong lose it. A weight floor keeps
  every strategy voting so it can earn its way back, returns are capped so one
  wild day can't dominate, and a shrink term bounds how long a lesson is
  remembered (~43 days by default). Learned weights persist across restarts.
  Turn it off with `learning.enabled: false`.
- **Learns from every backtest.** With `learning.auto_tune: true`, each
  backtest ends with a review of its trades (where the money was lost: shorts
  vs longs, stop-outs, which symbols, flip-flopping) and a learning round: it
  tries small changes to the threshold, stops, sizing, volatility veto,
  long-only and the online-learned weights, each on the first 70% of history,
  and keeps at most one, only if it also lowers losses on the last 30%, which
  the choice never looked at (score = return + 1.5 x max drawdown, and no
  "learning" to just stop trading). What it keeps goes in `learned.json`, which
  the next backtest and paper trading start from, so the bot improves run
  after run. `investment-bot learned` shows what it learned and the run-by-run
  history; `investment-bot learned --reset` forgets it; `backtest --no-tune`
  skips one round.

- **A champ-set builder that reads three charts.** It masters the 4-hour,
  1-hour and 30-minute charts separately (the clearest indicators of each,
  look-alikes counted once), then builds one setup that trades only on the
  1-hour chart, when all three agree and a confidence model says the target is
  likely before the stop. Stop, target and time limit come from how past
  trades really moved. It's only called **proven** after winning in most of
  four later stretches of history by more than luck would explain. Stocks and
  crypto are built apart. The 1h trader can let agreeing news keep a trade
  open longer, and respects the day-trade limit on small stock accounts. See
  [The champ-set builder](#the-champ-set-builder).

- **An indicator lab.** 126 indicators (trend, momentum, volatility, volume,
  candle patterns) on 10-minute, 30-minute, 1-hour, 4-hour and daily candles,
  for the most-traded stocks and crypto on Alpaca. Round after round it scores
  every one, finds the pairs and trios that signal clearly together, builds
  packages of 5 to 50 of them, and keeps challenging the best one. The
  package trader then trades the winner on Alpaca. See
  [The indicator lab](#the-indicator-lab).

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
- **Trades** — every closed trade, surviving restarts
- **Strategy Learning** — each strategy's current weight, how often its votes
  have been right, and the most recent lesson the bot absorbed
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

## The champ-set builder

```powershell
.\run-local.ps1 -Mode champ               # build: 4h, 1h and 30m charts, stocks and crypto
.\run-local.ps1 -Mode package             # trade the champion setups on Alpaca paper
.\run-local.ps1 -Mode champ -Only crypto  # one class only (-Only stock / crypto)
```

(or `investment-bot champ [--for 3d] [--only stock|crypto] [--fresh]` and
`investment-bot trade-setup [--once] [--dry-run] [--only stock|crypto]`).
Jarvis's **Champ-set builder** and **Paper trading** buttons run these.

The bot trades only on the **1-hour chart**, and reads the **4-hour** chart
(the bigger picture) and the **30-minute** chart (the closer look) before it
does. Stocks and crypto are built separately: their costs differ about 8x
(3 bps a side against Alpaca's 25), and so do their hours.

**The charts.** All three are made from 10-minute candles. Stock candles
follow the trading day: they start at the 9:30 open, and the last one ends at
the 16:00 close (two 4-hour candles a day, 9:30-13:30 and 13:30-16:00, in any
season). Crypto candles start on the UTC hour. A 4-hour candle counts only
once it has closed. Bad prints are tamed first: Alpaca's crypto candles are
mostly quotes, and some carry one-candle spikes that snap straight back. So
no 10-minute candle may move more than 6 usual moves from the previous one.
That rule only looks back, so the builder and the trader see the same thing.

**1. Master each chart** (4h, then 1h, then 30m), each on its own candles:

- *scout*: score every indicator by how the price moved after it voted (30m:
  1-2 hours ahead, 1h: 2-4, 4h: 4-8);
- keep only readings that held up in both halves of training;
- *look-alikes count once*: indicators that move together (correlation 0.7
  or more) form a group, and only the group's clearest member stays, up to
  `champ.per_chart` (35) per chart, with every family represented;
- *the set*: start with the best of each family, add whichever helps most,
  and give each one an equal vote, followed or faded. Tuned weights don't hold
  up on new data. The set size that predicts best on the last third of
  training wins.

**2. Combine.** A trade is possible when the 1h set leans one way and the 4h
and 30m sets agree. For each way of gating that (how far the 1h must lean, how
firmly the others must agree), on training data only, the builder fits:

- *the stop, target and time limit*, from how far past winners dipped first
  (MAE) and how far they ran (MFE). The time limit is 2 to 6 hours, and stocks
  always close by the day's close. The target is capped by what the 4-hour
  chart says a few hours usually move;
- *the confidence model*: a logistic model of how often trades with this much
  agreement on each chart reached the target before the stop. Its weights show
  what each chart really adds (the report says, for example, "4h x1.6, 30m
  x0.9");
- *the confidence bar*: a little above break-even, or the one you pick.
  Confidence here means the chance of reaching the target before the stop.

**3. Judge.** Every candidate is tested on four stretches of history after
training. It's **proven** only if it made money on training, in at least 3 of
the 4 test windows and in all of them together, and if its daily results
there beat the **luck bar**. That's the t-statistic the best of that many
tries would reach by pure luck (the expected maximum behind Bailey and
Lopez de Prado's deflated Sharpe ratio). Every try ever made counts, kept in
`champ.json` until `--fresh`, so re-running doesn't make luck easier. The last
20% of history, the **final check**, is never used to choose anything and is
only reported. The previous champion is re-tested and competes too. A proven
champion's bet is the largest whose training drawdown stays inside
`lab.drawdown_budget`. An unproven one bets 2% and never trades real money.

**The 1h setup trader** decides when a 1-hour candle closes (crypto on the
hour, stocks at :30 New York) and watches its trades every 10 minutes: stop,
target, time limit, and for stocks the day's close (at 15:50). Trades it says
yes to are taken surest first, with half the bet at the confidence bar and the
full bet 15 points above it.

- **News can stretch a trade.** At its normal end, if the symbol's news mood
  agrees (past `research.signals.threshold`) and the 1h chart still leans its
  way, the trade stays open up to `champ.news_cap_hours` (72) x news strength
  x signal strength, never past the story's own horizon. Its target moves
  toward the news's predicted move (at most 3x), and its stop moves to
  break-even once it's half an ATR up. News turning against it closes it.
  Only a stretched stock trade is held overnight. Every stretched trade
  records what the normal exit would have made, and after
  `champ.news_judge_after` (50) of them, the trader stops stretching if
  stretching did worse. News comes from `news.db`, so run research alongside
  (`-Research watch`). Old news can't be honestly backtested: an AI rating old
  articles often already knows what happened next.
- **Small accounts.** A US stock account under $25,000 gets 3 day trades per
  5 business days (the Pattern Day Trader rule). Any stock trade here can
  become one, so each keeps one in reserve, and when Alpaca's count leaves
  none there are no new stock trades. Crypto isn't affected. Under $2,000
  there are no stock shorts.
- **Real money** needs `live.allow_real_money: true`, and then only proven
  setups trade. `champ.trade` picks the classes it trades.

## The indicator lab

The older engine: 10-minute decisions on packages of indicators from five
timeframes. It still works from the command line, but the Jarvis buttons now
run the champ-set builder above.

```powershell
.\run-local.ps1 -Mode lab -For 8h -Round 1h   # find the clearest package of indicators
```

(or `investment-bot lab --for 8h --round 1h [--until-done]`, `investment-bot
package` to see the result, `investment-bot trade-package [--once] [--dry-run]`
and `investment-bot check [--quick]`).

`--until-done` gives the lab a finish line: its challenge rounds also try
every indicator the champion hasn't had yet, and it stops once all have been
tried once. That is one try each, not every combination.

**Strategy and money.** `backtest`, `learn`, `lab` and `trade-package` take
`--style refine|fewer|more|custom [--custom "your words"] --money 10000`
(`run-local.ps1 -Style ... -Custom ... -Money ...`), plus `--confidence 70 --bet 10`
(percent; `-Confidence`/`-Bet`) to override any strategy's two numbers. `fewer` = 70% confidence
and 25% of the money a trade; `more` = 35% confidence, 5% a trade; `custom`
is read for numbers and words ("80% sure, bet 10%, no shorts, hold longer")
and the bot prints how it read them. The picked settings are pinned: learning
and the builder refine everything else around them (and keep them as the
current strategy); paper trading lays them over the champion. `--money` caps
what the trader uses (only its own positions count against it), and sets the
backtests' starting cash. See `investment_bot/style.py`.

**Reports.** Every backtest, learning session, lab, builder, test-mode run
and trading session keeps a dated page in `reports/`
(`2026-10-05_213015_backtest.html`), labelled in its head with a title, a
one-line summary and a good/bad tone. Jarvis's TradeBot page lists them all.

**Data.** Once a day it scans everything Alpaca trades (about 13,500 US
stocks and ETFs, plus crypto pairs) and keeps the most-traded:
`lab.stocks` (200) priced at $5 or more, and `lab.crypto` (20). It
downloads two years of 10-minute candles and longer daily ones, cached in
`data_cache/alpaca`, and later runs only fetch what's new. Every indicator
is computed on every timeframe into `data_cache/features`, capped at
`lab.max_feature_gb`. A bigger candle's value only counts once that candle
has closed, so nothing peeks ahead. Stocks need `ALPACA_API_KEY` and
`ALPACA_SECRET_KEY` in `.env`. Crypto candles don't.

**History is split three ways by time.** The first 60% (training) is where
it looks and chooses. The next 20% (held-out) only confirms or vetoes a
choice. The last 20% (final check) is never used to decide anything, so it
shows how a package does on candles it was never tuned on.

**Rounds**, each at most `--round` long:

1. **Scout.** Every indicator column is scored on how the price moved after
   it voted (1h, 4h, about a day ahead). Nothing is traded. The result is
   the scoreboard, with "follow" or "fade" for each indicator.
2. **Patterns.** Each half of training is re-scouted, and only readings that
   held up in both go forward. Every pair of the best 60 is then checked,
   plus the best trios: which agree in a way that's followed by a clear move.
3. **Build.** Packages are assembled from those indicators, at least one
   from each family and up to 50, net of trading costs, and backtested. The
   best on training that holds up held-out becomes the **champion**. The
   bot's original five-strategy setup is the bar to beat.
4. **Challenge, and every round after.** It looks at the indicators again
   on a fresh stretch, then tries changes to the champion. It can add,
   drop, swap or reweight an indicator, or move the entry bar, exit bar or
   stop. It can also slow down: hold longer, wait between trades, or need a
   signal to last a few candles. Bigger jumps are tried too: random
   packages, mixes with the top 10, and two changes at once. A change is
   kept only if it beats the champion on training *and* held-out.

Every package is judged at the same 10% trade size, so betting less on a
loser can't pass for an improvement. **How much** to bet is set
afterwards. Within a package, stronger agreement means a bigger trade
(half size at the entry bar, full size at full agreement). The champion's
full size is the largest that keeps its training drawdown inside
`lab.drawdown_budget`. A champion not yet confirmed on held-out trades at
the smallest size (2%). Costs: 3 bps a side for stocks, 25 for crypto
(Alpaca's taker fee).

Results go to `lab.json` after every round, so a later session carries on
from the champion. Jarvis's TradeBot page shows the session, the champion's
indicators, the scoreboard and the package trader's orders.

**The package trader** reloads the champion each cycle, so a lab running at
the same time can improve it. It trades stocks only while the market is
open, and crypto around the clock. Its rules are the same as the backtest:
trailing ATR stops checked each candle, exits when the score fades,
`cooldown`, `confirm`. Shorts are stocks-only and only easy-to-borrow ones,
in whole shares. It refuses Alpaca's live endpoint unless
`live.allow_real_money: true`. It stops opening trades after
`live.max_daily_loss` (3%) in a day, and halts at `risk.max_drawdown` from
the peak. Positions it didn't open are left alone.

## Manual usage

```bash
pip install -r requirements.txt     # or `pip install .` for an `investment-bot` command

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
└── cli.py           # backtest / optimize / trade / strategies / serve

tests/               # pytest suite
TradeBot/            # C# / .NET 10 worker service (Alpaca paper client + smoke test)
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

## C# TradeBot (.NET rewrite)

[`TradeBot/`](TradeBot) is a .NET 10 worker service that is taking over the
always-on part of the bot. Today it is a heartbeat `BackgroundService` plus a
connectivity smoke test against Alpaca's **paper** API — the strategies, risk
engine and backtester still live in the Python package above.

```bash
cd TradeBot
dotnet run                          # always-on worker, Ctrl-C to stop

# Bring your OWN Alpaca paper keys; user-secrets keeps them outside the repo.
dotnet user-secrets set "Alpaca:ApiKey" "<your paper key id>"
dotnet user-secrets set "Alpaca:SecretKey" "<your paper secret>"

dotnet run -- smoke-test --dry-run  # shows what it would do; sends nothing
dotnet run -- smoke-test            # asks to confirm, then buys and closes
                                    # ~$10 of BTC/USD on the paper account
```

The client refuses to run against anything except Alpaca's paper endpoint.

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
pip install -e ".[dev]"
python -m pytest tests -q      # indicators, ledger, risk, strategies, engine,
                               # optimizer, learning, live state, data cache
```

CI runs the tests on Python 3.10 and 3.13 and builds the C# project on every
push and pull request (see [CONTRIBUTING.md](CONTRIBUTING.md)).

### Performance

Backtest speed is dominated by pandas overhead in the per-bar indicator maths.
The hot paths (RSI, MACD, Donchian, and ATR in the engine) avoid rebuilding
pandas objects each bar, which halves backtest time without changing a single
trade — optimizations are checked by hashing every trade and the whole equity
curve before and after. The Yahoo feed caches downloads on disk (CSV, no extra
dependencies), so repeat runs don't hit the network.
