import argparse
import csv
import fcntl
import hashlib
import json
import os
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any
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
HOURLY_HISTORY_COLUMNS = [
    "fetched_at",
    "source_timestamp",
    "bucket_start_utc",
    "rate",
    "bid",
    "ask",
    "spread",
    "market_status",
]
DEFAULT_DATA_DIR = Path("data")
DEFAULT_RATE_HISTORY_PATH = DEFAULT_DATA_DIR / "usd_jpy.csv"
DEFAULT_HOURLY_HISTORY_PATH = DEFAULT_DATA_DIR / "usd_jpy_hourly.csv"
DEFAULT_ALERT_STATE_PATH = DEFAULT_DATA_DIR / "alert_state.json"
DEFAULT_HOURLY_ALERT_THRESHOLD_PERCENT = Decimal("0.3")
DEFAULT_DAILY_ALERT_THRESHOLD_PERCENT = Decimal("1.0")
DEFAULT_ALERT_THRESHOLD_PERCENT = DEFAULT_DAILY_ALERT_THRESHOLD_PERCENT
HOURLY_ALERT_THRESHOLD_ENVIRONMENT_VARIABLE = "USD_JPY_HOURLY_ALERT_THRESHOLD_PERCENT"
DAILY_ALERT_THRESHOLD_ENVIRONMENT_VARIABLE = "USD_JPY_DAILY_ALERT_THRESHOLD_PERCENT"
ALERT_THRESHOLD_ENVIRONMENT_VARIABLE = "USD_JPY_ALERT_THRESHOLD_PERCENT"
ALERT_COOLDOWN = timedelta(hours=3)
DELIVERY_CLAIM_TTL = timedelta(minutes=15)
HOURLY_RETENTION = timedelta(days=90)
JST = ZoneInfo("Asia/Tokyo")

EXIT_OK = 0
EXIT_CONFIGURATION_ERROR = 2
EXIT_DELIVERY_FAILED = 3
EXIT_ENVELOPE_ERROR = 4
EXIT_STATE_UPDATE_FAILED = 5


@dataclass(frozen=True)
class DataPaths:
    daily_history: Path
    hourly_history: Path
    alert_state: Path


@dataclass(frozen=True)
class AlertThresholds:
    hourly: Decimal
    daily: Decimal


@dataclass(frozen=True)
class UsdJpyQuote:
    bid: Decimal
    ask: Decimal
    rate: Decimal
    spread: Decimal
    source_timestamp: datetime
    rate_date: str
    market_status: str


# PR #18以前の呼び出し元との互換性を保ちながら、Quoteという名称も公開する。
UsdJpyTicker = UsdJpyQuote


@dataclass(frozen=True)
class RateChange:
    amount: Decimal
    percent: Decimal
    direction: str


@dataclass(frozen=True)
class Comparison:
    change: RateChange
    reference: str


@dataclass(frozen=True)
class PrepareResult:
    delivery_kind: str
    envelope_path: Path
    daily_saved: bool
    hourly_saved: bool
    state_updated: bool
    state_healthy: bool


class StateFileError(ValueError):
    pass


class EnvelopeError(ValueError):
    pass


def resolve_data_paths(environ: dict[str, str] | os._Environ[str] | None = None) -> DataPaths:
    environment = os.environ if environ is None else environ
    data_dir_value = environment.get("QUANT_LAB_DATA_DIR")
    data_dir = Path(data_dir_value) if data_dir_value and data_dir_value.strip() else DEFAULT_DATA_DIR

    def resolve_file(variable: str, filename: str) -> Path:
        value = environment.get(variable)
        return Path(value) if value and value.strip() else data_dir / filename

    return DataPaths(
        daily_history=resolve_file("USD_JPY_DAILY_HISTORY_PATH", "usd_jpy.csv"),
        hourly_history=resolve_file("USD_JPY_HOURLY_HISTORY_PATH", "usd_jpy_hourly.csv"),
        alert_state=resolve_file("USD_JPY_ALERT_STATE_PATH", "alert_state.json"),
    )


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


def _parse_stored_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a timezone-aware ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be a timezone-aware ISO 8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be a timezone-aware ISO 8601 timestamp")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


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


def _parse_threshold(value: str | None, variable: str, default: Decimal) -> Decimal:
    if value is None or not value.strip():
        return default
    try:
        threshold = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{variable} must be a positive finite number") from error
    if not threshold.is_finite() or threshold <= 0:
        raise ValueError(f"{variable} must be a positive finite number")
    return threshold


def get_alert_thresholds(
    environ: dict[str, str] | os._Environ[str] | None = None,
) -> AlertThresholds:
    environment = os.environ if environ is None else environ
    hourly = _parse_threshold(
        environment.get(HOURLY_ALERT_THRESHOLD_ENVIRONMENT_VARIABLE),
        HOURLY_ALERT_THRESHOLD_ENVIRONMENT_VARIABLE,
        DEFAULT_HOURLY_ALERT_THRESHOLD_PERCENT,
    )
    daily_value = environment.get(DAILY_ALERT_THRESHOLD_ENVIRONMENT_VARIABLE)
    if daily_value is None:
        daily_value = environment.get(ALERT_THRESHOLD_ENVIRONMENT_VARIABLE)
    daily = _parse_threshold(
        daily_value,
        DAILY_ALERT_THRESHOLD_ENVIRONMENT_VARIABLE,
        DEFAULT_DAILY_ALERT_THRESHOLD_PERCENT,
    )
    return AlertThresholds(hourly=hourly, daily=daily)


def get_alert_threshold_percent(value: str | None = None) -> Decimal:
    if value is None:
        value = os.getenv(ALERT_THRESHOLD_ENVIRONMENT_VARIABLE)
    return _parse_threshold(
        value,
        ALERT_THRESHOLD_ENVIRONMENT_VARIABLE,
        DEFAULT_DAILY_ALERT_THRESHOLD_PERCENT,
    )


def should_alert(change_percent: Decimal | None, threshold_percent: Decimal) -> bool:
    return change_percent is not None and abs(change_percent) >= threshold_percent


def _format_change_value(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"))
    if rounded == 0:
        return "0.00"
    return f"{rounded:+.2f}"


def _format_alert_threshold(value: Decimal) -> str:
    rounded = f"{value:.2f}"
    return str(value) if rounded == "0.00" else rounded


def _format_comparison(label: str, comparison: Comparison | None, reference_label: str) -> str:
    if comparison is None:
        return f"{label}: 比較データなし"
    change = comparison.change
    return (
        f"{label}: {_format_change_value(change.amount)}円"
        f"（{_format_change_value(change.percent)}%、{change.direction}）\n"
        f"{reference_label}: {comparison.reference}"
    )


def build_monitoring_message(
    ticker: UsdJpyQuote,
    hourly: Comparison | None,
    daily: Comparison | None,
    triggered_comparisons: list[str] | None = None,
    thresholds: AlertThresholds | None = None,
) -> str:
    message = (
        f"USD/JPY 仲値: {ticker.rate}\n"
        f"bid: {ticker.bid}\nask: {ticker.ask}\nspread: {ticker.spread}\n"
        f"基準時刻: {_utc_iso(ticker.source_timestamp)}\n"
        f"市場ステータス: {ticker.market_status}\n"
        f"{_format_comparison('1時間前比', hourly, '1時間比較基準')}\n\n"
        f"{_format_comparison('日次比', daily, '日次比較基準')}"
    )
    if not triggered_comparisons:
        return message
    labels = {"hourly": "1時間前比", "daily": "日次比"}
    target = "、".join(labels[item] for item in triggered_comparisons)
    threshold_lines = ""
    if thresholds is not None:
        values = []
        if "hourly" in triggered_comparisons:
            values.append(f"1時間: {_format_alert_threshold(thresholds.hourly)}%")
        if "daily" in triggered_comparisons:
            values.append(f"日次: {_format_alert_threshold(thresholds.daily)}%")
        threshold_lines = f"\n設定閾値: {'、'.join(values)}"
    return (
        f"⚠️ USD/JPY変動アラート\n"
        f"アラート対象: {target}\n"
        f"{message}{threshold_lines}"
    )


def build_notification_message(
    ticker: UsdJpyQuote,
    change: RateChange | None,
    alert_threshold_percent: Decimal | None = None,
) -> str:
    """従来の日次比較だけを渡す呼び出し元と互換性のある通知文を作る。"""
    message = (
        f"USD/JPY 仲値: {ticker.rate}\n"
        f"bid: {ticker.bid}\nask: {ticker.ask}\nspread: {ticker.spread}\n"
        f"基準時刻: {_utc_iso(ticker.source_timestamp)}\n"
        f"市場ステータス: {ticker.market_status}"
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
        change.percent, alert_threshold_percent
    ):
        return message
    return (
        f"⚠️ USD/JPY変動アラート\n{message}\n"
        f"設定閾値: {_format_alert_threshold(alert_threshold_percent)}%"
    )


def find_previous_daily_reference(
    csv_path: str | Path, current_rate_date: str
) -> tuple[str, Decimal] | None:
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
    if latest is None:
        return None
    return latest[0].isoformat(), latest[1]


def find_previous_rate(csv_path: str | Path, current_rate_date: str) -> Decimal | None:
    reference = find_previous_daily_reference(csv_path, current_rate_date)
    return reference[1] if reference else None


def _atomic_write_csv(
    csv_path: Path, columns: list[str], rows: list[dict[str, str]]
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8",
            dir=csv_path.parent,
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        temporary_path.replace(csv_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _read_and_migrate_history(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return []
    with csv_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not {"fetched_at", "rate_date", "rate"}.issubset(
            reader.fieldnames
        ):
            raise ValueError(f"CSV has an unsupported header: {reader.fieldnames}")
        rows = [
            {column: row.get(column, "") or "" for column in RATE_HISTORY_COLUMNS}
            for row in reader
        ]
        needs_migration = reader.fieldnames != RATE_HISTORY_COLUMNS
    if needs_migration:
        _atomic_write_csv(csv_path, RATE_HISTORY_COLUMNS, rows)
    return rows


def save_usd_jpy_rate(
    ticker: UsdJpyQuote,
    csv_path: str | Path | None = None,
    fetched_at: datetime | None = None,
) -> bool:
    path = Path(csv_path) if csv_path is not None else resolve_data_paths().daily_history
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
            "source_timestamp": _utc_iso(ticker.source_timestamp),
            "market_status": ticker.market_status,
        }
    )
    _atomic_write_csv(path, RATE_HISTORY_COLUMNS, rows)
    return True


def _bucket_start(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _parse_hourly_row_timestamps(
    row: dict[str, str],
) -> tuple[datetime, datetime] | None:
    try:
        source_timestamp = _parse_stored_timestamp(
            row["source_timestamp"], "source_timestamp"
        )
    except ValueError:
        return None
    bucket = _parse_stored_timestamp(row["bucket_start_utc"], "bucket_start_utc")
    if bucket != _bucket_start(source_timestamp):
        raise ValueError("Hourly CSV contains an inconsistent bucket_start_utc")
    return source_timestamp, bucket


def _read_hourly_history(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    if csv_path.stat().st_size == 0:
        raise ValueError("Hourly CSV is empty and has no header")
    with csv_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != HOURLY_HISTORY_COLUMNS:
            raise ValueError(f"Hourly CSV has an unsupported header: {reader.fieldnames}")
        rows = list(reader)
    seen_buckets: set[datetime] = set()
    for row in rows:
        if (
            None in row
            or set(row) != set(HOURLY_HISTORY_COLUMNS)
            or any(value is None for value in row.values())
        ):
            raise ValueError("Hourly CSV contains a structurally invalid row")
        timestamps = _parse_hourly_row_timestamps(row)
        if timestamps is None:
            continue
        _, bucket = timestamps
        if bucket in seen_buckets:
            raise ValueError("Hourly CSV contains duplicate UTC buckets")
        seen_buckets.add(bucket)
    return rows


def find_hourly_reference(
    csv_path: str | Path, current_source_timestamp: datetime
) -> tuple[datetime, Decimal] | None:
    current = current_source_timestamp.astimezone(timezone.utc)
    current_bucket = _bucket_start(current)
    candidates: list[tuple[timedelta, datetime, Decimal]] = []
    for row in _read_hourly_history(Path(csv_path)):
        timestamps = _parse_hourly_row_timestamps(row)
        if timestamps is None:
            continue
        source_timestamp, bucket = timestamps
        try:
            rate = Decimal(row["rate"])
        except InvalidOperation:
            continue
        if not rate.is_finite() or rate <= 0 or source_timestamp > current:
            continue
        difference = current - source_timestamp
        if (
            timedelta(minutes=45) <= difference <= timedelta(minutes=90)
            and bucket != current_bucket
        ):
            candidates.append(
                (abs(difference - timedelta(minutes=60)), source_timestamp, rate)
            )
    if not candidates:
        return None
    _, source_timestamp, rate = min(
        candidates, key=lambda item: (item[0], -item[1].timestamp())
    )
    return source_timestamp, rate


def save_usd_jpy_hourly_rate(
    ticker: UsdJpyQuote,
    csv_path: str | Path | None = None,
    fetched_at: datetime | None = None,
) -> bool:
    path = Path(csv_path) if csv_path is not None else resolve_data_paths().hourly_history
    rows = _read_hourly_history(path)
    current_source = ticker.source_timestamp.astimezone(timezone.utc)
    current_bucket = _bucket_start(current_source)
    existing_buckets: set[datetime] = set()
    retained_rows: list[dict[str, str]] = []
    boundary = current_source - HOURLY_RETENTION
    for row in rows:
        timestamps = _parse_hourly_row_timestamps(row)
        if timestamps is None:
            retained_rows.append(row)
            continue
        source, bucket = timestamps
        existing_buckets.add(bucket)
        if source >= boundary or source > current_source:
            retained_rows.append(row)
    if current_bucket in existing_buckets:
        if retained_rows != rows:
            _atomic_write_csv(path, HOURLY_HISTORY_COLUMNS, retained_rows)
            return True
        return False
    fetched_at = (fetched_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    retained_rows.append(
        {
            "fetched_at": fetched_at.isoformat(timespec="seconds"),
            "source_timestamp": _utc_iso(current_source),
            "bucket_start_utc": _utc_iso(current_bucket),
            "rate": str(ticker.rate),
            "bid": str(ticker.bid),
            "ask": str(ticker.ask),
            "spread": str(ticker.spread),
            "market_status": ticker.market_status,
        }
    )
    _atomic_write_csv(path, HOURLY_HISTORY_COLUMNS, retained_rows)
    return True


def default_alert_state() -> dict[str, Any]:
    return {
        "version": 1,
        "comparisons": {
            "hourly": {"is_active": False, "last_notified_at": None},
            "daily": {"is_active": False, "last_notified_at": None},
        },
        "pending_alert": None,
    }


def _validate_alert_state(state: object) -> dict[str, Any]:
    if not isinstance(state, dict) or state.get("version") != 1:
        raise StateFileError("alert state must use schema version 1")
    if "pending_alert" not in state:
        raise StateFileError("alert state pending_alert is required")
    comparisons = state.get("comparisons")
    if not isinstance(comparisons, dict):
        raise StateFileError("alert state comparisons must be an object")
    for comparison_name in ("hourly", "daily"):
        comparison = comparisons.get(comparison_name)
        if (
            not isinstance(comparison, dict)
            or not isinstance(comparison.get("is_active"), bool)
            or "last_notified_at" not in comparison
        ):
            raise StateFileError(f"alert state {comparison_name} is invalid")
        last_notified_at = comparison["last_notified_at"]
        if last_notified_at is not None:
            try:
                _parse_stored_timestamp(last_notified_at, "last_notified_at")
            except ValueError as error:
                raise StateFileError(
                    f"alert state {comparison_name} last_notified_at is invalid"
                ) from error
    pending = state["pending_alert"]
    if pending is not None:
        if (
            not isinstance(pending, dict)
            or not isinstance(pending.get("event_id"), str)
            or not pending["event_id"]
            or not isinstance(pending.get("message"), str)
            or not pending["message"]
            or not isinstance(pending.get("triggered_comparisons"), list)
            or not pending["triggered_comparisons"]
            or any(
                item not in {"hourly", "daily"}
                for item in pending["triggered_comparisons"]
            )
        ):
            raise StateFileError("alert state pending_alert is invalid")
        try:
            _parse_stored_timestamp(pending.get("occurred_at"), "occurred_at")
        except ValueError as error:
            raise StateFileError("alert state pending_alert occurred_at is invalid") from error
        delivery_claim = pending.get("delivery_claim")
        if delivery_claim is not None:
            if (
                not isinstance(delivery_claim, dict)
                or not isinstance(delivery_claim.get("claim_id"), str)
                or not delivery_claim["claim_id"]
            ):
                raise StateFileError("alert state delivery claim is invalid")
            try:
                _parse_stored_timestamp(delivery_claim.get("claimed_at"), "claimed_at")
            except ValueError as error:
                raise StateFileError("alert state delivery claim claimed_at is invalid") from error
    return state


def load_alert_state(path: str | Path) -> tuple[dict[str, Any], bool]:
    state_path = Path(path)
    if not state_path.exists():
        return default_alert_state(), True
    try:
        with state_path.open(encoding="utf-8") as file:
            state = json.load(file)
        return _validate_alert_state(state), True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, StateFileError) as error:
        print(
            f"ERROR: alert state is invalid; strong alerts are suppressed and "
            f"{state_path} was not overwritten: {error}",
            file=sys.stderr,
        )
        return default_alert_state(), False


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            temporary_path = Path(file.name)
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def write_alert_state(path: str | Path, state: dict[str, Any]) -> None:
    _validate_alert_state(state)
    _atomic_write_json(Path(path), state)


@contextmanager
def _alert_state_lock(path: str | Path) -> Iterator[None]:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _delivery_claim_is_active(pending: dict[str, Any], now: datetime) -> bool:
    delivery_claim = pending.get("delivery_claim")
    if not isinstance(delivery_claim, dict):
        return False
    claimed_at = _parse_stored_timestamp(delivery_claim["claimed_at"], "claimed_at")
    return now - claimed_at < DELIVERY_CLAIM_TTL


def _pending_matches_envelope(
    pending: object, envelope: dict[str, Any]
) -> bool:
    return (
        isinstance(pending, dict)
        and pending.get("event_id") == envelope["event_id"]
        and pending.get("message") == envelope["message"]
        and pending.get("triggered_comparisons")
        == envelope["triggered_comparisons"]
    )


def _strong_alert_due(
    comparison_state: dict[str, Any], now: datetime
) -> bool:
    if not comparison_state["is_active"]:
        return True
    last_notified_at = comparison_state["last_notified_at"]
    if last_notified_at is None:
        return True
    return now - _parse_stored_timestamp(last_notified_at, "last_notified_at") >= ALERT_COOLDOWN


def _event_id(
    occurred_at: datetime, triggered_comparisons: list[str], message: str
) -> str:
    material = json.dumps(
        {
            "occurred_at": _utc_iso(occurred_at),
            "triggered_comparisons": triggered_comparisons,
            "message": message,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def prepare_delivery(
    envelope_path: str | Path,
    *,
    paths: DataPaths | None = None,
    thresholds: AlertThresholds | None = None,
    ticker: UsdJpyQuote | None = None,
    now: datetime | None = None,
) -> PrepareResult:
    # 外部APIの呼び出しやデータ更新より前に、すべての設定値を検証する。
    configured_thresholds = thresholds or get_alert_thresholds()
    configured_paths = paths or resolve_data_paths()
    prepared_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    quote = ticker or get_usd_jpy()

    daily_reference = find_previous_daily_reference(
        configured_paths.daily_history, quote.rate_date
    )
    daily = (
        Comparison(
            calculate_rate_change(quote.rate, daily_reference[1]),
            daily_reference[0],
        )
        if daily_reference
        else None
    )

    hourly: Comparison | None = None
    hourly_file_healthy = True
    try:
        hourly_reference = find_hourly_reference(
            configured_paths.hourly_history, quote.source_timestamp
        )
        if hourly_reference:
            hourly = Comparison(
                calculate_rate_change(quote.rate, hourly_reference[1]),
                _utc_iso(hourly_reference[0]),
            )
    except ValueError as error:
        hourly_file_healthy = False
        print(
            f"ERROR: hourly history is invalid; hourly comparison and update "
            f"were skipped: {error}",
            file=sys.stderr,
        )

    daily_saved = save_usd_jpy_rate(
        quote, configured_paths.daily_history, fetched_at=prepared_at
    )
    hourly_saved = False
    if hourly_file_healthy:
        try:
            hourly_saved = save_usd_jpy_hourly_rate(
                quote, configured_paths.hourly_history, fetched_at=prepared_at
            )
        except ValueError as error:
            hourly_file_healthy = False
            print(
                f"ERROR: hourly history is invalid; hourly update was skipped: {error}",
                file=sys.stderr,
            )

    with _alert_state_lock(configured_paths.alert_state):
        state, state_healthy = load_alert_state(configured_paths.alert_state)
        triggered: list[str] = []
        comparison_values = {
            "hourly": (hourly, configured_thresholds.hourly),
            "daily": (daily, configured_thresholds.daily),
        }
        if state_healthy:
            pending_before_prepare = state.get("pending_alert")
            claim_active = (
                isinstance(pending_before_prepare, dict)
                and _delivery_claim_is_active(pending_before_prepare, prepared_at)
            )
            if isinstance(pending_before_prepare, dict) and not claim_active:
                pending_before_prepare.pop("delivery_claim", None)

            for comparison_name, (comparison, threshold) in comparison_values.items():
                comparison_state = state["comparisons"][comparison_name]
                if quote.market_status == "CLOSE" or comparison is None:
                    continue
                if not should_alert(comparison.change.percent, threshold):
                    comparison_state["is_active"] = False
                    continue
                if _strong_alert_due(comparison_state, prepared_at):
                    triggered.append(comparison_name)
                comparison_state["is_active"] = True

            if triggered and not claim_active:
                strong_message = build_monitoring_message(
                    quote,
                    hourly,
                    daily,
                    triggered_comparisons=triggered,
                    thresholds=configured_thresholds,
                )
                state["pending_alert"] = {
                    "event_id": _event_id(prepared_at, triggered, strong_message),
                    "occurred_at": _utc_iso(prepared_at),
                    "triggered_comparisons": triggered,
                    "message": strong_message,
                }
            write_alert_state(configured_paths.alert_state, state)

    pending = state["pending_alert"] if state_healthy else None
    if pending is not None:
        delivery_kind = "strong_alert"
        message = pending["message"]
        event_id = pending["event_id"]
        triggered_for_envelope = pending["triggered_comparisons"]
    else:
        delivery_kind = "normal"
        message = build_monitoring_message(quote, hourly, daily)
        event_id = None
        triggered_for_envelope = []

    envelope = {
        "version": 1,
        "delivery_kind": delivery_kind,
        "message": message,
        "event_id": event_id,
        "triggered_comparisons": triggered_for_envelope,
        "alert_state_path": str(configured_paths.alert_state),
        "prepared_at": _utc_iso(prepared_at),
    }
    envelope_file = Path(envelope_path)
    _atomic_write_json(envelope_file, envelope)
    return PrepareResult(
        delivery_kind=delivery_kind,
        envelope_path=envelope_file,
        daily_saved=daily_saved,
        hourly_saved=hourly_saved,
        state_updated=state_healthy,
        state_healthy=state_healthy,
    )


def _load_envelope(path: str | Path) -> dict[str, Any]:
    try:
        with Path(path).open(encoding="utf-8") as file:
            envelope = json.load(file)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EnvelopeError(f"cannot read delivery envelope: {error}") from error
    if (
        not isinstance(envelope, dict)
        or envelope.get("version") != 1
        or envelope.get("delivery_kind") not in {"normal", "strong_alert"}
        or not isinstance(envelope.get("message"), str)
        or not envelope["message"]
        or not isinstance(envelope.get("alert_state_path"), str)
    ):
        raise EnvelopeError("delivery envelope has an unsupported schema")
    if envelope["delivery_kind"] == "strong_alert" and (
        not isinstance(envelope.get("event_id"), str)
        or not envelope["event_id"]
        or not isinstance(envelope.get("triggered_comparisons"), list)
    ):
        raise EnvelopeError("strong alert envelope is incomplete")
    return envelope


def send_slack_notification(
    message: str, token: str | None = None, channel: str | None = None
) -> bool:
    token = token or os.getenv("SLACK_BOT_TOKEN")
    channel = channel or os.getenv("SLACK_CHANNEL")
    if not token or not channel:
        print("Slack credentials not set; skipping notification.", file=sys.stderr)
        return False
    client = WebClient(token=token)
    response = client.chat_postMessage(channel=channel, text=message)
    if not response.get("ok", False):
        raise RuntimeError(f"Slack notification failed: {response}")
    print("Slack notification sent.", file=sys.stderr)
    return True


def deliver_envelope(
    envelope_path: str | Path,
    *,
    token: str | None = None,
    channel: str | None = None,
    now: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    try:
        envelope = _load_envelope(envelope_path)
    except EnvelopeError as error:
        return EXIT_ENVELOPE_ERROR, {"status": "envelope_error", "error": str(error)}

    state_path: Path | None = None
    claim_id: str | None = None
    operation_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if envelope["delivery_kind"] == "strong_alert":
        state_path = Path(envelope["alert_state_path"])
        claim_id = uuid.uuid4().hex
        try:
            with _alert_state_lock(state_path):
                state, healthy = load_alert_state(state_path)
                pending = state.get("pending_alert")
                if (
                    not healthy
                    or not isinstance(pending, dict)
                    or not _pending_matches_envelope(pending, envelope)
                ):
                    return EXIT_STATE_UPDATE_FAILED, {
                        "status": "delivery_rejected",
                        "delivery_kind": "strong_alert",
                        "state_commit_required": False,
                        "error": "pending alert no longer matches the delivery envelope",
                    }
                if _delivery_claim_is_active(pending, operation_at):
                    return EXIT_STATE_UPDATE_FAILED, {
                        "status": "delivery_rejected",
                        "delivery_kind": "strong_alert",
                        "state_commit_required": False,
                        "error": "pending alert is already being delivered",
                    }
                pending["delivery_claim"] = {
                    "claim_id": claim_id,
                    "claimed_at": _utc_iso(operation_at),
                }
                write_alert_state(state_path, state)
        except (OSError, StateFileError, ValueError) as error:
            return EXIT_STATE_UPDATE_FAILED, {
                "status": "delivery_rejected",
                "delivery_kind": "strong_alert",
                "state_commit_required": False,
                "error": str(error),
            }

    try:
        sent = send_slack_notification(envelope["message"], token, channel)
    except Exception as error:
        if state_path is not None and claim_id is not None:
            _release_delivery_claim(state_path, envelope["event_id"], claim_id)
        return EXIT_DELIVERY_FAILED, {
            "status": "delivery_failed",
            "delivery_kind": envelope["delivery_kind"],
            "error": str(error),
            "state_commit_required": False,
        }
    if not sent:
        if state_path is not None and claim_id is not None:
            _release_delivery_claim(state_path, envelope["event_id"], claim_id)
        return EXIT_DELIVERY_FAILED, {
            "status": "delivery_skipped",
            "delivery_kind": envelope["delivery_kind"],
            "state_commit_required": False,
        }
    if envelope["delivery_kind"] == "normal":
        return EXIT_OK, {
            "status": "sent",
            "delivery_kind": "normal",
            "state_commit_required": False,
        }

    assert state_path is not None
    assert claim_id is not None
    try:
        with _alert_state_lock(state_path):
            state, healthy = load_alert_state(state_path)
            pending = state.get("pending_alert")
            delivery_claim = (
                pending.get("delivery_claim") if isinstance(pending, dict) else None
            )
            if (
                not healthy
                or not isinstance(pending, dict)
                or not _pending_matches_envelope(pending, envelope)
                or not isinstance(delivery_claim, dict)
                or delivery_claim.get("claim_id") != claim_id
            ):
                return EXIT_STATE_UPDATE_FAILED, {
                    "status": "sent_state_update_failed",
                    "delivery_kind": "strong_alert",
                    "state_commit_required": False,
                    "error": "pending alert no longer matches the delivered event",
                }
            for comparison_name in pending["triggered_comparisons"]:
                state["comparisons"][comparison_name]["last_notified_at"] = _utc_iso(
                    operation_at
                )
            state["pending_alert"] = None
            write_alert_state(state_path, state)
    except (OSError, StateFileError, ValueError) as error:
        return EXIT_STATE_UPDATE_FAILED, {
            "status": "sent_state_update_failed",
            "delivery_kind": "strong_alert",
            "state_commit_required": False,
            "error": str(error),
        }
    return EXIT_OK, {
        "status": "sent",
        "delivery_kind": "strong_alert",
        "state_commit_required": True,
        "alert_state_path": str(state_path),
    }


def _release_delivery_claim(
    state_path: Path, event_id: str, claim_id: str
) -> None:
    try:
        with _alert_state_lock(state_path):
            state, healthy = load_alert_state(state_path)
            pending = state.get("pending_alert")
            if (
                not healthy
                or not isinstance(pending, dict)
                or pending.get("event_id") != event_id
            ):
                return
            delivery_claim = pending.get("delivery_claim")
            if (
                isinstance(delivery_claim, dict)
                and delivery_claim.get("claim_id") == claim_id
            ):
                pending.pop("delivery_claim")
                write_alert_state(state_path, state)
    except (OSError, StateFileError, ValueError):
        # claimには有効期限があるため、解除に失敗しても送信が永久に止まることはない。
        return


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _run_prepare(envelope_path: str) -> int:
    try:
        result = prepare_delivery(envelope_path)
    except Exception as error:
        _print_json({"status": "prepare_failed", "error": str(error)})
        return EXIT_CONFIGURATION_ERROR
    _print_json(
        {
            "status": "prepared",
            "delivery_kind": result.delivery_kind,
            "envelope_path": str(result.envelope_path),
            "daily_saved": result.daily_saved,
            "hourly_saved": result.hourly_saved,
            "state_updated": result.state_updated,
            "state_healthy": result.state_healthy,
        }
    )
    return EXIT_OK


def _run_deliver(envelope_path: str) -> int:
    exit_code, result = deliver_envelope(envelope_path)
    _print_json(result)
    return exit_code


def _run_legacy() -> int:
    with TemporaryDirectory(prefix="quant-lab-delivery-") as directory:
        envelope_path = str(Path(directory) / "delivery.json")
        prepare_code = _run_prepare(envelope_path)
        if prepare_code != EXIT_OK:
            return prepare_code
        exit_code, result = deliver_envelope(envelope_path)
        _print_json(result)
        if result.get("status") == "delivery_skipped":
            return EXIT_OK
        return exit_code


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and deliver USD/JPY monitoring")
    subparsers = parser.add_subparsers(dest="command")
    prepare_parser = subparsers.add_parser(
        "prepare", help="fetch, compare, persist data/state, and create an envelope"
    )
    prepare_parser.add_argument("--envelope", required=True, help="temporary JSON output path")
    deliver_parser = subparsers.add_parser(
        "deliver", help="send one saved envelope and finalize strong-alert state"
    )
    deliver_parser.add_argument("--envelope", required=True, help="prepared JSON input path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args([] if argv is None else argv)
    if args.command == "prepare":
        return _run_prepare(args.envelope)
    if args.command == "deliver":
        return _run_deliver(args.envelope)
    return _run_legacy()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
