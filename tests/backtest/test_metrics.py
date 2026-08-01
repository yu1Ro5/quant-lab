import unittest

import pandas as pd

from quant_lab_backtest.broker import Trade
from quant_lab_backtest.metrics import calculate_metrics


def trade(profit_loss: float) -> Trade:
    timestamp = pd.Timestamp("2026-07-01", tz="UTC")
    return Trade(timestamp, 100, timestamp, 100 + profit_loss, profit_loss)


class BacktestMetricsTests(unittest.TestCase):
    def test_aggregates_wins_losses_and_break_even(self) -> None:
        result = calculate_metrics([trade(20), trade(-5), trade(0)])
        self.assertEqual(result.total_trades, 3)
        self.assertAlmostEqual(result.win_rate, 1 / 3)
        self.assertEqual(result.total_profit_loss, 15)

    def test_returns_zero_metrics_without_trades(self) -> None:
        result = calculate_metrics([])
        self.assertEqual(result.total_trades, 0)
        self.assertEqual(result.win_rate, 0)
        self.assertEqual(result.total_profit_loss, 0)


if __name__ == "__main__":
    unittest.main()
