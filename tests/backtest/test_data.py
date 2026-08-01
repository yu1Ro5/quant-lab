import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from quant_lab_backtest.data import DataValidationError, load_parquet, validate_data


def valid_data() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Datetime": ["2026-01-02", "2026-01-01"],
            "Open": [101.0, 100.0],
            "High": [103.0, 102.0],
            "Low": [100.0, 99.0],
            "Close": [102.0, 101.0],
            "Volume": [1100.0, 1000.0],
            "Memo": ["second", "first"],
        }
    )


class LoadParquetTests(unittest.TestCase):
    def test_loads_dataframe_sorts_datetime_and_keeps_extra_columns(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "prices.parquet"
            valid_data().to_parquet(path, index=False)

            result = load_parquet(path)

        self.assertEqual(list(result["Close"]), [101, 102])
        self.assertEqual(list(result["Memo"]), ["first", "second"])
        self.assertIsInstance(result.loc[0, "Datetime"], pd.Timestamp)

    def test_rejects_missing_and_broken_files(self) -> None:
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.parquet"
            with self.assertRaisesRegex(DataValidationError, "見つかりません"):
                load_parquet(missing)

            broken = Path(directory) / "broken.parquet"
            broken.write_text("not parquet", encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "読み込めません"):
                load_parquet(broken)


class ValidateDataTests(unittest.TestCase):
    def test_rejects_empty_and_missing_columns(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "空です"):
            validate_data(pd.DataFrame())

        with self.assertRaisesRegex(DataValidationError, "Volume"):
            validate_data(valid_data().drop(columns="Volume"))

    def test_rejects_invalid_or_missing_values(self) -> None:
        cases = (
            ("Datetime", "not-a-date"),
            ("Open", None),
            ("Close", "not-a-number"),
        )
        for column, value in cases:
            data = valid_data()
            data[column] = data[column].astype("object")
            data.loc[0, column] = value
            with self.subTest(column=column, value=value), self.assertRaisesRegex(
                DataValidationError, "欠損・変換不能"
            ):
                validate_data(data)

    def test_rejects_duplicate_datetimes(self) -> None:
        data = valid_data()
        data.loc[1, "Datetime"] = data.loc[0, "Datetime"]
        with self.assertRaisesRegex(DataValidationError, "重複"):
            validate_data(data)

    def test_rejects_non_finite_non_positive_and_negative_volume(self) -> None:
        cases = (
            ("High", np.inf, "有限"),
            ("Open", 0, "0より大きい"),
            ("Volume", -1, "0以上"),
        )
        for column, value, message in cases:
            data = valid_data()
            data.loc[0, column] = value
            with self.subTest(column=column), self.assertRaisesRegex(
                DataValidationError, message
            ):
                validate_data(data)

    def test_rejects_inconsistent_high_and_low(self) -> None:
        high = valid_data()
        high.loc[0, "High"] = 100
        with self.assertRaisesRegex(DataValidationError, "High"):
            validate_data(high)

        low = valid_data()
        low.loc[0, "Low"] = 102.5
        with self.assertRaisesRegex(DataValidationError, "Low"):
            validate_data(low)


if __name__ == "__main__":
    unittest.main()
