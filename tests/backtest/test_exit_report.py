import json
import tempfile
import unittest
from pathlib import Path

from quant_lab.backtest.exit_comparison import run_exit_comparison
from quant_lab.backtest.exit_report import write_exit_html
from tests.backtest import test_exit_comparison


class ExitReportTests(unittest.TestCase):
    def test_readable_report_and_regeneration_preserves_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = test_exit_comparison.ExitComparisonTests().setup_files(Path(tmp))
            directory = run_exit_comparison(path, diagnostic=True)
            payload = (directory / "comparison.json").read_bytes()
            document = json.loads(payload)
            write_exit_html(directory, document["results"], document)
            content = (directory / "comparison.html").read_text()
            for label in [
                "主要な比較結果",
                "固定率との差",
                "比較している売り方",
                "累積ネット損益",
                "ドローダウン",
                "決済理由別",
                "月別",
                "再現性",
                "暫定",
                "サンプル不足",
            ]:
                self.assertTrue(label in content, label)
            self.assertNotIn("<th>input_sha256</th>", content)
            self.assertNotIn("<th>run_directory</th>", content)
            self.assertIn("<details>", content)
            self.assertEqual((directory / "comparison.json").read_bytes(), payload)
