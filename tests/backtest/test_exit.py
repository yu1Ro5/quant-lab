"""exitのテスト。外部APIは使用しない。"""

import unittest

import pandas as pd

from quant_lab.backtest.models import EntrySignal


class ExitTests(unittest.TestCase):
    def test_fixed_exit_levels_gaps_and_same_bar_priority(self):
        from quant_lab.backtest.models import Bar, Position
        from quant_lab.backtest.strategies.exit import FixedStop

        start = pd.Timestamp("2026-03-09T14:05Z")
        end = start + pd.Timedelta(minutes=5)
        position = Position(start, start, 1, 100, 100, EntrySignal())
        strategy = FixedStop()
        cases = [
            (100, 101.5, 99.0, 99.5, "stop_loss"),
            (98, 100.0, 97.0, 98.0, "stop_loss"),
            (102, 103.0, 101.5, 102.0, "take_profit"),
            (100, 101.5, 99.6, 101.0, "take_profit"),
        ]
        for opened, high, low, price, reason in cases:
            with self.subTest(open=opened, high=high, low=low):
                decision = strategy.on_bar(
                    Bar(start, end, opened, high, low, 100, 1), position
                )
                self.assertEqual((decision.price, decision.reason), (price, reason))
        self.assertIsNone(
            strategy.on_bar(Bar(start, end, 100, 100.5, 99.6, 100, 1), position)
        )
