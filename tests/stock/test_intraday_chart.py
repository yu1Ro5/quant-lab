"""5分足の保存整合性とチャートを一時データで検証する。"""

import hashlib
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from quant_lab.stock import intraday_chart
from quant_lab.stock.intraday import assess, json_bytes
from quant_lab.stock.storage import publish
from tests.stock.test_intraday import bars


class IntradayChartTests(unittest.TestCase):
    def test_detail_window_limits_actual_rows_to_five_sessions(self) -> None:
        days = pd.bdate_range("2026-11-02", periods=12).strftime("%Y-%m-%d").tolist()
        raw = pd.concat([bars(day) for day in days], ignore_index=True)
        data, quality = assess(raw, [{"time": day, "trade_date_type": "WHOLE"} for day in days])
        selected = intraday_chart.detail_window(data)
        self.assertEqual(len(selected), 390)
        self.assertEqual(selected.date.unique().tolist(), days[-5:])
        self.assertEqual(len(intraday_chart.detail_window(data, days[0], days[1])), 156)
        missing_last = data.loc[data.date != days[-1]]
        self.assertEqual(len(intraday_chart.detail_window(missing_last, session_dates=days)), 312)
        with self.assertRaises(ValueError):
            intraday_chart.detail_window(data, days[0], days[-1])

    @unittest.skipUnless(shutil.which("node"), "JavaScript動作検証用Node未導入")
    def test_period_controls_replace_rows_and_reject_long_ranges(self) -> None:
        days = pd.bdate_range("2026-11-02", periods=12).strftime("%Y-%m-%d").tolist()
        rows = [[day, day + " 09:35:00", day + " 14:35 UTC", 100, 102, 99, 101, 10]
                for day in days]
        payload = {"rows": rows, "sessions": [{"date": day, "expected": 78} for day in days]}
        script = r'''
const vm=require('node:vm'), assert=require('node:assert/strict');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const elements={};
for(const id of ['intraday-payload','period-start','period-end','previous-period',
 'next-period','apply-period','latest-period','period-status','intraday-detail']) {
 elements[id]={value:'',textContent:'',disabled:false,events:{},
   addEventListener(name,fn){this.events[name]=fn;}};
}
elements['intraday-payload'].textContent=JSON.stringify(input.payload);
const graph=elements['intraday-detail'];
graph.layout={xaxis:{},xaxis2:{},yaxis:{},yaxis2:{}};
let calls=0;
vm.runInNewContext(input.controller,{document:{getElementById:id=>elements[id]},
 Plotly:{react:async(g,traces,layout)=>{calls++;g.traces=traces;g.layout=layout;}}});
const click=async id=>{elements[id].events.click();await new Promise(setImmediate);};
(async()=>{
 assert.equal(elements['period-start'].value,input.payload.sessions[7].date);
 await click('previous-period');
 assert.equal(graph.traces[0].x.length,5);
 assert.equal(graph.traces[0].x[0],input.payload.rows[2][1]);
 assert.equal(graph.traces[1].y.length,5);
 assert.equal(graph.layout.xaxis2.rangeslider.visible,false);
 await click('next-period');
 assert.equal(graph.traces[0].x[0],input.payload.rows[7][1]);
 elements['period-start'].value=input.payload.sessions[0].date;
 elements['period-end'].value=input.payload.sessions[1].date;
 await click('apply-period');
 assert.equal(graph.traces[0].x.length,2);
 const before=calls;
 elements['period-end'].value=input.payload.sessions[11].date;
 await click('apply-period');
 assert.equal(calls,before);
 assert.match(elements['period-status'].textContent,/1〜5/);
 await click('latest-period');
 assert.equal(graph.traces[0].x[0],input.payload.rows[7][1]);
 assert.equal(elements['next-period'].disabled,true);
 console.log('period controls OK');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which("node") or "node", "-e", script],
                                input=json.dumps({"controller": intraday_chart.WINDOW_CONTROLLER,
                                                  "payload": payload}), text=True,
                                capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("period controls OK", result.stdout)

    def test_html_has_window_controls_and_separate_daily_overview(self) -> None:
        days = pd.bdate_range("2026-11-02", periods=8).strftime("%Y-%m-%d").tolist()
        data, quality = assess(pd.concat([bars(day) for day in days], ignore_index=True),
                                [{"time": day, "trade_date_type": "WHOLE"} for day in days])
        figure = intraday_chart.figure(data, quality)
        self.assertEqual(len(figure.data[0].x), 390)
        self.assertFalse(figure.layout.xaxis2.rangeslider.visible)
        overview = intraday_chart.overview_figure(data, quality)
        self.assertEqual(len(overview.data[0].x), 8)
        html = intraday_chart.page_html(data, {"dataset_sha256": "a" * 64,
                                              "fetched_at": "2026-11-19T00:00:00Z"}, quality)
        for marker in ['id="period-start"', 'id="period-end"', 'id="previous-period"',
                       'id="next-period"', 'id="intraday-payload"', 'Plotly.react', '日足集計']:
            self.assertIn(marker, html)

    def test_dataset_round_trip_and_hash_mismatch(self) -> None:
        data, quality = assess(bars("2026-11-16"),
                                [{"time": "2026-11-16", "trade_date_type": "WHOLE"}])
        content = data.to_parquet(index=False)
        digest = hashlib.sha256(content).hexdigest()
        quality["dataset_sha256"] = digest
        metadata = dict(source="moomoo", ktype="K_5M", autype="NONE", count=78,
                        quality_status="passed", dataset_sha256=digest)
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "aapl"
            publish(base, {".parquet": content, "_metadata.json": json_bytes(metadata),
                           "_quality.json": json_bytes(quality)})
            loaded, _, report = intraday_chart.load(base)
            self.assertEqual(len(loaded), 78)
            self.assertEqual(report["missing_bars"], 0)
            metadata["dataset_sha256"] = "wrong"
            Path(str(base) + "_metadata.json").write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, "ハッシュ"):
                intraday_chart.load(base)

    def test_figure_uses_eastern_axis_and_embedded_html(self) -> None:
        data, quality = assess(bars("2026-11-16"),
                                [{"time": "2026-11-16", "trade_date_type": "WHOLE"}])
        figure = intraday_chart.figure(data, quality)
        self.assertEqual([trace.type for trace in figure.data], ["candlestick", "bar"])
        self.assertEqual(pd.Timestamp(figure.data[0].x[0]).hour, 9)
        self.assertIn("14:35 UTC", figure.data[0].text[0])
        self.assertIn("plotly.js", figure.to_html(include_plotlyjs=True))


class StorageTests(unittest.TestCase):
    def test_publish_rolls_back_all_outputs_on_replace_failure(self) -> None:
        replace = os.replace
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "aapl"
            publish(base, {".csv": b"old csv", ".parquet": b"old parquet"})
            count = 0

            def fail_second(src: str | Path, dst: str | Path) -> None:
                nonlocal count
                count += 1
                if count == 2:
                    raise OSError("replace failed")
                replace(src, dst)

            with patch("os.replace", side_effect=fail_second), self.assertRaises(OSError):
                publish(base, {".csv": b"new csv", ".parquet": b"new parquet"})
            self.assertEqual(Path(str(base) + ".csv").read_bytes(), b"old csv")
            self.assertEqual(Path(str(base) + ".parquet").read_bytes(), b"old parquet")
            self.assertEqual(len(list(Path(tmp).iterdir())), 2)
