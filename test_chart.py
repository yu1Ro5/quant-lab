"""Tests for the USD/JPY chart model and component."""

import csv
import unittest
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import chart
import chart_app
import daily_kline


def write_klines(path: Path, count: int, *, reverse: bool = False) -> None:
    rows = []
    open_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(count):
        while open_time.weekday() >= 5:
            open_time += timedelta(days=1)
        rows.append(
            {
                "open_time": open_time.isoformat(),
                "bid_close": str(140 + index),
                "ask_close": str(Decimal(140 + index) + Decimal("0.02")),
            }
        )
        open_time += timedelta(days=1)
    if reverse:
        rows.reverse()
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["open_time", "bid_close", "ask_close"],
        )
        writer.writeheader()
        writer.writerows(rows)


class ChartDataTests(unittest.TestCase):
    def test_loads_latest_30_business_days_in_date_order(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "klines.csv"
            write_klines(path, 35, reverse=True)

            points = chart.load_chart_points(path)

        self.assertEqual(len(points), 30)
        self.assertEqual(points[0].trading_date, date(2026, 1, 8))
        self.assertEqual(points[-1].trading_date, date(2026, 2, 18))
        self.assertEqual(points[-1].close, Decimal("174.01"))
        self.assertEqual(
            points,
            sorted(points, key=lambda point: point.trading_date),
        )

    def test_displays_fewer_than_30_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "klines.csv"
            write_klines(path, 4)

            points = chart.load_chart_points(path)

        self.assertEqual(len(points), 4)

    def test_reports_missing_file_and_returns_empty_for_empty_data(self):
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.csv"
            empty = Path(directory) / "empty.csv"
            empty.touch()

            with self.assertRaisesRegex(
                chart.ChartDataMissingError,
                "日足CSVが見つかりません",
            ):
                chart.load_chart_points(missing)
            self.assertEqual(chart.load_chart_points(empty), [])

    def test_loads_csv_written_by_daily_kline_module(self):
        bid = daily_kline.Ohlc(
            open=Decimal("140.00"),
            high=Decimal("141.00"),
            low=Decimal("139.00"),
            close=Decimal("140.50"),
        )
        ask = daily_kline.Ohlc(
            open=Decimal("140.02"),
            high=Decimal("141.02"),
            low=Decimal("139.02"),
            close=Decimal("140.52"),
        )
        kline = daily_kline.DailyKline(
            open_time=datetime(2026, 1, 5, tzinfo=timezone.utc),
            bid=bid,
            ask=ask,
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usd_jpy_1day.csv"
            daily_kline.save_daily_klines([kline], path)

            points = chart.load_chart_points(path)

        self.assertEqual(
            points,
            [chart.ChartPoint(date(2026, 1, 5), Decimal("140.51"))],
        )

    def test_reports_invalid_data_as_a_chart_error(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.csv"
            path.write_text(
                "open_time,bid_close,ask_close\n"
                "2026-01-01T00:00:00+00:00,NaN,140.02\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(chart.ChartDataError, "Close"):
                chart.load_chart_points(path)

    def test_reports_non_utf8_data_as_a_chart_error(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-encoding.csv"
            path.write_bytes(b"\xff\xfe\x00")

            with self.assertRaisesRegex(chart.ChartDataError, "UTF-8"):
                chart.load_chart_points(path)

    def test_reports_malformed_csv_as_a_chart_error(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "malformed.csv"
            path.write_text(
                "open_time,bid_close,ask_close\n"
                '"2026-01-01T00:00:00+00:00,140.00,140.02\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(chart.ChartDataError, "形式が不正"):
                chart.load_chart_points(path)


class ChartComponentTests(unittest.TestCase):
    def test_chart_has_line_grid_axes_and_close_values(self):
        points = [
            chart.ChartPoint(date(2026, 1, 1), Decimal("140.01")),
            chart.ChartPoint(date(2026, 1, 2), Decimal("141.01")),
        ]

        specification = chart_app.build_chart(points).to_dict()

        self.assertEqual(specification["mark"]["type"], "line")
        self.assertTrue(specification["encoding"]["x"]["axis"]["grid"])
        self.assertTrue(specification["encoding"]["y"]["axis"]["grid"])
        self.assertEqual(specification["encoding"]["x"]["type"], "ordinal")
        self.assertEqual(
            specification["encoding"]["tooltip"][0]["type"],
            "nominal",
        )
        self.assertEqual(specification["encoding"]["y"]["title"], "Close価格")
        self.assertEqual(len(specification["data"]["values"]), 2)
        self.assertEqual(
            specification["data"]["values"][0]["date"],
            "2026-01-01",
        )

    def test_dark_mode_configuration_is_present(self):
        config = Path(".streamlit/config.toml").read_text(encoding="utf-8")

        self.assertIn('base = "dark"', config)
        self.assertIn('textColor = "#F8FAFC"', config)

    def test_renders_chart_and_loading_state_when_data_exists(self):
        points = [chart.ChartPoint(date(2026, 1, 1), Decimal("140.01"))]
        rendered_chart = Mock()
        with (
            patch.object(chart_app.st, "set_page_config"),
            patch.object(chart_app.st, "title"),
            patch.object(chart_app.st, "spinner", return_value=nullcontext()) as spinner,
            patch.object(chart_app.st, "caption"),
            patch.object(chart_app.st, "altair_chart") as altair_chart,
            patch("chart_app.load_chart_points", return_value=points),
            patch("chart_app.build_chart", return_value=rendered_chart),
        ):
            chart_app.render()

        spinner.assert_called_once_with("日足データを読み込んでいます...")
        altair_chart.assert_called_once_with(
            rendered_chart,
            width="stretch",
            theme="streamlit",
        )

    def test_renders_empty_state_without_chart(self):
        with (
            patch.object(chart_app.st, "set_page_config"),
            patch.object(chart_app.st, "title"),
            patch.object(chart_app.st, "spinner", return_value=nullcontext()),
            patch.object(chart_app.st, "info") as info,
            patch.object(chart_app.st, "altair_chart") as altair_chart,
            patch("chart_app.load_chart_points", return_value=[]),
        ):
            chart_app.render()

        info.assert_called_once_with("表示できる日足データがありません。")
        altair_chart.assert_not_called()

    def test_guides_daily_kline_fetch_when_csv_is_missing(self):
        missing = chart.ChartDataMissingError(
            "日足CSVが見つかりません: data/usd_jpy_1day.csv"
        )
        with (
            patch.object(chart_app.st, "set_page_config"),
            patch.object(chart_app.st, "title"),
            patch.object(chart_app.st, "spinner", return_value=nullcontext()),
            patch.object(chart_app.st, "info") as info,
            patch.object(chart_app.st, "altair_chart") as altair_chart,
            patch("chart_app.load_chart_points", side_effect=missing),
        ):
            chart_app.render()

        info.assert_called_once_with(
            "日足CSVが見つかりません: data/usd_jpy_1day.csv\n"
            "先に `uv run python daily_kline.py --from-year 2023 --to-year 2026` "
            "を実行して日足データを取得してください。"
        )
        altair_chart.assert_not_called()

    def test_renders_error_state_without_chart(self):
        with (
            patch.object(chart_app.st, "set_page_config"),
            patch.object(chart_app.st, "title"),
            patch.object(chart_app.st, "spinner", return_value=nullcontext()),
            patch.object(chart_app.st, "error") as error,
            patch.object(chart_app.st, "altair_chart") as altair_chart,
            patch(
                "chart_app.load_chart_points",
                side_effect=chart.ChartDataError("broken"),
            ),
        ):
            chart_app.render()

        error.assert_called_once_with("日足データを読み込めませんでした: broken")
        altair_chart.assert_not_called()


if __name__ == "__main__":
    unittest.main()
