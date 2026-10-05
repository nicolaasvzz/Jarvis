"""Standalone connectivity smoke test: fire one BTC/USD trade and close it.

Bypasses the strategy/ensemble/risk pipeline entirely. All this proves is
that ALPACA_API_KEY / ALPACA_SECRET_KEY are valid, crypto trading is enabled
on the account, and AlpacaBroker can submit and fill a crypto order in both
directions. No model, no signal, no config file.

Refuses to run against anything but Alpaca's paper endpoint. You run this
yourself — it is not something Claude executes on your behalf.

Usage:
    cp .env.example .env   # then fill in ALPACA_API_KEY / ALPACA_SECRET_KEY
    python smoke_test_btc.py                # $10 notional worth of BTC/USD
    python smoke_test_btc.py --qty 0.001     # exact BTC quantity instead
    python smoke_test_btc.py --dry-run       # print the plan, touch no API
    python smoke_test_btc.py --yes           # skip the confirmation prompt

Credentials come from a local .env file (via python-dotenv) or real
environment variables — real env vars always take precedence over .env.
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from investment_bot.broker.alpaca import PAPER_URL, AlpacaBroker, AlpacaCredentialsError
from investment_bot.broker.base import Order

load_dotenv()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="BTC/USD", help="Crypto symbol (default: BTC/USD)")
    p.add_argument(
        "--notional", type=float, default=10.0,
        help="Dollar amount to buy, converted to qty via the latest trade price (default: 10)",
    )
    p.add_argument(
        "--qty", type=float, default=None,
        help="Exact BTC quantity to buy instead of --notional",
    )
    p.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    p.add_argument("--dry-run", action="store_true", help="Print the plan, call no API")
    return p.parse_args()


def latest_price(symbol: str) -> float:
    import requests

    resp = requests.get(
        "https://data.alpaca.markets/v1beta3/crypto/us/latest/trades",
        params={"symbols": symbol},
        headers={
            "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
            "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
        },
        timeout=30,
    )
    resp.raise_for_status()
    return float(resp.json()["trades"][symbol]["p"])


def main() -> int:
    args = parse_args()

    base_url = os.environ.get("ALPACA_BASE_URL", PAPER_URL).rstrip("/")
    if base_url != PAPER_URL:
        print(
            f"Refusing to run: ALPACA_BASE_URL is set to '{base_url}', not the "
            f"paper endpoint ('{PAPER_URL}'). This script only ever trades paper.",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        qty = args.qty if args.qty is not None else "<computed from --notional at run time>"
        print(f"[dry-run] would BUY {qty} {args.symbol} on {base_url}, then SELL the same qty to close.")
        return 0

    try:
        broker = AlpacaBroker()
    except AlpacaCredentialsError as exc:
        print(exc, file=sys.stderr)
        return 1

    account = broker.account()
    print(f"Connected to paper account. equity=${account.get('equity')} buying_power=${account.get('buying_power')}")

    price = latest_price(args.symbol)
    qty = args.qty
    if qty is None:
        qty = round(args.notional / price, 8)
        print(f"Latest {args.symbol} price ~${price:,.2f} -> buying {qty} ({args.notional} notional)")
    else:
        print(f"Latest {args.symbol} price ~${price:,.2f}")

    if qty <= 0:
        print("Computed/given qty is not positive, aborting.", file=sys.stderr)
        return 1

    if not args.yes:
        reply = input(f"About to BUY {qty} {args.symbol} on the PAPER endpoint, then close it. Continue? [y/N] ")
        if reply.strip().lower() != "y":
            print("Aborted.")
            return 1

    import pandas as pd

    now = pd.Timestamp.utcnow()
    print(f"Submitting BUY {qty} {args.symbol}...")
    buy_fill = broker.submit(Order(symbol=args.symbol, qty=qty, reason="smoke test buy"), price, now)
    if buy_fill is None:
        print("Buy order was rejected or never filled. No position opened.", file=sys.stderr)
        return 1
    print(f"BUY filled: qty={buy_fill.qty} price=${buy_fill.price:,.2f} commission=${buy_fill.commission:.4f}")

    try:
        print(f"Submitting SELL {buy_fill.qty} {args.symbol} to close...")
        close_fill = broker.submit(
            Order(symbol=args.symbol, qty=-buy_fill.qty, reason="smoke test close"), buy_fill.price, pd.Timestamp.utcnow()
        )
    except Exception:
        print(
            f"\n!!! CLOSE FAILED. You have an OPEN position: {buy_fill.qty} {args.symbol}. "
            "Close it manually in the Alpaca dashboard or by re-running with --qty "
            f"{buy_fill.qty}.\n",
            file=sys.stderr,
        )
        raise

    if close_fill is None:
        print(
            f"\n!!! CLOSE was rejected. You have an OPEN position: {buy_fill.qty} {args.symbol}. "
            "Close it manually in the Alpaca dashboard.\n",
            file=sys.stderr,
        )
        return 1

    print(f"SELL filled: qty={close_fill.qty} price=${close_fill.price:,.2f} commission=${close_fill.commission:.4f}")
    pnl = (close_fill.price - buy_fill.price) * buy_fill.qty - buy_fill.commission - close_fill.commission
    print(f"\nRound trip complete. Net P&L (paper): ${pnl:.4f}")
    print("PASS: buy + close both filled on Alpaca's crypto paper endpoint.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
