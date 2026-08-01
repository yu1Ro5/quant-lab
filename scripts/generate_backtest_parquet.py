"""Generate the committed example and test-fixture Parquet files."""

import argparse
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE_PATH = PROJECT_ROOT / "examples/data/japanese_stock_sample.parquet"
DEFAULT_FIXTURE_PATH = PROJECT_ROOT / "tests/fixtures/backtest_sample.parquet"
COLUMNS = ["Datetime", "Open", "High", "Low", "Close", "Volume"]


def sample_data() -> pd.DataFrame:
    """Return readable example data that produces two completed trades."""
    return pd.DataFrame(
        [
            ("2026-07-01", 1000, 1020, 990, 1010, 100_000),
            ("2026-07-02", 1012, 1035, 1005, 1030, 120_000),
            ("2026-07-03", 1040, 1050, 1025, 1035, 110_000),
            ("2026-07-06", 1030, 1035, 1000, 1010, 130_000),
            ("2026-07-07", 1005, 1020, 995, 1015, 125_000),
            ("2026-07-08", 1018, 1040, 1010, 1035, 140_000),
            ("2026-07-09", 1045, 1060, 1040, 1055, 150_000),
        ],
        columns=COLUMNS,
    ).assign(Datetime=lambda frame: pd.to_datetime(frame["Datetime"], utc=True))


def fixture_data() -> pd.DataFrame:
    """Return compact fixture data covering normal and final closes."""
    return pd.DataFrame(
        [
            ("2026-08-03", 500, 510, 495, 505, 20_000),
            ("2026-08-04", 506, 520, 500, 515, 22_000),
            ("2026-08-05", 522, 525, 510, 512, 21_000),
            ("2026-08-06", 511, 515, 490, 495, 25_000),
            ("2026-08-07", 492, 505, 488, 500, 24_000),
            ("2026-08-10", 501, 515, 499, 510, 23_000),
            ("2026-08-11", 512, 520, 510, 518, 26_000),
        ],
        columns=COLUMNS,
    ).assign(Datetime=lambda frame: pd.to_datetime(frame["Datetime"], utc=True))


def write_parquet(data: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data.to_parquet(path, index=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="バックテスト用Parquetを生成します")
    parser.add_argument("--sample-output", type=Path, default=DEFAULT_SAMPLE_PATH)
    parser.add_argument("--fixture-output", type=Path, default=DEFAULT_FIXTURE_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    write_parquet(sample_data(), args.sample_output)
    write_parquet(fixture_data(), args.fixture_output)
    print(f"サンプルParquet生成: {args.sample_output}")
    print(f"テストParquet生成: {args.fixture_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
