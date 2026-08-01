import unittest

import pandas as pd

from quant_lab_backtest.broker import DummyBroker


class DummyBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = DummyBroker()
        self.entry_time = pd.Timestamp("2026-07-01", tz="UTC")
        self.exit_time = pd.Timestamp("2026-07-02", tz="UTC")

    def test_records_one_share_trade_and_profit_loss(self) -> None:
        self.broker.buy(self.entry_time, 1000)
        self.assertTrue(self.broker.has_position)

        self.broker.sell(self.exit_time, 1025)

        self.assertFalse(self.broker.has_position)
        self.assertEqual(len(self.broker.trades), 1)
        self.assertEqual(self.broker.trades[0].entry_price, 1000)
        self.assertEqual(self.broker.trades[0].exit_price, 1025)
        self.assertEqual(self.broker.trades[0].profit_loss, 25)

    def test_rejects_duplicate_buy_and_sell_without_position(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "保有している株がない"):
            self.broker.sell(self.exit_time, 1000)

        self.broker.buy(self.entry_time, 1000)
        with self.assertRaisesRegex(RuntimeError, "追加購入"):
            self.broker.buy(self.exit_time, 1010)

    def test_close_position_closes_only_when_held(self) -> None:
        self.broker.close_position(self.entry_time, 1000)
        self.assertEqual(self.broker.trades, ())

        self.broker.buy(self.entry_time, 1000)
        self.broker.close_position(self.exit_time, 990)
        self.assertEqual(self.broker.trades[0].profit_loss, -10)

    def test_rejects_invalid_price(self) -> None:
        for price in (0, -1, float("nan"), float("inf")):
            with self.subTest(price=price), self.assertRaisesRegex(ValueError, "売買価格"):
                self.broker.buy(self.entry_time, price)


if __name__ == "__main__":
    unittest.main()
