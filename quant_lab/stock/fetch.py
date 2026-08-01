"""Fetch Japanese stock 5-minute OHLCV data and save it as Parquet."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf  # type: ignore[import-untyped]

INTERVAL: Final = "5m"
JAPAN_TIMEZONE: Final = ZoneInfo("Asia/Tokyo")
MAX_LOOKBACK_DAYS: Final = 60
OUTPUT_COLUMNS: Final = ["datetime", "open", "high", "low", "close", "volume"]
SOURCE_COLUMNS: Final = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}
DEFAULT_OUTPUT_DIRECTORY: Final = Path("data/stock")


class StockFetchError(Exception):
    """An error that can be shown directly to a CLI user."""


def parse_date(value: str, argument_name: str) -> date:
    """Parse one ISO calendar date with an argument-specific error message."""
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise StockFetchError(
            f"{argument_name}はYYYY-MM-DD形式で指定してください: {value}"
        ) from error
    if parsed.isoformat() != value:
        raise StockFetchError(
            f"{argument_name}はYYYY-MM-DD形式で指定してください: {value}"
        )
    return parsed


def to_yahoo_symbol(symbol: str) -> str:
    """Validate a TSE security code and convert it for Yahoo Finance."""
    if len(symbol) != 4 or not symbol.isascii() or not symbol.isdigit():
        raise StockFetchError("銘柄コードは4桁の数字で指定してください")
    return f"{symbol}.T"


def validate_period(from_date: date, to_date: date, *, today: date) -> None:
    """Validate ordering and yfinance's rolling intraday-data window."""
    if from_date > to_date:
        raise StockFetchError("開始日は終了日以前の日付を指定してください")
    if from_date < today - timedelta(days=MAX_LOOKBACK_DAYS):
        raise StockFetchError("5分足は直近60日以内のデータのみ取得できます")
    if to_date > today:
        raise StockFetchError("終了日は今日以前の日付を指定してください")


def output_path(
    symbol: str,
    from_date: date,
    to_date: date,
    output_directory: Path = DEFAULT_OUTPUT_DIRECTORY,
) -> Path:
    """Build the path prescribed by Issue #30."""
    return output_directory / (
        f"{symbol}_{from_date.isoformat()}_{to_date.isoformat()}_{INTERVAL}.parquet"
    )


def fetch_ohlcv(
    yahoo_symbol: str,
    from_date: date,
    to_date: date,
    *,
    download: Callable[..., pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Download an inclusive date range of fixed 5-minute data."""
    downloader = download or yf.download
    exclusive_end = to_date + timedelta(days=1)
    try:
        frame = downloader(
            yahoo_symbol,
            start=from_date.isoformat(),
            end=exclusive_end.isoformat(),
            interval=INTERVAL,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception as error:
        raise StockFetchError(f"株価データの取得に失敗しました: {error}") from error
    if not isinstance(frame, pd.DataFrame):
        raise StockFetchError("株価データの取得結果が不正です")
    if frame.empty:
        raise StockFetchError("指定期間の株価データが見つかりませんでした")
    return frame


def _find_source_column(columns: pd.Index, source_name: str) -> object:
    expected = source_name.casefold()
    matches: list[object] = []
    for column in columns:
        parts = column if isinstance(column, tuple) else (column,)
        if any(str(part).casefold() == expected for part in parts):
            matches.append(column)
    if len(matches) != 1:
        raise StockFetchError(f"必須カラムが存在しません: {source_name}")
    return matches[0]


def _japan_datetime_index(index: pd.Index) -> pd.DatetimeIndex:
    try:
        converted = pd.DatetimeIndex(pd.to_datetime(index, errors="raise"))
        if converted.tz is None:
            return converted.tz_localize(JAPAN_TIMEZONE)
        return converted.tz_convert(JAPAN_TIMEZONE)
    except (TypeError, ValueError) as error:
        raise StockFetchError("datetimeを日本時間へ変換できません") from error


def format_ohlcv(
    raw: pd.DataFrame,
    from_date: date,
    to_date: date,
) -> pd.DataFrame:
    """Normalize, filter, and validate a yfinance OHLCV frame."""
    if raw.empty:
        raise StockFetchError("指定期間の株価データが見つかりませんでした")

    selected = pd.DataFrame(index=raw.index)
    for output_name, source_name in SOURCE_COLUMNS.items():
        column = _find_source_column(raw.columns, source_name)
        selected[output_name] = raw[column]

    selected.index = _japan_datetime_index(selected.index)
    period_start = pd.Timestamp(from_date, tz=JAPAN_TIMEZONE)
    period_end = pd.Timestamp(to_date + timedelta(days=1), tz=JAPAN_TIMEZONE)
    selected = selected.loc[
        (selected.index >= period_start) & (selected.index < period_end)
    ]
    if selected.empty:
        raise StockFetchError("指定期間の株価データが見つかりませんでした")
    if selected.index.duplicated().any():
        raise StockFetchError("datetimeに重複があります")
    if selected.isna().any(axis=None):
        raise StockFetchError("OHLCVに欠損値があります")

    try:
        for column in ("open", "high", "low", "close"):
            selected[column] = pd.to_numeric(selected[column], errors="raise").astype(
                "float64"
            )
        volume = pd.to_numeric(selected["volume"], errors="raise").astype("float64")
    except (TypeError, ValueError, OverflowError) as error:
        raise StockFetchError("価格または出来高を数値型に変換できません") from error

    numeric_values = selected[["open", "high", "low", "close"]].to_numpy()
    if not np.isfinite(numeric_values).all() or not np.isfinite(volume).all():
        raise StockFetchError("価格または出来高を数値型に変換できません")
    if not np.equal(volume, np.floor(volume)).all():
        raise StockFetchError("出来高を整数型に変換できません")
    try:
        selected["volume"] = volume.astype("int64")
    except (TypeError, ValueError, OverflowError) as error:
        raise StockFetchError("出来高を整数型に変換できません") from error

    selected = selected.sort_index()
    result = selected.rename_axis("datetime").reset_index()
    return result[OUTPUT_COLUMNS]


def save_parquet(frame: pd.DataFrame, path: Path) -> None:
    """Create the output directory and write a readable Parquet file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
    except (OSError, ValueError, ImportError) as error:
        raise StockFetchError(
            f"Parquetファイルの保存に失敗しました: {error}"
        ) from error


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="日本株の5分足OHLCVを取得してParquetへ保存します。"
    )
    parser.add_argument("--symbol", required=True, help="4桁の東証銘柄コード")
    parser.add_argument("--from", dest="from_value", required=True, help="取得開始日")
    parser.add_argument(
        "--to", dest="to_value", required=True, help="取得終了日（当日を含む）"
    )
    return parser


def run(
    argv: list[str] | None = None,
    *,
    today: date | None = None,
    download: Callable[..., pd.DataFrame] | None = None,
    output_directory: Path = DEFAULT_OUTPUT_DIRECTORY,
) -> Path:
    """Execute the validated fetch-format-save workflow and return its path."""
    args = _build_argument_parser().parse_args(argv)
    yahoo_symbol = to_yahoo_symbol(args.symbol)
    from_date = parse_date(args.from_value, "--from")
    to_date = parse_date(args.to_value, "--to")
    validate_period(
        from_date,
        to_date,
        today=today or datetime.now(JAPAN_TIMEZONE).date(),
    )

    raw = fetch_ohlcv(
        yahoo_symbol,
        from_date,
        to_date,
        download=download,
    )
    frame = format_ohlcv(raw, from_date, to_date)
    path = output_path(args.symbol, from_date, to_date, output_directory)
    save_parquet(frame, path)

    print(f"保存しました: {path}")
    print(f"銘柄コード: {args.symbol}")
    print(f"データ件数: {len(frame):,}件")
    print(f"最初の日時: {frame['datetime'].iloc[0]}")
    print(f"最後の日時: {frame['datetime'].iloc[-1]}")
    return path


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and convert domain failures into concise user messages."""
    try:
        run(argv)
    except StockFetchError as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
