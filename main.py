import csv
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

import requests
from slack_sdk import WebClient

TICKER_URL = "https://forex-api.coin.z.com/public/v1/ticker"
MARKET_STATUS_URL = "https://forex-api.coin.z.com/public/v1/status"
HTTP_TIMEOUT_SECONDS = 10
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
DEFAULT_ALERT_THRESHOLD_PERCENT = Decimal("1.0")
ALERT_THRESHOLD_ENVIRONMENT_VARIABLE = "USD_JPY_ALERT_THRESHOLD_PERCENT"
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


# Keep the name used by PR #18 callers while exposing the quote terminology.
UsdJpyTicker = UsdJpyQuote


@dataclass(frozen=True)
class RateChange:
    amount: Decimal
    percent: Decimal
    direction: str


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("GMO API response has a missing or invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"GMO API response has an invalid timestamp: {value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_price(ticker: dict[str, object], field: str) -> Decimal:
    value = ticker.get(field)
    try:
        if value is None or isinstance(value, bool):
            raise InvalidOperation
        price = Decimal(str(value))
        if not price.is_finite():
            raise InvalidOperation
        return price
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"USD_JPY ticker has a missing or invalid {field}: {value!r}") from error


def _get_api_payload(url: str) -> dict[str, object]:
    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.Timeout as error:
        raise RuntimeError(f"GMO API request timed out after {HTTP_TIMEOUT_SECONDS} seconds") from error
    except requests.RequestException as error:
        raise RuntimeError(f"GMO API HTTP request failed: {error}") from error

    try:
        payload = response.json()
    except (ValueError, requests.JSONDecodeError) as error:
        raise ValueError("GMO API returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("GMO API response must be a JSON object")
    if payload.get("status") != 0:
        raise ValueError(f"GMO API returned unsuccessful status: {payload.get('status')!r}")
    return payload


def get_usd_jpy() -> UsdJpyQuote:
    payload = _get_api_payload(TICKER_URL)
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("GMO API response has an invalid data array")

    ticker = next(
        (item for item in data if isinstance(item, dict) and item.get("symbol") == "USD_JPY"),
        None,
    )
    if ticker is None:
        raise ValueError("GMO API response does not contain USD_JPY")

    bid = _parse_price(ticker, "bid")
    ask = _parse_price(ticker, "ask")
    source_timestamp = _parse_timestamp(payload.get("responsetime"))

    status_payload = _get_api_payload(MARKET_STATUS_URL)
    status_data = status_payload.get("data")
    if not isinstance(status_data, dict):
        raise ValueError("GMO API market status response has invalid data")
    market_status = status_data.get("status")
    if market_status not in {"OPEN", "CLOSE"}:
        raise ValueError(f"GMO API returned an invalid market status: {market_status!r}")
    return UsdJpyQuote(
        bid=bid,
        ask=ask,
        rate=(bid + ask) / Decimal("2"),
        spread=ask - bid,
        source_timestamp=source_timestamp,
        rate_date=source_timestamp.astimezone(JST).date().isoformat(),
        market_status=market_status,
    )


def calculate_rate_change(current_rate: Decimal, previous_rate: Decimal) -> RateChange:
    if (
        not current_rate.is_finite()
        or current_rate <= 0
        or not previous_rate.is_finite()
        or previous_rate <= 0
    ):
        raise ValueError("Rates must be finite positive Decimal values")
    amount = current_rate - previous_rate
    if amount > 0:
        direction = "円安"
    elif amount < 0:
        direction = "円高"
    else:
        direction = "変化なし"
    return RateChange(
        amount=amount,
        percent=amount / previous_rate * Decimal("100"),
        direction=direction,
    )


def get_alert_threshold_percent(value: str | None = None) -> Decimal:
    if value is None:
        value = os.getenv(ALERT_THRESHOLD_ENVIRONMENT_VARIABLE)
    if value is None or not value.strip():
        return DEFAULT_ALERT_THRESHOLD_PERCENT
    try:
        threshold = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(
            f"{ALERT_THRESHOLD_ENVIRONMENT_VARIABLE} must be a positive finite number"
        ) from error
    if not threshold.is_finite() or threshold <= 0:
        raise ValueError(
            f"{ALERT_THRESHOLD_ENVIRONMENT_VARIABLE} must be a positive finite number"
        )
    return threshold


def should_alert(
    change_percent: Decimal | None,
    threshold_percent: Decimal,
) -> bool:
    return change_percent is not None and abs(change_percent) >= threshold_percent


def _format_change_value(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"))
    if rounded == 0:
        return "0.00"
    return f"{rounded:+.2f}"


def build_notification_message(
    ticker: UsdJpyQuote,
    change: RateChange | None,
    alert_threshold_percent: Decimal | None = None,
) -> str:
    message = (
        f"USD/JPY 仲値: {ticker.rate}\n"
        f"bid: {ticker.bid}\nask: {ticker.ask}\nspread: {ticker.spread}\n"
        f"基準時刻: {ticker.source_timestamp.isoformat()}\n市場ステータス: {ticker.market_status}"
    )
    if change is None:
        return f"{message}\n前回比: 比較データなし"
    message = (
        f"{message}\n"
        f"前回比: {_format_change_value(change.amount)}円"
        f"（{_format_change_value(change.percent)}%）\n"
        f"方向: {change.direction}"
    )
    if alert_threshold_percent is None or not should_alert(
        change.percent,
        alert_threshold_percent,
    ):
        return message
    return (
        f"⚠️ USD/JPY変動アラート\n{message}\n"
        f"設定閾値: {alert_threshold_percent:.2f}%"
    )


def find_previous_rate(
    csv_path: str | Path,
    current_rate_date: str,
) -> Decimal | None:
    path = Path(csv_path)
    if not path.exists() or path.stat().st_size == 0:
        return None

    try:
        current_date = date.fromisoformat(current_rate_date)
    except ValueError as error:
        raise ValueError(f"Invalid current rate_date: {current_rate_date!r}") from error

    latest: tuple[date, Decimal] | None = None
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not {"rate_date", "rate"}.issubset(reader.fieldnames):
            raise ValueError(f"CSV has an unsupported header: {reader.fieldnames}")
        for row in reader:
            try:
                candidate_date = date.fromisoformat(row.get("rate_date", ""))
                candidate_rate = Decimal(row.get("rate", ""))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if (
                candidate_date >= current_date
                or not candidate_rate.is_finite()
                or candidate_rate <= 0
            ):
                continue
            if latest is None or candidate_date > latest[0]:
                latest = (candidate_date, candidate_rate)
    return latest[1] if latest else None


def _read_and_migrate_history(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return []
    with csv_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not {"fetched_at", "rate_date", "rate"}.issubset(reader.fieldnames):
            raise ValueError(f"CSV has an unsupported header: {reader.fieldnames}")
        rows = [{column: row.get(column, "") or "" for column in RATE_HISTORY_COLUMNS} for row in reader]
        needs_migration = reader.fieldnames != RATE_HISTORY_COLUMNS
    if needs_migration:
        _write_history(csv_path, rows)
    return rows


def _write_history(csv_path: Path, rows: list[dict[str, str]]) -> None:
    with NamedTemporaryFile("w", newline="", encoding="utf-8", dir=csv_path.parent, delete=False) as file:
        temporary_path = Path(file.name)
        writer = csv.DictWriter(file, fieldnames=RATE_HISTORY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(csv_path)


def save_usd_jpy_rate(
    ticker: UsdJpyQuote,
    csv_path: str | Path = DEFAULT_RATE_HISTORY_PATH,
    fetched_at: datetime | None = None,
) -> bool:
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _read_and_migrate_history(path)
    if any(row["rate_date"] == ticker.rate_date for row in rows):
        return False
    fetched_at = (fetched_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows.append(
        {
            "fetched_at": fetched_at.isoformat(timespec="seconds"),
            "rate_date": ticker.rate_date,
            "rate": str(ticker.rate),
            "bid": str(ticker.bid),
            "ask": str(ticker.ask),
            "spread": str(ticker.spread),
            "source_timestamp": ticker.source_timestamp.isoformat(),
            "market_status": ticker.market_status,
        }
    )
    _write_history(path, rows)
    return True


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
    alert_threshold_percent = get_alert_threshold_percent()
    ticker = get_usd_jpy()
    previous_rate = find_previous_rate(DEFAULT_RATE_HISTORY_PATH, ticker.rate_date)
    change = (
        calculate_rate_change(ticker.rate, previous_rate)
        if previous_rate is not None
        else None
    )
    alert = should_alert(
        change.percent if change is not None else None,
        alert_threshold_percent,
    )
    message = build_notification_message(
        ticker,
        change,
        alert_threshold_percent,
    )
    print("=== USD/JPY ===")
    print(message)
    print(f"Alert: {'yes' if alert else 'no'}")
    saved = save_usd_jpy_rate(ticker)
    if saved:
        print(f"CSV: saved to {DEFAULT_RATE_HISTORY_PATH}")
    else:
        print(f"CSV: {ticker.rate_date} is already saved; skipping")
    send_slack_notification(message)


if __name__ == "__main__":
    main()
