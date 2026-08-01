import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
from pandas.testing import assert_frame_equal

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = PROJECT_ROOT / "scripts/generate_backtest_parquet.py"
COMMITTED_SAMPLE = PROJECT_ROOT / "examples/data/japanese_stock_sample.parquet"
COMMITTED_FIXTURE = PROJECT_ROOT / "tests/fixtures/backtest_sample.parquet"


class SampleDataTests(unittest.TestCase):
    def test_generator_reproduces_committed_parquet_contents(self) -> None:
        with TemporaryDirectory() as directory:
            sample = Path(directory) / "sample/sample.parquet"
            fixture = Path(directory) / "fixture/fixture.parquet"
            result = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR),
                    "--sample-output",
                    str(sample),
                    "--fixture-output",
                    str(fixture),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            assert_frame_equal(pd.read_parquet(sample), pd.read_parquet(COMMITTED_SAMPLE))
            assert_frame_equal(
                pd.read_parquet(fixture), pd.read_parquet(COMMITTED_FIXTURE)
            )


if __name__ == "__main__":
    unittest.main()
