# Local Crypto Paper Trader

A simulated-only cryptocurrency trader that reads public Coinbase five-minute candles, asks Laya for a **BUY / SELL / HOLD** signal, applies deterministic risk checks, and records virtual fills in a JSON portfolio. It does not connect to exchange accounts, use private or order endpoints, request trade API credentials, or submit real orders.

## Run it locally

The trader itself uses Python's standard library. Laya is invoked from the installed CLI; for a standalone setup, install the pinned CPU runtime with `python3 -m pip install -r requirements-laya.txt`.

```bash
cd /workspace/crypto-paper-trader

# Lightweight local tests; no network or Laya model load
python3 -m unittest discover -s tests -v

# Preview public market data, Laya signals, and risk checks without fills or state writes
python3 -m paper_trader --dry-run --symbols BTC-USD

# Run one simulated cycle (default: BTC-USD and ETH-USD)
python3 -m paper_trader

# Keep polling every five minutes; stop with Ctrl-C
python3 -m paper_trader --loop --interval 300
```

The first Laya inference may need internet access to download its model. Set `LAYA_CLI=/path/to/laya` to use another executable. The `--state` option changes the virtual portfolio path. Useful options include `--symbols BTC-USD,ETH-USD`, `--initial-cash 10000`, `--trade-fraction 0.05`, `--max-position-fraction 0.20`, `--fee-bps 10`, and `--slippage-bps 5`.

## GitHub Actions schedule

`.github/workflows/paper-trader.yml` runs one paper-trading cycle on a five-minute Actions schedule (`*/5 * * * *`) using the standard `ubuntu-latest` hosted runner. It installs the pinned Laya CLI and CPU-only PyTorch runtime, caches pip downloads and the Hugging Face/PyTorch model directories where practical, runs the unit tests, and commits an updated portfolio only after a successful non-dry-run cycle. No repository secrets or exchange credentials are needed. Runs are serialized to prevent overlapping state updates.

To safely test the hosted setup, open **Actions → Laya paper trader → Run workflow** and leave **dry_run** checked (the default). This fetches public candles and runs Laya, but does not simulate fills or write portfolio state. Scheduled runs use paper mode. A manual run with dry-run unchecked also performs a simulated paper cycle and may update the public portfolio.

The schedule follows the five-minute candle cadence but is not a real-time guarantee: GitHub may delay or drop scheduled events during periods of high Actions load, and a run may not start exactly at a candle boundary. A cold run may also take longer while Laya and its model are downloaded; the workflow cache can be evicted and rebuilt. Scheduled workflows run from the repository's default branch.

**Public-state notice:** `data/portfolio.json` is tracked so Actions can restore and update the virtual portfolio between runs. Because this repository is public, its current state, positions, balances, simulated trade history, and every committed past state are visible to anyone. Do not put personal information or credentials in this file. The project remains paper-only; this workflow does not use real order endpoints.

To stop the schedule, open **Actions → Laya paper trader → ⋯ → Disable workflow**. This stops future scheduled and manual runs; it does not delete the repository or its public portfolio history. You can also disable it from the same menu later.

## What it does

- Fetches completed candles from Coinbase's [public product candles endpoint](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles). The request is read-only and unauthenticated; the bot never calls private or order endpoints. Coinbase documents five-minute granularity, a maximum of 300 candles per request, and public REST rate limits of 10 requests/second per IP ([rate limits](https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits)). This bot normally makes one request per configured asset every five minutes.
- Computes EMA-9, EMA-21, RSI-14, and short-horizon returns from completed candles. Incomplete or stale data is rejected.
- Batches configured asset snapshots into a single Laya CLI prediction. Laya's numeric confidence is printed as an **audit-only diagnostic**; it does not control trade sizing or bypass the rule checks. Confidence is not calibrated and must not be interpreted as a probability or reliable advice.
- Vetoes BUY recommendations unless trend and momentum tests pass; vetoes SELL when there is no position or no confirming weakness. A 5% virtual stop-loss can exit a position even if Laya says HOLD. A valid BUY uses up to 5% of portfolio equity and each asset is capped at 20% of equity. Shorting and leverage are disabled.
- Applies configurable assumed fees and adverse slippage to **virtual** fills only. SELL closes the whole simulated position.

## Limitations and safety

This is an educational paper-trading example, **not financial advice**, not a return forecast, and not a production trading system. Laya's action is a heuristic classification over a small indicator snapshot, not a validated strategy. Its checkpoint may be miscalibrated; even the deterministic gates can fail in fast markets, and the simulator omits order-book depth, partial fills, exchange outages, taxes, and many real-world costs. Simulated results do not predict real execution or future returns.

Market prices require internet access and can be delayed, missing, or throttled. On a market-data or model error the cycle fails closed rather than substituting a real order or making an unverified fill. The data provider is fixed to Coinbase public candles; only products available on that endpoint will work.
