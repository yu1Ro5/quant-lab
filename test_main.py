import csv
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch
from contextlib import redirect_stdout

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
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            self.assertFalse(main.send_slack_notification("hello"))
        self.assertIn("skipping notification", output.getvalue())

    def test_posts_with_credentials_without_external_connection(self):
        with patch("main.WebClient") as client_class:
            client_class.return_value.chat_postMessage.return_value = {"ok": True}
            self.assertTrue(main.send_slack_notification("hello", "token", "#alerts"))
        client_class.return_value.chat_postMessage.assert_called_once_with(channel="#alerts", text="hello")


class MainFlowTests(unittest.TestCase):
    def test_rejects_invalid_threshold_before_external_or_csv_processing(self):
        with (
            patch.dict(
                os.environ,
                {"USD_JPY_ALERT_THRESHOLD_PERCENT": "NaN"},
                clear=True,
            ),
            patch("main.get_usd_jpy") as get_quote,
            patch("main.find_previous_rate") as find_rate,
            patch("main.save_usd_jpy_rate") as save_rate,
            patch("main.send_slack_notification") as send_message,
            self.assertRaises(ValueError),
        ):
            main.main()

        get_quote.assert_not_called()
        find_rate.assert_not_called()
        save_rate.assert_not_called()
        send_message.assert_not_called()

    def test_compares_before_saving_and_sends_the_generated_message(self):
        ticker = ApiTests().get_ticker(response_payload())
        events = []
        output = StringIO()

        def find_previous_rate(*_args):
            events.append("compare")
            return Decimal("145.30")

        def save_rate(*_args):
            events.append("save")
            return True

        def send_message(message):
            events.append(("send", message))
            return True

        with (
            patch("main.get_usd_jpy", return_value=ticker),
            patch("main.find_previous_rate", side_effect=find_previous_rate),
            patch("main.save_usd_jpy_rate", side_effect=save_rate),
            patch("main.send_slack_notification", side_effect=send_message),
            patch.dict(
                os.environ,
                {"USD_JPY_ALERT_THRESHOLD_PERCENT": "0.5"},
                clear=True,
            ),
            redirect_stdout(output),
        ):
            main.main()

        self.assertEqual(events[:2], ["compare", "save"])
        self.assertEqual(events[2][0], "send")
        self.assertIn("前回比: +0.83円（+0.57%）", events[2][1])
        self.assertIn("⚠️ USD/JPY変動アラート", events[2][1])
        self.assertIn("Alert: yes", output.getvalue())
        self.assertEqual(
            sum(event[0] == "send" for event in events if isinstance(event, tuple)),
            1,
        )

    def test_prints_no_alert_and_sends_once_when_change_is_below_threshold(self):
        ticker = ApiTests().get_ticker(response_payload())
        output = StringIO()
        with (
            patch("main.get_usd_jpy", return_value=ticker),
            patch("main.find_previous_rate", return_value=Decimal("145.30")),
            patch("main.save_usd_jpy_rate", return_value=False),
            patch("main.send_slack_notification") as send_message,
            patch.dict(
                os.environ,
                {"USD_JPY_ALERT_THRESHOLD_PERCENT": "1.0"},
                clear=True,
            ),
            redirect_stdout(output),
        ):
            main.main()

        self.assertIn("Alert: no", output.getvalue())
        send_message.assert_called_once()
        self.assertNotIn(
            "⚠️ USD/JPY変動アラート",
            send_message.call_args.args[0],
        )


if __name__ == "__main__":
    unittest.main()
