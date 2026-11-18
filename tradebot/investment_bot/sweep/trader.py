"""Trade the sweep setup on the MetaTrader 5 terminal (the demo account).

Every few seconds it checks whether a lower-chart candle has closed for a
profile. When one has, it reruns the engine on each symbol's recent candles and
places any order the engine made on that candle:

* a **limit order** at the entry, with the stop and target attached on the
  server, so they hold even if this program stops (a market order instead if
  price is already through the entry and the trade still pays ``min_rr``);
* **sized** to risk ``risk`` of the account. If even the smallest lot would
  risk more than ``max_risk``, the trade is skipped: the account is too small
  for that stop;
* **managed** here: half comes off at ``partial_r`` and the stop moves to the
  entry, orders the setup no longer wants are cancelled, and trades past the
  time limit are closed.

Only proven profiles trade (see ``prove.py``). ``also_unproven`` lets unproven
ones trade too, for forward testing, and only on a demo account. An unproven
profile trades its latest hand-tuned version (``sweep_champions.json``, from the
workbench), not the untuned defaults.
A 10% drop from the peak halts everything.

It never logs in: open MetaTrader 5, log in, and turn on **Algo Trading**.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import data
from .engine import DEFAULT, PROFILES, TF_SECONDS, Engine, Params
from .bench import load_champions as tuned_versions
from .prove import load_champions, params_of

STATE_FILE = "sweep_trader.json"
MAGIC = 20290616
LOOKBACK = {"htf": 800, "mtf": 2000, "ltf": 3000}


@dataclass
class TraderConfig:
    risk: float = 0.01          # share of the account a trade risks
    max_risk: float = 0.03      # skip a trade if the smallest lot would risk more than this
    max_open: int = 3           # open trades and orders, all profiles together
    halt_drawdown: float = 0.10 # stop everything after this drop from the peak
    also_unproven: bool = False # demo only: trade profiles that haven't proven themselves
    profiles: tuple[str, ...] = tuple(PROFILES)
    symbols: tuple[str, ...] = tuple(data.UNIVERSE)
    poll_seconds: int = 15


class SweepTrader:
    def __init__(self, cfg: TraderConfig, say: Callable[[str], None] = print,
                 state_file: str | Path = STATE_FILE, after_save: Callable[[], Any] | None = None):
        self.cfg, self.say, self.path = cfg, say, Path(state_file)
        self.after_save = after_save
        self.state = self._load()
        self.mt5 = data.connect()
        self.specs: dict[str, data.Spec] = {}
        self.last_bar: dict[str, int] = {}

    # ----------------------------------------------------------- state ----
    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"orders": {}, "positions": {}, "closed": [], "log": [], "equity": [],
                    "peak": 0.0, "halted": False}

    def save(self) -> None:
        self.state["log"] = self.state["log"][-300:]
        self.state["equity"] = self.state["equity"][-3000:]
        self.state["closed"] = self.state["closed"][-500:]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, default=str), encoding="utf-8")
        tmp.replace(self.path)
        if self.after_save:
            try:
                self.after_save()
            except Exception:  # the dashboard must never stop trading
                pass

    def log(self, symbol: str, what: str) -> None:
        line = {"at": datetime.now().isoformat(timespec="seconds"), "symbol": symbol, "what": what}
        self.state["log"].append(line)
        self.say(f"[{line['at'][11:19]}] {symbol}: {what}")

    # ---------------------------------------------------------- choices ----
    def plans(self) -> dict[str, Params]:
        """The profiles that may trade, and the version each trades."""
        champs = load_champions()
        out = {p: params_of(champs[p]) for p in self.cfg.profiles if p in champs}
        if self.cfg.also_unproven:
            if self.mt5.account_info().trade_mode != self.mt5.ACCOUNT_TRADE_MODE_DEMO:
                raise RuntimeError("also_unproven is for demo accounts only.")
            tuned = tuned_versions()
            for p in self.cfg.profiles:
                out.setdefault(p, tuned.get(p, DEFAULT))
        return out

    def spec(self, symbol: str) -> data.Spec | None:
        if symbol not in self.specs:
            sp = data.spec(self.mt5, symbol)
            if sp is None:
                return None
            self.specs[symbol] = sp
        return self.specs[symbol]

    # ------------------------------------------------------------ loop ----
    def run(self, once: bool = False) -> None:
        plans = self.plans()
        if not plans:
            self.log("-", "no profile is proven yet, so nothing trades. Run the backtest "
                          "(sweep-prove), or pass --also-unproven to forward-test on the demo.")
        else:
            self.log("-", "trading " + ", ".join(f"{p} ({v.label()})" for p, v in plans.items()))
        while True:
            try:
                self.cycle(plans, force=once)
            except Exception as e:  # keep going; the terminal reconnects
                self.log("-", f"error: {e}")
                time.sleep(5)
                try:
                    self.mt5 = data.connect()
                except RuntimeError:
                    pass
            self.save()
            if once:
                return
            time.sleep(self.cfg.poll_seconds)

    def cycle(self, plans: dict[str, Params], force: bool = False) -> None:
        mt5 = self.mt5
        acct = mt5.account_info()
        eq = float(acct.equity)
        self.state["account"] = {"login": acct.login, "server": acct.server, "currency": acct.currency,
                                 "balance": acct.balance, "equity": eq, "leverage": acct.leverage,
                                 "demo": acct.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO}
        self.state["algo_trading"] = bool(mt5.terminal_info().trade_allowed)
        self.state["peak"] = max(float(self.state.get("peak") or 0), eq)
        now = datetime.now().isoformat(timespec="minutes")
        if not self.state["equity"] or self.state["equity"][-1][0] != now:
            self.state["equity"].append([now, round(eq, 2)])
        if eq < self.state["peak"] * (1 - self.cfg.halt_drawdown) and not self.state["halted"]:
            self.state["halted"] = True
            self.log("-", f"HALTED: equity {eq:.2f} is {self.cfg.halt_drawdown:.0%} under the peak. "
                          "Cancelling orders; open trades keep their stops.")
            for tk in list(self.state["orders"]):
                self._cancel(int(tk), "halted")
        self._sync()
        self._manage()
        if self.state["halted"]:
            return
        for p, params in plans.items():
            pf = PROFILES[p]
            for sym in self.cfg.symbols:
                key = f"{p}:{sym}"
                head = data.rates(mt5, sym, pf.ltf, 2)   # just the last closed candle
                if not len(head):
                    continue
                last = int(head["time"].iat[-1])
                if self.last_bar.get(key) == last and not force:
                    continue
                first = key not in self.last_bar
                self.last_bar[key] = last
                if first and not force:
                    continue  # on start, only act on candles that close from now on
                bars = data.rates(mt5, sym, pf.ltf, LOOKBACK["ltf"])
                if len(bars) >= 500:
                    self._decide(p, params, sym, bars)

    def _decide(self, p: str, params: Params, sym: str, ltf) -> None:
        pf, mt5 = PROFILES[p], self.mt5
        sp = self.spec(sym)
        if sp is None:
            return
        htf = data.rates(mt5, sym, pf.htf, LOOKBACK["htf"])
        mtf = data.rates(mt5, sym, pf.mtf, LOOKBACK["mtf"])
        eng = Engine(sym, pf, htf, mtf, ltf, params, sp.point)
        eng.run()
        last = len(ltf) - 1
        fresh = [o for o in eng.orders if o.signal_i == last]
        if not fresh:
            return
        od = fresh[-1]
        busy = {v["symbol"] for v in self.state["orders"].values()} | \
               {v["symbol"] for v in self.state["positions"].values()}
        if sym in busy:
            self.log(sym, f"{p} setup skipped: already in a trade or order on {sym}")
            return
        if len(self.state["orders"]) + len(self.state["positions"]) >= self.cfg.max_open:
            self.log(sym, f"{p} setup skipped: {self.cfg.max_open} trades/orders already open")
            return
        self._place(p, params, sym, sp, od.side, od.entry, od.stop, od.target, od.why,
                    expires=int(time.time()) + pf.entry_valid * TF_SECONDS[pf.ltf])

    # ---------------------------------------------------------- orders ----
    def _lots(self, sp: data.Spec, risk_dist: float) -> float | None:
        eq = float(self.mt5.account_info().equity)
        per_lot = sp.money_per_unit(1.0) * risk_dist
        if per_lot <= 0:
            return None
        lots = eq * self.cfg.risk / per_lot
        step = sp.vol_step or 0.01
        lots = int(lots / step + 1e-9) * step
        if lots < sp.vol_min:
            if sp.vol_min * per_lot <= eq * self.cfg.max_risk:
                lots = sp.vol_min
            else:
                return None
        return round(min(lots, sp.vol_max), 8)

    def _filling(self, sym: str) -> int:
        mt5 = self.mt5
        fm = mt5.symbol_info(sym).filling_mode
        if fm & 1:
            return mt5.ORDER_FILLING_FOK
        if fm & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def _place(self, p: str, params: Params, sym: str, sp: data.Spec, side: int, entry: float,
               stop: float, target: float, why: str, expires: int) -> None:
        mt5 = self.mt5
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
            return
        price_now = tick.ask if side == 1 else tick.bid
        market = (price_now <= entry) if side == 1 else (price_now >= entry)
        px = price_now if market else entry
        risk_dist = (px - stop) * side
        if risk_dist <= 0 or (target - px) * side / risk_dist < params.min_rr:
            self.log(sym, f"{p} setup skipped: price already moved (no longer {params.min_rr:g}R)")
            return
        lots = self._lots(sp, risk_dist)
        if lots is None:
            self.log(sym, f"{p} setup skipped: even {sp.vol_min} lots would risk more than "
                          f"{self.cfg.max_risk:.0%} of the account with this stop")
            return
        if not self.state["algo_trading"]:
            self.log(sym, f"{p} setup found but Algo Trading is off in MT5: turn it on to let it trade "
                          f"({'buy' if side == 1 else 'sell'} {px:.5g}, stop {stop:.5g}, target {target:.5g})")
            return
        d = sp.digits
        req = {
            "symbol": sym, "volume": lots, "sl": round(stop, d), "tp": round(target, d),
            "magic": MAGIC, "comment": f"sweep:{p}", "type_filling": self._filling(sym),
            "deviation": 20, "type_time": mt5.ORDER_TIME_GTC,
        }
        if market:
            req.update(action=mt5.TRADE_ACTION_DEAL, price=px,
                       type=mt5.ORDER_TYPE_BUY if side == 1 else mt5.ORDER_TYPE_SELL)
        else:
            req.update(action=mt5.TRADE_ACTION_PENDING, price=round(entry, d),
                       type=mt5.ORDER_TYPE_BUY_LIMIT if side == 1 else mt5.ORDER_TYPE_SELL_LIMIT)
        res = mt5.order_send(req)
        if res is None or res.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
            self.log(sym, f"{p} order refused: {getattr(res, 'retcode', None)} "
                          f"{getattr(res, 'comment', mt5.last_error())}")
            return
        info = {"symbol": sym, "profile": p, "side": side, "entry": px, "stop": stop, "target": target,
                "lots": lots, "risk_dist": risk_dist, "partial_r": params.partial_r, "half": False,
                "why": why, "placed": datetime.now().isoformat(timespec="seconds"),
                "expires": expires, "max_hold_s": PROFILES[p].max_hold * TF_SECONDS[PROFILES[p].ltf]}
        if market:
            info["opened"] = info["placed"]
            self.state["positions"][str(res.order)] = info
        else:
            self.state["orders"][str(res.order)] = info
        self.log(sym, f"{p}: {'bought' if market and side == 1 else 'sold' if market else 'limit'} "
                      f"{'buy' if side == 1 else 'sell'} {lots} lots at {px:.5g}, stop {stop:.5g}, "
                      f"target {target:.5g}. {why}")

    def _cancel(self, ticket: int, why: str) -> None:
        mt5 = self.mt5
        res = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
        info = self.state["orders"].pop(str(ticket), None)
        if info:
            self.log(info["symbol"], f"order cancelled ({why})" +
                     ("" if res and res.retcode == mt5.TRADE_RETCODE_DONE else f", terminal says {getattr(res, 'retcode', '?')}"))

    # ------------------------------------------------- what the terminal says ----
    def _sync(self) -> None:
        """Move filled orders to positions and closed positions to history."""
        mt5 = self.mt5
        live_orders = {str(o.ticket) for o in (mt5.orders_get() or []) if o.magic == MAGIC}
        live_pos = {str(q.ticket): q for q in (mt5.positions_get() or []) if q.magic == MAGIC}
        for tk, info in list(self.state["orders"].items()):
            if tk in live_orders:
                if time.time() > info["expires"]:
                    self._cancel(int(tk), "waited too long for the entry")
                continue
            self.state["orders"].pop(tk)
            pos = live_pos.get(tk)  # on hedging accounts the position keeps the order's ticket
            if pos is not None:
                info["opened"] = datetime.now().isoformat(timespec="seconds")
                info["entry"] = pos.price_open
                self.state["positions"][tk] = info
                self.log(info["symbol"], f"{info['profile']} order filled at {pos.price_open:.5g}")
            else:
                self.log(info["symbol"], f"{info['profile']} order gone (filled and closed, or removed)")
        for tk, info in list(self.state["positions"].items()):
            if tk in live_pos:
                info["profit"] = live_pos[tk].profit
                continue
            self.state["positions"].pop(tk)
            deals = mt5.history_deals_get(position=int(tk)) or []
            profit = sum(dd.profit + dd.commission + dd.swap for dd in deals)
            info["closed"] = datetime.now().isoformat(timespec="seconds")
            info["pnl"] = round(profit, 2)
            self.state["closed"].append(info)
            self.log(info["symbol"], f"{info['profile']} trade closed: {profit:+.2f}")

    def _manage(self) -> None:
        mt5 = self.mt5
        for tk, info in self.state["positions"].items():
            pos = next(iter(mt5.positions_get(ticket=int(tk)) or []), None)
            if pos is None:
                continue
            sym, s = info["symbol"], info["side"]
            tick = mt5.symbol_info_tick(sym)
            now_px = tick.bid if s == 1 else tick.ask
            opened = datetime.fromisoformat(info["opened"]).timestamp()
            if time.time() - opened > info["max_hold_s"]:
                self._close(pos, pos.volume, "time limit")
                continue
            if info["partial_r"] and not info["half"]:
                level = pos.price_open + s * info["partial_r"] * info["risk_dist"]
                if (now_px - level) * s >= 0:
                    sp = self.spec(sym)
                    half = int(pos.volume / 2 / sp.vol_step + 1e-9) * sp.vol_step
                    if half >= sp.vol_min:
                        self._close(pos, round(half, 8), f"half off at {info['partial_r']:g}R")
                    res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": pos.ticket,
                                          "symbol": sym, "sl": pos.price_open, "tp": pos.tp})
                    info["half"] = True
                    self.log(sym, "stop moved to entry" +
                             ("" if res and res.retcode == mt5.TRADE_RETCODE_DONE else " (terminal refused)"))

    def _close(self, pos, volume: float, why: str) -> None:
        mt5 = self.mt5
        tick = mt5.symbol_info_tick(pos.symbol)
        buy = pos.type == mt5.POSITION_TYPE_BUY
        res = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "position": pos.ticket,
            "volume": volume, "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if buy else tick.ask, "deviation": 20, "magic": MAGIC,
            "comment": "sweep close", "type_filling": self._filling(pos.symbol)})
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        self.log(pos.symbol, f"closed {volume} lots: {why}" + ("" if ok else f" (refused: {getattr(res, 'retcode', '?')})"))


def trade(cfg: TraderConfig | None = None, once: bool = False, say: Callable[[str], None] = print,
          after_save: Callable[[], Any] | None = None) -> None:
    SweepTrader(cfg or TraderConfig(), say, after_save=after_save).run(once=once)


__all__ = ["SweepTrader", "TraderConfig", "trade", "replace"]
