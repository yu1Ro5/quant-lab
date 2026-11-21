"""正式評価のゲートと暫定表示。"""

import json
import tempfile
import unittest
from pathlib import Path

from quant_lab.backtest.config import parse_config
from quant_lab.backtest.data import DataValidationError
from quant_lab.backtest.runner import run_experiment

from .fixtures import candles, configuration


class GateTests(unittest.TestCase):
    def test_formal_rejected_with_report_diagnostic_explicit_and_quality_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.parquet"
            candles().to_parquet(source, index=False)
            raw = configuration()
            raw["data"]["path"] = str(source)
            raw["output"]["directory"] = str(root / "results")
            config = parse_config(raw)
            with self.assertRaises(DataValidationError):
                run_experiment(config)
            reports = list((root / "results").glob("*/quality.json"))
            self.assertEqual(len(reports), 1)
            self.assertFalse(json.loads(reports[0].read_text())["backtest_ready"])
            self.assertFalse((reports[0].parent / "trades.csv").exists())
            run = run_experiment(config, diagnostic=True)
            metrics = json.loads((run / "metrics.json").read_text())
            self.assertEqual(metrics["evaluation_status"], "provisional")
            self.assertFalse(metrics["backtest_ready"])
            self.assertIn("暫定", (run / "equity_curve.html").read_text())
            check = run_experiment(config, quality_only=True)
            self.assertTrue((check / "quality.md").exists())
            self.assertFalse((check / "trades.csv").exists())

    def test_diagnostic_never_overrides_metadata_conflict_or_bad_ohlc(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.parquet"
            raw = configuration()
            raw["data"]["path"] = str(source)
            raw["output"]["directory"] = str(root / "results")
            candles().to_parquet(source, index=False)
            (root / "input_metadata.json").write_text(
                json.dumps({"dataset_sha256": "wrong"})
            )
            with self.assertRaises(DataValidationError):
                run_experiment(parse_config(raw), diagnostic=True)
            (root / "input_metadata.json").unlink()
            frame = candles()
            frame.loc[10, "high"] = 1
            frame.to_parquet(source, index=False)
            with self.assertRaises(DataValidationError):
                run_experiment(parse_config(raw), diagnostic=True)

    def test_formal_runs_only_with_bound_review_and_reference_then_rechecks(self):
        import hashlib

        from .test_quality import QualityTests

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.parquet"
            candles().to_parquet(source, index=False)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            kwargs = QualityTests().verified()
            meta = kwargs["metadata"]
            meta["dataset_sha256"] = digest
            meta_bytes = json.dumps(meta).encode()
            (root / "input_metadata.json").write_bytes(meta_bytes)
            ev = kwargs["evidence"]
            ev.update(
                dataset_sha256=digest,
                metadata_sha256=hashlib.sha256(meta_bytes).hexdigest(),
            )
            (root / "evidence.json").write_text(json.dumps(ev))
            ref = root / "daily.csv"
            kwargs["reference"].to_csv(ref, index=False)
            ref_meta = kwargs["reference_metadata"]
            ref_meta["dataset_sha256"] = hashlib.sha256(ref.read_bytes()).hexdigest()
            (root / "daily.json").write_text(json.dumps(ref_meta))
            raw = configuration()
            raw["data"]["path"] = str(source)
            raw["output"]["directory"] = str(root / "results")
            raw["quality"] = dict(
                evidence_path=str(root / "evidence.json"),
                daily_reference_path=str(ref),
                daily_metadata_path=str(root / "daily.json"),
            )
            config = parse_config(raw)
            run = run_experiment(config)
            metrics = json.loads((run / "metrics.json").read_text())
            self.assertTrue(metrics["backtest_ready"])
            self.assertEqual(metrics["evaluation_status"], "formal")
            self.assertEqual(metrics["total_trades"], 1)
            ref.write_text(ref.read_text().replace("780.0", "800.0"))
            with self.assertRaises(DataValidationError):
                run_experiment(config)

    def test_bad_json_missing_configured_artifact_and_cli_quality_exit(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        import yaml

        from quant_lab.backtest.cli import main

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.parquet"
            candles().to_parquet(source, index=False)
            raw = configuration()
            raw["data"]["path"] = str(source)
            raw["output"]["directory"] = str(root / "results")
            path = root / "config.yaml"
            path.write_text(yaml.safe_dump(raw))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--config", str(path), "--quality-only"]), 2)
                self.assertEqual(main(["--config", str(path)]), 1)
                self.assertEqual(main(["--config", str(path), "--diagnostic"]), 0)
            for value in ["[]", '{"x": NaN}', "{broken"]:
                (root / "input_metadata.json").write_text(value)
                run = run_experiment(parse_config(raw), quality_only=True)
                self.assertEqual(
                    json.loads((run / "quality.json").read_text())["checks"][
                        "input_artifacts"
                    ]["status"],
                    "fail",
                )
            (root / "input_metadata.json").unlink()
            raw["quality"] = {"evidence_path": str(root / "missing.json")}
            run = run_experiment(parse_config(raw), quality_only=True)
            self.assertFalse(
                json.loads((run / "quality.json").read_text())["backtest_ready"]
            )
