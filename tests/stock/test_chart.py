"""保存データのグラフ入力とPlotly構造のテスト。株価は単体テスト専用。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from quant_lab.stock import chart


class ChartTests(unittest.TestCase):
    def test_csv_is_validated_and_sorted(self) -> None:
        data = pd.DataFrame(dict(date=["2026-11-17", "2026-11-16"],
                                 open=[100, 101], high=[103, 104], low=[99, 100],
                                 close=[102, 103], volume=[1000, 2000]))
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.csv"
            data.to_csv(path, index=False)
            result = chart.load_daily(path)
            self.assertEqual(result.date.tolist(), ["2026-11-16", "2026-11-17"])
            data.assign(high=1).to_csv(path, index=False)
            with self.assertRaises(ValueError):
                chart.load_daily(path)

    def test_empty_date_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.csv"
            pd.DataFrame(dict(date=[None], open=[100], high=[102], low=[99],
                              close=[101], volume=[1000])).to_csv(path, index=False)
            with self.assertRaises(ValueError):
                chart.load_daily(path)

    def test_real_figure_has_ohlc_volume_close_and_offline_html(self) -> None:
        data = pd.DataFrame(dict(date=["2026-11-16"], open=[100], high=[102],
                                 low=[99], close=[101], volume=[1000]))
        fig = chart.build_figure(data)
        self.assertEqual([trace.type for trace in fig.data],
                         ["candlestick", "bar", "scatter"])
        self.assertEqual(list(fig.data[0].close), [101])
        self.assertEqual(list(fig.data[1].y), [1000])
        self.assertEqual(list(fig.data[2].y), [101])
        self.assertTrue(fig.layout.xaxis3.rangeslider.visible)
        html = fig.to_html(include_plotlyjs=True)
        self.assertIn("plotly.js", html)
        self.assertNotIn('<script src="https://cdn.plot.ly', html)


if __name__ == "__main__":
    unittest.main()
