"""足ラベルとは独立したORBの因果関係を固定する。"""

import unittest

import pandas as pd

from quant_lab.backtest.config import parse_config
from quant_lab.backtest.sessions import prepare_sessions
from quant_lab.backtest.strategies.entry import Orb

from .fixtures import candles, configuration, execute


class OrbTimingTests(unittest.TestCase):
    def test_first_six_bars_and_closed_signal_next_open_for_both_labels(self):
        baseline = None
        for label in ("start", "end"):
            raw = configuration()
            raw["data"]["bar_timestamp"] = label
            frame = candles()
            if label == "start":
                frame["datetime"] -= pd.Timedelta(minutes=5)
            session = prepare_sessions(frame, parse_config(raw)).sessions[0]
            orb = Orb()
            for count in range(1, 7):
                self.assertIsNone(orb.on_close(session.bars[:count], session))
            signal = orb.on_close(session.bars[:7], session)
            self.assertEqual(signal.details, {"orb_high": 101.0, "orb_low": 99.0})
            trade = execute(frame, raw).trades[0]
            self.assertEqual(trade.signal_time, session.bars[6].end)
            self.assertEqual(trade.entry_time, session.bars[7].start)
            self.assertEqual(trade.entry_time, trade.signal_time)
            self.assertEqual(trade.entry_reference_price, session.bars[7].open)
            if baseline is not None:
                self.assertEqual(trade, baseline)
            baseline = trade

    def test_intrabar_breakout_without_close_is_not_a_signal(self):
        frame = candles()
        frame.loc[6:, "high"] = 110.0
        frame.loc[6:, ["open", "low", "close"]] = [100.0, 99.0, 100.0]
        self.assertEqual(execute(frame).trades, [])

    def test_future_prices_do_not_change_entry_and_no_fabricated_final_exit(self):
        frame = candles()
        frame.loc[7:, ["open", "high", "low", "close"]] = [103.0, 103.1, 102.9, 103.0]
        original = execute(frame).trades[0]
        self.assertEqual(original.exit_reason, "market_close")
        self.assertEqual(
            original.exit_time, pd.Timestamp("2026-03-09 16:00", tz="America/New_York")
        )
        mutated = frame.copy()
        mutated.loc[50:, ["open", "high", "low", "close"]] = [
            104.0,
            104.1,
            103.9,
            104.0,
        ]
        trade = execute(mutated).trades[0]
        self.assertEqual(trade.entry_time, original.entry_time)
        self.assertEqual(trade.entry_price, original.entry_price)
        result = execute(frame.iloc[:-1])
        self.assertEqual(result.trades, [])
        self.assertEqual(len(result.exclusions), 1)
