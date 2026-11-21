"""外部接続不要の比較実行・品質ゲート・再現性試験。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from quant_lab.backtest.cost_sensitivity import break_even, run_cost_comparison
from quant_lab.backtest.data import DataValidationError
from quant_lab.backtest.intraday_engine import ExperimentEngine
from quant_lab.backtest.validation import ConfigError

from .fixtures import candles, configuration


class CostComparisonTests(unittest.TestCase):
    def setup_files(self, root):
        source = root / "input.parquet"
        candles().to_parquet(source, index=False)
        raw = configuration()
        raw["data"]["path"] = str(source)
        base = root / "base.yaml"
        base.write_text(yaml.safe_dump(raw))
        comparison = dict(
            base_config="base.yaml",
            output_directory=str(root / "results"),
            scenarios=[
                dict(
                    name="zero_friction", spread_bps_per_side=0, slippage_bps_per_side=0
                ),
                dict(name="baseline", spread_bps_per_side=1, slippage_bps_per_side=2),
            ],
        )
        path = root / "compare.yaml"
        path.write_text(yaml.safe_dump(comparison))
        return path, comparison

    def test_reruns_engine_and_preserves_quality_and_cost_breakdown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, _ = self.setup_files(root)
            original = ExperimentEngine.run
            calls = []

            def run(engine, prepared):
                calls.append(engine.config.execution)
                return original(engine, prepared)

            with patch.object(ExperimentEngine, "run", run):
                directory = run_cost_comparison(path, diagnostic=True)
            self.assertEqual(len(calls), 2)
            report = json.loads((directory / "comparison.json").read_text())
            rows = report["results"]
            self.assertEqual(len({r["input_sha256"] for r in rows}), 1)
            self.assertFalse(rows[0]["backtest_ready"])
            self.assertEqual(rows[0]["evaluation_status"], "provisional")
            self.assertGreater(rows[0]["total_cost"], 0)
            self.assertEqual(rows[0]["spread"], 0)
            self.assertEqual(rows[0]["slippage"], 0)
            for row in rows:
                self.assertAlmostEqual(
                    row["net_pnl"], row["gross_pnl"] - row["total_cost"]
                )
                self.assertAlmostEqual(row["final_cash_usd"], 5000 + row["net_pnl"])
                self.assertAlmostEqual(
                    row["total_cost"],
                    sum(
                        row[k]
                        for k in (
                            "commission",
                            "sec_fee",
                            "finra_fee",
                            "cat_fee",
                            "spread",
                            "slippage",
                        )
                    ),
                )
            self.assertIn("暫定・診断専用", (directory / "comparison.html").read_text())
            self.assertIn("plotly", (directory / "comparison.html").read_text())
            self.assertTrue((directory / "comparison.csv").is_file())
            for scenario in report["scenarios"]:
                run_dir = Path(scenario["run_directory"])
                self.assertTrue((run_dir / "trades.csv").is_file())
                self.assertTrue((run_dir / "config.yaml").is_file())

    def test_formal_gate_is_not_bypassed(self):
        with tempfile.TemporaryDirectory() as temp:
            path, _ = self.setup_files(Path(temp))
            with self.assertRaises(DataValidationError):
                run_cost_comparison(path)

    def test_unknown_strategy_override_and_duplicate_names_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path, raw = self.setup_files(Path(temp))
            raw["scenarios"][0]["entry"] = {}
            path.write_text(yaml.safe_dump(raw))
            with self.assertRaises(ConfigError):
                run_cost_comparison(path, diagnostic=True)
            del raw["scenarios"][0]["entry"]
            raw["scenarios"][1]["name"] = raw["scenarios"][0]["name"]
            path.write_text(yaml.safe_dump(raw))
            with self.assertRaises(ConfigError):
                run_cost_comparison(path, diagnostic=True)

    def test_snapshot_survives_original_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, _ = self.setup_files(root)
            original = ExperimentEngine.run
            calls = []

            def run(engine, prepared):
                calls.append(1)
                if len(calls) == 1:
                    (root / "input.parquet").write_bytes(b"changed original")
                return original(engine, prepared)

            with patch.object(ExperimentEngine, "run", run):
                directory = run_cost_comparison(path, diagnostic=True)
            self.assertEqual(len(calls), 2)
            rows = json.loads((directory / "comparison.json").read_text())["results"]
            self.assertEqual(rows[0]["input_sha256"], rows[1]["input_sha256"])

    def test_costs_change_buying_power_and_trade_count(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, raw = self.setup_files(root)
            base = yaml.safe_load((root / "base.yaml").read_text())
            base["portfolio"]["initial_capital_usd"] = 103.5
            (root / "base.yaml").write_text(yaml.safe_dump(base))
            raw["scenarios"][1]["spread_bps_per_side"] = 1000
            path.write_text(yaml.safe_dump(raw))
            directory = run_cost_comparison(path, diagnostic=True)
            rows = json.loads((directory / "comparison.json").read_text())["results"]
            self.assertEqual(rows[0]["total_trades"], 1)
            self.assertEqual(rows[1]["total_trades"], 0)

    def test_modified_saved_input_rejects_comparison_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, _ = self.setup_files(root)
            original = ExperimentEngine.run

            def run(engine, prepared):
                result = original(engine, prepared)
                Path(engine.config.data.path).write_bytes(b"tampered")
                return result

            with patch.object(ExperimentEngine, "run", run):
                with self.assertRaises((DataValidationError, ValueError)):
                    run_cost_comparison(path, diagnostic=True)
            self.assertFalse(list((root / "results").rglob("comparison.json")))
            self.assertTrue(list((root / "results").rglob("FAILED")))

    def test_break_even_brackets_exact_zero_and_no_trade(self):
        def row(name, spread, slip, pnl, trades=1):
            return dict(
                name=name,
                spread_bps_per_side=spread,
                slippage_bps_per_side=slip,
                net_pnl=pnl,
                total_trades=trades,
            )

        report = break_even([row("a", 0, 2, 1), row("b", 2, 2, -1)])
        self.assertEqual(
            report["sign_change_intervals"][0]["axis"], "spread_bps_per_side"
        )
        self.assertEqual(report["sign_change_intervals"][0]["lower"], 0)
        self.assertEqual(
            break_even([row("flat", 0, 0, 0)])["exact_scenarios"], ["flat"]
        )
        self.assertEqual(break_even([row("empty", 0, 0, 0, 0)])["exact_scenarios"], [])
        self.assertIn(
            "見つからず", break_even([row("a", 0, 0, 1), row("b", 1, 2, -1)])["status"]
        )


class ComparisonCliTests(unittest.TestCase):
    def test_cli_comparison_and_exclusive_modes(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from quant_lab.backtest.cli import main

        with tempfile.TemporaryDirectory() as temp:
            path, _ = CostComparisonTests().setup_files(Path(temp))
            with redirect_stdout(io.StringIO()) as output:
                code = main(["--compare-costs", str(path), "--diagnostic"])
            self.assertEqual(code, 0)
            self.assertIn("comparison.html", output.getvalue())
            for args in [
                ["--compare-costs", str(path), "--config", "base.yaml"],
                ["--compare-costs", str(path), "--quality-only"],
            ]:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main(args)
