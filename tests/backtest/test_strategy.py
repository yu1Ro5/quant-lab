import unittest

import pandas as pd

from quant_lab_backtest.strategy import BreakoutStrategy, Signal


class BreakoutStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = BreakoutStrategy()
        self.previous = pd.Series({"High": 1020, "Low": 990})

    def test_buys_when_close_exceeds_previous_high_while_flat(self) -> None:
        signal = self.strategy.generate_signal(
            self.previous, pd.Series({"Close": 1021}), False
        )
        self.assertIs(signal, Signal.BUY)

    def test_sells_when_close_falls_below_previous_low_while_held(self) -> None:
        signal = self.strategy.generate_signal(
            self.previous, pd.Series({"Close": 989}), True
        )
        self.assertIs(signal, Signal.SELL)

    def test_holds_at_boundaries_and_for_inapplicable_actions(self) -> None:
        cases = (
            (1020, False),
            (1021, True),
            (990, True),
            (989, False),
        )
        for close, has_position in cases:
            with self.subTest(close=close, has_position=has_position):
                self.assertIs(
                    self.strategy.generate_signal(
                        self.previous, pd.Series({"Close": close}), has_position
                    ),
                    Signal.HOLD,
                )


if __name__ == "__main__":
    unittest.main()
