"""runnerのテスト。外部APIは使用しない。"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from quant_lab.backtest.config import parse_config
from quant_lab.backtest.runner import run_experiment

from .fixtures import candles, configuration


class RunnerTests(unittest.TestCase):
    def test_reports_reproducible_unique_directories_and_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.parquet"
            candles().to_parquet(source, index=False)
            raw = configuration()
            raw["data"]["path"] = str(source)
            raw["output"]["directory"] = str(root / "results")
            config = parse_config(raw)
            first, second = run_experiment(config), run_experiment(config)
            self.assertNotEqual(first, second)
            for name in [
                "config.yaml",
                "trades.csv",
                "metrics.json",
                "equity_curve.html",
                "run_metadata.json",
            ]:
                self.assertTrue((first / name).is_file())
            for name in ["trades.csv", "metrics.json"]:
                self.assertEqual(
                    (first / name).read_bytes(), (second / name).read_bytes()
                )
            metadata = json.loads((first / "run_metadata.json").read_text())
            self.assertEqual(
                metadata["input_sha256"],
                hashlib.sha256(source.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                json.loads((first / "metrics.json").read_text())["total_trades"], 1
            )

    def test_input_metadata_mismatch_rejected_and_quality_warning_reported(self):
        from quant_lab.backtest.data import DataValidationError

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.parquet"
            candles().to_parquet(source, index=False)
            raw = configuration()
            raw["data"]["path"] = str(source)
            raw["output"]["directory"] = str(root / "results")
            config = parse_config(raw)
            sidecar = root / "input_metadata.json"
            sidecar.write_text(json.dumps({"dataset_sha256": "wrong"}))
            with self.assertRaises(DataValidationError):
                run_experiment(config)
            sidecar.write_text(json.dumps({"symbol": "US.OTHER"}))
            with self.assertRaises(DataValidationError):
                run_experiment(config)
            sidecar.write_text(
                json.dumps(
                    {
                        "backtest_ready": False,
                        "timestamp_label": {
                            "interpretation": "end_inferred",
                            "official_confirmation": "unconfirmed",
                        },
                    }
                )
            )
            run = run_experiment(config)
            metrics = json.loads((run / "metrics.json").read_text())
            self.assertTrue(metrics["warnings"])
            metadata = json.loads((run / "run_metadata.json").read_text())
            self.assertEqual(
                metadata["input_metadata_sha256"],
                hashlib.sha256(sidecar.read_bytes()).hexdigest(),
            )
            self.assertEqual(len(metadata["implementation_sha256"]), 64)
