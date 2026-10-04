"""Read-only Coinbase Exchange candle client and small indicator helpers."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import time
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://api.exchange.coinbase.com"
GRANULARITY_SECONDS = 300
MIN_CANDLES = 26


@dataclass(frozen=True)
class Candle:
    timestamp: int
    low: float
    high: float
    open: float
    close: float
    volume: float

    @classmethod
    def from_coinbase_row(cls, row: object) -> "Candle":
        if not isinstance(row, list) or len(row) < 6:
            raise ValueError("Malformed Coinbase candle row")
        try:
            ts = int(row[0])
            low, high, open_price, close, volume = map(float, row[1:6])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Non-numeric Coinbase candle field") from exc
        values = (low, high, open_price, close, volume)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Non-finite Coinbase candle field")
        if ts <= 0 or min(low, high, open_price, close) <= 0 or volume < 0:
            raise ValueError("Invalid Coinbase candle values")
        if low > high or not (low <= open_price <= high and low <= close <= high):
            raise ValueError("Inconsistent Coinbase candle OHLC values")
        return cls(ts, low, high, open_price, close, volume)


def fetch_candles(
    product: str,
    *,
    granularity: int = GRANULARITY_SECONDS,
    timeout: float = 12.0,
    now: float | None = None,
    min_candles: int = MIN_CANDLES,
) -> list[Candle]:
    """Fetch completed five-minute candles using only Coinbase's public endpoint."""
    if granularity not in (60, 300, 900, 3600, 21600, 86400):
        raise ValueError("Unsupported Coinbase candle granularity")
    if not product or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for ch in product):
        raise ValueError(f"Invalid product id: {product!r}")
    query = urlencode({"granularity": granularity})
    url = f"{BASE_URL}/products/{quote(product, safe='-')}/candles?{query}"
    request = Request(url, headers={"User-Agent": "local-crypto-paper-trader/1.0", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        raise RuntimeError(f"Coinbase returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not retrieve Coinbase candles: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected Coinbase response: expected a candle list")

    by_timestamp: dict[int, Candle] = {}
    for row in payload:
        candle = Candle.from_coinbase_row(row)
        by_timestamp[candle.timestamp] = candle
    wall_clock = time.time() if now is None else now
    current_bucket = int(wall_clock // granularity) * granularity
    # Exclude the still-forming candle; make decisions only on completed bars.
    candles = sorted(
        (c for c in by_timestamp.values() if c.timestamp + granularity <= current_bucket),
        key=lambda c: c.timestamp,
    )
    if len(candles) < min_candles:
        raise RuntimeError(f"Only {len(candles)} completed candles for {product}; need {min_candles}")
    age = wall_clock - (candles[-1].timestamp + granularity)
    if age > 2 * granularity:
        raise RuntimeError(f"Latest completed candle for {product} is stale ({int(age)} seconds old)")
    return candles[-300:]


def ema(values: Iterable[float], period: int) -> float:
    values = list(values)
    if period < 1 or len(values) < period:
        raise ValueError(f"Need at least {period} values for EMA")
    alpha = 2.0 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def rsi(values: Iterable[float], period: int = 14) -> float:
    values = list(values)
    if period < 1 or len(values) <= period:
        raise ValueError(f"Need more than {period} values for RSI")
    changes = [b - a for a, b in zip(values[-period - 1 : -1], values[-period:])]
    gains = sum(max(change, 0.0) for change in changes) / period
    losses = sum(max(-change, 0.0) for change in changes) / period
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    relative_strength = gains / losses
    return 100.0 - (100.0 / (1.0 + relative_strength))


def summarize(candles: list[Candle]) -> dict[str, float | int]:
    if len(candles) < MIN_CANDLES:
        raise ValueError(f"Need at least {MIN_CANDLES} candles")
    closes = [c.close for c in candles]
    return {
        "candle_timestamp": candles[-1].timestamp,
        "close": closes[-1],
        "ema9": ema(closes, 9),
        "ema21": ema(closes, 21),
        "rsi14": rsi(closes, 14),
        "return_1": closes[-1] / closes[-2] - 1.0,
        "return_5": closes[-1] / closes[-6] - 1.0,
        "return_20": closes[-1] / closes[-21] - 1.0,
        "candles_used": len(candles),
    }
