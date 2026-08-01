import unittest
from pathlib import Path

import pandas as pd

from quant_lab_backtest.broker import DummyBroker
from quant_lab_backtest.data import load_parquet
from quant_lab_backtest.engine import BacktestEngine
from quant_lab_backtest.strategy import BreakoutStrategy

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = PROJECT_ROOT / "tests/fixtures/backtest_sample.parquet"


def frame(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["datetime", "open", "high", "low", "close"]
    ).assign(datetime=lambda data: pd.to_datetime(data["datetime"], utc=True))


class BacktestEngineTests(unittest.TestCase):
    def test_runs_committed_parquet_fixture(self) -> None:
        trades = BacktestEngine(BreakoutStrategy(), DummyBroker()).run(
            load_parquet(FIXTURE_PATH)
        )

        self.assertEqual(len(trades), 2)
        self.assertEqual([trade.profit_loss for trade in trades], [-30, 6])

    def test_executes_signals_at_next_open_and_closes_at_final_close(self) -> None:
        data = frame(
            [
                ("2026-07-01", 1000, 1020, 990, 1010),
                ("2026-07-02", 1012, 1035, 1005, 1030),
                ("2026-07-03", 1040, 1050, 1025, 1035),
                ("2026-07-06", 1030, 1035, 1000, 1010),
                ("2026-07-07", 1005, 1020, 995, 1015),
                ("2026-07-08", 1018, 1040, 1010, 1035),
                ("2026-07-09", 1045, 1060, 1040, 1055),
            ]
        )

        trades = BacktestEngine(BreakoutStrategy(), DummyBroker()).run(data)

        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0].entry_price, 1040)
        self.assertEqual(trades[0].exit_price, 1005)
        self.assertEqual(trades[1].entry_price, 1045)
        self.assertEqual(trades[1].exit_price, 1055)
        self.assertEqual(trades[1].exit_time, pd.Timestamp("2026-07-09", tz="UTC"))

    def test_does_not_execute_buy_signal_from_final_bar(self) -> None:
        data = frame(
            [
                ("2026-07-01", 100, 102, 99, 101),
                ("2026-07-02", 101, 104, 100, 103),
            ]
        )
        trades = BacktestEngine(BreakoutStrategy(), DummyBroker()).run(data)
        self.assertEqual(trades, [])

    def test_returns_no_trades_for_empty_or_single_bar(self) -> None:
        engine = BacktestEngine(BreakoutStrategy(), DummyBroker())
        self.assertEqual(engine.run(pd.DataFrame()), [])
        self.assertEqual(
            engine.run(frame([("2026-07-01", 100, 102, 99, 101)])), []
        )

    def test_resets_broker_state_for_each_run(self) -> None:
        data = load_parquet(FIXTURE_PATH)
        broker = DummyBroker()
        engine = BacktestEngine(BreakoutStrategy(), broker)

        first_trades = engine.run(data)
        second_trades = engine.run(data)

        self.assertEqual(len(first_trades), 2)
        self.assertEqual(len(second_trades), 2)
        self.assertEqual(second_trades, first_trades)
        self.assertEqual(broker.trades, tuple(second_trades))


if __name__ == "__main__":
    unittest.main()
