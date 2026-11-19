"""entryのテスト。外部APIは使用しない。"""

import unittest

from quant_lab.backtest.config import parse_config
from quant_lab.backtest.sessions import prepare_sessions

from .fixtures import candles, configuration


class EntryTests(unittest.TestCase):
    def test_orb_requires_six_closed_opening_bars_and_strict_breakout(self):
        from quant_lab.backtest.models import Session
        from quant_lab.backtest.strategies.entry import Orb

        session = prepare_sessions(candles(), parse_config(configuration())).sessions[0]
        context = Session(session.date, session.open, session.close)
        strategy = Orb()
        for count in range(1, 7):
            self.assertIsNone(strategy.on_close(session.bars[:count], context))
        signal = strategy.on_close(session.bars[:7], context)
        self.assertEqual(signal.details, {"orb_high": 101.0, "orb_low": 99.0})
        self.assertIsNone(strategy.on_close(session.bars[1:7], context))
        from dataclasses import replace

        equal = (*session.bars[:6], replace(session.bars[6], close=101.0))
        self.assertIsNone(strategy.on_close(equal, context))
