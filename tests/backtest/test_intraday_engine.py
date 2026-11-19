"""intraday_engineのテスト。外部APIは使用しない。"""

import unittest

import pandas as pd

from quant_lab.backtest.models import EntrySignal, ExitDecision

from .fixtures import candles, configuration, execute


class IntradayEngineTests(unittest.TestCase):
    def test_next_open_stop_priority_costs_and_one_trade(self):
        result = execute()
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(
            trade.signal_time, pd.Timestamp("2026-03-09 10:05", tz="America/New_York")
        )
        self.assertEqual(trade.entry_time, trade.signal_time)
        self.assertEqual(trade.entry_reference_price, 103.0)
        self.assertAlmostEqual(trade.entry_price, 103 * 1.0002)
        self.assertEqual(trade.exit_reason, "stop_loss")
        self.assertEqual(trade.shares, 1)
        self.assertAlmostEqual(trade.commission, 0.2)
        self.assertAlmostEqual(trade.net_pnl, trade.gross_pnl - trade.total_cost)
        self.assertAlmostEqual(result.final_cash, 5000 + trade.net_pnl)
        self.assertLess(trade.net_pnl, trade.gross_pnl)

    def test_take_profit_gap_and_market_close(self):
        for mode, expected in [
            ("target", "take_profit"),
            ("gap", "stop_loss"),
            ("close", "market_close"),
        ]:
            with self.subTest(mode=mode):
                frame = candles()
                frame.loc[7:, ["open", "high", "low", "close"]] = [
                    103.0,
                    103.1,
                    102.9,
                    103.0,
                ]
                if mode == "target":
                    frame.loc[8, "high"] = 106.0
                if mode == "gap":
                    frame.loc[8, ["open", "high", "low", "close"]] = [
                        90.0,
                        91.0,
                        89.0,
                        90.0,
                    ]
                trade = execute(frame).trades[0]
                self.assertEqual(trade.exit_reason, expected)
                if mode == "gap":
                    self.assertEqual(trade.exit_reference_price, 90.0)
                if mode == "close":
                    self.assertEqual(trade.exit_time.hour, 20)  # DST UTC

    def test_cash_limit_and_fixed_shares(self):
        raw = configuration()
        raw["portfolio"]["initial_capital_usd"] = 100
        result = execute(raw=raw)
        self.assertEqual(result.trades, [])
        self.assertGreater(len(result.warnings), 0)
        raw["portfolio"]["initial_capital_usd"] = 5000
        raw["sizing"]["params"]["shares"] = 3
        self.assertEqual(execute(raw=raw).trades[0].shares, 3)

    def test_components_independently_replaceable_and_history_is_causal(self):
        class Entry:
            def on_close(self, history, session):
                # 現在までの確定足のみ。次の高値105を参照できない。
                if len(history) == 7:
                    self.assertion = history[-1].high == 102
                    return EntrySignal({"custom": 1})
                return None

        class Exit:
            def on_bar(self, bar, position):
                return ExitDecision(bar.open, "custom", bar.start)

        class Sizing:
            def size(self, cash, price, signal):
                return 2

        entry = Entry()
        for replacements in [
            {"entry": entry},
            {"exit": Exit()},
            {"sizing": Sizing()},
            {"entry": entry, "exit": Exit(), "sizing": Sizing()},
        ]:
            result = execute(**replacements)
            self.assertEqual(len(result.trades), 1)
        self.assertTrue(entry.assertion)
        self.assertEqual(execute(exit=Exit()).trades[0].exit_reason, "custom")
        self.assertEqual(execute(sizing=Sizing()).trades[0].shares, 2)

    def test_final_two_bars_cannot_create_last_bar_entry(self):
        frame = candles()
        frame.loc[:, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.0, 100.0]
        frame.loc[76:, ["high", "close"]] = [102.0, 102.0]
        self.assertEqual(execute(frame).trades, [])

    def test_prefix_invariance_future_prices_do_not_change_earlier_trade(self):
        original = execute().trades[0]
        frame = candles()
        frame.loc[8:, ["open", "high", "low", "close"]] = [500.0, 501.0, 499.0, 500.0]
        self.assertEqual(execute(frame).trades[0], original)

    def test_multiple_days_close_each_day_and_limit_resets(self):
        raw = configuration()
        raw["period"]["end"] = "2026-03-10"
        frame = pd.concat([candles(), candles("2026-03-10")], ignore_index=True)
        result = execute(frame, raw)
        self.assertEqual(len(result.trades), 2)
        for trade in result.trades:
            self.assertEqual(
                trade.entry_time.tz_convert("America/New_York").date(),
                trade.exit_time.tz_convert("America/New_York").date(),
            )
