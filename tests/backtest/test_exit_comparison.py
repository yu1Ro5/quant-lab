import json
import tempfile
import unittest
from pathlib import Path

import yaml

from quant_lab.backtest.exit_comparison import run_exit_comparison
from quant_lab.backtest.validation import ConfigError
from tests.backtest.fixtures import candles, configuration


class ExitComparisonTests(unittest.TestCase):
    def setup_files(self, root):
        raw = configuration()
        source = root / "input.parquet"
        candles().to_parquet(source)
        raw["data"]["path"] = str(source)
        raw["quality"] = {"mode": "formal"}
        (root / "base.yaml").write_text(yaml.safe_dump(raw))
        comparison = dict(
            base_config="base.yaml",
            output_directory=str(root / "results"),
            periods=dict(
                in_sample=dict(start="2026-03-09", end="2026-03-09"),
                out_of_sample=dict(start="2026-03-10", end="2026-03-10"),
            ),
            exit_candidates=[
                dict(name="fixed", **raw["exit"]),
                dict(
                    name="time",
                    strategy="time_exit",
                    params=dict(max_holding_minutes=60),
                ),
            ],
        )
        path = root / "compare.yaml"
        path.write_text(yaml.safe_dump(comparison))
        return path, comparison

    def test_independent_runs_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self.setup_files(Path(tmp))
            result = run_exit_comparison(path, diagnostic=True)
            document = json.loads((result / "comparison.json").read_text())
            self.assertEqual(len(document["results"]), 4)
            self.assertTrue((result / "comparison.html").is_file())
            self.assertTrue((result / "comparison.csv").is_file())
            self.assertEqual(
                [r["total_trades"] for r in document["results"]], [1, 1, 0, 0]
            )
            configs = [r["config"] for r in document["runs"]]
            for config in configs:
                self.assertEqual(config["portfolio"]["initial_capital_usd"], 5000)
                self.assertEqual(
                    config["execution"]["cost_model"], "webull_jp_us_equity"
                )
                self.assertEqual(config["sizing"]["params"]["shares"], 1)

    def test_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, raw = self.setup_files(Path(tmp))
            raw["periods"]["out_of_sample"]["start"] = "2026-03-09"
            path.write_text(yaml.safe_dump(raw))
            with self.assertRaises(ConfigError):
                run_exit_comparison(path, diagnostic=True)

    def test_formal_gate_and_snapshot_tampering(self):
        from unittest.mock import patch

        from quant_lab.backtest.data import DataValidationError
        from quant_lab.backtest.intraday_engine import ExperimentEngine

        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self.setup_files(Path(tmp))
            with self.assertRaises(DataValidationError):
                run_exit_comparison(path)
            original = ExperimentEngine.run

            def tamper(engine, prepared):
                result = original(engine, prepared)
                Path(engine.config.data.path).write_bytes(b"tampered")
                return result

            with (
                patch.object(ExperimentEngine, "run", tamper),
                self.assertRaises(ValueError),
            ):
                run_exit_comparison(path, diagnostic=True)
            self.assertFalse(list((Path(tmp) / "results").rglob("comparison.json")))

    def test_cli(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from quant_lab.backtest.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self.setup_files(Path(tmp))
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(["--compare-exits", str(path), "--diagnostic"]), 0
                )
            self.assertIn("comparison.html", output.getvalue())
            for extra in [
                ["--config", "base.yaml"],
                ["--compare-costs", "costs.yaml"],
                ["--quality-only"],
            ]:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main(["--compare-exits", str(path), *extra])

    def test_nonempty_periods_use_same_nonexit_settings_and_independent_capital(self):
        import pandas as pd

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, _ = self.setup_files(root)
            source = root / "input.parquet"
            pd.concat([candles(), candles("2026-03-10")], ignore_index=True).to_parquet(
                source
            )
            before = (root / "base.yaml").read_bytes()
            directory = run_exit_comparison(path, diagnostic=True)
            self.assertEqual((root / "base.yaml").read_bytes(), before)
            report = json.loads((directory / "comparison.json").read_text())
            self.assertEqual(
                [r["total_trades"] for r in report["results"]], [1, 1, 1, 1]
            )
            baseline = report["runs"][0]["config"]
            for run, row in zip(report["runs"], report["results"], strict=True):
                for key in [
                    "data",
                    "entry",
                    "sizing",
                    "execution",
                    "portfolio",
                    "quality",
                ]:
                    self.assertEqual(run["config"][key], baseline[key])
                self.assertAlmostEqual(row["final_cash_usd"], 5000 + row["net_pnl"])
                self.assertEqual(row["evaluation_status"], "provisional")
                self.assertTrue(row["warnings"])
                self.assertTrue(
                    (Path(row["run_directory"]) / "monthly_performance.csv").is_file()
                )
                self.assertTrue(
                    (
                        Path(row["run_directory"]) / "exit_reason_performance.csv"
                    ).is_file()
                )

    def test_original_input_mutation_keeps_snapshot(self):
        from unittest.mock import patch

        from quant_lab.backtest.intraday_engine import ExperimentEngine

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, _ = self.setup_files(root)
            original = ExperimentEngine.run

            def mutate(engine, prepared):
                (root / "input.parquet").write_bytes(b"changed original")
                return original(engine, prepared)

            with patch.object(ExperimentEngine, "run", mutate):
                directory = run_exit_comparison(path, diagnostic=True)
            rows = json.loads((directory / "comparison.json").read_text())["results"]
            self.assertEqual(len({r["input_sha256"] for r in rows}), 1)

    def test_invalid_candidates_and_periods(self):
        from copy import deepcopy

        with tempfile.TemporaryDirectory() as tmp:
            path, raw = self.setup_files(Path(tmp))
            variants = []
            for period in [
                {"start": "2026-03-11", "end": "2026-03-10"},
                {"start": "bad", "end": "2026-03-10"},
            ]:
                changed = deepcopy(raw)
                changed["periods"]["out_of_sample"] = period
                variants.append(changed)
            for candidate in [
                dict(name="fixed", strategy="time_exit", params={}),
                dict(name="../bad", strategy="fixed_stop", params={}),
                dict(name="new", strategy="fixed_stop", params={}, sizing={}),
            ]:
                changed = deepcopy(raw)
                changed["exit_candidates"].append(candidate)
                variants.append(changed)
            for changed in variants:
                path.write_text(yaml.safe_dump(changed))
                with self.subTest(raw=changed), self.assertRaises(ConfigError):
                    run_exit_comparison(path, diagnostic=True)

    def test_quality_artifacts_snapshot_and_tamper_detection(self):
        from dataclasses import replace

        from quant_lab.backtest.comparison_inputs import snapshot_inputs, verify_inputs
        from quant_lab.backtest.config import QualityConfig, parse_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = configuration()
            raw["data"]["path"] = str(root / "input.parquet")
            (root / "input.parquet").write_bytes(b"input")
            (root / "input_metadata.json").write_bytes(b"metadata")
            for name in ["evidence.json", "daily.csv", "daily_metadata.json"]:
                (root / name).write_text(name)
            base = replace(
                parse_config(raw),
                quality=QualityConfig(
                    evidence_path=str(root / "evidence.json"),
                    daily_reference_path=str(root / "daily.csv"),
                    daily_metadata_path=str(root / "daily_metadata.json"),
                ),
            )
            output = root / "saved"
            output.mkdir()
            saved, artifacts = snapshot_inputs(base, output)
            self.assertEqual(len(artifacts), 5)
            self.assertNotEqual(saved.quality.evidence_path, base.quality.evidence_path)
            verify_inputs(artifacts)
            for artifact in artifacts.values():
                path = Path(artifact["snapshot_path"])
                before = path.read_bytes()
                path.write_bytes(b"tampered")
                with self.assertRaises(ValueError):
                    verify_inputs(artifacts)
                path.write_bytes(before)
