import csv
from datetime import datetime, timezone
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import main


class CsvHistoryTests(unittest.TestCase):
    def test_save_usd_jpy_rate_creates_csv_with_header_and_row(self) -> None:
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "data" / "usd_jpy.csv"
            fetched_at = datetime(2026, 6, 26, 12, 30, tzinfo=timezone.utc)

            saved = main.save_usd_jpy_rate(
                rate=161.65,
                rate_date="2026-06-26",
                csv_path=csv_path,
                fetched_at=fetched_at,
            )

            self.assertTrue(saved)
            self.assertTrue(csv_path.exists())
            with csv_path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(
            rows,
            [
                {
                    "fetched_at": "2026-06-26T12:30:00+00:00",
                    "rate_date": "2026-06-26",
                    "rate": "161.65",
                }
            ],
        )

    def test_save_usd_jpy_rate_skips_existing_rate_date(self) -> None:
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "usd_jpy.csv"
            first_saved = main.save_usd_jpy_rate(
                rate=161.65,
                rate_date="2026-06-26",
                csv_path=csv_path,
                fetched_at=datetime(2026, 6, 26, 12, 30, tzinfo=timezone.utc),
            )
            second_saved = main.save_usd_jpy_rate(
                rate=162.75,
                rate_date="2026-06-26",
                csv_path=csv_path,
                fetched_at=datetime(2026, 6, 26, 13, 30, tzinfo=timezone.utc),
            )

            with csv_path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))

        self.assertTrue(first_saved)
        self.assertFalse(second_saved)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rate"], "161.65")


class SlackNotificationTests(unittest.TestCase):
    def test_build_notification_message(self) -> None:
        message = main.build_notification_message(161.65, "2026-06-26")
        self.assertEqual(message, "USD/JPY rate: 161.65 as of 2026-06-26")

    def test_send_slack_notification_skips_without_credentials(self) -> None:
        buffer = StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(buffer):
            result = main.send_slack_notification("hello")

        self.assertFalse(result)
        self.assertIn("Slack credentials not set; skipping notification.", buffer.getvalue())

    def test_send_slack_notification_posts_when_credentials_are_provided(self) -> None:
        with patch("main.WebClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.chat_postMessage.return_value = {"ok": True}
            with patch.dict(
                os.environ,
                {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL": "#alerts"},
                clear=True,
            ):
                result = main.send_slack_notification("hello")

        self.assertTrue(result)
        mock_client_cls.assert_called_once_with(token="xoxb-test")
        mock_client.chat_postMessage.assert_called_once_with(channel="#alerts", text="hello")

    def test_send_slack_notification_requires_sdk_when_credentials_are_provided(self) -> None:
        with (
            patch("main.WebClient", None),
            patch.dict(
                os.environ,
                {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL": "#alerts"},
                clear=True,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "slack-sdk is required"):
                main.send_slack_notification("hello")


if __name__ == "__main__":
    unittest.main()
