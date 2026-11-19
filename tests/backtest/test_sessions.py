"""sessionsのテスト。外部APIは使用しない。"""

import unittest

import pandas as pd

from quant_lab.backtest.config import parse_config
from quant_lab.backtest.intraday_engine import ExperimentEngine
from quant_lab.backtest.sessions import prepare_sessions

from .fixtures import candles, configuration, execute


class SessionsTests(unittest.TestCase):
    def test_missing_bars_exclude_whole_day_never_carry(self):
        for indexes in [[0], [0, 1, 2], [20], [77], list(range(78))]:
            result = execute(candles().drop(indexes))
            self.assertEqual(result.trades, [])
            self.assertEqual(len(result.exclusions), 1)

    def test_dst_half_day_holiday_and_start_end_equivalence(self):
        for day, close, utc_hour in [
            ("2026-03-06", "16:00", 14),
            ("2026-03-09", "16:00", 13),
            ("2026-11-27", "13:00", 14),
        ]:
            raw = configuration()
            raw["period"] = {"start": day, "end": day}
            frame = candles(day, close)
            config = parse_config(raw)
            prepared = prepare_sessions(frame, config)
            self.assertEqual(prepared.sessions[0].open.hour, utc_hour)
            a = ExperimentEngine(config).run(prepared)
            raw["data"]["bar_timestamp"] = "start"
            frame["datetime"] -= pd.Timedelta(minutes=5)
            b = execute(frame, raw)
            self.assertEqual(a.trades, b.trades)
        raw["period"] = {"start": "2026-11-26", "end": "2026-11-26"}
        self.assertEqual(execute(candles("2026-11-26"), raw).trades, [])

    def test_naive_timezone_is_explicit_and_extended_hours_filtered(self):
        frame = candles()
        naive = frame.copy()
        naive["datetime"] = naive.datetime.dt.tz_localize(None)
        self.assertEqual(execute(frame).trades, execute(naive).trades)
        extra = frame.iloc[[0]].copy()
        extra["datetime"] -= pd.Timedelta(hours=1)
        result = execute(pd.concat([frame, extra]))
        self.assertEqual(result.trades, execute(frame).trades)
        self.assertTrue(result.warnings)

    def test_off_grid_duplicate_bad_ohlc_and_timezone_rejected(self):
        from quant_lab.backtest.data import DataValidationError

        frame = candles()
        frame.loc[20, "datetime"] += pd.Timedelta(seconds=1)
        self.assertEqual(len(execute(frame).exclusions), 1)
        with self.assertRaises(DataValidationError):
            execute(pd.concat([candles(), candles().iloc[[0]]]))
        frame = candles()
        frame.loc[10, "high"] = 1
        with self.assertRaises(DataValidationError):
            execute(frame)
        frame = candles()
        frame["datetime"] = "invalid"
        with self.assertRaises(DataValidationError):
            execute(frame)
        for time in ["2026-03-08 02:30", "2026-11-01 01:30"]:
            frame = candles()
            frame["datetime"] = time
            with self.assertRaises(DataValidationError):
                execute(frame)
