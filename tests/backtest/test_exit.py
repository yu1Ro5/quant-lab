"""exitのテスト。外部APIは使用しない。"""

import unittest
from dataclasses import replace

import pandas as pd

from quant_lab.backtest.models import Bar, EntrySignal, Position
from quant_lab.backtest.registry import EXIT
from quant_lab.backtest.strategies.exit import FixedStop
from quant_lab.backtest.validation import ConfigError


class ExitTests(unittest.TestCase):
    def test_fixed_exit_levels_gaps_and_same_bar_priority(self):
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


class NewExitTests(unittest.TestCase):
    def setUp(self):
        self.start = pd.Timestamp("2026-03-09T14:05Z")
        self.position = Position(self.start, self.start, 1, 100, 100, EntrySignal())

    def bar(self, minutes=0, opened=100, high=100, low=99.6):
        start = self.start + pd.Timedelta(minutes=minutes)
        return Bar(start, start + pd.Timedelta(minutes=5), opened, high, low, opened, 1)

    def test_trailing_uses_previous_high_and_resets_new_position(self):
        strategy = EXIT.create("trailing_stop", {})
        self.assertIsNone(strategy.on_bar(self.bar(high=110), self.position))
        decision = strategy.on_bar(
            self.bar(5, opened=108, high=109, low=107), self.position
        )
        self.assertEqual(
            (decision.price, decision.reason, decision.time),
            (108, "trailing_stop", self.start + pd.Timedelta(minutes=5)),
        )
        new = replace(self.position, entry_time=self.start + pd.Timedelta(days=1))
        self.assertIsNone(strategy.on_bar(self.bar(high=100), new))

    def test_time_open_precedes_future_low_but_open_gap_stop_wins(self):
        strategy = EXIT.create("time_exit", {"max_holding_minutes": 60})
        for opened, reason in [(100, "time_exit"), (98, "stop_loss")]:
            bar = self.bar(60, opened=opened, high=101, low=97)
            decision = strategy.on_bar(bar, self.position)
            self.assertEqual(
                (decision.price, decision.time, decision.reason),
                (opened, bar.start, reason),
            )
        self.assertEqual(
            strategy.on_bar(self.bar(55, low=99), self.position).reason, "stop_loss"
        )

    def test_time_validation(self):
        for params in [
            {},
            {"max_holding_minutes": 7},
            {"exit_time_et": "10:03"},
            {"exit_time_et": "25:00"},
            {"max_holding_minutes": 60, "exit_time_et": "11:00"},
        ]:
            with self.subTest(params=params), self.assertRaises(ConfigError):
                EXIT.create("time_exit", params)

    def test_late_entry_immediately_exits(self):
        strategy = EXIT.create("time_exit", {"exit_time_et": "10:00"})
        bar = self.bar()
        decision = strategy.on_bar(bar, self.position)
        self.assertEqual((decision.reason, decision.time), ("time_exit", bar.start))

    def test_trailing_line_never_falls_and_intrabar_hit(self):
        strategy = EXIT.create("trailing_stop", {})
        self.assertIsNone(strategy.on_bar(self.bar(high=102, low=99.6), self.position))
        self.assertIsNone(
            strategy.on_bar(
                self.bar(5, opened=101.7, high=101.8, low=101.6), self.position
            )
        )
        decision = strategy.on_bar(
            self.bar(10, opened=101.7, high=103, low=101.4), self.position
        )
        self.assertAlmostEqual(decision.price, 101.49)
        self.assertEqual(decision.time, self.start + pd.Timedelta(minutes=15))

    def test_engine_short_session_closes_all_exits_and_resets_runs(self):
        from tests.backtest.fixtures import candles, configuration, execute

        for strategy, params in [
            ("fixed_stop", {"stop_loss_pct": 10, "take_profit_pct": 10}),
            ("trailing_stop", {"stop_loss_pct": 10, "trailing_stop_pct": 10}),
            ("time_exit", {"stop_loss_pct": 10, "max_holding_minutes": 600}),
        ]:
            raw = configuration()
            raw["period"] = {"start": "2026-11-27", "end": "2026-11-27"}
            raw["exit"] = {"strategy": strategy, "params": params}
            frame = candles("2026-11-27", "13:00")
            result = execute(frame, raw)
            self.assertEqual(len(result.trades), 1)
            trade = result.trades[0]
            self.assertEqual(
                (trade.exit_reason, trade.exit_time),
                ("market_close", pd.Timestamp("2026-11-27T18:00Z")),
            )
            self.assertAlmostEqual(trade.net_pnl, trade.gross_pnl - trade.total_cost)
            self.assertAlmostEqual(result.final_cash, 5000 + trade.net_pnl)

    def test_time_first_available_start_and_exact_net_pnl(self):
        from quant_lab.backtest.config import ExecutionConfig
        from quant_lab.backtest.execution import SimulationBroker

        strategy = EXIT.create("time_exit", {"max_holding_minutes": 60})
        bar = self.bar(65, opened=101, high=102, low=90)
        decision = strategy.on_bar(bar, self.position)
        self.assertEqual(
            (decision.price, decision.time, decision.reason),
            (101, bar.start, "time_exit"),
        )
        broker = SimulationBroker(5000, ExecutionConfig(0, 0, 0))
        broker.buy(self.start, 100, 1, self.start, EntrySignal())
        trade = broker.sell(
            decision.time, decision.price, decision.reason, "2026-03-09", "US.AAPL"
        )
        self.assertEqual(
            (trade.entry_price, trade.exit_price, trade.gross_pnl, trade.net_pnl),
            (100, 101, 1, 1),
        )
        self.assertEqual(broker.cash, 5001)
