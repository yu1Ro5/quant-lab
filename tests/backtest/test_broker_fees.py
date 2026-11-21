"""公表料金の固定試算と現金・費用整合性。"""

import unittest
from decimal import Decimal as D

import pandas as pd

from quant_lab.backtest.broker_fees import EFFECTIVE_DATES, SCHEDULE, webull_fees
from quant_lab.backtest.config import ExecutionConfig
from quant_lab.backtest.execution import SimulationBroker
from quant_lab.backtest.models import EntrySignal


class WebullFeesTests(unittest.TestCase):
    def test_fixed_schedule_and_effective_dates(self):
        self.assertEqual(SCHEDULE, "2026-11-21")
        self.assertEqual(EFFECTIVE_DATES["cat"], "2026-05-01")
        self.assertEqual(EFFECTIVE_DATES["sec"], "2026-04-02")
        self.assertEqual(EFFECTIVE_DATES["commission"], "2026-07-27T17:30:00+09:00")

    def test_buy_sell_routes_zero_and_one_share(self):
        buy = webull_fees(D("200"), 1, buy=True, route="exchange")
        self.assertEqual(buy.total, D("0.000003"))
        sell = webull_fees(D("200"), 1, buy=False, route="exchange")
        self.assertEqual(sell.sec, D("0.01"))
        self.assertEqual(sell.finra, D("0.01"))
        self.assertEqual(sell.commission, 0)
        self.assertEqual(
            webull_fees(D("200"), 1, buy=True, route="otc").cat, D("0.00000003")
        )
        self.assertEqual(webull_fees(D("200"), 0, buy=False, route="exchange").total, 0)

    def test_minimum_cap_and_decimal_boundary(self):
        fee = webull_fees(D("0.0001"), 100000, buy=False, route="exchange")
        self.assertEqual(fee.finra, D("9.79"))
        self.assertEqual(fee.sec, D("0.01"))
        fee = webull_fees(D("1000"), 100, buy=False, route="exchange")
        self.assertEqual(fee.sec, D("2.06"))
        fee = webull_fees(D("1000.0000001"), 100, buy=False, route="exchange")
        self.assertEqual(fee.sec, D("2.07"))
        self.assertEqual(fee.finra, D("0.02"))

    def test_buying_power_includes_cat_and_exact_boundary(self):
        t = pd.Timestamp("2026-01-02T15:00Z")
        costs = ExecutionConfig(0, 0, 0, cost_model="webull_jp_us_equity")
        broker = SimulationBroker(100, costs)
        self.assertFalse(broker.buy(t, 100, 1, t, EntrySignal()))
        broker = SimulationBroker(100.000003, costs)
        self.assertTrue(broker.buy(t, 100, 1, t, EntrySignal()))
        self.assertEqual(broker.cash, 0)

    def test_cash_cost_reconciliation_and_independent_friction(self):
        t = pd.Timestamp("2026-01-02T15:00Z")
        broker = SimulationBroker(
            1000, ExecutionConfig(0, 2, 1, cost_model="webull_jp_us_equity")
        )
        self.assertTrue(broker.buy(t, 100, 2, t, EntrySignal()))
        trade = broker.sell(t, 110, "test", "2026-01-02", "US.AAPL")
        self.assertAlmostEqual(trade.spread, 0.042)
        self.assertAlmostEqual(trade.slippage, 0.084)
        self.assertAlmostEqual(trade.cat_fee, 0.000012)
        self.assertAlmostEqual(
            trade.total_cost,
            trade.commission
            + trade.sec_fee
            + trade.finra_fee
            + trade.cat_fee
            + trade.spread
            + trade.slippage,
        )
        self.assertAlmostEqual(trade.net_pnl, trade.gross_pnl - trade.total_cost)
        self.assertAlmostEqual(broker.cash, 1000 + trade.net_pnl)
        self.assertAlmostEqual(trade.entry_price, 100.03)
        self.assertAlmostEqual(trade.exit_price, 109.967)

    def test_webull_configuration_rejects_duplicate_fees_and_unknown_schedule(self):
        from quant_lab.backtest.config import ConfigError, parse_config
        from tests.backtest.fixtures import configuration

        raw = configuration()
        raw["execution"]["cost_model"] = "webull_jp_us_equity"
        with self.assertRaises(ConfigError):
            parse_config(raw)
        raw["execution"]["commission_usd_per_side"] = 0
        parsed = parse_config(raw)
        self.assertEqual(parse_config(parsed.snapshot()), parsed)
        for key, value in [
            ("cost_model", "unknown"),
            ("cat_route", "unknown"),
            ("fee_schedule", "2026-01-01"),
        ]:
            invalid = parsed.snapshot()
            invalid["execution"][key] = value
            with self.subTest(key=key), self.assertRaises(ConfigError):
                parse_config(invalid)

    def test_zero_trades_has_no_fees(self):
        from quant_lab.backtest.config import parse_config
        from quant_lab.backtest.intraday_engine import ExperimentEngine
        from quant_lab.backtest.metrics import experiment_metrics
        from quant_lab.backtest.models import PreparedData
        from tests.backtest.fixtures import configuration

        raw = configuration()
        raw["execution"].update(
            cost_model="webull_jp_us_equity", commission_usd_per_side=0
        )
        result = ExperimentEngine(parse_config(raw)).run(PreparedData())
        metrics = experiment_metrics(result, 5000)
        self.assertEqual(metrics["total_cost"], 0)
        self.assertEqual(metrics["total_trades"], 0)
        self.assertEqual(result.final_cash, 5000)
