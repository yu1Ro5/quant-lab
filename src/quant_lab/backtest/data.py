"""OHLCVバックテストデータのParquet読込と検証。"""

from pathlib import Path

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ("datetime", "open", "high", "low", "close", "volume")
PRICE_COLUMNS = ("open", "high", "low", "close")
NUMERIC_COLUMNS = (*PRICE_COLUMNS, "volume")


class DataValidationError(ValueError):
    """バックテストに使用できない入力データを検出した場合のエラー。"""


def load_parquet(path: str | Path) -> pd.DataFrame:
    """Parquetを読み込み、検証済みのOHLCVデータを日時順で返す。"""
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
    """OHLCVのDataFrameを検証し、バックテスト用の形式に整える。"""
    if data.empty:
        raise DataValidationError("Parquetデータが空です")

    missing = [column for column in REQUIRED_COLUMNS if column not in data.columns]
    if missing:
        raise DataValidationError(f"必須カラムが不足しています: {', '.join(missing)}")

    normalized = data.copy()
    normalized["datetime"] = pd.to_datetime(
        normalized["datetime"], format="mixed", errors="coerce", utc=True
    )
    for column in NUMERIC_COLUMNS:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    if normalized[list(REQUIRED_COLUMNS)].isna().any().any():
        raise DataValidationError("日時またはOHLCVに欠損・変換不能な値があります")
    if normalized["datetime"].duplicated().any():
        raise DataValidationError("datetimeが重複しています")

    numeric_values = normalized[list(NUMERIC_COLUMNS)].to_numpy(dtype=float)
    if not bool(np.isfinite(numeric_values).all()):
        raise DataValidationError("OHLCVには有限な数値だけを指定してください")
    if (normalized[list(PRICE_COLUMNS)] <= 0).any().any():
        raise DataValidationError("価格は0より大きい値にしてください")
    if (normalized["volume"] < 0).any():
        raise DataValidationError("volumeは0以上にしてください")

    highest_body_price = normalized[["open", "low", "close"]].max(axis=1)
    lowest_body_price = normalized[["open", "high", "close"]].min(axis=1)
    if (normalized["high"] < highest_body_price).any():
        raise DataValidationError("highがopen、low、closeの最大値を下回っています")
    if (normalized["low"] > lowest_body_price).any():
        raise DataValidationError("lowがopen、high、closeの最小値を上回っています")

    return normalized.sort_values("datetime").reset_index(drop=True)
