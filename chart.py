"""Transform persisted USD/JPY daily KLines into chart-ready points."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

DEFAULT_KLINE_HISTORY_PATH: Final = Path("data/usd_jpy_1day.csv")
CHART_DAYS: Final = 30
REQUIRED_COLUMNS: Final = {"open_time", "bid_close", "ask_close"}


class ChartDataError(ValueError):
    """Raised when persisted daily KLine data cannot be charted safely."""


class ChartDataMissingError(ChartDataError):
    """Raised when the daily KLine CSV has not been generated yet."""


@dataclass(frozen=True)
class ChartPoint:
    """One trading day's midpoint close price."""

    trading_date: date
    close: Decimal


def _parse_point(row: dict[str, str]) -> ChartPoint:
    try:
        open_time = datetime.fromisoformat(row["open_time"])
    except (KeyError, TypeError, ValueError) as error:
        raise ChartDataError(
            f"日足CSVのopen_timeが不正です: {row.get('open_time')!r}"
        ) from error
    if open_time.tzinfo is None or open_time.utcoffset() is None:
        raise ChartDataError(
            f"日足CSVのopen_timeにタイムゾーンがありません: {row['open_time']!r}"
        )

    try:
        bid_close = Decimal(row["bid_close"])
        ask_close = Decimal(row["ask_close"])
    except (InvalidOperation, KeyError, TypeError) as error:
        raise ChartDataError("日足CSVのClose価格が不正です") from error
    if (
        not bid_close.is_finite()
        or bid_close <= 0
        or not ask_close.is_finite()
        or ask_close <= 0
    ):
        raise ChartDataError("日足CSVのClose価格は正の有限数である必要があります")

    return ChartPoint(
        trading_date=open_time.date(),
        close=(bid_close + ask_close) / Decimal("2"),
    )


def load_chart_points(
    csv_path: str | Path = DEFAULT_KLINE_HISTORY_PATH,
    limit: int = CHART_DAYS,
) -> list[ChartPoint]:
    """Load the latest daily KLines as ascending midpoint Close prices.

    The KLine CSV is produced by the existing daily-data acquisition process.
    Each row is one trading day, so the latest ``limit`` rows represent the
    latest ``limit`` business days without filling weekends or holidays.
    """
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    path = Path(csv_path)
    if not path.exists():
        raise ChartDataMissingError(f"日足CSVが見つかりません: {path}")
    if path.stat().st_size == 0:
        return []

    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
            raise ChartDataError(
                f"日足CSVのヘッダーが未対応です: {reader.fieldnames}"
            )
        points = [_parse_point(row) for row in reader]

    points.sort(key=lambda point: point.trading_date)
    if len({point.trading_date for point in points}) != len(points):
        raise ChartDataError("日足CSVに重複した日付があります")
    return points[-limit:]
