import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch

import requests

import main


def ticker_payload(*, bid="161.650", ask="161.670"):
    return {
        "status": 0,
        "data": [
            {"symbol": "EUR_USD", "bid": "1.1", "ask": "1.2"},
            {
                "symbol": "USD_JPY",
                "bid": bid,
                "ask": ask,
                "timestamp": "2026-06-26T15:30:00.123Z",
            },
        ],
    }


def status_payload(status="OPEN"):
    return {"status": 0, "data": {"status": status}}


def quote(rate_date="2026-06-27"):
    return main.UsdJpyQuote(
        bid=Decimal("161.650"),
        ask=Decimal("161.670"),
        rate=Decimal("161.660"),
        spread=Decimal("0.020"),
        source_timestamp=datetime(2026, 6, 26, 15, 30, 0, 123000, tzinfo=timezone.utc),
        rate_date=rate_date,
        market_status="OPEN",
    )


class TickerTests(unittest.TestCase):
    @patch("main.requests.get")
    def test_gets_usd_jpy_and_calculates_decimal_values(self, get):
        ticker_response = Mock()
        ticker_response.json.return_value = ticker_payload()
        status_response = Mock()
        status_response.json.return_value = status_payload()
        get.side_effect = [ticker_response, status_response]
        result = main.get_usd_jpy()
        self.assertEqual(
            get.call_args_list,
            [call(main.TICKER_URL, timeout=10), call(main.STATUS_URL, timeout=10)],
        )
        ticker_response.raise_for_status.assert_called_once()
        status_response.raise_for_status.assert_called_once()
        self.assertEqual(result.bid, Decimal("161.650"))
        self.assertEqual(result.ask, Decimal("161.670"))
        self.assertEqual(result.rate, Decimal("161.660"))
        self.assertEqual(result.spread, Decimal("0.020"))
        self.assertEqual(
            result.rate_date, "2026-06-27"
        )  # UTC timestamp converted to JST

    @patch("main.requests.get")
    def test_supports_open_and_close(self, get):
        responses = []
        for payload in [
            ticker_payload(),
            status_payload("OPEN"),
            ticker_payload(),
            status_payload("CLOSE"),
        ]:
            response = Mock()
            response.json.return_value = payload
            responses.append(response)
        get.side_effect = responses
        self.assertEqual(main.get_usd_jpy().market_status, "OPEN")
        self.assertEqual(main.get_usd_jpy().market_status, "CLOSE")

    @patch("main.requests.get")
    def test_rejects_invalid_market_status_from_status_endpoint(self, get):
        responses = []
        for payload in [ticker_payload(), status_payload("MAINTENANCE")]:
            response = Mock()
            response.json.return_value = payload
            responses.append(response)
        get.side_effect = responses
        with self.assertRaisesRegex(ValueError, "invalid market status"):
            main.get_usd_jpy()

    @patch("main.requests.get")
    def test_rejects_api_error_and_missing_pair(self, get):
        responses = []
        for payload in [
            {"status": 7, "data": []},
            {"status": 0, "data": []},
            status_payload(),
        ]:
            response = Mock()
            response.json.return_value = payload
            responses.append(response)
        get.side_effect = responses
        with self.assertRaisesRegex(ValueError, "error status"):
            main.get_usd_jpy()
        with self.assertRaisesRegex(ValueError, "does not contain USD_JPY"):
            main.get_usd_jpy()

    @patch("main.requests.get")
    def test_rejects_missing_or_invalid_prices(self, get):
        responses = []
        for payload in [
            ticker_payload(bid=None),
            status_payload(),
            ticker_payload(ask="invalid"),
            status_payload(),
        ]:
            response = Mock()
            response.json.return_value = payload
            responses.append(response)
        get.side_effect = responses
        with self.assertRaisesRegex(ValueError, "invalid bid"):
            main.get_usd_jpy()
        with self.assertRaisesRegex(ValueError, "invalid ask"):
            main.get_usd_jpy()

    @patch("main.requests.get", side_effect=requests.Timeout("timed out"))
    def test_propagates_timeout(self, get):
        with self.assertRaises(requests.Timeout):
            main.get_usd_jpy()

    @patch("main.requests.get")
    def test_propagates_http_error(self, get):
        get.return_value.raise_for_status.side_effect = requests.HTTPError(
            "bad response"
        )
        with self.assertRaises(requests.HTTPError):
            main.get_usd_jpy()


class CsvHistoryTests(unittest.TestCase):
    def test_migrates_legacy_csv_without_losing_data(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usd_jpy.csv"
            path.write_text(
                "fetched_at,rate_date,rate\nold,2026-06-26,160.0\n", encoding="utf-8"
            )
            saved = main.save_usd_jpy_rate(
                quote(), path, datetime(2026, 6, 27, tzinfo=timezone.utc)
            )
            with path.open(newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                rows = list(reader)
                fields = reader.fieldnames
        self.assertTrue(saved)
        self.assertEqual(fields, main.RATE_HISTORY_COLUMNS)
        self.assertEqual(rows[0]["rate"], "160.0")
        self.assertEqual(rows[0]["bid"], "")
        self.assertEqual(rows[1]["bid"], "161.650")
        self.assertEqual(rows[1]["source_timestamp"], "2026-06-26T15:30:00.123Z")

    def test_skips_duplicate_rate_date(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usd_jpy.csv"
            self.assertTrue(main.save_usd_jpy_rate(quote(), path))
            self.assertFalse(main.save_usd_jpy_rate(quote(), path))
            with path.open(newline="", encoding="utf-8") as file:
                self.assertEqual(len(list(csv.DictReader(file))), 1)


class NotificationTests(unittest.TestCase):
    def test_message_contains_quote_details(self):
        message = main.build_notification_message(quote())
        for value in (
            "bid: 161.650",
            "ask: 161.670",
            "mid: 161.660",
            "spread: 0.020",
            "market: OPEN",
        ):
            self.assertIn(value, message)

    @patch("main.WebClient")
    def test_posts_to_slack(self, client_class):
        client_class.return_value.chat_postMessage.return_value = {"ok": True}
        self.assertTrue(main.send_slack_notification("hello", "token", "channel"))
        client_class.return_value.chat_postMessage.assert_called_once_with(
            channel="channel", text="hello"
        )


if __name__ == "__main__":
    unittest.main()
