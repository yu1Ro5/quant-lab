"""experiment_e2eのテスト。外部APIは使用しない。"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from .fixtures import candles, configuration


class ExperimentE2ETests(unittest.TestCase):
    def test_cli_yaml_success_and_error_and_zero_trade_csv_header(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        import yaml

        from quant_lab.backtest.cli import main

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = configuration()
            source = root / "input.parquet"
            frame = candles()
            frame.loc[:, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.0, 100.0]
            frame.to_parquet(source, index=False)
            raw["data"]["path"] = str(source)
            raw["output"]["directory"] = str(root / "results")
            path = root / "config.yaml"
            path.write_text(yaml.safe_dump(raw))
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(main(["--config", str(path)]), 0)
                self.assertEqual(main(["--config", str(root / "missing")]), 1)
            run = next((root / "results").iterdir())
            self.assertEqual(len(pd.read_csv(run / "trades.csv")), 0)
            self.assertIn("実験結果:", stdout.getvalue())
            self.assertIn("エラー:", stderr.getvalue())
            with redirect_stderr(stderr), self.assertRaises(SystemExit):
                main([str(source), "--config", str(path)])

    def test_cli_process_trades_reports_and_reproducibility(self):
        """人工Parquet＋YAML→実CLI→全成果物。部品のmockは使用しない。"""
        import subprocess
        import sys

        import yaml

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.parquet"
            candles().to_parquet(source, index=False)
            raw = configuration()
            raw["data"]["path"] = str(source)
            raw["output"]["directory"] = str(root / "results")
            config = root / "config.yaml"
            config.write_text(yaml.safe_dump(raw))
            runs = []
            for _ in range(2):
                process = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "quant_lab.backtest",
                        "--config",
                        str(config),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(process.returncode, 0, process.stderr)
                run = Path(process.stdout.splitlines()[0].removeprefix("実験結果: "))
                self.assertIn(
                    f"実験結果HTML：{run / 'equity_curve.html'}", process.stdout
                )
                runs.append(run)
                for name in [
                    "config.yaml",
                    "trades.csv",
                    "metrics.json",
                    "equity_curve.html",
                    "run_metadata.json",
                ]:
                    self.assertTrue((run / name).is_file(), name)
                trades = pd.read_csv(run / "trades.csv")
                self.assertEqual(len(trades), 1)
                trade = trades.iloc[0]
                self.assertEqual(trade.exit_reason, "stop_loss")
                self.assertEqual(trade.entry_reference_price, 103)
                self.assertEqual(trade.shares, 1)
                stop = 103 * 1.0002 * 0.995
                expected_gross = stop - 103
                expected_net = expected_gross - 0.2 - (103 + stop) * 0.0002
                self.assertAlmostEqual(trade.gross_pnl, expected_gross)
                self.assertAlmostEqual(trade.net_pnl, expected_net)
                metrics = json.loads((run / "metrics.json").read_text())
                self.assertEqual(metrics["total_trades"], 1)
                self.assertAlmostEqual(metrics["net_pnl"], expected_net)
                self.assertAlmostEqual(metrics["final_cash_usd"], 5000 + expected_net)
                metadata = json.loads((run / "run_metadata.json").read_text())
                self.assertEqual(
                    metadata["input_sha256"],
                    hashlib.sha256(source.read_bytes()).hexdigest(),
                )
                self.assertEqual(
                    yaml.safe_load((run / "config.yaml").read_text())["data"]["path"],
                    str(source.resolve()),
                )
                self.assertIn("Plotly.newPlot", (run / "equity_curve.html").read_text())
            self.assertNotEqual(*runs)
            for name in ["trades.csv", "metrics.json"]:
                self.assertEqual(
                    (runs[0] / name).read_bytes(), (runs[1] / name).read_bytes()
                )
