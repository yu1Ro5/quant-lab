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

    def test_average_ratio_reason_month_and_flat_edge_cases(self):
        from dataclasses import replace

        from quant_lab.backtest.metrics import experiment_metrics

        result = execute()
        trade = result.trades[0]
        result.trades = [
            replace(trade, net_pnl=4, gross_pnl=5, total_cost=1),
            replace(trade, net_pnl=-2, gross_pnl=-1, total_cost=1),
            replace(trade, net_pnl=0, gross_pnl=1, total_cost=1),
        ]
        metrics = experiment_metrics(result, 5000)
        self.assertEqual(metrics["average_profit"], 4)
        self.assertEqual(metrics["average_loss"], -2)
        self.assertEqual(metrics["profit_loss_ratio"], 2)
        self.assertEqual(metrics["profit_factor"], 2)
        self.assertEqual(metrics["win_rate"], 1 / 3)
        self.assertEqual(metrics["exit_reason_performance"][0]["net_pnl"], 2)
        self.assertEqual(metrics["monthly_performance"][0]["group"], "2026-03")
        for values, ratio in [([], None), ([0], None), ([4], None), ([-2], 0)]:
            result.trades = [replace(trade, net_pnl=p) for p in values]
            self.assertEqual(
                experiment_metrics(result, 5000)["profit_loss_ratio"], ratio
            )
