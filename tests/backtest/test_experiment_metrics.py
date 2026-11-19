"""experiment_metricsのテスト。外部APIは使用しない。"""

import unittest

from .fixtures import candles, execute


class ExperimentMetricsTests(unittest.TestCase):
    def test_metrics_profit_factor_and_mark_to_market_drawdown(self):
        from quant_lab.backtest.metrics import experiment_metrics

        result = execute()
        metrics = experiment_metrics(result, 5000)
        self.assertEqual(metrics["profit_factor"], 0)
        self.assertAlmostEqual(metrics["net_pnl"], result.final_cash - 5000)
        self.assertGreater(metrics["max_drawdown_usd"], 0)
        frame = candles()
        frame.loc[:, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.0, 100.0]
        flat = experiment_metrics(execute(frame), 5000)
        self.assertIsNone(flat["profit_factor"])
        self.assertEqual(flat["max_drawdown_usd"], 0)
        self.assertEqual(flat["no_trade_days"], 1)
