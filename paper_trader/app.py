"""Command-line runner for the local crypto paper trader."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time

from .laya_client import LayaClient, make_prompt
from .market import fetch_candles, summarize
from .paper import (
    DEFAULT_INITIAL_CASH,
    apply_paper_fill,
    guarded_action,
    load_portfolio,
    portfolio_equity,
    save_portfolio,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    _local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    DEFAULT_STATE = _local_app_data / "CryptoPaperTrader" / "portfolio.json"
else:
    DEFAULT_STATE = PROJECT_ROOT / "data" / "portfolio.json"


def _symbols(value: str) -> list[str]:
    symbols = [part.strip().upper() for part in value.split(",") if part.strip()]
    if not symbols:
        raise argparse.ArgumentTypeError("Specify at least one Coinbase product, e.g. BTC-USD")
    if len(set(symbols)) != len(symbols):
        raise argparse.ArgumentTypeError("Duplicate products are not allowed")
    for symbol in symbols:
        if not symbol or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for ch in symbol):
            raise argparse.ArgumentTypeError(f"Invalid Coinbase product: {symbol!r}")
    return symbols


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local, simulated-only cryptocurrency paper trader using public Coinbase data and Laya.")
    parser.add_argument("--symbols", type=_symbols, default=_symbols("BTC-USD,ETH-USD"), help="comma-separated Coinbase products (default: BTC-USD,ETH-USD)")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help=f"local virtual portfolio JSON (default: {DEFAULT_STATE})")
    parser.add_argument("--initial-cash", type=float, default=DEFAULT_INITIAL_CASH, help="starting virtual USD balance (default: 10000)")
    parser.add_argument("--interval", type=int, default=300, help="seconds between cycles in --loop mode (default: 300)")
    parser.add_argument("--loop", action="store_true", help="keep monitoring until interrupted; without this, run one cycle")
    parser.add_argument("--dry-run", action="store_true", help="fetch data and infer signals without saving state or simulated fills")
    parser.add_argument("--trade-fraction", type=float, default=0.05, help="max equity fraction allocated per simulated buy (default: 0.05)")
    parser.add_argument("--max-position-fraction", type=float, default=0.20, help="max equity exposure per asset (default: 0.20)")
    parser.add_argument("--fee-bps", type=float, default=10.0, help="simulated fee in basis points, 10 = 0.10%% (default: 10)")
    parser.add_argument("--slippage-bps", type=float, default=5.0, help="simulated adverse slippage in basis points, 5 = 0.05%% (default: 5)")
    return parser


def _load_market(symbols: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    indicators: dict[str, dict] = {}
    failures: dict[str, str] = {}
    for symbol in symbols:
        try:
            indicators[symbol] = summarize(fetch_candles(symbol))
        except Exception as exc:
            failures[symbol] = str(exc)
    return indicators, failures


def run_cycle(args: argparse.Namespace, client: LayaClient | None = None) -> int:
    state = load_portfolio(args.state, args.initial_cash)
    indicators_by_symbol, failures = _load_market(args.symbols)
    if failures:
        for symbol, message in failures.items():
            print(f"{symbol}: market data unavailable — {message}", file=sys.stderr)
    if not indicators_by_symbol:
        raise RuntimeError("No usable market data; no trades or portfolio changes were made")

    prices = {symbol: float(info["close"]) for symbol, info in indicators_by_symbol.items()}
    prices_for_context = {**state.get("last_prices", {}), **prices}
    prompts = {}
    for symbol, info in indicators_by_symbol.items():
        position = state["positions"].get(symbol, {})
        prompts[symbol] = make_prompt(
            symbol,
            info,
            float(position.get("quantity", 0.0)),
            float(position.get("average_entry_price", 0.0)),
        )
    client = client or LayaClient()
    decisions = client.predict(prompts)

    had_changes = False
    for symbol, info in indicators_by_symbol.items():
        candle_ts = int(info["candle_timestamp"])
        if not args.dry_run and candle_ts <= int(state.get("last_processed_candles", {}).get(symbol, 0)):
            print(f"{symbol}: {datetime.fromtimestamp(candle_ts, timezone.utc).isoformat()} already processed — no action")
            state.setdefault("last_prices", {})[symbol] = prices[symbol]
            had_changes = True
            continue

        position = state["positions"].get(symbol, {})
        quantity = float(position.get("quantity", 0.0))
        average_entry = float(position.get("average_entry_price", 0.0))
        prediction = decisions[symbol]
        action, reason = guarded_action(
            prediction.action,
            info,
            quantity=quantity,
            average_entry_price=average_entry,
        )
        fill = None
        if not args.dry_run:
            fill = apply_paper_fill(
                state,
                symbol,
                action,
                candle_timestamp=candle_ts,
                market_price=prices[symbol],
                prices=prices_for_context,
                trade_fraction=args.trade_fraction,
                max_position_fraction=args.max_position_fraction,
                fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
            )
            state.setdefault("last_processed_candles", {})[symbol] = candle_ts
            state.setdefault("last_prices", {})[symbol] = prices[symbol]
            had_changes = True
        elif action in {"BUY", "SELL"}:
            fill = {
                "symbol": symbol,
                "side": action,
                "market_price": prices[symbol],
                "simulated_fill_price": prices[symbol] * (1 + args.slippage_bps / 10_000 if action == "BUY" else 1 - args.slippage_bps / 10_000),
                "note": "preview only; no fill or state change",
            }

        confidence_note = "not reported" if prediction.confidence is None else f"{prediction.confidence:.3f} (diagnostic only; uncalibrated)"
        line = (
            f"{symbol} @ ${prices[symbol]:,.2f} | Laya={prediction.action} | "
            f"confidence={confidence_note} | guarded={action} ({reason})"
        )
        if fill:
            if "quantity" in fill:
                line += f" | PAPER {fill['side']} {fill['quantity']:.8f} @ ${fill['simulated_fill_price']:,.2f} fee=${fill['fee']:.2f}"
            else:
                line += f" | would PAPER {fill['side']} @ ${fill['simulated_fill_price']:,.2f}"
        print(line)

    if had_changes and not args.dry_run:
        save_portfolio(args.state, state)
    equity = portfolio_equity(state, prices_for_context)
    print(f"Portfolio ({'dry-run' if args.dry_run else 'paper'}): cash=${state['cash']:,.2f}, equity=${equity:,.2f}, realized P/L=${state.get('realized_pnl', 0.0):,.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.interval < 60:
        parser.error("--interval must be at least 60 seconds")
    if not math.isfinite(args.initial_cash) or args.initial_cash <= 0:
        parser.error("--initial-cash must be positive and finite")
    if not (0 < args.trade_fraction <= args.max_position_fraction <= 1):
        parser.error("require 0 < --trade-fraction <= --max-position-fraction <= 1")
    if args.fee_bps < 0 or args.slippage_bps < 0 or args.slippage_bps >= 10_000:
        parser.error("fee/slippage basis points are out of range")
    if args.loop:
        print(f"Monitoring {', '.join(args.symbols)} every {args.interval}s; simulated trading only. Press Ctrl-C to stop.")
        while True:
            started = time.monotonic()
            try:
                run_cycle(args)
            except Exception as exc:
                print(f"Cycle failed closed; no model-driven fills were made: {exc}", file=sys.stderr)
            sleep_for = max(0.0, args.interval - (time.monotonic() - started))
            try:
                time.sleep(sleep_for)
            except KeyboardInterrupt:
                print("Stopped.")
                return 0
    try:
        return run_cycle(args)
    except Exception as exc:
        print(f"Cycle failed closed; no portfolio state was committed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
