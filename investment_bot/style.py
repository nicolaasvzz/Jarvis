"""The strategy to try, picked from Jarvis's menus: a preset, or your own words.

    --style refine    keep refining the current strategy (nothing pinned)
    --style fewer     fewer trades, more risk: 70% confidence, 25% a trade
    --style more      less risk, more trades: 35% confidence, 5% a trade
    --style custom --custom "only trade when very sure, bet 30%, no shorts"
    --money 10000     the money to trade with (0 = the whole account)

A style *pins* settings: the mode keeps them fixed and refines everything else
around them. Its two numbers mean the same in every mode:

- **confidence** - how strongly the indicators must agree before it trades
  (the entry bar on a score from 0 to 1): higher means fewer, surer trades.
- **bet** - the share of the money that goes into one trade.

The indicator lab and the package trader use them as they are (the package's
``threshold`` and ``size``). The original five-strategy setup (learning mode,
backtests) caps a position at the bet and risks about a twelfth of it down to
the stop, since its stops sit some 8% away.

Custom words are read for numbers and keywords, and the bot says how it read
them; anything it can't place is left to the mode ("refine").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

STOP_DISTANCE = 0.08   # typical stop distance in the five-strategy setup (4-5 ATRs)

PRESETS = {
    "refine": ("Continue refining the current strategy", {}),
    "fewer": ("Fewer trades (70% confidence), more risk (25% a trade)",
              {"threshold": 0.70, "size": 0.25}),
    "more": ("Less risk (5% a trade), more trades (35% confidence)",
             {"threshold": 0.35, "size": 0.05}),
}

SURE, BOLD = 0.70, 0.25      # "high confidence" / "more risk" without a number
LOOSE, CAREFUL = 0.35, 0.05  # "more trades" / "less risk" without a number


@dataclass
class Style:
    name: str = "refine"
    text: str = ""
    threshold: float | None = None   # confidence, 0..1
    size: float | None = None        # share of the money a trade, 0..1
    shorts: bool | None = None
    min_hold: int | None = None      # candles (the lab and package trader only)
    reading: list[str] = field(default_factory=list)

    @property
    def pinned(self) -> bool:
        return any(v is not None for v in (self.threshold, self.size, self.shorts, self.min_hold))

    def describe(self) -> str:
        if not self.pinned:
            return "refine the current strategy (nothing pinned)"
        parts = []
        if self.threshold is not None:
            parts.append(f"{self.threshold:.0%} confidence")
        if self.size is not None:
            parts.append(f"{self.size:.0%} a trade")
        if self.shorts is not None:
            parts.append("shorts on" if self.shorts else "no shorts")
        if self.min_hold is not None:
            parts.append(f"hold at least {self.min_hold} candles")
        return ", ".join(parts)

    # -- per engine ---------------------------------------------------------

    def package_pins(self) -> dict[str, Any]:
        """For a lab package (Package.with_)."""
        pins: dict[str, Any] = {}
        if self.threshold is not None:
            pins["threshold"] = self.threshold
        if self.size is not None:
            pins["size"] = self.size
        if self.shorts is not None:
            pins["shorts"] = self.shorts
        if self.min_hold is not None:
            pins["min_hold"] = self.min_hold
        return pins

    def config_overrides(self) -> dict[str, Any]:
        """For the five-strategy setup (memory.apply_overrides keys)."""
        out: dict[str, Any] = {}
        if self.threshold is not None:
            out["strategy.threshold"] = self.threshold
        if self.size is not None:
            out["risk.max_position_weight"] = self.size
            out["risk.risk_per_trade"] = round(min(max(self.size * STOP_DISTANCE, 0.001), 0.1), 4)
        if self.shorts is not None:
            out["strategy.long_only"] = not self.shorts
        return out


def pick(name: str | None, custom: str = "") -> Style:
    """The style for a menu choice. Unknown names count as refine."""
    name = (name or "refine").lower()
    if name == "custom":
        return read_style(custom)
    label, values = PRESETS.get(name, PRESETS["refine"])
    style = Style(name=name if name in PRESETS else "refine", **values)
    style.reading.append(label)
    return style


_NUM = r"(\d+(?:\.\d+)?)"


def _share(text: str) -> float:
    value = float(text)
    return value / 100 if value > 1 else value


def read_style(text: str) -> Style:
    """What strategy the words ask for. Numbers win over keywords."""
    style = Style(name="custom", text=(text or "").strip())
    words = style.text.lower()
    if not words:
        style.reading.append("nothing written: refining the current strategy")
        return style

    conf = (re.search(_NUM + r"\s*%?\s*(?:confidence|confident|conf\b|sure|certain)", words)
            or re.search(r"confiden(?:ce|t)\s*(?:of|at|above|over|is|=|:)?\s*" + _NUM, words))
    bet = (re.search(r"(?:risk|bet|betting|stake|size|put|use|using|invest|allocate)\D{0,20}"
                     + _NUM + r"\s*%", words)
           or re.search(_NUM + r"\s*%\s*(?:a|an|per|of|on)?\s*(?:each\s+)?"
                        r"(?:trade|position|bet|wallet|account|money|equity|portfolio|balance)",
                        words))
    if conf:
        style.threshold = _share(conf.group(1))
        style.reading.append(f"{style.threshold:.0%} confidence before a trade")
    elif re.search(r"fewer trades|less trades|less often|selective|picky|high(?:er)? confidence|"
                   r"very sure|only (?:the )?best|strong signals?", words):
        style.threshold = SURE
        style.reading.append(f"fewer, surer trades: {SURE:.0%} confidence")
    elif re.search(r"more trades|trade more|more often|frequent|active|low(?:er)? confidence",
                   words):
        style.threshold = LOOSE
        style.reading.append(f"more trades: {LOOSE:.0%} confidence")
    if bet:
        style.size = _share(bet.group(1))
        style.reading.append(f"{style.size:.0%} of the money a trade")
    elif re.search(r"less risk|lower risk|low risk|safe|careful|small(?:er)? (?:bets?|trades?|"
                   r"positions?)|conservative|cautious", words):
        style.size = CAREFUL
        style.reading.append(f"less risk: {CAREFUL:.0%} a trade")
    elif re.search(r"more risk|higher risk|high risk|aggressive|risky|riskier|big(?:ger)? "
                   r"(?:bets?|trades?|positions?)|all in", words):
        style.size = BOLD
        style.reading.append(f"more risk: {BOLD:.0%} a trade")
    if re.search(r"no shorts?|without short|long[- ]only|don'?t short|never short|only longs?",
                 words):
        style.shorts = False
        style.reading.append("no shorts")
    elif re.search(r"\bshort", words):
        style.shorts = True
        style.reading.append("shorts on")
    if re.search(r"hold (?:them |it |positions? )?longer|longer holds?|swing|patient", words):
        style.min_hold = 12
        style.reading.append("hold at least 12 candles")
    if style.threshold is not None:
        style.threshold = round(min(max(style.threshold, 0.05), 0.95), 3)
    if style.size is not None:
        style.size = round(min(max(style.size, 0.01), 0.5), 3)
        if style.size >= 0.5:
            style.reading.append("(capped at 50% a trade)")
    if not style.pinned:
        style.reading.append("found nothing to pin: refining the current strategy")
    return style


def money_text(money: float) -> str:
    return f"${money:,.0f}" if money > 0 else "the whole account"
