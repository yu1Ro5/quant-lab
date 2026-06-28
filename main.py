import os

import requests
from slack_sdk import WebClient


def get_usd_jpy() -> tuple[float, str]:
    url = "https://api.frankfurter.dev/v1/latest?from=USD&to=JPY"

    response = requests.get(url, timeout=10)
    response.raise_for_status()

    data = response.json()

    return data["rates"]["JPY"], data["date"]


def build_notification_message(rate: float, date: str) -> str:
    return f"USD/JPY rate: {rate} as of {date}"


def send_slack_notification(message: str, token: str | None = None, channel: str | None = None) -> bool:
    token = token or os.getenv("SLACK_BOT_TOKEN")
    channel = channel or os.getenv("SLACK_CHANNEL")
    if not token or not channel:
        print("Slack credentials not set; skipping notification.")
        return False

    client = WebClient(token=token)
    response = client.chat_postMessage(channel=channel, text=message)
    if not response.get("ok", False):
        raise RuntimeError(f"Slack notification failed: {response}")

    print("Slack notification sent.")
    return True


def main() -> None:
    rate, date = get_usd_jpy()

    print("=== USD/JPY ===")
    print(f"Rate : {rate}")
    print(f"Date : {date}")

    message = build_notification_message(rate, date)
    send_slack_notification(message)


if __name__ == "__main__":
    main()
