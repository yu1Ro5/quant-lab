import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import main


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


if __name__ == "__main__":
    unittest.main()
