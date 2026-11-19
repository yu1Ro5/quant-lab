"""コスト前後の資産系列とHTMLサマリーを検証。"""

import tempfile
import unittest
from pathlib import Path

from quant_lab.backtest.report import equity_figure, write_reports

from .fixtures import candles, configuration, execute


class ReportTests(unittest.TestCase):
    def test_gross_equity_adds_only_incurred_costs_including_open_position(self):
        frame = candles()
        frame.loc[7:, ["open", "high", "low", "close"]] = [103.0, 103.1, 102.9, 103.0]
        raw = configuration()
        raw["execution"]["spread_bps_per_side"] = 3
        result = execute(frame, raw)
        # 最初の7本には購入費用もまだ発生しない。
        for point in result.equity[:8]:
            self.assertEqual(point["gross_equity"], point["equity"])
        point = result.equity[8]
        self.assertAlmostEqual(
            point["gross_equity"] - point["equity"], 0.1 + 103 * 0.0005
        )
        last = result.equity[-1]
        self.assertAlmostEqual(
            last["gross_equity"], 5000 + sum(t.gross_pnl for t in result.trades)
        )
        self.assertAlmostEqual(
            last["gross_equity"] - last["equity"],
            sum(t.total_cost for t in result.trades),
        )
        figure = equity_figure(result)
        self.assertEqual(len(figure.data), 2)
        self.assertEqual(list(figure.data[0].x), list(figure.data[1].x))
        self.assertEqual(figure.data[0].y[-1], last["gross_equity"])
        self.assertEqual(figure.data[1].y[-1], last["equity"])

    def test_summary_precedes_chart_and_handles_zero_trades(self):
        for flat in [False, True]:
            frame = candles()
            if flat:
                frame.loc[:, ["open", "high", "low", "close"]] = [
                    100.0,
                    101.0,
                    99.0,
                    100.0,
                ]
            result = execute(frame)
            with tempfile.TemporaryDirectory() as temp:
                write_reports(Path(temp), result, 5000)
                document = (Path(temp) / "equity_curve.html").read_text()
                self.assertLess(
                    document.index("<table"), document.index('class="plotly-graph-div"')
                )
                for label in [
                    "表示項目",
                    "今回の値",
                    "分かること",
                    "初期資金 → 最終残高",
                    "コスト控除後の総損益",
                    "コスト控除前の総損益",
                    "総取引コスト",
                    "取引回数",
                    "勝率",
                    "Profit Factor",
                    "最大ドローダウン",
                ]:
                    self.assertIn(label, document)
                self.assertIn("$5,000.00", document)
                if flat:
                    self.assertIn("算出不可（取引なし・損益ゼロ）", document)
                    self.assertTrue(
                        all(p["equity"] == p["gross_equity"] for p in result.equity)
                    )
