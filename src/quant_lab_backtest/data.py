"""Parquet loading and validation for OHLCV backtest data."""

from pathlib import Path

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ("Datetime", "Open", "High", "Low", "Close", "Volume")
PRICE_COLUMNS = ("Open", "High", "Low", "Close")
NUMERIC_COLUMNS = (*PRICE_COLUMNS, "Volume")


class DataValidationError(ValueError):
    """Raised when a backtest input cannot be used safely."""


def load_parquet(path: str | Path) -> pd.DataFrame:
    """Load a Parquet file and return validated, chronological OHLCV data."""
    parquet_path = Path(path)
    if not parquet_path.is_file():
        raise DataValidationError(f"Parquetファイルが見つかりません: {parquet_path}")

    try:
        data = pd.read_parquet(parquet_path)
    except Exception as error:
        raise DataValidationError(
            f"Parquetファイルを読み込めません: {parquet_path}: {error}"
        ) from error

    return validate_data(data)


def validate_data(data: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize an OHLCV DataFrame."""
    if data.empty:
        raise DataValidationError("Parquetデータが空です")

    missing = [column for column in REQUIRED_COLUMNS if column not in data.columns]
    if missing:
        raise DataValidationError(f"必須カラムが不足しています: {', '.join(missing)}")

    normalized = data.copy()
    normalized["Datetime"] = pd.to_datetime(
        normalized["Datetime"], format="mixed", errors="coerce", utc=True
    )
    for column in NUMERIC_COLUMNS:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    if normalized[list(REQUIRED_COLUMNS)].isna().any().any():
        raise DataValidationError("日時またはOHLCVに欠損・変換不能な値があります")
    if normalized["Datetime"].duplicated().any():
        raise DataValidationError("Datetimeが重複しています")

    numeric_values = normalized[list(NUMERIC_COLUMNS)].to_numpy(dtype=float)
    if not bool(np.isfinite(numeric_values).all()):
        raise DataValidationError("OHLCVには有限な数値だけを指定してください")
    if (normalized[list(PRICE_COLUMNS)] <= 0).any().any():
        raise DataValidationError("価格は0より大きい値にしてください")
    if (normalized["Volume"] < 0).any():
        raise DataValidationError("Volumeは0以上にしてください")

    highest_body_price = normalized[["Open", "Low", "Close"]].max(axis=1)
    lowest_body_price = normalized[["Open", "High", "Close"]].min(axis=1)
    if (normalized["High"] < highest_body_price).any():
        raise DataValidationError("HighがOpen、Low、Closeの最大値を下回っています")
    if (normalized["Low"] > lowest_body_price).any():
        raise DataValidationError("LowがOpen、High、Closeの最小値を上回っています")

    return normalized.sort_values("Datetime").reset_index(drop=True)
