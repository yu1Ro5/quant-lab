import csv
import json
import os
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch

import requests

import main


def response_payload(*, status=0, responsetime="2026-07-23T15:30:00.123Z"):
    return {
        "status": status,
        "responsetime": responsetime,
        "data": [
            {"symbol": "EUR_USD", "bid": "1.17000", "ask": "1.17010"},
            {"symbol": "USD_JPY", "bid": "146.125", "ask": "146.135"},
        ],
    }


def status_payload(*, status=0, market_status="OPEN"):
    return {"status": status, "data": {"status": market_status}}


class ApiTests(unittest.TestCase):
    def get_ticker(self, payload, market_payload=None):
        ticker_response = Mock()
        ticker_response.json.return_value = payload
        ticker_response.raise_for_status.return_value = None
        status_response = Mock()
        status_response.json.return_value = market_payload or status_payload()
        status_response.raise_for_status.return_value = None
        with patch("main.requests.get", side_effect=[ticker_response, status_response]) as request:
            ticker = main.get_usd_jpy()
        self.assertEqual(
            request.call_args_list,
            [
                call(main.TICKER_URL, timeout=main.HTTP_TIMEOUT_SECONDS),
                call(main.MARKET_STATUS_URL, timeout=main.HTTP_TIMEOUT_SECONDS),
            ],
        )
        return ticker

    def test_finds_usd_jpy_after_first_item_and_calculates_decimal_values(self):
        ticker = self.get_ticker(response_payload())
        self.assertEqual(ticker.bid, Decimal("146.125"))
        self.assertEqual(ticker.ask, Decimal("146.135"))
        self.assertEqual(ticker.rate, Decimal("146.130"))
        self.assertEqual(ticker.spread, Decimal("0.010"))
        self.assertIsInstance(ticker.rate, Decimal)
        self.assertEqual(ticker.rate_date, "2026-07-24")
        self.assertEqual(ticker.source_timestamp, datetime(2026, 7, 23, 15, 30, 0, 123000, timezone.utc))

    def test_handles_open_and_close(self):
        for status in ("OPEN", "CLOSE"):
            with self.subTest(status=status):
                self.assertEqual(
                    self.get_ticker(
                        response_payload(), status_payload(market_status=status)
                    ).market_status,
                    status,
                )

    def test_reads_market_status_from_status_endpoint_not_ticker(self):
        payload = response_payload()
        payload["data"][1]["status"] = "CLOSE"
        ticker = self.get_ticker(payload, status_payload(market_status="OPEN"))
        self.assertEqual(ticker.market_status, "OPEN")

    def test_rejects_unsuccessful_api_status(self):
        with self.assertRaisesRegex(ValueError, "unsuccessful status"):
            self.get_ticker(response_payload(status=5))

    def test_rejects_missing_usd_jpy(self):
        payload = response_payload()
        payload["data"] = payload["data"][:1]
        with self.assertRaisesRegex(ValueError, "does not contain USD_JPY"):
            self.get_ticker(payload)

    def test_rejects_missing_or_invalid_prices(self):
        for field, value in (("bid", None), ("ask", "not-a-number"), ("bid", "NaN")):
            payload = response_payload()
            if value is None:
                payload["data"][1].pop(field)
            else:
                payload["data"][1][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                self.get_ticker(payload)

    def test_rejects_invalid_timestamp(self):
        with self.assertRaisesRegex(ValueError, "invalid timestamp"):
            self.get_ticker(response_payload(responsetime="yesterday"))

    def test_rejects_invalid_market_status_response(self):
        for payload in (
            {"status": 0, "data": []},
            status_payload(market_status="MAINTENANCE"),
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                ValueError, "market status"
            ):
                self.get_ticker(response_payload(), payload)

    def test_handles_http_error_and_timeout(self):
        for error, message in (
            (requests.HTTPError("503 Server Error"), "HTTP request failed"),
            (requests.Timeout("slow"), "timed out"),
        ):
            with self.subTest(error=error), patch("main.requests.get", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, message):
                    main.get_usd_jpy()


class CsvHistoryTests(unittest.TestCase):
    def setUp(self):
        self.ticker = ApiTests().get_ticker(response_payload())

    def test_migrates_old_csv_without_losing_existing_values(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usd_jpy.csv"
            path.write_text(
                "fetched_at,rate_date,rate\n2026-07-21T17:02:27+00:00,2026-07-21,162.74\n",
                encoding="utf-8",
            )
            saved = main.save_usd_jpy_rate(
                self.ticker, path, datetime(2026, 7, 23, 16, 0, tzinfo=timezone.utc)
            )
            with path.open(newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                rows = list(reader)
                header = reader.fieldnames
        self.assertTrue(saved)
        self.assertEqual(header, main.RATE_HISTORY_COLUMNS)
        self.assertEqual(rows[0]["fetched_at"], "2026-07-21T17:02:27+00:00")
        self.assertEqual(rows[0]["rate_date"], "2026-07-21")
        self.assertEqual(rows[0]["rate"], "162.74")
        self.assertEqual(rows[0]["bid"], "")
        self.assertEqual(rows[1]["rate"], "146.130")
        self.assertEqual(rows[1]["market_status"], "OPEN")

    def test_skips_duplicate_rate_date_and_still_migrates_header(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usd_jpy.csv"
            path.write_text(
                "fetched_at,rate_date,rate\nold,2026-07-24,100\n", encoding="utf-8"
            )
            self.assertFalse(main.save_usd_jpy_rate(self.ticker, path))
            with path.open(newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                rows = list(reader)
                self.assertEqual(reader.fieldnames, main.RATE_HISTORY_COLUMNS)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["rate"], "100")

    def test_finds_latest_valid_rate_before_current_date_regardless_of_row_order(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usd_jpy.csv"
            path.write_text(
                "fetched_at,rate_date,rate\n"
                "third,2026-07-23,145.10\n"
                "first,2026-07-18,143.00\n"
                "same,2026-07-24,999.00\n"
                "second,2026-07-21,144.20\n",
                encoding="utf-8",
            )

            previous_rate = main.find_previous_rate(path, "2026-07-24")

        self.assertEqual(previous_rate, Decimal("145.10"))

    def test_uses_old_format_rate_across_calendar_gap(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usd_jpy.csv"
            path.write_text(
                "fetched_at,rate_date,rate\n"
                "friday,2026-07-17,142.50\n",
                encoding="utf-8",
            )

            previous_rate = main.find_previous_rate(path, "2026-07-21")

        self.assertEqual(previous_rate, Decimal("142.50"))

    def test_excludes_same_date_and_future_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usd_jpy.csv"
            path.write_text(
                "fetched_at,rate_date,rate\n"
                "past,2026-07-20,140\n"
                "same,2026-07-21,141\n"
                "future,2026-07-22,142\n",
                encoding="utf-8",
            )

            previous_rate = main.find_previous_rate(path, "2026-07-21")

        self.assertEqual(previous_rate, Decimal("140"))

    def test_skips_invalid_dates_and_non_positive_or_non_finite_rates(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usd_jpy.csv"
            path.write_text(
                "fetched_at,rate_date,rate\n"
                "valid,2026-07-15,139.50\n"
                "bad-date,not-a-date,150\n"
                "zero,2026-07-16,0\n"
                "negative,2026-07-17,-1\n"
                "nan,2026-07-18,NaN\n"
                "infinity,2026-07-19,Infinity\n",
                encoding="utf-8",
            )

            previous_rate = main.find_previous_rate(path, "2026-07-21")

        self.assertEqual(previous_rate, Decimal("139.50"))

    def test_returns_none_when_no_valid_comparison_data_exists(self):
        with TemporaryDirectory() as directory:
            missing_path = Path(directory) / "missing.csv"

            previous_rate = main.find_previous_rate(missing_path, "2026-07-21")

        self.assertIsNone(previous_rate)


class RateChangeTests(unittest.TestCase):
    def test_calculates_increase_amount_percent_and_yen_weakening(self):
        change = main.calculate_rate_change(Decimal("162.74"), Decimal("161.92"))

        self.assertEqual(change.amount, Decimal("0.82"))
        self.assertEqual(change.percent, Decimal("0.82") / Decimal("161.92") * 100)
        self.assertEqual(change.direction, "円安")

    def test_calculates_decrease_amount_percent_and_yen_strengthening(self):
        change = main.calculate_rate_change(Decimal("162.00"), Decimal("162.82"))

        self.assertEqual(change.amount, Decimal("-0.82"))
        self.assertEqual(change.percent, Decimal("-0.82") / Decimal("162.82") * 100)
        self.assertEqual(change.direction, "円高")

    def test_reports_no_change_for_equal_rates(self):
        change = main.calculate_rate_change(Decimal("162.00"), Decimal("162.00"))

        self.assertEqual(change.amount, Decimal("0.00"))
        self.assertEqual(change.percent, Decimal("0"))
        self.assertEqual(change.direction, "変化なし")

    def test_uses_unrounded_amount_for_direction(self):
        change = main.calculate_rate_change(Decimal("100.0001"), Decimal("100.0000"))

        message = main.build_notification_message(
            ApiTests().get_ticker(response_payload()),
            change,
        )

        self.assertEqual(change.direction, "円安")
        self.assertIn("前回比: 0.00円（0.00%）", message)
        self.assertIn("方向: 円安", message)


class AlertThresholdTests(unittest.TestCase):
    def test_uses_default_for_missing_empty_or_whitespace_value(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                if value is None:
                    environment = {}
                else:
                    environment = {"USD_JPY_ALERT_THRESHOLD_PERCENT": value}
                with patch.dict(os.environ, environment, clear=True):
                    self.assertEqual(
                        main.get_alert_threshold_percent(),
                        Decimal("1.0"),
                    )

    def test_reads_positive_decimal(self):
        self.assertEqual(
            main.get_alert_threshold_percent("0.5"),
            Decimal("0.5"),
        )

    def test_rejects_non_positive_invalid_and_non_finite_values(self):
        for value in (
            "0",
            "-0.1",
            "not-a-number",
            "NaN",
            "Infinity",
            "-Infinity",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                main.get_alert_threshold_percent(value)

    def test_alerts_using_absolute_unrounded_change_percent(self):
        cases = (
            ("0.999", "1.0", False),
            ("1.000", "1.0", True),
            ("1.004", "1.0", True),
            ("-1.000", "1.0", True),
            ("-1.004", "1.0", True),
            (None, "1.0", False),
        )
        for change_percent, threshold, expected in cases:
            with self.subTest(change_percent=change_percent, threshold=threshold):
                value = Decimal(change_percent) if change_percent is not None else None
                self.assertIs(
                    main.should_alert(value, Decimal(threshold)),
                    expected,
                )


class SlackNotificationTests(unittest.TestCase):
    def test_normal_message_is_unchanged(self):
        ticker = ApiTests().get_ticker(response_payload())
        change = main.calculate_rate_change(ticker.rate, Decimal("145.30"))

        message = main.build_notification_message(ticker, change)

        self.assertEqual(
            message,
            "USD/JPY 仲値: 146.130\n"
            "bid: 146.125\n"
            "ask: 146.135\n"
            "spread: 0.010\n"
            "基準時刻: 2026-07-23T15:30:00.123000+00:00\n"
            "市場ステータス: OPEN\n"
            "前回比: +0.83円（+0.57%）\n"
            "方向: 円安",
        )

    def test_message_contains_all_market_values(self):
        ticker = ApiTests().get_ticker(response_payload())
        change = main.calculate_rate_change(ticker.rate, Decimal("145.30"))
        message = main.build_notification_message(ticker, change)
        for value in ("146.130", "146.125", "146.135", "0.010", "2026-07-23T15:30:00.123000+00:00", "OPEN"):
            self.assertIn(value, message)
        self.assertIn("前回比: +0.83円（+0.57%）", message)
        self.assertIn("方向: 円安", message)

    def test_message_omits_direction_when_comparison_data_is_unavailable(self):
        ticker = ApiTests().get_ticker(response_payload())

        message = main.build_notification_message(ticker, None)

        self.assertIn("前回比: 比較データなし", message)
        self.assertNotIn("方向:", message)

    def test_formats_negative_values_with_sign_and_two_decimal_places(self):
        ticker = ApiTests().get_ticker(response_payload())
        change = main.calculate_rate_change(ticker.rate, Decimal("146.95"))

        message = main.build_notification_message(ticker, change)

        self.assertIn("前回比: -0.82円（-0.56%）", message)
        self.assertIn("方向: 円高", message)

    def test_adds_alert_heading_and_threshold_only_when_threshold_is_met(self):
        ticker = ApiTests().get_ticker(response_payload())
        change = main.RateChange(
            amount=Decimal("1.00"),
            percent=Decimal("1.004"),
            direction="円安",
        )

        message = main.build_notification_message(
            ticker,
            change,
            alert_threshold_percent=Decimal("1.004"),
        )

        self.assertTrue(message.startswith("⚠️ USD/JPY変動アラート\n"))
        self.assertTrue(message.endswith("\n設定閾値: 1.00%"))

    def test_preserves_small_positive_threshold_instead_of_displaying_zero(self):
        ticker = ApiTests().get_ticker(response_payload())
        change = main.RateChange(
            amount=Decimal("0.01"),
            percent=Decimal("0.004"),
            direction="円安",
        )

        message = main.build_notification_message(
            ticker,
            change,
            alert_threshold_percent=Decimal("0.004"),
        )

        self.assertTrue(message.endswith("\n設定閾値: 0.004%"))
        self.assertNotIn("設定閾値: 0.00%", message)

    def test_keeps_extremely_small_threshold_in_compact_exponent_form(self):
        ticker = ApiTests().get_ticker(response_payload())
        threshold = Decimal("1E-1000")
        change = main.RateChange(
            amount=Decimal("0.01"),
            percent=threshold,
            direction="円安",
        )

        message = main.build_notification_message(
            ticker,
            change,
            alert_threshold_percent=threshold,
        )

        self.assertTrue(message.endswith("\n設定閾値: 1E-1000%"))
        self.assertLess(len(message), 500)

    def test_does_not_add_alert_text_below_threshold_or_without_comparison(self):
        ticker = ApiTests().get_ticker(response_payload())
        below_threshold = main.RateChange(
            amount=Decimal("1.00"),
            percent=Decimal("0.999"),
            direction="円安",
        )
        for change in (below_threshold, None):
            with self.subTest(change=change):
                message = main.build_notification_message(
                    ticker,
                    change,
                    alert_threshold_percent=Decimal("1.0"),
                )
                self.assertNotIn("⚠️ USD/JPY変動アラート", message)
                self.assertNotIn("設定閾値:", message)

    def test_adds_alert_for_increase_and_decrease(self):
        ticker = ApiTests().get_ticker(response_payload())
        for percent, direction in (
            (Decimal("1.1"), "円安"),
            (Decimal("-1.1"), "円高"),
        ):
            with self.subTest(percent=percent):
                message = main.build_notification_message(
                    ticker,
                    main.RateChange(
                        amount=Decimal("1") if percent > 0 else Decimal("-1"),
                        percent=percent,
                        direction=direction,
                    ),
                    alert_threshold_percent=Decimal("1.0"),
                )
                self.assertIn("⚠️ USD/JPY変動アラート", message)
                self.assertIn("設定閾値: 1.00%", message)

    def test_skips_without_secrets(self):
        output = StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(output):
            self.assertFalse(main.send_slack_notification("hello"))
        self.assertIn("skipping notification", output.getvalue())

    def test_posts_with_credentials_without_external_connection(self):
        with patch("main.WebClient") as client_class:
            client_class.return_value.chat_postMessage.return_value = {"ok": True}
            self.assertTrue(main.send_slack_notification("hello", "token", "#alerts"))
        client_class.return_value.chat_postMessage.assert_called_once_with(channel="#alerts", text="hello")


def make_quote(
    rate: str = "102",
    source_timestamp: datetime | None = None,
    market_status: str = "OPEN",
) -> main.UsdJpyQuote:
    source = source_timestamp or datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    mid = Decimal(rate)
    return main.UsdJpyQuote(
        bid=mid - Decimal("0.005"),
        ask=mid + Decimal("0.005"),
        rate=mid,
        spread=Decimal("0.010"),
        source_timestamp=source,
        rate_date=source.astimezone(main.JST).date().isoformat(),
        market_status=market_status,
    )


def make_paths(directory: str) -> main.DataPaths:
    data = Path(directory) / "data"
    return main.DataPaths(
        daily_history=data / "usd_jpy.csv",
        hourly_history=data / "usd_jpy_hourly.csv",
        alert_state=data / "alert_state.json",
    )


def seed_comparison_history(paths: main.DataPaths) -> None:
    paths.daily_history.parent.mkdir(parents=True, exist_ok=True)
    paths.daily_history.write_text(
        "fetched_at,rate_date,rate\n"
        "old,2026-07-25,100\n",
        encoding="utf-8",
    )
    previous = make_quote(
        "100", datetime(2026, 7, 26, 11, tzinfo=timezone.utc)
    )
    main.save_usd_jpy_hourly_rate(
        previous,
        paths.hourly_history,
        fetched_at=datetime(2026, 7, 26, 11, 1, tzinfo=timezone.utc),
    )


class DataPathTests(unittest.TestCase):
    def test_uses_legacy_defaults_when_environment_is_unset(self):
        self.assertEqual(main.resolve_data_paths({}), main.DataPaths(
            Path("data/usd_jpy.csv"),
            Path("data/usd_jpy_hourly.csv"),
            Path("data/alert_state.json"),
        ))

    def test_data_directory_and_individual_paths_are_resolved_at_call_time(self):
        environment = {"QUANT_LAB_DATA_DIR": "/tmp/first"}
        self.assertEqual(
            main.resolve_data_paths(environment).daily_history,
            Path("/tmp/first/usd_jpy.csv"),
        )
        environment["QUANT_LAB_DATA_DIR"] = "/tmp/second"
        environment["USD_JPY_HOURLY_HISTORY_PATH"] = "/tmp/custom/hourly.csv"
        paths = main.resolve_data_paths(environment)
        self.assertEqual(paths.daily_history, Path("/tmp/second/usd_jpy.csv"))
        self.assertEqual(paths.hourly_history, Path("/tmp/custom/hourly.csv"))
        self.assertEqual(paths.alert_state, Path("/tmp/second/alert_state.json"))

    def test_save_default_path_observes_environment_changes(self):
        with TemporaryDirectory() as directory:
            with patch.dict(
                os.environ, {"QUANT_LAB_DATA_DIR": directory}, clear=True
            ):
                main.save_usd_jpy_rate(make_quote())
            self.assertTrue((Path(directory) / "usd_jpy.csv").exists())


class HourlyHistoryTests(unittest.TestCase):
    def test_creates_expected_header_and_keeps_first_value_in_bucket(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hourly.csv"
            first = make_quote(
                "100", datetime(2026, 7, 26, 12, 5, tzinfo=timezone.utc)
            )
            second = make_quote(
                "101", datetime(2026, 7, 26, 12, 55, tzinfo=timezone.utc)
            )
            self.assertTrue(main.save_usd_jpy_hourly_rate(first, path))
            self.assertFalse(main.save_usd_jpy_hourly_rate(second, path))
            with path.open(newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                rows = list(reader)
                self.assertEqual(reader.fieldnames, main.HOURLY_HISTORY_COLUMNS)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["rate"], "100")
            self.assertEqual(
                rows[0]["bucket_start_utc"], "2026-07-26T12:00:00+00:00"
            )

    def test_retains_exact_90_day_boundary_and_prunes_older_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hourly.csv"
            current = datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc)
            for timestamp, rate in (
                (current - timedelta(days=90), "99"),
                (current - timedelta(days=90, hours=1), "98"),
            ):
                main.save_usd_jpy_hourly_rate(make_quote(rate, timestamp), path)
            main.save_usd_jpy_hourly_rate(make_quote("100", current), path)
            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(
                [row["rate"] for row in rows],
                ["99", "100"],
            )

    def test_rejects_bad_header_without_overwriting_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hourly.csv"
            original = "wrong,header\nvalue,value\n"
            path.write_text(original, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported header"):
                main.save_usd_jpy_hourly_rate(make_quote(), path)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_rejects_existing_empty_file_without_initializing_it(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hourly.csv"
            path.touch()
            with self.assertRaisesRegex(ValueError, "empty"):
                main.save_usd_jpy_hourly_rate(make_quote(), path)
            self.assertEqual(path.stat().st_size, 0)

    def test_comparison_boundaries_and_exclusions(self):
        current = datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc)
        cases = (
            (timedelta(minutes=45), True),
            (timedelta(minutes=60), True),
            (timedelta(minutes=90), True),
            (timedelta(minutes=44, seconds=59), False),
            (timedelta(minutes=90, seconds=1), False),
        )
        for difference, expected in cases:
            with self.subTest(difference=difference), TemporaryDirectory() as directory:
                path = Path(directory) / "hourly.csv"
                main.save_usd_jpy_hourly_rate(
                    make_quote("100", current - difference), path
                )
                result = main.find_hourly_reference(path, current)
                self.assertIs(result is not None, expected)

    def test_selects_closest_to_60_minutes_and_newer_on_tie(self):
        current = datetime(2026, 7, 26, 12, 5, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hourly.csv"
            for difference, rate in (
                (timedelta(minutes=70), "70"),
                (timedelta(minutes=60), "60"),
            ):
                main.save_usd_jpy_hourly_rate(
                    make_quote(rate, current - difference), path
                )
            reference = main.find_hourly_reference(path, current)
            self.assertEqual(reference[1], Decimal("60"))

        current = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hourly.csv"
            for difference, rate in (
                (timedelta(minutes=70), "70"),
                (timedelta(minutes=50), "50"),
            ):
                main.save_usd_jpy_hourly_rate(
                    make_quote(rate, current - difference), path
                )
            reference = main.find_hourly_reference(path, current)
            self.assertEqual(reference[1], Decimal("50"))

    def test_uses_source_timestamp_and_skips_invalid_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hourly.csv"
            path.write_text(
                ",".join(main.HOURLY_HISTORY_COLUMNS)
                + "\n"
                + "2026-07-26T11:30:00+00:00,invalid,invalid,100,99,101,2,OPEN\n"
                + "2026-07-26T20:00:00+00:00,2026-07-26T11:30:00+00:00,"
                + "2026-07-26T11:00:00+00:00,101,100,102,2,OPEN\n",
                encoding="utf-8",
            )
            result = main.find_hourly_reference(
                path, datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc)
            )
            self.assertEqual(result[1], Decimal("101"))

    def test_invalid_bucket_timestamp_stops_hourly_processing_without_overwrite(self):
        current = datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hourly.csv"
            original = (
                ",".join(main.HOURLY_HISTORY_COLUMNS)
                + "\n"
                + "2026-07-26T11:31:00+00:00,2026-07-26T11:30:00+00:00,"
                + "not-a-time,100,99,101,2,OPEN\n"
            )
            path.write_text(original, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "bucket_start_utc"):
                main.find_hourly_reference(path, current)
            with self.assertRaisesRegex(ValueError, "bucket_start_utc"):
                main.save_usd_jpy_hourly_rate(
                    make_quote(
                        "101",
                        datetime(2026, 7, 26, 11, 45, tzinfo=timezone.utc),
                    ),
                    path,
                )
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_excludes_candidate_from_the_current_utc_bucket(self):
        current = datetime(2026, 7, 26, 12, 59, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hourly.csv"
            main.save_usd_jpy_hourly_rate(
                make_quote(
                    "100", datetime(2026, 7, 26, 12, 14, tzinfo=timezone.utc)
                ),
                path,
            )
            self.assertIsNone(main.find_hourly_reference(path, current))


class IndependentThresholdTests(unittest.TestCase):
    def test_defaults_empty_values_and_independent_values(self):
        for value in (None, "", "   "):
            environment = {}
            if value is not None:
                environment = {
                    main.HOURLY_ALERT_THRESHOLD_ENVIRONMENT_VARIABLE: value,
                    main.DAILY_ALERT_THRESHOLD_ENVIRONMENT_VARIABLE: value,
                }
            thresholds = main.get_alert_thresholds(environment)
            self.assertEqual(thresholds.hourly, Decimal("0.3"))
            self.assertEqual(thresholds.daily, Decimal("1.0"))
        self.assertEqual(
            main.get_alert_thresholds(
                {
                    main.HOURLY_ALERT_THRESHOLD_ENVIRONMENT_VARIABLE: "0.4",
                    main.DAILY_ALERT_THRESHOLD_ENVIRONMENT_VARIABLE: "1.2",
                }
            ),
            main.AlertThresholds(Decimal("0.4"), Decimal("1.2")),
        )

    def test_rejects_each_invalid_setting_before_api_or_writes(self):
        invalid_values = ("bad", "NaN", "Infinity", "0", "-1")
        for variable in (
            main.HOURLY_ALERT_THRESHOLD_ENVIRONMENT_VARIABLE,
            main.DAILY_ALERT_THRESHOLD_ENVIRONMENT_VARIABLE,
        ):
            for value in invalid_values:
                with (
                    self.subTest(variable=variable, value=value),
                    TemporaryDirectory() as directory,
                    patch.dict(os.environ, {variable: value}, clear=True),
                    patch("main.get_usd_jpy") as get_quote,
                    patch("main.save_usd_jpy_rate") as save_daily,
                    patch("main.send_slack_notification") as send,
                    self.assertRaises(ValueError),
                ):
                    main.prepare_delivery(Path(directory) / "envelope.json")
                get_quote.assert_not_called()
                save_daily.assert_not_called()
                send.assert_not_called()


class PrepareDeliverTests(unittest.TestCase):
    thresholds = main.AlertThresholds(Decimal("0.5"), Decimal("0.5"))

    def prepare(
        self,
        directory: str,
        quote: main.UsdJpyQuote | None = None,
        now: datetime | None = None,
    ) -> tuple[main.DataPaths, Path, main.PrepareResult]:
        paths = make_paths(directory)
        if not paths.daily_history.exists():
            seed_comparison_history(paths)
        envelope = Path(directory) / "delivery.json"
        result = main.prepare_delivery(
            envelope,
            paths=paths,
            thresholds=self.thresholds,
            ticker=quote or make_quote(),
            now=now or datetime(2026, 7, 26, 12, 1, tzinfo=timezone.utc),
        )
        return paths, envelope, result

    def test_prepare_never_calls_slack_and_combines_two_alerts(self):
        with TemporaryDirectory() as directory, patch(
            "main.send_slack_notification"
        ) as send:
            paths, envelope, result = self.prepare(directory)
            send.assert_not_called()
            self.assertEqual(result.delivery_kind, "strong_alert")
            value = json.loads(envelope.read_text(encoding="utf-8"))
            self.assertEqual(value["triggered_comparisons"], ["hourly", "daily"])
            self.assertIn("アラート対象: 1時間前比、日次比", value["message"])
            self.assertIn("1時間比較基準: 2026-07-26T11:00:00+00:00", value["message"])
            self.assertIn("日次比較基準: 2026-07-25", value["message"])
            self.assertTrue(paths.daily_history.exists())
            self.assertTrue(paths.hourly_history.exists())
            self.assertTrue(paths.alert_state.exists())

    def test_deliver_calls_slack_once_and_only_success_finalizes_strong_alert(self):
        with TemporaryDirectory() as directory:
            paths, envelope, _ = self.prepare(directory)
            with patch("main.send_slack_notification", return_value=True) as send:
                code, output = main.deliver_envelope(
                    envelope,
                    token="token",
                    channel="#alerts",
                    now=datetime(2026, 7, 26, 12, 2, tzinfo=timezone.utc),
                )
            self.assertEqual(code, main.EXIT_OK)
            self.assertTrue(output["state_commit_required"])
            send.assert_called_once()
            state = json.loads(paths.alert_state.read_text(encoding="utf-8"))
            self.assertIsNone(state["pending_alert"])
            self.assertEqual(
                state["comparisons"]["hourly"]["last_notified_at"],
                "2026-07-26T12:02:00+00:00",
            )
            self.assertEqual(
                state["comparisons"]["daily"]["last_notified_at"],
                "2026-07-26T12:02:00+00:00",
            )

    def test_deliver_rejects_stale_strong_alert_before_calling_slack(self):
        for changed_field, changed_value in (
            ("event_id", "replacement-event"),
            ("message", "replacement message"),
        ):
            with self.subTest(changed_field=changed_field), TemporaryDirectory() as directory:
                paths, envelope, _ = self.prepare(directory)
                state = json.loads(paths.alert_state.read_text(encoding="utf-8"))
                state["pending_alert"][changed_field] = changed_value
                main.write_alert_state(paths.alert_state, state)

                with patch("main.send_slack_notification") as send:
                    code, output = main.deliver_envelope(
                        envelope, token="token", channel="#alerts"
                    )

                self.assertEqual(code, main.EXIT_STATE_UPDATE_FAILED)
                self.assertEqual(output["status"], "delivery_rejected")
                self.assertFalse(output["state_commit_required"])
                send.assert_not_called()
                self.assertEqual(
                    json.loads(paths.alert_state.read_text(encoding="utf-8")), state
                )

    def test_delivery_claim_prevents_concurrent_duplicate_send(self):
        with TemporaryDirectory() as directory:
            paths, envelope, _ = self.prepare(directory)
            nested_result = []
            delivered_at = datetime(2026, 7, 26, 12, 2, tzinfo=timezone.utc)

            def send_once(*_args):
                nested_result.append(
                    main.deliver_envelope(
                        envelope,
                        token="token",
                        channel="#alerts",
                        now=delivered_at,
                    )
                )
                return True

            with patch(
                "main.send_slack_notification", side_effect=send_once
            ) as send:
                code, output = main.deliver_envelope(
                    envelope,
                    token="token",
                    channel="#alerts",
                    now=delivered_at,
                )

            self.assertEqual(code, main.EXIT_OK)
            self.assertTrue(output["state_commit_required"])
            send.assert_called_once()
            self.assertEqual(
                nested_result,
                [
                    (
                        main.EXIT_STATE_UPDATE_FAILED,
                        {
                            "status": "delivery_rejected",
                            "delivery_kind": "strong_alert",
                            "state_commit_required": False,
                            "error": "pending alert is already being delivered",
                        },
                    )
                ],
            )
            self.assertIsNone(
                json.loads(paths.alert_state.read_text(encoding="utf-8"))[
                    "pending_alert"
                ]
            )

    def test_prepare_does_not_replace_pending_alert_during_delivery(self):
        with TemporaryDirectory() as directory:
            paths, envelope, _ = self.prepare(directory)
            original_envelope = json.loads(envelope.read_text(encoding="utf-8"))
            concurrent_envelope = Path(directory) / "concurrent.json"

            def prepare_while_sending(*_args):
                result = main.prepare_delivery(
                    concurrent_envelope,
                    paths=paths,
                    thresholds=self.thresholds,
                    ticker=make_quote(
                        "105",
                        datetime(2026, 7, 26, 12, 2, tzinfo=timezone.utc),
                    ),
                    now=datetime(2026, 7, 26, 12, 3, tzinfo=timezone.utc),
                )
                self.assertEqual(result.delivery_kind, "strong_alert")
                return True

            with patch(
                "main.send_slack_notification", side_effect=prepare_while_sending
            ):
                code, _ = main.deliver_envelope(
                    envelope,
                    token="token",
                    channel="#alerts",
                    now=datetime(2026, 7, 26, 12, 2, tzinfo=timezone.utc),
                )

            self.assertEqual(code, main.EXIT_OK)
            self.assertEqual(
                json.loads(concurrent_envelope.read_text(encoding="utf-8"))[
                    "event_id"
                ],
                original_envelope["event_id"],
            )
            self.assertIsNone(
                json.loads(paths.alert_state.read_text(encoding="utf-8"))[
                    "pending_alert"
                ]
            )

    def test_expired_delivery_claim_can_be_retried(self):
        with TemporaryDirectory() as directory:
            paths, envelope, _ = self.prepare(directory)
            state = json.loads(paths.alert_state.read_text(encoding="utf-8"))
            state["pending_alert"]["delivery_claim"] = {
                "claim_id": "abandoned",
                "claimed_at": "2026-07-26T11:46:59+00:00",
            }
            main.write_alert_state(paths.alert_state, state)

            with patch("main.send_slack_notification", return_value=True) as send:
                code, _ = main.deliver_envelope(
                    envelope,
                    token="token",
                    channel="#alerts",
                    now=datetime(2026, 7, 26, 12, 2, tzinfo=timezone.utc),
                )

            self.assertEqual(code, main.EXIT_OK)
            send.assert_called_once()

    def test_failed_strong_alert_is_retried_but_failed_normal_is_not(self):
        with TemporaryDirectory() as directory:
            paths, envelope, _ = self.prepare(directory)
            with patch(
                "main.send_slack_notification", side_effect=RuntimeError("Slack down")
            ) as send:
                code, _ = main.deliver_envelope(envelope, token="x", channel="y")
            self.assertEqual(code, main.EXIT_DELIVERY_FAILED)
            send.assert_called_once()
            pending = json.loads(
                paths.alert_state.read_text(encoding="utf-8")
            )["pending_alert"]
            self.assertIsNotNone(pending)
            self.assertNotIn("delivery_claim", pending)

            _, retry_envelope, retry_result = self.prepare(
                directory,
                make_quote(
                    "100.1",
                    datetime(2026, 7, 26, 13, tzinfo=timezone.utc),
                    market_status="CLOSE",
                ),
                datetime(2026, 7, 26, 13, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(retry_result.delivery_kind, "strong_alert")
            self.assertEqual(
                json.loads(retry_envelope.read_text(encoding="utf-8"))["event_id"],
                pending["event_id"],
            )

        with TemporaryDirectory() as directory:
            paths = make_paths(directory)
            seed_comparison_history(paths)
            envelope = Path(directory) / "normal.json"
            main.prepare_delivery(
                envelope,
                paths=paths,
                thresholds=main.AlertThresholds(Decimal("10"), Decimal("10")),
                ticker=make_quote("100.1"),
                now=datetime(2026, 7, 26, 12, 1, tzinfo=timezone.utc),
            )
            with patch("main.send_slack_notification", return_value=False):
                code, _ = main.deliver_envelope(envelope, token="x", channel="y")
            self.assertEqual(code, main.EXIT_DELIVERY_FAILED)
            self.assertIsNone(
                json.loads(paths.alert_state.read_text(encoding="utf-8"))[
                    "pending_alert"
                ]
            )

    def test_cooldown_rearm_and_comparison_independence(self):
        with TemporaryDirectory() as directory:
            paths, envelope, _ = self.prepare(directory)
            with patch("main.send_slack_notification", return_value=True):
                main.deliver_envelope(
                    envelope,
                    token="x",
                    channel="y",
                    now=datetime(2026, 7, 26, 12, 2, tzinfo=timezone.utc),
                )
            _, second_envelope, second = self.prepare(
                directory,
                make_quote("104", datetime(2026, 7, 26, 13, tzinfo=timezone.utc)),
                datetime(2026, 7, 26, 13, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(second.delivery_kind, "normal")

            self.prepare(
                directory,
                make_quote(
                    "100.1", datetime(2026, 7, 26, 14, tzinfo=timezone.utc)
                ),
                datetime(2026, 7, 26, 14, 1, tzinfo=timezone.utc),
            )
            _, rearmed_envelope, rearmed = self.prepare(
                directory,
                make_quote("104", datetime(2026, 7, 26, 15, tzinfo=timezone.utc)),
                datetime(2026, 7, 26, 15, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(rearmed.delivery_kind, "strong_alert")
            self.assertIn(
                "daily",
                json.loads(rearmed_envelope.read_text(encoding="utf-8"))[
                    "triggered_comparisons"
                ],
            )

        with TemporaryDirectory() as directory:
            paths = make_paths(directory)
            seed_comparison_history(paths)
            state = main.default_alert_state()
            state["comparisons"]["hourly"] = {
                "is_active": True,
                "last_notified_at": "2026-07-26T11:30:00+00:00",
            }
            main.write_alert_state(paths.alert_state, state)
            envelope = Path(directory) / "independent.json"
            main.prepare_delivery(
                envelope,
                paths=paths,
                thresholds=self.thresholds,
                ticker=make_quote(),
                now=datetime(2026, 7, 26, 12, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(
                json.loads(envelope.read_text(encoding="utf-8"))[
                    "triggered_comparisons"
                ],
                ["daily"],
            )

    def test_three_hour_boundary_allows_renotification(self):
        with TemporaryDirectory() as directory:
            paths = make_paths(directory)
            seed_comparison_history(paths)
            state = main.default_alert_state()
            for name in ("hourly", "daily"):
                state["comparisons"][name] = {
                    "is_active": True,
                    "last_notified_at": "2026-07-26T09:01:00+00:00",
                }
            main.write_alert_state(paths.alert_state, state)
            envelope = Path(directory) / "delivery.json"
            main.prepare_delivery(
                envelope,
                paths=paths,
                thresholds=self.thresholds,
                ticker=make_quote(),
                now=datetime(2026, 7, 26, 12, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(
                json.loads(envelope.read_text(encoding="utf-8"))[
                    "triggered_comparisons"
                ],
                ["hourly", "daily"],
            )

    def test_close_saves_and_notifies_normally_without_changing_active_state(self):
        with TemporaryDirectory() as directory:
            paths = make_paths(directory)
            seed_comparison_history(paths)
            state = main.default_alert_state()
            state["comparisons"]["hourly"]["is_active"] = True
            state["comparisons"]["daily"]["is_active"] = True
            main.write_alert_state(paths.alert_state, state)
            envelope = Path(directory) / "close.json"
            result = main.prepare_delivery(
                envelope,
                paths=paths,
                thresholds=self.thresholds,
                ticker=make_quote("102", market_status="CLOSE"),
                now=datetime(2026, 7, 26, 12, 1, tzinfo=timezone.utc),
            )
            updated = json.loads(paths.alert_state.read_text(encoding="utf-8"))
            self.assertEqual(result.delivery_kind, "normal")
            self.assertTrue(result.daily_saved)
            self.assertTrue(result.hourly_saved)
            self.assertTrue(updated["comparisons"]["hourly"]["is_active"])
            self.assertTrue(updated["comparisons"]["daily"]["is_active"])
            self.assertIn(
                "市場ステータス: CLOSE",
                json.loads(envelope.read_text(encoding="utf-8"))["message"],
            )

    def test_missing_comparisons_do_not_clear_active_state(self):
        with TemporaryDirectory() as directory:
            paths = make_paths(directory)
            state = main.default_alert_state()
            state["comparisons"]["hourly"]["is_active"] = True
            state["comparisons"]["daily"]["is_active"] = True
            main.write_alert_state(paths.alert_state, state)
            envelope = Path(directory) / "missing.json"
            result = main.prepare_delivery(
                envelope,
                paths=paths,
                thresholds=self.thresholds,
                ticker=make_quote("100"),
                now=datetime(2026, 7, 26, 12, 1, tzinfo=timezone.utc),
            )
            updated = json.loads(paths.alert_state.read_text(encoding="utf-8"))
            self.assertEqual(result.delivery_kind, "normal")
            self.assertTrue(updated["comparisons"]["hourly"]["is_active"])
            self.assertTrue(updated["comparisons"]["daily"]["is_active"])

    def test_corrupt_state_is_not_overwritten_and_suppresses_strong_alert(self):
        with TemporaryDirectory() as directory:
            paths = make_paths(directory)
            seed_comparison_history(paths)
            original = "{broken"
            paths.alert_state.write_text(original, encoding="utf-8")
            envelope = Path(directory) / "corrupt.json"
            with patch("main.send_slack_notification") as send:
                result = main.prepare_delivery(
                    envelope,
                    paths=paths,
                    thresholds=self.thresholds,
                    ticker=make_quote(),
                    now=datetime(2026, 7, 26, 12, 1, tzinfo=timezone.utc),
                )
            self.assertFalse(result.state_healthy)
            self.assertEqual(result.delivery_kind, "normal")
            self.assertEqual(paths.alert_state.read_text(encoding="utf-8"), original)
            self.assertTrue(result.daily_saved)
            self.assertTrue(result.hourly_saved)
            send.assert_not_called()

    def test_missing_required_state_keys_are_not_overwritten(self):
        invalid_states = (
            {
                "version": 1,
                "comparisons": {
                    "hourly": {"is_active": False, "last_notified_at": None},
                    "daily": {"is_active": False, "last_notified_at": None},
                },
            },
            {
                "version": 1,
                "comparisons": {
                    "hourly": {"is_active": False},
                    "daily": {"is_active": False, "last_notified_at": None},
                },
                "pending_alert": None,
            },
        )
        for invalid_state in invalid_states:
            with self.subTest(invalid_state=invalid_state), TemporaryDirectory() as directory:
                paths = make_paths(directory)
                seed_comparison_history(paths)
                original = json.dumps(invalid_state)
                paths.alert_state.write_text(original, encoding="utf-8")
                envelope = Path(directory) / "invalid-state.json"

                result = main.prepare_delivery(
                    envelope,
                    paths=paths,
                    thresholds=self.thresholds,
                    ticker=make_quote(),
                    now=datetime(2026, 7, 26, 12, 1, tzinfo=timezone.utc),
                )

                self.assertFalse(result.state_healthy)
                self.assertEqual(result.delivery_kind, "normal")
                self.assertEqual(
                    paths.alert_state.read_text(encoding="utf-8"),
                    original,
                )
                self.assertTrue(result.daily_saved)
                self.assertTrue(result.hourly_saved)
                message = json.loads(envelope.read_text(encoding="utf-8"))[
                    "message"
                ]
                self.assertNotIn("⚠️ USD/JPY変動アラート", message)

    def test_new_pending_alert_replaces_old_pending_with_latest_only(self):
        with TemporaryDirectory() as directory:
            paths = make_paths(directory)
            seed_comparison_history(paths)
            state = main.default_alert_state()
            state["pending_alert"] = {
                "event_id": "old",
                "occurred_at": "2026-07-26T10:00:00+00:00",
                "triggered_comparisons": ["hourly"],
                "message": "old message",
            }
            main.write_alert_state(paths.alert_state, state)
            envelope = Path(directory) / "latest.json"
            main.prepare_delivery(
                envelope,
                paths=paths,
                thresholds=self.thresholds,
                ticker=make_quote(),
                now=datetime(2026, 7, 26, 12, 1, tzinfo=timezone.utc),
            )
            updated = json.loads(paths.alert_state.read_text(encoding="utf-8"))
            self.assertNotEqual(updated["pending_alert"]["event_id"], "old")
            self.assertNotIn("old message", updated["pending_alert"]["message"])
            self.assertEqual(
                json.loads(envelope.read_text(encoding="utf-8"))["event_id"],
                updated["pending_alert"]["event_id"],
            )

    def test_bad_hourly_file_does_not_block_daily_save_or_normal_envelope(self):
        with TemporaryDirectory() as directory:
            paths = make_paths(directory)
            paths.daily_history.parent.mkdir(parents=True, exist_ok=True)
            paths.daily_history.write_text(
                "fetched_at,rate_date,rate\nold,2026-07-25,100\n",
                encoding="utf-8",
            )
            original = "bad,header\n"
            paths.hourly_history.write_text(original, encoding="utf-8")
            envelope = Path(directory) / "delivery.json"
            result = main.prepare_delivery(
                envelope,
                paths=paths,
                thresholds=self.thresholds,
                ticker=make_quote("100.1"),
                now=datetime(2026, 7, 26, 12, 1, tzinfo=timezone.utc),
            )
            self.assertTrue(result.daily_saved)
            self.assertFalse(result.hourly_saved)
            self.assertEqual(paths.hourly_history.read_text(encoding="utf-8"), original)
            self.assertEqual(result.delivery_kind, "normal")

    def test_message_keeps_market_fields_and_marks_missing_comparisons(self):
        message = main.build_monitoring_message(make_quote(), None, None)
        for text in (
            "USD/JPY 仲値: 102",
            "bid: 101.995",
            "ask: 102.005",
            "spread: 0.010",
            "基準時刻: 2026-07-26T12:00:00+00:00",
            "市場ステータス: OPEN",
            "1時間前比: 比較データなし",
            "日次比: 比較データなし",
        ):
            self.assertIn(text, message)


if __name__ == "__main__":
    unittest.main()
