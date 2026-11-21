"""品質判定の回帰テスト。実相場・APIは使用しない。"""

import unittest

import pandas as pd

from quant_lab.backtest.config import parse_config
from quant_lab.backtest.quality import assess_quality

from .fixtures import candles, configuration


class QualityTests(unittest.TestCase):
    def assess(self, frame=None, raw=None, **kwargs):
        return assess_quality(
            candles() if frame is None else frame,
            parse_config(raw or configuration()),
            "abc",
            **kwargs,
        )

    def test_no_metadata_or_evidence_cannot_be_ready(self):
        report = self.assess()
        self.assertFalse(report["backtest_ready"])
        self.assertEqual(report["checks"]["schema"]["status"], "pass")
        self.assertEqual(report["checks"]["timestamp_semantics"]["status"], "unknown")
        self.assertEqual(report["checks"]["daily_comparison"]["status"], "unknown")

    def test_missing_first_middle_last_and_whole_session(self):
        for indexes in [[0], [5], [20], [77], list(range(78))]:
            with self.subTest(indexes=indexes):
                report = self.assess(candles().drop(indexes))
                self.assertFalse(report["backtest_ready"])
                self.assertEqual(report["checks"]["session_grid"]["status"], "fail")
                self.assertEqual(len(report["exclusions"]), 1)
                self.assertTrue(report["sessions"][0]["missing"])

    def test_invalid_values_duplicates_order_and_types(self):
        frames = [candles().iloc[::-1], pd.concat([candles(), candles().iloc[[0]]])]
        for col, value in [
            ("high", 1),
            ("volume", -1),
            ("open", float("nan")),
            ("close", float("inf")),
            ("volume", "10"),
        ]:
            f = candles()
            if isinstance(value, str):
                f[col] = f[col].astype(str)
            else:
                f.loc[10, col] = value
            frames.append(f)
        for frame in frames:
            with self.subTest():
                self.assertFalse(self.assess(frame)["structural_ready"])

    def test_dst_half_day_holiday_and_naive(self):
        for day, close, hour, count in [
            ("2026-03-06", "16:00", 14, 78),
            ("2026-03-09", "16:00", 13, 78),
            ("2026-11-02", "16:00", 14, 78),
            ("2026-11-27", "13:00", 14, 42),
        ]:
            raw = configuration()
            raw["period"] = {"start": day, "end": day}
            f = candles(day, close)
            f["datetime"] = f.datetime.dt.tz_localize(None)
            report = self.assess(f, raw)
            self.assertTrue(report["structural_ready"])
            self.assertEqual(report["sessions"][0]["expected_bars"], count)
            self.assertEqual(pd.Timestamp(report["sessions"][0]["open"]).hour, hour)
        raw["period"] = {"start": "2026-11-26", "end": "2026-11-26"}
        self.assertEqual(
            self.assess(candles("2026-11-26"), raw)["checks"]["calendar"]["status"],
            "fail",
        )

    def test_metadata_flag_not_evidence_and_conflicts_fail(self):
        report = self.assess(metadata={"backtest_ready": True})
        self.assertFalse(report["backtest_ready"])
        for metadata in [
            {"dataset_sha256": "wrong"},
            {"autype": "QFQ"},
            {"symbol": "US.OTHER"},
            {"count": 1},
            {"ktype": "K_DAY"},
            {"timestamp_label": {"interpretation": "start"}},
        ]:
            with self.subTest(metadata=metadata):
                self.assertEqual(
                    self.assess(metadata=metadata)["checks"]["metadata"]["status"],
                    "fail",
                )

    def test_reproducible_except_execution_timestamp(self):
        a, b = self.assess(), self.assess()
        a.pop("validated_at")
        b.pop("validated_at")
        self.assertEqual(a, b)

    def verified(self, frame=None):
        frame = candles() if frame is None else frame
        meta = dict(
            dataset_sha256="abc",
            symbol="US.AAPL",
            ktype="K_5M",
            autype="NONE",
            session="regular",
            count=len(frame),
            first_timestamp=frame.datetime.iloc[0].isoformat(),
            last_timestamp=frame.datetime.iloc[-1].isoformat(),
        )
        assertions = {
            k: dict(
                status="pass",
                value=v,
                source="fixture specification",
                observation="synthetic controlled input",
                reviewed_by="test",
                reviewed_at="2026-11-20",
            )
            for k, v in dict(
                timestamp_semantics="end",
                native_timezone="America/New_York",
                adjustment="none",
                volume_unit="shares",
                session_scope="regular",
                finalization="closed_bars_only",
                provenance="reviewed_acquisition",
            ).items()
        }
        evidence = dict(
            dataset_sha256="abc", metadata_sha256="meta", assertions=assertions
        )
        ref = pd.DataFrame(
            [
                dict(
                    date="2026-03-09",
                    open=frame.open.iloc[0],
                    high=frame.high.max(),
                    low=frame.low.min(),
                    close=frame.close.iloc[-1],
                    volume=frame.volume.sum(),
                )
            ]
        )
        ref_meta = dict(
            source="independent fixture",
            timeframe="1d",
            observation="controlled reference",
            reviewed_by="test",
            reviewed_at="2026-11-20",
            symbol="US.AAPL",
            adjustment="none",
            session="regular",
            volume_unit="shares",
            timezone="America/New_York",
            dataset_sha256="ref",
        )
        return dict(
            metadata=meta,
            metadata_sha256="meta",
            evidence=evidence,
            reference=ref,
            reference_metadata=ref_meta,
            reference_sha256="ref",
        )

    def test_verified_contract_and_daily_comparison_required_for_pass(self):
        kwargs = self.verified()
        self.assertTrue(self.assess(**kwargs)["backtest_ready"])
        kwargs["evidence"]["dataset_sha256"] = "stale"
        self.assertFalse(self.assess(**kwargs)["backtest_ready"])

    def test_daily_deltas_conditions_missing_dates_and_tolerance(self):
        kwargs = self.verified()
        kwargs["reference"].loc[0, "low"] -= 0.04
        kwargs["reference"].loc[0, "volume"] += 100
        report = self.assess(**kwargs)
        self.assertEqual(report["checks"]["daily_comparison"]["status"], "fail")
        self.assertAlmostEqual(report["daily_comparison"][0]["low_delta"], 0.04)
        kwargs["reference_metadata"].pop("volume_unit")
        self.assertEqual(
            self.assess(**kwargs)["checks"]["daily_comparison"]["status"], "unknown"
        )
        kwargs = self.verified()
        kwargs["reference"] = kwargs["reference"].iloc[:0]
        self.assertEqual(
            self.assess(**kwargs)["checks"]["daily_comparison"]["status"], "fail"
        )
        kwargs = self.verified()
        kwargs["reference_metadata"]["tolerance"] = {"price_absolute": float("nan")}
        self.assertEqual(
            self.assess(**kwargs)["checks"]["daily_comparison"]["status"], "fail"
        )
        kwargs = self.verified()
        kwargs["reference"].loc[0, "low"] -= 0.04
        kwargs["reference_metadata"]["tolerance"] = {"price_absolute": 0.1}
        self.assertEqual(
            self.assess(**kwargs)["checks"]["daily_comparison"]["status"], "unknown"
        )
        kwargs["reference_metadata"]["tolerance"].update(
            reason="fixture only", reviewed_by="test"
        )
        self.assertTrue(self.assess(**kwargs)["backtest_ready"])

    def test_quality_config_validation_and_legacy_default(self):
        from quant_lab.backtest.config import ConfigError

        self.assertEqual(parse_config(configuration()).quality.mode, "formal")
        for quality in [
            {"mode": "ignore"},
            {"evidence_path": True},
            {"daily_metadata_path": "x"},
            {"typo": 1},
        ]:
            raw = configuration()
            raw["quality"] = quality
            with self.assertRaises(ConfigError):
                parse_config(raw)

    def test_nullable_numeric_missing_dtype_datetime_and_duplicate_column_reported(
        self,
    ):
        frame = candles()
        frame["high"] = frame.high.astype("Float64")
        frame.loc[0, "high"] = pd.NA
        self.assertEqual(self.assess(frame)["checks"]["ohlcv"]["status"], "fail")
        frame = candles()
        frame["datetime"] = range(len(frame))
        self.assertEqual(self.assess(frame)["checks"]["schema"]["status"], "fail")
        frame = pd.concat([candles(), candles()[["high"]]], axis=1)
        self.assertEqual(self.assess(frame)["checks"]["schema"]["status"], "fail")

    def test_metadata_timezone_and_period_must_describe_saved_frame(self):
        kwargs = self.verified()
        kwargs["metadata"]["timezone"] = "UTC"  # fixture is stored as ET-aware
        self.assertEqual(self.assess(**kwargs)["checks"]["metadata"]["status"], "fail")
        kwargs = self.verified()
        kwargs["metadata"].update(
            request_start="2026-03-10", effective_end="2026-03-09"
        )
        self.assertEqual(self.assess(**kwargs)["checks"]["metadata"]["status"], "fail")
        kwargs = self.verified()
        kwargs["metadata"].update(
            request_start="2026-03-01",
            request_end="2026-03-08",
            effective_end="2026-03-09",
        )
        self.assertEqual(self.assess(**kwargs)["checks"]["metadata"]["status"], "fail")

    def test_ambiguous_dst_off_grid_and_future_bars_are_never_ready(self):
        for stamp in ["2026-03-08 02:30", "2026-11-01 01:30"]:
            frame = candles()
            frame["datetime"] = stamp
            self.assertEqual(
                self.assess(frame)["checks"]["timestamps"]["status"], "fail"
            )
        frame = candles()
        frame.loc[10, "datetime"] += pd.Timedelta(seconds=1)
        self.assertEqual(self.assess(frame)["checks"]["session_grid"]["status"], "fail")
        frame = candles("2099-03-09")
        self.assertEqual(self.assess(frame)["checks"]["finalization"]["status"], "fail")

    def test_reviewed_failure_remains_failure_not_unknown(self):
        kwargs = self.verified()
        kwargs["evidence"]["assertions"]["volume_unit"]["status"] = "fail"
        report = self.assess(**kwargs)
        self.assertEqual(report["checks"]["volume_unit"]["status"], "fail")
        self.assertFalse(report["backtest_ready"])

    def test_dataframe_index_does_not_duplicate_daily_aggregation(self):
        frame = candles()
        frame.index = [0] * len(frame)
        report = self.assess(frame, **self.verified())
        self.assertTrue(report["backtest_ready"])
        self.assertEqual(report["daily_comparison"][0]["volume_5m"], 780.0)
