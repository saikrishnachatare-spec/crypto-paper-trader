"""Virtual portfolio accounting and non-configurable safety boundaries."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Mapping


DEFAULT_INITIAL_CASH = 10_000.0


def new_portfolio(initial_cash: float = DEFAULT_INITIAL_CASH) -> dict:
    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("Initial cash must be a positive finite number")
    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "initial_cash": float(initial_cash),
        "cash": float(initial_cash),
        "realized_pnl": 0.0,
        "positions": {},
        "last_prices": {},
        "last_processed_candles": {},
        "trades": [],
    }


def load_portfolio(path: Path, initial_cash: float = DEFAULT_INITIAL_CASH) -> dict:
    if not path.exists():
        return new_portfolio(initial_cash)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("version") != 1 or not isinstance(state.get("positions"), dict):
            raise ValueError("unsupported portfolio state schema")
        for field in ("initial_cash", "cash", "realized_pnl"):
            value = float(state[field])
            if not math.isfinite(value):
                raise ValueError(f"invalid {field}")
        if state["cash"] < -1e-8:
            raise ValueError("negative cash balance")
        return state
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Could not load portfolio at {path}: {exc}") from exc


def save_portfolio(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def portfolio_equity(state: Mapping, prices: Mapping[str, float]) -> float:
    value = float(state["cash"])
    stored = state.get("last_prices", {})
    for symbol, position in state.get("positions", {}).items():
        price = float(prices.get(symbol, stored.get(symbol, 0.0)))
        value += float(position["quantity"]) * price
    return value


def guarded_action(
    requested_action: str,
    indicators: Mapping[str, float | int],
    *,
    quantity: float,
    average_entry_price: float,
    stop_loss_fraction: float = 0.05,
) -> tuple[str, str]:
    """Apply deterministic vetoes; no Laya confidence value is used here."""
    close = float(indicators["close"])
    ema9 = float(indicators["ema9"])
    ema21 = float(indicators["ema21"])
    rsi14 = float(indicators["rsi14"])
    return_1 = float(indicators["return_1"])
    return_5 = float(indicators["return_5"])
    if quantity > 0 and average_entry_price > 0 and close <= average_entry_price * (1.0 - stop_loss_fraction):
        return "SELL", f"protective virtual stop reached ({stop_loss_fraction:.0%} below average entry)"
    requested_action = requested_action.upper()
    if requested_action == "BUY":
        tests = [
            (close > ema9, "price is not above EMA-9"),
            (ema9 > ema21, "EMA-9 is not above EMA-21"),
            (35.0 <= rsi14 <= 68.0, "RSI is outside the 35–68 entry band"),
            (return_5 >= 0.0, "five-candle momentum is negative"),
            (abs(return_1) < 0.025, "last-candle move is too large"),
        ]
        failed = [reason for passed, reason in tests if not passed]
        if failed:
            return "HOLD", "risk veto: " + "; ".join(failed)
        return "BUY", "Laya BUY passed deterministic entry checks"
    if requested_action == "SELL":
        if quantity <= 0:
            return "HOLD", "risk veto: no virtual position to sell"
        if ema9 < ema21 or return_5 <= -0.005:
            return "SELL", "Laya SELL passed deterministic trend/momentum check"
        return "HOLD", "risk veto: trend/momentum does not confirm an exit"
    return "HOLD", "Laya chose HOLD"


def apply_paper_fill(
    state: dict,
    symbol: str,
    action: str,
    *,
    candle_timestamp: int,
    market_price: float,
    prices: Mapping[str, float],
    trade_fraction: float = 0.05,
    max_position_fraction: float = 0.20,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
) -> dict | None:
    """Simulate an order, returning a fill record; returns None for HOLD/no capacity."""
    if action not in {"BUY", "SELL"}:
        return None
    if not math.isfinite(market_price) or market_price <= 0:
        raise ValueError("Market price must be positive and finite")
    if not (0 < trade_fraction <= max_position_fraction <= 1):
        raise ValueError("Require 0 < trade fraction <= max position fraction <= 1")
    if fee_bps < 0 or not (0 <= slippage_bps < 10_000):
        raise ValueError("Fee and slippage basis points are invalid")
    fee_rate = fee_bps / 10_000.0
    slippage = slippage_bps / 10_000.0
    position = state["positions"].get(symbol, {"quantity": 0.0, "average_entry_price": 0.0})
    quantity = float(position["quantity"])
    execution_price = market_price * (1.0 + slippage if action == "BUY" else 1.0 - slippage)
    now = datetime.now(timezone.utc).isoformat()

    if action == "BUY":
        equity = portfolio_equity(state, prices)
        current_market_value = quantity * market_price
        max_value = equity * max_position_fraction
        remaining_value = max(0.0, max_value - current_market_value)
        target_value = min(equity * trade_fraction, remaining_value)
        max_affordable_value = max(0.0, float(state["cash"])) / (1.0 + fee_rate)
        target_value = min(target_value, max_affordable_value)
        if target_value <= 1e-9:
            return None
        add_quantity = target_value / execution_price
        gross = add_quantity * execution_price
        fee = gross * fee_rate
        total_cost = gross + fee
        if total_cost > state["cash"] + 1e-8:
            return None
        old_cost_basis = quantity * float(position["average_entry_price"])
        new_quantity = quantity + add_quantity
        state["cash"] = max(0.0, float(state["cash"]) - total_cost)
        state["positions"][symbol] = {
            "quantity": new_quantity,
            "average_entry_price": (old_cost_basis + total_cost) / new_quantity,
        }
        side = "BUY"
    else:
        if quantity <= 0:
            return None
        gross = quantity * execution_price
        fee = gross * fee_rate
        proceeds = gross - fee
        state["cash"] = float(state["cash"]) + proceeds
        realized = proceeds - quantity * float(position["average_entry_price"])
        state["realized_pnl"] = float(state.get("realized_pnl", 0.0)) + realized
        state["positions"].pop(symbol, None)
        add_quantity = quantity
        total_cost = proceeds
        side = "SELL"

    fill = {
        "time": now,
        "symbol": symbol,
        "side": side,
        "quantity": add_quantity,
        "market_price": market_price,
        "simulated_fill_price": execution_price,
        "fee": fee,
        "cash_delta": -total_cost if side == "BUY" else total_cost,
        "candle_timestamp": candle_timestamp,
    }
    state.setdefault("trades", []).append(fill)
    return fill
