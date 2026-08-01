"""日本株5分足OHLCVの取得・保存処理を検証するテスト。"""

import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import numpy as np
import pandas as pd

from quant_lab.stock import fetch


def sample_frame(*, multi_index: bool = False) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [
            "2026-07-29 06:25:00+00:00",
            "2026-07-28 00:00:00+00:00",
            "2026-07-29 00:00:00+00:00",
            "2026-07-30 00:00:00+00:00",
        ]
    )
    columns: pd.Index
    if multi_index:
        columns = pd.MultiIndex.from_tuples(
            [(name, "9434.T") for name in ("Open", "High", "Low", "Close", "Volume")]
        )
    else:
        columns = pd.Index(["Open", "High", "Low", "Close", "Volume"])
    return pd.DataFrame(
        [
            [151.0, 152.0, 150.0, 151.5, 3000],
            [149.0, 150.0, 148.0, 149.5, 1000],
            [150.0, 151.0, 149.0, 150.5, 2000],
            [152.0, 153.0, 151.0, 152.5, 4000],
        ],
        index=index,
        columns=columns,
    )


class StockFetchValidationTests(unittest.TestCase):
    def test_converts_tse_symbol_for_yahoo_finance(self) -> None:
        self.assertEqual(fetch.to_yahoo_symbol("9434"), "9434.T")

    def test_rejects_invalid_symbols(self) -> None:
        for symbol in ("943", "94345", "ABCD", "９４３４"):
            with (
                self.subTest(symbol=symbol),
                self.assertRaisesRegex(fetch.StockFetchError, "4桁"),
            ):
                fetch.to_yahoo_symbol(symbol)

    def test_rejects_invalid_dates_and_reversed_period(self) -> None:
        with self.assertRaisesRegex(fetch.StockFetchError, "YYYY-MM-DD"):
            fetch.parse_date("2026/07/29", "--from")
        with self.assertRaisesRegex(fetch.StockFetchError, "開始日"):
            fetch.validate_period(
                date(2026, 7, 30),
                date(2026, 7, 29),
                today=date(2026, 8, 1),
            )

    def test_sixty_day_limit_uses_injected_date_and_has_fixed_boundary(self) -> None:
        today = date(2026, 8, 1)
        fetch.validate_period(date(2026, 6, 3), today, today=today)
        with self.assertRaisesRegex(fetch.StockFetchError, "直近60日"):
            fetch.validate_period(date(2026, 6, 2), today, today=today)
        with self.assertRaisesRegex(fetch.StockFetchError, "今日以前"):
            fetch.validate_period(today, date(2026, 8, 2), today=today)


class StockFetchDataTests(unittest.TestCase):
    def test_fetch_uses_exclusive_next_day_to_include_requested_end_date(self) -> None:
        download = Mock(return_value=sample_frame())
        result = fetch.fetch_ohlcv(
            "9434.T",
            date(2026, 7, 28),
            date(2026, 7, 29),
            download=download,
        )

        self.assertEqual(len(result), 4)
        download.assert_called_once_with(
            "9434.T",
            start="2026-07-28",
            end="2026-07-30",
            interval="5m",
            auto_adjust=False,
            progress=False,
            threads=False,
        )

    def test_formats_six_columns_in_japan_time_sorted_and_in_period(self) -> None:
        result = fetch.format_ohlcv(
            sample_frame(), date(2026, 7, 28), date(2026, 7, 29)
        )

        self.assertEqual(result.columns.tolist(), fetch.OUTPUT_COLUMNS)
        self.assertEqual(len(result), 3)
        self.assertEqual(str(result["datetime"].dt.tz), "Asia/Tokyo")
        self.assertTrue(result["datetime"].is_monotonic_increasing)
        self.assertEqual(
            result["datetime"].iloc[-1].isoformat(), "2026-07-29T15:25:00+09:00"
        )
        self.assertEqual(result["volume"].dtype, np.dtype("int64"))
        self.assertTrue(
            all(
                result[name].dtype == np.dtype("float64")
                for name in ("open", "high", "low", "close")
            )
        )

    def test_formats_yfinance_multi_index_columns(self) -> None:
        result = fetch.format_ohlcv(
            sample_frame(multi_index=True),
            date(2026, 7, 28),
            date(2026, 7, 29),
        )
        self.assertEqual(result.columns.tolist(), fetch.OUTPUT_COLUMNS)
        self.assertEqual(len(result), 3)

    def test_treats_naive_market_times_as_japan_time(self) -> None:
        frame = sample_frame()
        frame.index = (
            pd.DatetimeIndex(frame.index).tz_convert("Asia/Tokyo").tz_localize(None)
        )
        result = fetch.format_ohlcv(frame, date(2026, 7, 28), date(2026, 7, 29))
        self.assertEqual(str(result["datetime"].dt.tz), "Asia/Tokyo")

    def test_rejects_empty_or_missing_columns(self) -> None:
        with self.assertRaisesRegex(fetch.StockFetchError, "見つかりません"):
            fetch.format_ohlcv(pd.DataFrame(), date(2026, 7, 28), date(2026, 7, 29))
        with self.assertRaisesRegex(fetch.StockFetchError, "Volume"):
            fetch.format_ohlcv(
                sample_frame().drop(columns="Volume"),
                date(2026, 7, 28),
                date(2026, 7, 29),
            )

    def test_rejects_missing_values_and_duplicate_datetimes(self) -> None:
        missing = sample_frame()
        missing.iloc[1, 0] = np.nan
        with self.assertRaisesRegex(fetch.StockFetchError, "欠損"):
            fetch.format_ohlcv(missing, date(2026, 7, 28), date(2026, 7, 29))

        duplicate = sample_frame()
        duplicate.index = pd.DatetimeIndex(
            [
                duplicate.index[0],
                duplicate.index[1],
                duplicate.index[1],
                duplicate.index[3],
            ]
        )
        with self.assertRaisesRegex(fetch.StockFetchError, "重複"):
            fetch.format_ohlcv(duplicate, date(2026, 7, 28), date(2026, 7, 29))

    def test_rejects_non_numeric_prices_and_fractional_volume(self) -> None:
        invalid_price = sample_frame().astype(object)
        invalid_price.iloc[1, 0] = "invalid"
        with self.assertRaisesRegex(fetch.StockFetchError, "数値型"):
            fetch.format_ohlcv(invalid_price, date(2026, 7, 28), date(2026, 7, 29))

        invalid_volume = sample_frame().astype({"Volume": "float64"})
        invalid_volume.iloc[1, 4] = 1.5
        with self.assertRaisesRegex(fetch.StockFetchError, "整数型"):
            fetch.format_ohlcv(invalid_volume, date(2026, 7, 28), date(2026, 7, 29))

    def test_wraps_download_errors_and_rejects_empty_download(self) -> None:
        with self.assertRaisesRegex(fetch.StockFetchError, "取得に失敗"):
            fetch.fetch_ohlcv(
                "9434.T",
                date(2026, 7, 28),
                date(2026, 7, 29),
                download=Mock(side_effect=RuntimeError("network down")),
            )
        with self.assertRaisesRegex(fetch.StockFetchError, "見つかりません"):
            fetch.fetch_ohlcv(
                "9434.T",
                date(2026, 7, 28),
                date(2026, 7, 29),
                download=Mock(return_value=pd.DataFrame()),
            )


class StockFetchPersistenceAndCliTests(unittest.TestCase):
    def test_saves_expected_filename_and_can_read_parquet(self) -> None:
        frame = fetch.format_ohlcv(sample_frame(), date(2026, 7, 28), date(2026, 7, 29))
        with TemporaryDirectory() as directory:
            path = fetch.output_path(
                "9434",
                date(2026, 7, 28),
                date(2026, 7, 29),
                Path(directory),
            )
            fetch.save_parquet(frame, path)
            restored = pd.read_parquet(path)

        self.assertEqual(path.name, "9434_2026-07-28_2026-07-29_5m.parquet")
        pd.testing.assert_frame_equal(restored, frame)

    def test_run_prints_saved_summary(self) -> None:
        output = StringIO()
        with TemporaryDirectory() as directory, redirect_stdout(output):
            path = fetch.run(
                [
                    "--symbol",
                    "9434",
                    "--from",
                    "2026-07-28",
                    "--to",
                    "2026-07-29",
                ],
                today=date(2026, 8, 1),
                download=Mock(return_value=sample_frame()),
                output_directory=Path(directory) / "stock",
            )

        text = output.getvalue()
        self.assertIn(f"保存しました: {path}", text)
        self.assertIn("銘柄コード: 9434", text)
        self.assertIn("データ件数: 3件", text)
        self.assertIn("最初の日時:", text)
        self.assertIn("最後の日時:", text)

    def test_main_shows_user_friendly_error(self) -> None:
        output = StringIO()
        with redirect_stderr(output):
            result = fetch.main(
                [
                    "--symbol",
                    "invalid",
                    "--from",
                    "2026-07-28",
                    "--to",
                    "2026-07-29",
                ]
            )
        self.assertEqual(result, 1)
        self.assertIn("エラー: 銘柄コードは4桁", output.getvalue())

    def test_module_help_is_available(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "quant_lab.stock.fetch", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--symbol", result.stdout)
        self.assertIn("--from", result.stdout)
        self.assertIn("--to", result.stdout)


if __name__ == "__main__":
    unittest.main()
