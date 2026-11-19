"""executionのテスト。外部APIは使用しない。"""

import unittest

import pandas as pd

from quant_lab.backtest.models import EntrySignal


class ExecutionTests(unittest.TestCase):
    def test_broker_cost_breakdown_cash_and_fixed_sizing(self):
        from quant_lab.backtest.config import ExecutionConfig
        from quant_lab.backtest.execution import SimulationBroker
        from quant_lab.backtest.strategies.sizing import FixedShares

        start = pd.Timestamp("2026-03-09T14:05Z")
        broker = SimulationBroker(1000, ExecutionConfig(0.1, 2.0, 3.0))
        signal = EntrySignal()
        self.assertEqual(FixedShares(2).size(broker.cash, 100, signal), 2)
        self.assertTrue(broker.buy(start, 100, 2, start, signal))
        self.assertAlmostEqual(broker.cash, 799.8)
        trade = broker.sell(start, 110, "test", "2026-03-09", "US.AAPL")
        self.assertEqual(trade.gross_pnl, 20)
        self.assertAlmostEqual(trade.slippage, 0.084)
        self.assertAlmostEqual(trade.spread, 0.126)
        self.assertAlmostEqual(trade.commission, 0.2)
        self.assertAlmostEqual(trade.net_pnl, 19.59)
        self.assertAlmostEqual(broker.cash, 1019.59)
        self.assertIsNone(broker.position)
