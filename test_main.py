import csv
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout

import requests

import main


def response_payload(*, status=0, timestamp="2026-07-23T15:30:00.123Z", market_status="OPEN"):
    return {
        "status": status,
        "timestamp": timestamp,
        "data": [
            {"symbol": "EUR_USD", "bid": "1.17000", "ask": "1.17010", "status": "OPEN"},
            {"symbol": "USD_JPY", "bid": "146.125", "ask": "146.135", "status": market_status},
        ],
    }


class ApiTests(unittest.TestCase):
    def get_ticker(self, payload):
        response = Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        with patch("main.requests.get", return_value=response) as request:
            ticker = main.get_usd_jpy()
        request.assert_called_once_with(main.TICKER_URL, timeout=main.HTTP_TIMEOUT_SECONDS)
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
                self.assertEqual(self.get_ticker(response_payload(market_status=status)).market_status, status)

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
            self.get_ticker(response_payload(timestamp="yesterday"))

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


class SlackNotificationTests(unittest.TestCase):
    def test_message_contains_all_market_values(self):
        ticker = ApiTests().get_ticker(response_payload())
        message = main.build_notification_message(ticker)
        for value in ("146.130", "146.125", "146.135", "0.010", "2026-07-23T15:30:00.123000+00:00", "OPEN"):
            self.assertIn(value, message)

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


if __name__ == "__main__":
    unittest.main()
