"""The sweep setup: the strategy from the two "Simple R2K - R10M" videos, as rules.

Three charts, one setup (for a long; a short is the mirror image):

1. **Target (higher chart).** Swing highs on the higher chart that price has
   not gone above since are *liquidity*: stops sit there and price is drawn to
   them. The nearest one above price is the target.
2. **Zones (middle chart).** The middle chart's structure must point up (its
   last close broke a swing high, not a swing low). A *demand zone* is the
   candle a strong move started from: the last down candle before an up candle
   whose body is at least ``disp_atr`` ATRs (or that up candle itself).
3. **Trigger (lower chart).** Price comes down into a fresh demand zone and
   takes out a recent swing low (the *sweep*: stops run, then price turns).
   Wait for the *confirmation*: a close above the last swing high. The candle
   the turn started from is the new demand zone. A limit order goes at its
   top, the stop just under the sweep's low, and the target is the higher
   chart's liquidity. Only if the target pays ``min_rr`` times the risk.
4. **Management.** Half comes off at ``partial_r`` times the risk and the stop
   moves to the entry. The rest rides to the target, the stop, or the time
   limit.

Everything is computed bar by bar, using only what was known when each bar
closed. Live trading reruns the same code on recent bars, so backtest and live
make the same decisions.

Prices are the broker's bid. A long buys at the ask (bid + spread) and sells
at the bid. Every trade pays at least ``cost_bp`` basis points of the price
both ways, even where the demo shows no spread.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

TF_SECONDS = {"M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


@dataclass(frozen=True)
class Profile:
    """Which three charts one version of the setup reads, and its time scale."""
    name: str
    htf: str
    mtf: str
    ltf: str
    arm_bars: int        # lower-chart bars from touching a zone to the confirmation
    entry_valid: int     # lower-chart bars a limit order waits
    max_hold: int        # lower-chart bars a trade may stay open
    words: str = ""


PROFILES: dict[str, Profile] = {
    "swing": Profile("swing", "D1", "H4", "H1", arm_bars=48, entry_valid=24, max_hold=480,
                     words="daily target, 4h zones, 1h trigger (days to weeks)"),
    "intraday": Profile("intraday", "H4", "H1", "M15", arm_bars=48, entry_valid=24, max_hold=192,
                        words="4h target, 1h zones, 15m trigger (hours to 2 days)"),
    "scalp": Profile("scalp", "H1", "M15", "M5", arm_bars=36, entry_valid=18, max_hold=144,
                     words="1h target, 15m zones, 5m trigger (minutes to hours)"),
}


@dataclass(frozen=True)
class Params:
    htf_k: int = 3            # a swing point beats this many candles each side (higher chart)
    mtf_k: int = 3            # ... middle chart
    ltf_k: int = 2            # ... lower chart
    disp_atr: float = 1.2     # a strong move: a candle body of at least this many ATRs
    zone_life: int = 120      # middle-chart candles a zone stays usable
    entry_frac: float = 0.0   # where in the entry candle to buy: 0 = its top, 0.5 = its middle
    stop_buf_atr: float = 0.1 # stop this many lower-chart ATRs beyond the sweep
    min_rr: float = 3.0       # the target must pay at least this many times the risk
    max_rr: float = 10.0      # targets further than this are pulled in to this
    partial_r: float = 2.0    # take half off here and move the stop to entry (0 = off)
    require_sweep: bool = True  # need the liquidity grab before the confirmation
    htf_filter: bool = False  # also need the higher chart's structure to agree
    cost_bp: float = 1.0      # least cost each way, in basis points of the price
    stop_slip: float = 0.25   # stops fill this share of the cost worse
    sides: int = 0            # 0 both ways, 1 longs only, -1 shorts only
    hour_from: int = 0        # only trigger on candles closing in these server hours
    hour_to: int = 24
    target_mode: str = "liquidity"  # "liquidity" (the videos) or "fixed" (fixed_r times the risk)
    fixed_r: float = 3.0
    arm_scale: float = 1.0    # how long to wait for the confirmation, times the profile's
    hold_scale: float = 1.0   # how long a trade may stay open, times the profile's
    sweep_reject: bool = False  # the sweep only counts if its candle closes back past the level
    break_body_atr: float = 0.0 # the confirmation candle's body must be at least this many ATRs
    discount: bool = False    # buy only below the middle of the higher chart's last swing range (sell above)
    trail_swings: bool = False  # after the partial, trail the stop to each new lower-chart swing
    max_vol_ratio: float = 0.0  # skip setups when ATR is more than this many times its 200-candle average (0 = off)
    trail_after_r: float = 0.0  # with trail_swings: only start trailing once price has gone this many R our way
    skip_days: tuple = ()       # weekdays (server time, 0 = Monday) on which no new setups are taken
    sweep_in_zone: bool = False # the sweep's extreme must stay inside the zone (a wick into it, not through it)
    enter_on_close: bool = False  # enter at the confirmation candle's close instead of waiting for a pullback
    cluster_atr: float = 0.0  # target only "equal highs/lows": another untaken level within this many higher-chart ATRs
    zone_touches: int = 1     # how many separate visits a zone gets (price must leave it in between)
    partial_at_zone: bool = False  # take the half at the first opposing zone (if 1R+ away) instead of partial_r
    ma_filter: int = 0        # buy only when the higher chart closed above its N-candle average (sell below); 0 = off
    break_past_atr: float = 0.0  # the confirmation must close at least this many ATRs past the swing it breaks
    sweep_max_atr: float = 0.0   # the sweep may run at most this many ATRs past the level it takes (0 = any)
    sweep_min_atr: float = 0.0   # the sweep must run at least this many ATRs past the level it takes

    def label(self) -> str:
        bits = [f"rr>={self.min_rr:g}"]
        bits.append("sweep" if self.require_sweep else "no sweep")
        bits.append("enter top" if self.entry_frac == 0 else f"enter {self.entry_frac:.0%} down")
        bits.append(f"half at {self.partial_r:g}R" if self.partial_r else "no partial")
        if self.htf_filter:
            bits.append("higher chart agrees")
        if self.sweep_reject:
            bits.append("sweep closes back")
        if self.discount:
            bits.append("buy cheap / sell dear")
        if self.trail_swings:
            bits.append("trail to swings" + (f" after {self.trail_after_r:g}R" if self.trail_after_r else ""))
        if self.sweep_min_atr:
            bits.append(f"sweep at least {self.sweep_min_atr:g} ATR deep")
        if self.sweep_max_atr:
            bits.append(f"sweep at most {self.sweep_max_atr:g} ATR deep")
        if self.break_past_atr:
            bits.append(f"close {self.break_past_atr:g} ATR past the swing")
        if self.ma_filter:
            bits.append(f"higher-chart {self.ma_filter} average agrees")
        if self.partial_at_zone:
            bits.append("half at opposing zone")
        if self.zone_touches > 1:
            bits.append(f"{self.zone_touches} visits per zone")
        if self.cluster_atr:
            bits.append(f"equal-level targets ({self.cluster_atr:g} ATR)")
        if self.sweep_in_zone:
            bits.append("sweep inside zone")
        if self.enter_on_close:
            bits.append("enter on the break")
        if self.skip_days:
            bits.append("skip days " + "/".join("MTWTFSS"[d] for d in self.skip_days))
        if self.max_vol_ratio:
            bits.append(f"skip vol > {self.max_vol_ratio:g}x")
        if self.break_body_atr:
            bits.append(f"break body {self.break_body_atr:g} ATR")
        if self.sides:
            bits.append("longs only" if self.sides == 1 else "shorts only")
        if (self.hour_from, self.hour_to) != (0, 24):
            bits.append(f"hours {self.hour_from}-{self.hour_to}")
        if self.target_mode == "fixed":
            bits.append(f"fixed {self.fixed_r:g}R target")
        for name, val, d in (("zones", self.disp_atr, 1.2), ("stop room", self.stop_buf_atr, 0.1),
                             ("zone life", self.zone_life, 120), ("wait", self.arm_scale, 1.0),
                             ("hold", self.hold_scale, 1.0), ("max", self.max_rr, 10.0),
                             ("swing size", self.ltf_k, 2), ("zone swings", self.mtf_k, 3),
                             ("target swings", self.htf_k, 3)):
            if val != d:
                bits.append(f"{name} {val:g}")
        return ", ".join(bits)


DEFAULT = Params()


@dataclass
class Order:
    side: int                 # +1 long, -1 short
    entry: float
    stop: float
    target: float
    signal_i: int             # lower-chart bar the order was made on (at its close)
    expires_i: int
    zone: tuple[float, float]
    why: str = ""
    partial_px: float = 0.0   # where half comes off, when set by the opposing zone


@dataclass
class Trade:
    symbol: str
    profile: str
    side: int
    entry: float
    stop: float
    target: float
    signal_time: int          # epoch seconds (bar close)
    fill_time: int = 0
    exit_time: int = 0
    exit_price: float = 0.0
    r: float = 0.0            # result in multiples of the risk, after costs
    reason: str = ""
    partial: bool = False
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- helpers ----

def atr(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    h, lo, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - lo, np.maximum(abs(h - prev), abs(lo - prev)))
    out = np.empty_like(tr)
    out[0] = tr[0]
    a = 1.0 / n
    for i in range(1, len(tr)):
        out[i] = out[i - 1] + a * (tr[i] - out[i - 1])
    return out


def pivots(high: np.ndarray, low: np.ndarray, k: int) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Swing highs and lows as (bar, price). Each is known only ``k`` bars later."""
    n = len(high)
    highs, lows = [], []
    for i in range(k, n - k):
        h = high[i]
        if h > high[i - k:i].max() and h >= high[i + 1:i + k + 1].max():
            highs.append((i, float(h)))
        lo = low[i]
        if lo < low[i - k:i].min() and lo <= low[i + 1:i + k + 1].min():
            lows.append((i, float(lo)))
    return highs, lows


def close_times(df: pd.DataFrame, tf: str) -> np.ndarray:
    return df["time"].to_numpy().astype(np.int64) + TF_SECONDS[tf]


@dataclass
class _Frame:
    """One chart with its swing points, each stamped with when it became known."""
    df: pd.DataFrame
    tf: str
    k: int
    close_t: np.ndarray = field(init=False)
    atr: np.ndarray = field(init=False)
    events: list[tuple[int, str, int, float]] = field(init=False)  # (known_t, kind, bar, price)

    def __post_init__(self) -> None:
        self.close_t = close_times(self.df, self.tf)
        self.atr = atr(self.df)
        hs, ls = pivots(self.df["high"].to_numpy(), self.df["low"].to_numpy(), self.k)
        ev = [(int(self.close_t[i + self.k]), "H", i, p) for i, p in hs]
        ev += [(int(self.close_t[i + self.k]), "L", i, p) for i, p in ls]
        ev.sort(key=lambda e: (e[0], e[2]))
        self.events = ev


# ----------------------------------------------------------------- engine ----

class Engine:
    """Runs the setup over one symbol, bar by bar on the lower chart.

    ``run()`` returns the closed trades. Afterwards ``pending`` holds an order
    still waiting, ``position`` an open trade, and ``orders`` every order made,
    which is what live trading reads.
    """

    def __init__(self, symbol: str, profile: Profile, htf: pd.DataFrame, mtf: pd.DataFrame,
                 ltf: pd.DataFrame, params: Params = DEFAULT, point: float = 0.0):
        self.symbol, self.pf, self.p = symbol, profile, params
        self.h = _Frame(htf.reset_index(drop=True), profile.htf, params.htf_k)
        self.m = _Frame(mtf.reset_index(drop=True), profile.mtf, params.mtf_k)
        self.l = _Frame(ltf.reset_index(drop=True), profile.ltf, params.ltf_k)
        self.point = point
        self.orders: list[Order] = []
        self.trades: list[Trade] = []
        self.pending: Order | None = None
        self.position: dict[str, Any] | None = None

    # -- costs --
    def _cost(self, i: int) -> float:
        d = self.l.df
        spread = float(d["spread"].iat[i]) * self.point if "spread" in d and self.point else 0.0
        return max(spread, self.p.cost_bp * 1e-4 * float(d["close"].iat[i]))

    def run(self, start_i: int = 0) -> list[Trade]:
        p, pf = self.p, self.pf
        L = self.l
        o_, h_, l_, c_ = (L.df[k].to_numpy() for k in ("open", "high", "low", "close"))
        n = len(c_)
        lt = L.close_t
        arm_bars = max(2, int(round(pf.arm_bars * p.arm_scale)))
        self._trail: dict[int, float | None] = {1: None, -1: None}
        if p.max_vol_ratio:
            avg = pd.Series(L.atr).rolling(200, min_periods=50).mean().to_numpy()
            vol_ratio = np.where(avg > 0, L.atr / np.where(avg > 0, avg, 1), 1.0)
        hours = (p.hour_from, p.hour_to) != (0, 24)
        M, H = self.m, self.h
        mo, mh, ml, mc = (M.df[k].to_numpy() for k in ("open", "high", "low", "close"))
        hh, hl, hc = (H.df[k].to_numpy() for k in ("high", "low", "close"))
        h_ma = (pd.Series(hc).rolling(p.ma_filter, min_periods=p.ma_filter).mean().to_numpy()
                if p.ma_filter else None)

        # structure state
        liq_hi: list[float] = []   # higher-chart highs not yet taken
        liq_lo: list[float] = []
        h_last = {"H": None, "L": None}
        h_trend = 0
        m_last = {"H": None, "L": None}
        m_trend = 0
        zones: list[dict[str, Any]] = []   # middle-chart demand (+1) / supply (-1) zones
        l_last = {"H": None, "L": None}    # (bar, price) of the latest lower-chart swings
        arm: dict[int, dict[str, Any] | None] = {1: None, -1: None}
        hev, mev, lev = H.events, M.events, L.events
        hi_ = mi_ = li_ = 0                     # next unread swing event of each chart
        hb = mb = 0                             # next higher/middle candle to read

        for j in range(n):
            T = int(lt[j])
            # 1) the bar happens: fills, stops and targets
            if j >= start_i:
                self._manage(j, o_[j], h_[j], l_[j], c_[j], int(lt[j]))

            # 2) what this bar's close teaches the charts
            # Higher-chart candles and swing points, in time order: each candle
            # takes out the liquidity it ran through, then the swings it
            # confirms join the list. (Before the lower chart's history starts,
            # this is the only thing keeping the list honest.)
            while hb < len(hc) and H.close_t[hb] <= T:
                if liq_hi and hh[hb] >= min(liq_hi):
                    liq_hi = [x for x in liq_hi if x > hh[hb]]
                if liq_lo and hl[hb] <= max(liq_lo):
                    liq_lo = [x for x in liq_lo if x < hl[hb]]
                if p.htf_filter:
                    if h_last["H"] and hc[hb] > h_last["H"][1]:
                        h_trend = 1
                    elif h_last["L"] and hc[hb] < h_last["L"][1]:
                        h_trend = -1
                self._h_atr = float(H.atr[hb])
                while hi_ < len(hev) and hev[hi_][0] <= H.close_t[hb]:
                    _, kind, bar, price = hev[hi_]
                    h_last[kind] = (bar, price)
                    (liq_hi if kind == "H" else liq_lo).append(price)
                    hi_ += 1
                hb += 1
            while mb < len(mc) and M.close_t[mb] <= T:
                if m_last["H"] and mc[mb] > m_last["H"][1]:
                    m_trend = 1
                elif m_last["L"] and mc[mb] < m_last["L"][1]:
                    m_trend = -1
                body = mc[mb] - mo[mb]
                a = M.atr[mb - 1] if mb else M.atr[mb]
                if mb and abs(body) >= p.disp_atr * a:
                    side = 1 if body > 0 else -1
                    prev_opposite = (mc[mb - 1] - mo[mb - 1]) * side < 0
                    o = mb - 1 if prev_opposite else mb
                    zones.append({"side": side, "lo": float(ml[o]), "hi": float(mh[o]),
                                  "born": mb, "used": False})
                mb += 1
            zones = [z for z in zones if mb - z["born"] <= p.zone_life and not z["used"]]
            while mi_ < len(mev) and mev[mi_][0] <= T:
                _, kind, bar, price = mev[mi_]
                m_last[kind] = (bar, price)
                mi_ += 1
            # liquidity this bar ran through is gone
            if liq_hi and h_[j] >= min(liq_hi):
                liq_hi = [x for x in liq_hi if x > h_[j]]
            if liq_lo and l_[j] <= max(liq_lo):
                liq_lo = [x for x in liq_lo if x < l_[j]]
            # zones price closed through are dead
            for z in zones:
                if (z["side"] == 1 and c_[j] < z["lo"]) or (z["side"] == -1 and c_[j] > z["hi"]):
                    z["used"] = True
            new_l: list[tuple[str, int, float]] = []
            while li_ < len(lev) and lev[li_][0] <= T:
                _, kind, bar, price = lev[li_]
                l_last[kind] = (bar, price)
                new_l.append((kind, bar, price))
                li_ += 1
            self._trail = {1: l_last["L"][1] if l_last["L"] else None,
                           -1: l_last["H"][1] if l_last["H"] else None}

            # 3) the trigger, on the close
            in_hours = not hours or p.hour_from <= (T // 3600) % 24 < p.hour_to
            for side in (1, -1):
                if p.sides and side != p.sides:
                    continue
                trend_ok = m_trend == side and (not p.htf_filter or h_trend == side)
                a = arm[side]
                if a is not None:
                    dead = (side == 1 and c_[j] < a["zone"]["lo"]) or (side == -1 and c_[j] > a["zone"]["hi"])
                    if dead or not trend_ok or j - a["start"] > arm_bars:
                        z0 = a["zone"]
                        z0["visits"] = z0.get("visits", 0) + 1
                        z0["away"] = False
                        if dead or z0["visits"] >= p.zone_touches:
                            z0["used"] = True
                        arm[side] = a = None
                if a is None:
                    if not trend_ok:
                        continue
                    for z in zones:   # a revisit needs price to have left the zone first
                        if z.get("visits") and ((side == 1 and l_[j] > z["hi"]) or (side == -1 and h_[j] < z["lo"])):
                            z["away"] = True
                    touched = [z for z in zones if z["side"] == side and not z["used"]
                               and (not z.get("visits") or z.get("away"))
                               and ((side == 1 and l_[j] <= z["hi"] and c_[j] >= z["lo"])
                                    or (side == -1 and h_[j] >= z["lo"] and c_[j] <= z["hi"]))]
                    if not touched:
                        continue
                    z = max(touched, key=lambda z: z["hi"] * side)  # the first one price reaches
                    ref = l_last["H" if side == 1 else "L"]
                    liq = l_last["L" if side == 1 else "H"]
                    ext = l_[j] if side == 1 else h_[j]
                    a = arm[side] = {
                        "zone": z, "start": j, "ref": ref[1] if ref else None,
                        "liq": liq[1] if liq else None, "ext": ext, "ext_i": j,
                        "swept": liq is not None and (ext - liq[1]) * side < 0
                        and (not p.sweep_reject or (c_[j] - liq[1]) * side > 0),
                    }
                    continue  # confirmation comes on a later bar
                # armed: track the extreme, the sweep and the latest swing to break
                ext = l_[j] if side == 1 else h_[j]
                if (ext - a["ext"]) * side < 0:
                    a["ext"], a["ext_i"] = ext, j
                if a["liq"] is not None and (ext - a["liq"]) * side < 0 and (
                        not p.sweep_reject or (c_[j] - a["liq"]) * side > 0):
                    a["swept"] = True
                for kind, bar, price in new_l:
                    if kind == ("H" if side == 1 else "L") and bar >= a["start"]:
                        a["ref"] = price
                if a["ref"] is None or (p.require_sweep and not a["swept"]):
                    continue
                if (c_[j] - a["ref"]) * side <= 0 or not in_hours:
                    continue
                if p.break_body_atr and (c_[j] - o_[j]) * side < p.break_body_atr * L.atr[j]:
                    continue
                if p.break_past_atr and (c_[j] - a["ref"]) * side < p.break_past_atr * L.atr[j]:
                    continue
                if p.sweep_min_atr and (a["liq"] is None or (a["liq"] - a["ext"]) * side < p.sweep_min_atr * L.atr[j]):
                    continue
                if p.sweep_max_atr and a["liq"] is not None and (a["liq"] - a["ext"]) * side > p.sweep_max_atr * L.atr[j]:
                    continue
                if p.max_vol_ratio and vol_ratio[j] > p.max_vol_ratio:
                    continue
                if p.sweep_in_zone and (a["ext"] - (a["zone"]["lo"] if side == 1 else a["zone"]["hi"])) * side < 0:
                    continue
                if p.skip_days and (T // 86400 + 3) % 7 in p.skip_days:
                    continue
                if h_ma is not None:
                    k = hb - 1   # the last higher-chart candle closed by now
                    if k < 0 or np.isnan(h_ma[k]) or (hc[k] - h_ma[k]) * side <= 0:
                        continue
                if p.discount and h_last["H"] and h_last["L"]:
                    mid = (h_last["H"][1] + h_last["L"][1]) / 2
                    if (c_[j] - mid) * side > 0:
                        continue
                # confirmed: build the order
                self._zones_now = zones
                order = self._order(side, j, a, o_, h_, l_, c_, liq_hi if side == 1 else liq_lo)
                a["zone"]["used"] = True
                arm[side] = None
                if order is not None:
                    self.orders.append(order)
                    if j >= start_i and self.pending is None and self.position is None:
                        self.pending = order
        return self.trades

    def _order(self, side: int, j: int, a: dict[str, Any], o_, h_, l_, c_, liq: list[float]) -> Order | None:
        p = self.p
        # the candle the turn started from: the last candle against the turn
        start = a["ext_i"]
        cand = [i for i in range(start, j) if (c_[i] - o_[i]) * side < 0]
        e = cand[-1] if cand else start
        top, bot = (h_[e], l_[e]) if side == 1 else (l_[e], h_[e])
        entry = c_[j] if p.enter_on_close else top - p.entry_frac * (top - bot)
        entry = min(entry, c_[j]) if side == 1 else max(entry, c_[j])
        stop = a["ext"] - side * p.stop_buf_atr * self.l.atr[j]
        risk = (entry - stop) * side
        if risk <= 0:
            return None
        if p.target_mode == "fixed":
            target = entry + side * p.fixed_r * risk
            rr = p.fixed_r
        else:
            ahead = [x for x in liq if (x - c_[j]) * side > 0]
            if p.cluster_atr:
                tol = p.cluster_atr * getattr(self, "_h_atr", 0.0)
                ahead = [x for x in ahead if sum(abs(y - x) <= tol for y in liq) >= 2]
            if not ahead:
                return None
            target = min(ahead) if side == 1 else max(ahead)
            rr = (target - entry) * side / risk
            if rr < p.min_rr:
                return None
            if rr > p.max_rr:
                target = entry + side * p.max_rr * risk
        why = (f"{'demand' if side == 1 else 'supply'} zone {a['zone']['lo']:.5g}-{a['zone']['hi']:.5g}, "
               f"{'swept' if a['swept'] else 'no sweep'}, broke {a['ref']:.5g}, "
               f"target {'high' if side == 1 else 'low'} {target:.5g} ({min(rr, p.max_rr):.1f}R)")
        part = 0.0
        if p.partial_at_zone:
            near = [z["lo"] if side == 1 else z["hi"] for z in getattr(self, "_zones_now", [])
                    if z["side"] == -side and not z["used"]]
            near = [x for x in near if (x - entry) * side >= risk and (target - x) * side > 0]
            if near:
                part = min(near) if side == 1 else max(near)
        return Order(side, float(entry), float(stop), float(target), j, j + self.pf.entry_valid,
                     (a["zone"]["lo"], a["zone"]["hi"]), why, float(part))

    # -- the order and the trade, through one bar --
    def _manage(self, j: int, o: float, h: float, lo: float, c: float, t: int) -> None:
        cost = self._cost(j)
        pos = self.position
        if pos is None and self.pending is not None:
            od = self.pending
            s = od.side
            if j > od.expires_i:
                self.pending = None
            elif j > od.signal_i:
                # long: the ask (bid + cost) must reach the entry; short: the bid
                fill = (lo + cost <= od.entry) if s == 1 else (h >= od.entry)
                if not fill and ((h >= od.target) if s == 1 else (lo + cost <= od.target)):
                    self.pending = None   # ran to the target without us
                elif fill:
                    px = min(od.entry, o + cost) if s == 1 else max(od.entry, o)
                    self.pending = None
                    pos = self.position = {
                        "order": od, "side": s, "entry": px, "stop": od.stop, "target": od.target,
                        "risk": (px - od.stop) * s, "fill_i": j, "fill_t": t, "half": False, "banked": 0.0,
                    }
                    # a stop inside the fill bar counts; a target needs a later bar
                    if (lo <= pos["stop"]) if s == 1 else (h + cost >= pos["stop"]):
                        self._close(pos, pos["stop"] - s * self.p.stop_slip * cost, t, "stop")
                    return
        if pos is None:
            return
        s, risk = pos["side"], pos["risk"]
        stop_hit = (lo <= pos["stop"]) if s == 1 else (h + cost >= pos["stop"])
        if stop_hit:
            gap = (o < pos["stop"]) if s == 1 else (o + cost > pos["stop"])
            px = (o if s == 1 else o + cost) if gap else pos["stop"]
            self._close(pos, px - s * self.p.stop_slip * cost, t, "breakeven" if pos["half"] else "stop")
            return
        best = h if s == 1 else lo + cost
        pos["peak"] = max(pos.get("peak", pos["entry"]) * s, best * s) * s
        far_enough = (pos["peak"] - pos["entry"]) * s >= self.p.trail_after_r * risk
        if self.p.trail_swings and pos["half"] and far_enough:
            sw = getattr(self, "_trail", {}).get(s)
            if sw is not None and (sw - pos["stop"]) * s > 0 and (sw - (c if s == 1 else c + cost)) * s < 0:
                pos["stop"] = sw   # the latest swing behind price, known before this candle
        if self.p.partial_r and not pos["half"]:
            od_part = pos["order"].partial_px
            level = od_part if od_part else pos["entry"] + s * self.p.partial_r * risk
            if (best - level) * s >= 0 and (pos["target"] - level) * s > 0:
                pos["half"] = True
                pos["banked"] = 0.5 * (level - pos["entry"]) * s / risk
                pos["stop"] = pos["entry"]
        if (best - pos["target"]) * s >= 0:
            self._close(pos, pos["target"], t, "target")
            return
        if j - pos["fill_i"] >= self.pf.max_hold * self.p.hold_scale:
            self._close(pos, c if s == 1 else c + cost, t, "time")

    def _close(self, pos: dict[str, Any], px: float, t: int, reason: str) -> None:
        s, risk = pos["side"], pos["risk"]
        r_rest = (px - pos["entry"]) * s / risk
        r = pos["banked"] + (0.5 if pos["half"] else 1.0) * r_rest
        od: Order = pos["order"]
        self.trades.append(Trade(
            self.symbol, self.pf.name, s, pos["entry"], od.stop, od.target,
            int(self.l.close_t[od.signal_i]), pos["fill_t"], t, float(px), float(r), reason,
            pos["half"], od.why))
        self.position = None


def variants() -> list[Params]:
    """The small set of versions the backtest compares (on the first part of history only)."""
    out = []
    for rr in (2.5, 3.0):
        for sweep in (True, False):
            for frac in (0.0, 0.5):
                for part in (0.0, 2.0):
                    out.append(replace(DEFAULT, min_rr=rr, require_sweep=sweep,
                                       entry_frac=frac, partial_r=part))
    return out
