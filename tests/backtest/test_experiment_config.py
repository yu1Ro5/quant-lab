"""experiment_configのテスト。外部APIは使用しない。"""

import tempfile
import unittest
from pathlib import Path

from quant_lab.backtest.config import ConfigError, parse_config

from .fixtures import configuration


class ExperimentConfigTests(unittest.TestCase):
    def test_valid_and_invalid_config(self):
        self.assertEqual(parse_config(configuration()).entry.strategy, "orb")
        mutations = [
            ("sizing", "params", {"shares": 0}),
            ("sizing", "params", {"shares": True}),
            ("sizing", "params", {"shares": 1.5}),
            ("entry", "strategy", "__import__"),
            ("entry", "params", {"opening_range_minutes": 31}),
            (
                "exit",
                "params",
                {
                    "stop_loss_pct": -1,
                    "take_profit_pct": 1,
                    "close_at_market_end": True,
                },
            ),
            ("execution", "slippage_bps_per_side", -1),
            ("execution", "slippage_bps_per_side", float("nan")),
            ("execution", "slippage_bps_per_side", "2"),
            ("portfolio", "max_trades_per_day", True),
            ("period", "end", "2025-01-01"),
            ("data", "timezone", "unknown"),
            ("data", "bar_timestamp", "guess"),
        ]
        for section, key, value in mutations:
            with self.subTest(section=section, key=key, value=value):
                raw = configuration()
                raw[section][key] = value
                with self.assertRaises(ConfigError):
                    parse_config(raw)
        for raw in [{}, {**configuration(), "typo": 1}]:
            with self.assertRaises(ConfigError):
                parse_config(raw)

    def test_yaml_errors_duplicate_unsafe_missing_and_normalized_defaults(self):
        import yaml

        from quant_lab.backtest.config import load_config

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.yaml"
            for content in [
                "experiment: {name: a, name: b}",
                "!!python/object:os.system {}",
                "- a",
                "[]",
                "data: [broken",
                "",
            ]:
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises(ConfigError):
                        load_config(path)
            raw = configuration()
            raw["sizing"]["params"] = {}
            path.write_text(yaml.safe_dump(raw))
            self.assertEqual(
                load_config(path).snapshot()["sizing"]["params"]["shares"], 1
            )
        raw = configuration()
        raw["execution"]["spread_bps_per_side"] = 9999
        with self.assertRaises(ConfigError):
            parse_config(raw)
        for section in ["data", "portfolio", "execution", "period", "entry"]:
            raw = configuration()
            raw[section].pop(next(iter(raw[section])))
            with self.assertRaises(ConfigError):
                parse_config(raw)
        raw = configuration()
        raw["exit"]["params"]["close_at_market_end"] = False
        with self.assertRaises(ConfigError):
            parse_config(raw)

    def test_huge_yaml_number_is_clear_config_error(self):
        raw = configuration()
        raw["portfolio"]["initial_capital_usd"] = 10**1000
        with self.assertRaises(ConfigError):
            parse_config(raw)
