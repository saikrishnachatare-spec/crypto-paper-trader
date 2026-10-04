from __future__ import annotations

import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paper_trader.app import run_cycle
from paper_trader.laya_client import ModelDecision
from paper_trader.market import Candle, ema, rsi, summarize
from paper_trader.paper import (
    apply_paper_fill,
    guarded_action,
    load_portfolio,
    new_portfolio,
    portfolio_equity,
    save_portfolio,
)


class MarketTests(unittest.TestCase):
    def test_candle_parse_and_reject_invalid(self):
        candle = Candle.from_coinbase_row([1_700_000_000, 9, 12, 10, 11, 4.5])
        self.assertEqual(candle.close, 11)
        with self.assertRaises(ValueError):
            Candle.from_coinbase_row([1_700_000_000, 12, 9, 10, 11, 4.5])

    def test_ema_and_rsi(self):
        self.assertAlmostEqual(ema([1, 2, 3, 4], 3), 3.0)
        self.assertEqual(rsi(list(range(1, 17))), 100.0)
        self.assertEqual(rsi(list(range(16, 0, -1))), 0.0)

    def test_summary_builds_expected_fields(self):
        candles = [Candle(i + 1000, p - 1, p + 1, p, p, 1) for i, p in enumerate(range(100, 130))]
        output = summarize(candles)
        self.assertEqual(output["candle_timestamp"], 1029)
        self.assertGreater(output["ema9"], output["ema21"])
        self.assertGreater(output["return_5"], 0)
        self.assertTrue(math.isfinite(output["rsi14"]))


class PaperTradingTests(unittest.TestCase):
    def setUp(self):
        self.features = {
            "candle_timestamp": 123,
            "close": 110.0, "ema9": 108.0, "ema21": 105.0,
            "rsi14": 55.0, "return_1": 0.002, "return_5": 0.02,
        }

    def test_model_buy_is_vetoed_if_trend_disagrees(self):
        features = {**self.features, "ema9": 100.0}
        action, reason = guarded_action("BUY", features, quantity=0, average_entry_price=0)
        self.assertEqual(action, "HOLD")
        self.assertIn("risk veto", reason)

    def test_stop_loss_can_exit_without_model_sell(self):
        action, reason = guarded_action("HOLD", {**self.features, "close": 90.0}, quantity=1, average_entry_price=100)
        self.assertEqual(action, "SELL")
        self.assertIn("stop", reason)

    def test_buy_sell_simulates_fees_slippage_and_no_short(self):
        state = new_portfolio(1000)
        prices = {"BTC-USD": 100.0}
        buy = apply_paper_fill(state, "BTC-USD", "BUY", candle_timestamp=123, market_price=100, prices=prices)
        self.assertIsNotNone(buy)
        self.assertGreater(buy["simulated_fill_price"], 100)
        self.assertGreater(buy["fee"], 0)
        self.assertGreater(state["positions"]["BTC-USD"]["quantity"], 0)
        self.assertLess(state["cash"], 1000)
        sell = apply_paper_fill(state, "BTC-USD", "SELL", candle_timestamp=456, market_price=100, prices=prices)
        self.assertIsNotNone(sell)
        self.assertLess(sell["simulated_fill_price"], 100)
        self.assertNotIn("BTC-USD", state["positions"])
        self.assertEqual(apply_paper_fill(state, "BTC-USD", "SELL", candle_timestamp=789, market_price=100, prices=prices), None)

    def test_position_cap_and_equity(self):
        state = new_portfolio(1000)
        prices = {"BTC-USD": 100.0}
        for index in range(10):
            apply_paper_fill(state, "BTC-USD", "BUY", candle_timestamp=index, market_price=100, prices=prices, trade_fraction=0.1, max_position_fraction=0.2)
        self.assertLessEqual(state["positions"]["BTC-USD"]["quantity"] * 100, portfolio_equity(state, prices) * 0.2 + 1e-6)

    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "portfolio.json"
            state = new_portfolio(250)
            save_portfolio(path, state)
            loaded = load_portfolio(path)
            self.assertEqual(loaded["cash"], 250)

    def test_same_candle_is_processed_only_once(self):
        class FakeClient:
            def predict(self, prompts):
                return {symbol: ModelDecision("BUY", 0.9) for symbol in prompts}

        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "portfolio.json"
            from argparse import Namespace
            args = Namespace(
                state=state_file, initial_cash=1000, symbols=["BTC-USD"], dry_run=False,
                trade_fraction=0.05, max_position_fraction=0.2, fee_bps=10, slippage_bps=5,
            )
            market = {"BTC-USD": self.features}
            with patch("paper_trader.app._load_market", return_value=(market, {})):
                run_cycle(args, FakeClient())
                run_cycle(args, FakeClient())
            saved = load_portfolio(state_file)
            self.assertEqual(len(saved["trades"]), 1)
            self.assertEqual(saved["last_processed_candles"]["BTC-USD"], 123)


if __name__ == "__main__":
    unittest.main()
