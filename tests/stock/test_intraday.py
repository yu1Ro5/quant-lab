"""米国5分足の品質・時刻・API制限をオフライン検証する。"""

import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

from quant_lab.stock import intraday as m


def bars(day: str, close: str = "16:00") -> pd.DataFrame:
    times = pd.date_range(day + " 09:35", day + " " + close, freq="5min")
    return pd.DataFrame(dict(time_key=times.strftime("%Y-%m-%d %H:%M:%S"),
                             code="US.AAPL", open=100., high=102., low=99.,
                             close=101., volume=10))


class IntradayTests(unittest.TestCase):
    def test_probe_windows_and_revised_data_do_not_overwrite_evidence(self) -> None:
        client = Mock()
        client.history.side_effect = [bars("2019-01-02"), bars("2019-11-19"),
                                      bars("2019-01-02").assign(volume=11)]
        client.call.side_effect = [(0, [{"time": day, "trade_date_type": "WHOLE"}])
                                   for day in ("2019-01-02", "2019-11-19", "2019-01-02")]
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "aapl"
            Path(str(base) + "_metadata.json").write_text(json.dumps({"ktype": "K_5M"}))
            annual = m.probe(client, [2019], base)
            first_path = Path(annual["results"][0]["paths"][".parquet"])
            original = first_path.read_bytes()
            boundary = m.probe(client, [2019], base, date(2019, 11, 19))
            self.assertNotEqual(str(first_path), boundary["results"][0]["paths"][".parquet"])
            self.assertEqual(first_path.read_bytes(), original)
            saved = json.loads(Path(str(base) + "_history_probe.json").read_text())
            evidence = pd.read_parquet(saved["results"][0]["paths"][".parquet"])
            self.assertEqual(evidence.datetime_et.iloc[0].isoformat(),
                             saved["results"][0]["first"])
            revised = m.probe(client, [2019], base)
            self.assertNotEqual(str(first_path), revised["results"][0]["paths"][".parquet"])
            self.assertEqual(first_path.read_bytes(), original)

    def test_boundary_probe_requests_only_seven_days(self) -> None:
        client = Mock()
        client.history.return_value = bars("2018-11-19")
        client.call.return_value = (0, [{"time": "2018-11-19", "trade_date_type": "WHOLE"}])
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "aapl"
            Path(str(base) + "_metadata.json").write_text(json.dumps({"ktype": "K_5M"}))
            report = m.probe(client, [2025, 2024], base, date(2018, 11, 19))
            self.assertTrue(report["earliest_confirmed"].startswith("2018-11-19"))
            client.history.assert_called_once_with(date(2018, 11, 19), date(2018, 11, 25), sample=True)

    def test_retry_exhaustion_is_bounded(self) -> None:
        ctx = Mock()
        ctx.request_trading_days.return_value = (-1, "request frequency limit")
        client = m.QuoteClient(ctx, SimpleNamespace(RET_OK=0), pause=lambda _: None)
        with self.assertRaises(ValueError):
            client.call("request_trading_days")
        self.assertEqual(ctx.request_trading_days.call_count, 4)
        self.assertEqual(client.retries, 3)

    def test_end_label_maps_to_utc_and_interval_start(self) -> None:
        data, quality = m.assess(bars("2026-11-16"),
                                 [{"time": "2026-11-16", "trade_date_type": "WHOLE"}])
        self.assertEqual(len(data), 78)
        self.assertEqual(data.datetime.iloc[0].isoformat(), "2026-11-16T14:35:00+00:00")
        self.assertEqual(data.interval_start_utc.iloc[0].isoformat(),
                         "2026-11-16T14:30:00+00:00")
        self.assertEqual(quality["status"], "passed")

    def test_short_session_and_dst(self) -> None:
        data, quality = m.assess(bars("2026-07-02", "13:00"),
                                 [{"time": "2026-07-02", "trade_date_type": "MORNING"}])
        self.assertEqual(len(data), 42)
        self.assertEqual(data.datetime.iloc[0].hour, 13)
        self.assertEqual(quality["expected_bars"], 42)

    def test_gap_is_recorded_without_imputation(self) -> None:
        raw = bars("2026-11-16").drop(index=[3])
        data, quality = m.assess(raw, [{"time": "2026-11-16", "trade_date_type": "WHOLE"}])
        self.assertEqual(len(data), 77)
        self.assertEqual(quality["status"], "needs_review")
        self.assertEqual(quality["missing_bars"], 1)
        self.assertIn("halt_or_provider_gap_unconfirmed", quality["gap_cause"])

    def test_float_volume_is_preserved_without_rounding(self) -> None:
        raw = bars("2026-11-16").assign(volume=10.125)
        data, quality = m.assess(raw, [{"time": "2026-11-16", "trade_date_type": "WHOLE"}])
        self.assertEqual(data.volume.iloc[0], 10.125)
        self.assertEqual(quality["fractional_volume_bars"], 78)

    def test_duplicate_invalid_price_and_outside_session_fail(self) -> None:
        raw = bars("2026-11-16")
        for bad in [pd.concat([raw, raw.iloc[:1]]), raw.assign(high=1),
                    raw.assign(volume=-1), raw.assign(open=float("nan")),
                    raw.assign(time_key="2026-11-16 09:30:00")]:
            with self.subTest(), self.assertRaises(ValueError):
                m.assess(bad, [{"time": "2026-11-16", "trade_date_type": "WHOLE"}])

    def test_unfinished_today_is_excluded(self) -> None:
        cal = [{"time": "2026-11-16", "trade_date_type": "WHOLE"},
               {"time": "2026-11-17", "trade_date_type": "WHOLE"}]
        now = datetime(2026, 11, 17, 20, tzinfo=timezone.utc)
        self.assertEqual(len(m.completed_sessions(cal, date(2026, 11, 17), now)), 1)

    def test_pagination_and_retry_preserve_cursor(self) -> None:
        ctx = Mock()
        ctx.request_history_kline.side_effect = [
            (0, bars("2026-11-16"), b"next"),
            (-1, "request frequency limit", None),
            (0, bars("2026-11-17"), None)]
        sdk = SimpleNamespace(RET_OK=0, KLType=SimpleNamespace(K_5M="K_5M"),
                              AuType=SimpleNamespace(NONE="NONE"))
        client = m.QuoteClient(ctx, sdk, pause=lambda _: None)
        result = client.history(date(2026, 11, 16), date(2026, 11, 17))
        self.assertEqual(len(result), 156)
        self.assertEqual(ctx.request_history_kline.call_args.kwargs["page_req_key"], b"next")
        self.assertEqual(client.retries, 1)

    def test_permission_error_not_retried(self) -> None:
        ctx = Mock()
        ctx.request_trading_days.return_value = (-1, "permission denied")
        client = m.QuoteClient(ctx, SimpleNamespace(RET_OK=0), pause=lambda _: None)
        with self.assertRaises(ValueError):
            client.call("request_trading_days", code="US.AAPL")
        self.assertEqual(ctx.request_trading_days.call_count, 1)

    def test_daily_aggregation_and_outer_comparison(self) -> None:
        data, quality = m.assess(bars("2026-11-16"),
                                 [{"time": "2026-11-16", "trade_date_type": "WHOLE"}])
        daily = m.aggregate(data, quality)
        self.assertEqual(daily.volume.iloc[0], 780)
        reference = pd.DataFrame(dict(date=["2026-11-16", "2026-11-17"],
                                     open=[100, 100], high=[102, 102], low=[99, 99],
                                     close=[100, 101], volume=[780, 780]))
        comparison = m.compare(daily, reference)
        self.assertEqual(comparison.close_delta.iloc[0], 1)
        self.assertEqual(comparison.match_status.iloc[1], "reference_only")


if __name__ == "__main__":
    unittest.main()
