import csv
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from slack_sdk import WebClient
except ImportError:
    WebClient = None

RATE_HISTORY_COLUMNS = ["fetched_at", "rate_date", "rate"]
DEFAULT_RATE_HISTORY_PATH = Path("data/usd_jpy.csv")


def get_usd_jpy() -> tuple[float, str]:
    url = "https://api.frankfurter.dev/v1/latest?from=USD&to=JPY"

    response = requests.get(url, timeout=10)
    response.raise_for_status()

    data = response.json()

    return data["rates"]["JPY"], data["date"]


def build_notification_message(rate: float, date: str) -> str:
    return f"USD/JPY rate: {rate} as of {date}"


def _rate_date_already_saved(csv_path: Path, rate_date: str) -> bool:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return False

    with csv_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        return any(row.get("rate_date") == rate_date for row in reader)


def save_usd_jpy_rate(
    rate: float,
    rate_date: str,
    csv_path: str | Path = DEFAULT_RATE_HISTORY_PATH,
    fetched_at: datetime | None = None,
) -> bool:
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if _rate_date_already_saved(path, rate_date):
        return False

    fetched_at = fetched_at or datetime.now(timezone.utc)
    should_write_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=RATE_HISTORY_COLUMNS)
        if should_write_header:
            writer.writeheader()
        writer.writerow(
            {
                "fetched_at": fetched_at.isoformat(timespec="seconds"),
                "rate_date": rate_date,
                "rate": rate,
            }
        )

    return True


def send_slack_notification(message: str, token: str | None = None, channel: str | None = None) -> bool:
    token = token or os.getenv("SLACK_BOT_TOKEN")
    channel = channel or os.getenv("SLACK_CHANNEL")
    if not token or not channel:
        print("Slack credentials not set; skipping notification.")
        return False
    if WebClient is None:
        raise RuntimeError("slack-sdk is required to send Slack notifications.")

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

    saved = save_usd_jpy_rate(rate, date)
    if saved:
        print(f"CSV  : saved to {DEFAULT_RATE_HISTORY_PATH}")
    else:
        print(f"CSV  : {date} is already saved; skipping")

    message = build_notification_message(rate, date)
    send_slack_notification(message)


if __name__ == "__main__":
    main()
