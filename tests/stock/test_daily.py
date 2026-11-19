"""日足の変換・ページ取得・保存をオフライン検証する。"""

import json
import os
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd

from quant_lab.stock import daily


def frame(day: str = "2026-11-16") -> pd.DataFrame:
    return pd.DataFrame([dict(time_key=day + " 00:00:00", open=100,
                              high=102, low=99, close=101, volume=1000)])


SDK = SimpleNamespace(RET_OK=0, KLType=SimpleNamespace(K_DAY="K_DAY"),
                      AuType=SimpleNamespace(NONE="NONE"))


class DailyTests(unittest.TestCase):
    def test_parquet_write_failure_preserves_existing_outputs(self) -> None:
        data = daily.normalize(frame(), ["2026-11-16"],
                               date(2026, 11, 16), date(2026, 11, 16))
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "aapl"
            paths = [Path(str(base) + suffix)
                     for suffix in (".csv", ".parquet", "_metadata.json")]
            for path in paths:
                path.write_bytes(b"previous output")
            with patch.object(pd.DataFrame, "to_parquet", side_effect=OSError("write failed")):
                with self.assertRaises(OSError):
                    daily.save(data, base, {})
            for path in paths:
                self.assertEqual(path.read_bytes(), b"previous output")
            self.assertEqual(set(Path(tmp).iterdir()), set(paths))

    def test_replace_failure_restores_existing_and_removes_new_outputs(self) -> None:
        data = daily.normalize(frame(), ["2026-11-16"],
                               date(2026, 11, 16), date(2026, 11, 16))
        replace = os.replace
        for existing in (True, False):
            for fail_at in (1, 2, 3):
                with self.subTest(existing=existing, fail_at=fail_at), TemporaryDirectory() as tmp:
                    base = Path(tmp) / "aapl"
                    paths = [Path(str(base) + suffix)
                             for suffix in (".csv", ".parquet", "_metadata.json")]
                    if existing:
                        for path in paths:
                            path.write_bytes(b"previous output")
                    calls = 0

                    def fail_once(src: str | Path, dst: str | Path,
                                  *, failure: int = fail_at) -> None:
                        nonlocal calls
                        calls += 1
                        if calls == failure:
                            raise OSError("replace failed")
                        replace(src, dst)

                    with patch("os.replace", side_effect=fail_once):
                        with self.assertRaises(OSError):
                            daily.save(data, base, {})
                    for path in paths:
                        if existing:
                            self.assertEqual(path.read_bytes(), b"previous output")
                        else:
                            self.assertFalse(path.exists())
                    self.assertEqual(set(Path(tmp).iterdir()), set(paths) if existing else set())

    def test_rollback_failure_retains_backup_and_reports_recovery_path(self) -> None:
        data = daily.normalize(frame(), ["2026-11-16"],
                               date(2026, 11, 16), date(2026, 11, 16))
        replace = os.replace
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "aapl"
            for suffix in (".csv", ".parquet", "_metadata.json"):
                Path(str(base) + suffix).write_bytes(b"previous output")
            calls = 0

            def fail_commit_and_rollback(src: str | Path, dst: str | Path) -> None:
                nonlocal calls
                calls += 1
                if calls in (3, 4):
                    raise OSError("replace failed")
                replace(src, dst)

            with patch("os.replace", side_effect=fail_commit_and_rollback):
                with self.assertRaises(daily.DailyError) as caught:
                    daily.save(data, base, {})
            staging = next(p for p in Path(tmp).iterdir() if p.is_dir())
            self.assertIn(str(staging), str(caught.exception))
            self.assertEqual((staging / "previous-parquet").read_bytes(), b"previous output")
            self.assertEqual(Path(str(base) + ".csv").read_bytes(), b"previous output")

    def test_fetch_metadata_and_exact_sessions(self) -> None:
        ctx = Mock()
        ctx.request_trading_days.return_value = (0, [
            {"time": "2026-01-02", "trade_date_type": "WHOLE"}])
        ctx.request_history_kline.return_value = (0, frame("2026-01-02"), None)
        with TemporaryDirectory() as tmp, patch.object(daily, "version", return_value="test"):
            result = daily.fetch(ctx, SDK, date(2026, 1, 1), date(2026, 1, 4),
                                 Path(tmp) / "aapl")
            metadata = json.loads(Path(result["paths"]["metadata"]).read_text())
            self.assertEqual(result["first_date"], "2026-01-02")
            self.assertEqual(metadata["autype"], "NONE")
            self.assertEqual(metadata["request_end"], "2026-01-04")
            self.assertEqual(metadata["effective_end"], "2026-01-02")
            self.assertEqual(metadata["expected_sessions"], 1)

    def test_probe_does_not_claim_absolute_oldest(self) -> None:
        ctx = Mock()
        ctx.request_history_kline.side_effect = [
            (0, frame(f"{year}-01-03" if year == 2006 else f"{year}-01-02"), None)
            for year in (2025, 2020, 2010, 2006)]
        # 2010-01-02は土曜日なので、品質エラーとして残す。
        with TemporaryDirectory() as tmp, patch.object(daily.time, "sleep"):
            report = daily.probe(ctx, SDK, Path(tmp) / "probe.json")
            self.assertEqual(report["earliest_confirmed"], "2006-01-03")
            self.assertIsNone(report["absolute_earliest"])
            self.assertEqual(report["results"][2]["status"], "error")

    def test_end_excludes_new_york_today(self) -> None:
        now = datetime(2026, 11, 18, 3, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.assertEqual(daily.completed_end(date(2026, 11, 18), now),
                         date(2026, 11, 16))

    def test_end_includes_closed_session_and_handles_half_day_and_dst(self) -> None:
        for month, day in [(7, 2), (11, 17)]:
            now = datetime(2026, month, day, 16, tzinfo=ZoneInfo("America/New_York"))
            self.assertEqual(daily.completed_end(date(2026, month, day), now),
                             date(2026, month, day))
        now = datetime(2026, 11, 27, 13, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(daily.completed_end(date(2026, 11, 27), now, 13),
                         date(2026, 11, 27))

    def test_holiday_is_not_filled_in(self) -> None:
        data = daily.normalize(frame("2026-01-02"), ["2026-01-02"],
                               date(2026, 1, 1), date(2026, 1, 2))
        self.assertEqual(data.date.tolist(), ["2026-01-02"])

    def test_unknown_symbol_and_out_of_range_are_rejected(self) -> None:
        for bad in [frame().assign(code="US.MSFT"), frame("2026-11-17")]:
            with self.assertRaises(ValueError):
                daily.normalize(bad, ["2026-11-16"], date(2026, 11, 16),
                                date(2026, 11, 16))

    def test_pagination_keeps_every_row(self) -> None:
        ctx = Mock()
        ctx.request_history_kline.side_effect = [
            (0, frame(), b"next"), (0, frame("2026-11-17"), None)]
        result = daily.history(ctx, SDK, date(2026, 11, 16), date(2026, 11, 17),
                               pause=lambda _: None)
        self.assertEqual(len(result), 2)
        self.assertEqual(ctx.request_history_kline.call_args.kwargs["page_req_key"],
                         b"next")

    def test_api_error_is_not_empty_success(self) -> None:
        ctx = Mock()
        ctx.request_history_kline.return_value = (-1, "permission or rate limit", None)
        with self.assertRaisesRegex(daily.DailyError, "permission or rate limit"):
            daily.history(ctx, SDK, date(2026, 1, 1), date(2026, 1, 2))

    def test_repeated_page_key_is_rejected(self) -> None:
        ctx = Mock()
        ctx.request_history_kline.return_value = (0, frame(), b"same")
        with self.assertRaisesRegex(daily.DailyError, "ページ"):
            daily.history(ctx, SDK, date(2026, 1, 1), date(2026, 11, 17),
                          pause=lambda _: None)

    def test_quality_rejects_duplicate_missing_invalid_and_non_session(self) -> None:
        for bad in [pd.concat([frame(), frame()]),
                    frame().assign(close=None), frame().assign(high=98),
                    frame().assign(volume=-1), frame("2026-11-15")]:
            with self.subTest(data=bad.to_dict()), self.assertRaises(ValueError):
                daily.normalize(bad, ["2026-11-16"], date(2026, 11, 16),
                                date(2026, 11, 16))

    def test_missing_session_is_rejected(self) -> None:
        with self.assertRaisesRegex(daily.DailyError, "営業日"):
            daily.normalize(frame(), ["2026-11-16", "2026-11-17"],
                            date(2026, 11, 16), date(2026, 11, 17))

    def test_saves_csv_parquet_metadata_and_backtest_compatible(self) -> None:
        from quant_lab.backtest.data import load_parquet

        data = daily.normalize(pd.concat([frame("2026-11-17"), frame()]),
                               ["2026-11-16", "2026-11-17"],
                               date(2026, 11, 16), date(2026, 11, 17))
        with TemporaryDirectory() as tmp:
            paths = daily.save(data, Path(tmp) / "aapl", {"source": "moomoo"})
            self.assertEqual(len(load_parquet(paths["parquet"])), 2)
            csv = pd.read_csv(paths["csv"])
            self.assertEqual(csv.date.tolist(), ["2026-11-16", "2026-11-17"])
            metadata = json.loads(Path(paths["metadata"]).read_text())
            self.assertEqual(metadata["count"], 2)
            self.assertEqual(metadata["first_date"], "2026-11-16")


if __name__ == "__main__":
    unittest.main()
