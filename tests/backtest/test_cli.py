import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from quant_lab.backtest.cli import main

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PATH = PROJECT_ROOT / "examples/data/japanese_stock_sample.parquet"


class CliTests(unittest.TestCase):
    def test_runs_committed_sample_end_to_end(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main([str(SAMPLE_PATH)])

        self.assertEqual(exit_code, 0)
        self.assertIn("Parquet読込成功: 7件", output.getvalue())
        self.assertIn("総取引回数: 2", output.getvalue())
        self.assertIn("勝率: 50.00%", output.getvalue())
        self.assertIn("総損益: -25.00円", output.getvalue())

    def test_returns_one_for_input_error(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            exit_code = main(["missing.parquet"])
        self.assertEqual(exit_code, 1)
        self.assertIn("エラー:", error.getvalue())


if __name__ == "__main__":
    unittest.main()
