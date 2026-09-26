"""Alpaca Markets adapter for real order routing (paper or live account).

Requires environment variables:
    ALPACA_API_KEY, ALPACA_SECRET_KEY
and optionally ALPACA_BASE_URL (defaults to the paper-trading endpoint —
you must explicitly point it at the live endpoint to trade real money).

Orders are submitted as market DAY orders. The returned Fill uses the
broker-reported average fill price when available, otherwise the reference
price you passed in.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import pandas as pd

from .base import Broker, ExecutionModel, Fill, Order

PAPER_URL = "https://paper-api.alpaca.markets"


class AlpacaCredentialsError(RuntimeError):
    pass


@dataclass
class AlpacaBroker(Broker):
    execution: ExecutionModel = field(default_factory=ExecutionModel)
    poll_seconds: float = 1.0
    poll_attempts: int = 15

    def __post_init__(self):
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        self.base_url = os.environ.get("ALPACA_BASE_URL", PAPER_URL).rstrip("/")
        if not self.api_key or not self.secret_key:
            raise AlpacaCredentialsError(
                "Set ALPACA_API_KEY and ALPACA_SECRET_KEY to use the Alpaca broker. "
                "Defaults to the PAPER endpoint; set ALPACA_BASE_URL for live."
            )

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
        }

    def account(self) -> dict:
        import requests

        resp = requests.get(f"{self.base_url}/v2/account", headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def submit(self, order: Order, ref_price: float, timestamp: pd.Timestamp) -> Fill | None:
        import requests

        is_crypto = "/" in order.symbol
        side = "buy" if order.qty > 0 else "sell"
        payload = {
            "symbol": order.symbol,
            # Crypto trades in fractional units; equities here trade in whole
            # shares. Only equities get truncated to an integer.
            "qty": str(abs(order.qty)) if is_crypto else str(abs(int(order.qty))),
            "side": side,
            "type": "market",
            # Crypto has no trading session to close a "day" order against;
            # Alpaca requires gtc/ioc for crypto symbols.
            "time_in_force": "gtc" if is_crypto else "day",
        }
        resp = requests.post(
            f"{self.base_url}/v2/orders", json=payload, headers=self._headers(), timeout=30
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Alpaca rejected order {payload}: {resp.status_code} {resp.text}")
        order_id = resp.json()["id"]

        # Poll briefly for the fill so we can report a real average price.
        avg_price = None
        for _ in range(self.poll_attempts):
            status = requests.get(
                f"{self.base_url}/v2/orders/{order_id}", headers=self._headers(), timeout=30
            ).json()
            if status.get("status") == "filled" and status.get("filled_avg_price"):
                avg_price = float(status["filled_avg_price"])
                break
            time.sleep(self.poll_seconds)

        price = avg_price if avg_price is not None else ref_price
        fill = Fill(
            symbol=order.symbol,
            qty=order.qty,
            price=price,
            commission=self.execution.commission(order.qty, price),
            timestamp=timestamp,
            reason=order.reason,
        )
        self.fills.append(fill)
        return fill
