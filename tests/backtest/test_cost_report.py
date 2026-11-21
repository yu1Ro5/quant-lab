"""表示の再生成が機械可読成果物を変更しないことを検証。"""

import hashlib
import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from quant_lab.backtest.cost_report import write_cost_html
from quant_lab.backtest.cost_sensitivity import run_cost_comparison

from . import test_cost_sensitivity


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.headings = []
        self.in_heading = False
        self.details = 0

    def handle_starttag(self, tag, attrs):
        if tag == "h2":
            self.in_heading = True
        if tag == "details":
            self.details += 1

    def handle_endtag(self, tag):
        if tag == "h2":
            self.in_heading = False

    def handle_data(self, data):
        if self.in_heading:
            self.headings.append(data)


class CostReportTests(unittest.TestCase):
    def test_html_regeneration_preserves_csv_json_and_escapes_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            config, _ = test_cost_sensitivity.CostComparisonTests().setup_files(Path(temp))
            directory = run_cost_comparison(config, diagnostic=True)
            files = [directory / "comparison.csv", directory / "comparison.json"]
            before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
            manifest = json.loads(files[1].read_text())
            rows = manifest["results"]
            rows[0]["name"] = "<script>alert(1)</script>"
            write_cost_html(directory, rows, manifest)
            self.assertEqual(
                before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
            )
            content = (directory / "comparison.html").read_text()
            self.assertNotIn("<script>alert(1)</script>", content)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", content)
            self.assertIn("暫定・診断専用", content)
            self.assertIn("$0.000006", content)
            self.assertIn("comparison.csv", content)
            self.assertIn("equity_curve.html", content)
            parsed = VisibleText()
            parsed.feed(content)
            self.assertEqual(
                parsed.headings[:3], ["ネット損益の比較", "主要指標", "費用の内訳"]
            )
            self.assertEqual(parsed.details, 3)
            # 成績表にハッシュや保存パスを混ぜない。
            first_table = content.split("<table>")[1].split("</table>")[0]
            self.assertNotIn("input_sha256", first_table)
            self.assertNotIn("run_directory", first_table)
