"""固定株数Sizingの単体テスト。"""

import unittest

from quant_lab.backtest.models import EntrySignal
from quant_lab.backtest.strategies.sizing import FixedShares


class SizingTests(unittest.TestCase):
    def test_fixed_shares_ignores_price_and_signal_details(self):
        sizing = FixedShares(3)
        for cash, price in [(5000, 100), (100, 200)]:
            self.assertEqual(sizing.size(cash, price, EntrySignal()), 3)
