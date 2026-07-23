import csv
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from slack_sdk import WebClient

TICKER_URL = "https://forex-api.coin.z.com/public/v1/ticker"
RATE_HISTORY_COLUMNS = [
    "fetched_at",
    "rate_date",
    "rate",
    "bid",
    "ask",
    "spread",
    "source_timestamp",
    "market_status",
]
DEFAULT_RATE_HISTORY_PATH = Path("data/usd_jpy.csv")
JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class UsdJpyQuote:
    bid: Decimal
    ask: Decimal
    rate: Decimal
    spread: Decimal
    source_timestamp: datetime
    rate_date: str
    market_status: str


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("USD_JPY ticker is missing a valid timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"USD_JPY ticker has an invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("USD_JPY ticker timestamp must include a UTC offset.")
    return parsed.astimezone(timezone.utc)


def _parse_price(ticker: dict[str, object], field: str) -> Decimal:
    value = ticker.get(field)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"USD_JPY ticker has an invalid {field}: {value!r}") from exc


def get_usd_jpy() -> UsdJpyQuote:
    response = requests.get(TICKER_URL, timeout=10)
    response.raise_for_status()
    payload = response.json()

    if payload.get("status") != 0:
        raise ValueError(
            f"GMO Coin API returned an error status: {payload.get('status')!r}"
        )

    ticker = next(
        (item for item in payload.get("data", []) if item.get("symbol") == "USD_JPY"),
        None,
    )
    if ticker is None:
        raise ValueError("GMO Coin API response does not contain USD_JPY.")

    bid = _parse_price(ticker, "bid")
    ask = _parse_price(ticker, "ask")
    timestamp = _parse_timestamp(ticker.get("timestamp"))
    market_status = ticker.get("status")
    if market_status not in {"OPEN", "CLOSE"}:
        raise ValueError(
            f"USD_JPY ticker has an invalid market status: {market_status!r}"
        )

    return UsdJpyQuote(
        bid=bid,
        ask=ask,
        rate=(bid + ask) / 2,
        spread=ask - bid,
        source_timestamp=timestamp,
        rate_date=timestamp.astimezone(JST).date().isoformat(),
        market_status=market_status,
    )


def build_notification_message(quote: UsdJpyQuote) -> str:
    return (
        f"USD/JPY ({quote.rate_date}) | bid: {quote.bid} | ask: {quote.ask} | "
        f"mid: {quote.rate} | spread: {quote.spread} | market: {quote.market_status}"
    )


def _read_history(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return []
    with csv_path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def save_usd_jpy_rate(
    quote: UsdJpyQuote,
    csv_path: str | Path = DEFAULT_RATE_HISTORY_PATH,
    fetched_at: datetime | None = None,
) -> bool:
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _read_history(path)
    if any(row.get("rate_date") == quote.rate_date for row in rows):
        return False

    fetched_at = (fetched_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows.append(
        {
            "fetched_at": fetched_at.isoformat(timespec="seconds"),
            "rate_date": quote.rate_date,
            "rate": str(quote.rate),
            "bid": str(quote.bid),
            "ask": str(quote.ask),
            "spread": str(quote.spread),
            "source_timestamp": quote.source_timestamp.isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z"),
            "market_status": quote.market_status,
        }
    )

    # Rewriting also migrates legacy three-column files while retaining every row.
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file, fieldnames=RATE_HISTORY_COLUMNS, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)
    return True


def send_slack_notification(
    message: str, token: str | None = None, channel: str | None = None
) -> bool:
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
    quote = get_usd_jpy()
    print("=== USD/JPY ===")
    print(f"Bid    : {quote.bid}")
    print(f"Ask    : {quote.ask}")
    print(f"Mid    : {quote.rate}")
    print(f"Spread : {quote.spread}")
    print(f"Market : {quote.market_status}")
    print(f"Date   : {quote.rate_date}")

    if save_usd_jpy_rate(quote):
        print(f"CSV    : saved to {DEFAULT_RATE_HISTORY_PATH}")
    else:
        print(f"CSV    : {quote.rate_date} is already saved; skipping")
    send_slack_notification(build_notification_message(quote))


if __name__ == "__main__":
    main()
