"""保存済み5分足のHTML表示と外部日足比較。OpenDへの接続は不要。"""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from quant_lab.stock.daily import NY, VALUES, DailyError
from quant_lab.stock.intraday import DEFAULT, aggregate, assess, compare, json_bytes
from quant_lab.stock.storage import publish


def load(base: Path) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    data = pd.read_parquet(str(base) + ".parquet")
    metadata = json.loads(Path(str(base) + "_metadata.json").read_text())
    quality = json.loads(Path(str(base) + "_quality.json").read_text())
    digest = hashlib.sha256(Path(str(base) + ".parquet").read_bytes()).hexdigest()
    if digest != metadata.get("dataset_sha256") or digest != quality.get("dataset_sha256"):
        raise DailyError("Parquetとメタデータ/品質報告のハッシュが一致しません")
    if (metadata.get("source"), metadata.get("ktype"), metadata.get("autype")) != (
            "moomoo", "K_5M", "NONE"):
        raise DailyError("moomoo未調整5分足のメタデータではありません")
    utc = pd.to_datetime(data.datetime, utc=True, errors="raise")
    raw = data.assign(time_key=utc.dt.tz_convert(NY).dt.strftime("%Y-%m-%d %H:%M:%S"))
    sessions = [{"time": item["date"], "trade_date_type": "MORNING" if item["expected"] == 42 else "WHOLE"}
                for item in quality["sessions"]]
    validated, actual = assess(raw, sessions)
    if (len(data) != metadata["count"] or actual["missing_bars"] != quality["missing_bars"]
            or actual["status"] != metadata["quality_status"]):
        raise DailyError("データと品質メタデータが一致しません")
    if not utc.equals(pd.to_datetime(validated.datetime, utc=True)):
        raise DailyError("保存データの時刻順序が不正です")
    return validated, metadata, actual


def detail_window(data: pd.DataFrame, start: str | None = None,
                  end: str | None = None, *, session_dates: list[str] | None = None) -> pd.DataFrame:
    """軸だけでなく、描画に渡す行そのものを最大5営業日に制限する。"""
    days = sorted(session_dates if session_dates is not None else data.date.unique())
    if start is None and end is None:
        selected = days[-5:]
    else:
        selected = [day for day in days if (start is None or day >= start)
                    and (end is None or day <= end)]
    if not selected or len(selected) > 5:
        raise DailyError("表示期間はデータがある1〜5営業日を指定してください")
    return data.loc[data.date.isin(selected)].copy()


def figure(data: pd.DataFrame, quality: dict[str, Any]) -> Any:
    data = detail_window(data, session_dates=[s["date"] for s in quality["sessions"]])
    go = importlib.import_module("plotly.graph_objects")
    make_subplots = importlib.import_module("plotly.subplots").make_subplots
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.75, 0.25], vertical_spacing=0.05)
    eastern = data.datetime_et.dt.tz_localize(None)
    utc_hover = data.datetime.dt.strftime("%Y-%m-%d %H:%M UTC")
    fig.add_trace(go.Candlestick(x=eastern, open=data.open, high=data.high,
                                 low=data.low, close=data.close, name="OHLC (USD)",
                                 text=utc_hover), row=1, col=1)
    fig.add_trace(go.Bar(x=eastern, y=data.volume, name="Volume", text=utc_hover), row=2, col=1)
    fig.update_layout(title=f"US.AAPL · 5分足 · 未調整 · 通常立会 · {quality['status']}"
                           f" · 欠落{quality['missing_bars']}本 · 時刻は終了ラベルと推定（公式未確認）",
                      height=850, template="plotly_white", hovermode="x unified")
    fig.update_yaxes(title_text="USD", row=1, col=1)
    fig.update_yaxes(title_text="Shares", row=2, col=1)
    days = quality["sessions"][-5:]
    bounds = [eastern.min(), eastern.max()] if not data.empty else [
        days[0]["date"] + " 09:30:00", days[-1]["date"] + " 16:00:00"]
    fig.update_xaxes(type="date", rangeslider_visible=False, range=bounds)
    fig.update_xaxes(title_text="米国東部時間 (America/New_York、夏時間を反映)", row=2, col=1)
    return fig


def overview_figure(data: pd.DataFrame, quality: dict[str, Any]) -> Any:
    """全期間の概要は、5分足から集計した日足だけを描画する。"""
    go = importlib.import_module("plotly.graph_objects")
    days = aggregate(data, quality)
    fig = go.Figure(go.Candlestick(x=days.date, open=days.open, high=days.high,
                                   low=days.low, close=days.close, name="日足集計",
                                   text=days.complete.map({True: "5分足欠落なし", False: "欠落あり・不完全な集計"})))
    fig.update_layout(title=f"全期間の概要（日足集計・{len(days)}営業日）", height=350,
                      template="plotly_white", yaxis_title="USD",
                      xaxis=dict(type="date", rangeslider=dict(visible=False)))
    return fig


WINDOW_CONTROLLER = r"""
const payload = JSON.parse(document.getElementById('intraday-payload').textContent);
const days = payload.sessions.map(s => s.date);
const start = document.getElementById('period-start');
const end = document.getElementById('period-end');
const previous = document.getElementById('previous-period');
const next = document.getElementById('next-period');
const apply = document.getElementById('apply-period');
const latest = document.getElementById('latest-period');
const status = document.getElementById('period-status');
const graph = document.getElementById('intraday-detail');
let first = Math.max(0, days.length - 5), last = days.length - 1, busy = false;
function controls() {
  start.value = days[first]; end.value = days[last];
  previous.disabled = busy || first === 0;
  next.disabled = busy || last === days.length - 1;
  apply.disabled = latest.disabled = start.disabled = end.disabled = busy;
}
function summary(rows, selected) {
  const expected = payload.sessions.filter(s => selected.has(s.date))
    .reduce((total, s) => total + s.expected, 0);
  status.textContent = `${days[first]}〜${days[last]} / ${last-first+1}営業日 / ` +
    `${rows.length}本（期待${expected}本・欠落${expected-rows.length}本）。時刻は米国東部時間。`;
}
async function draw(a, b) {
  if (busy) return;
  first = a; last = b; busy = true; controls();
  status.textContent = '表示を更新しています…';
  try {
    const selected = new Set(days.slice(first, last+1));
    const rows = payload.rows.filter(r => selected.has(r[0]));
    const x = rows.map(r => r[1]), utc = rows.map(r => r[2]);
    const traces = [
      {type:'candlestick', x, open:rows.map(r=>r[3]), high:rows.map(r=>r[4]),
       low:rows.map(r=>r[5]), close:rows.map(r=>r[6]), text:utc, name:'OHLC (USD)'},
      {type:'bar', x, y:rows.map(r=>r[7]), text:utc, name:'Volume', xaxis:'x2', yaxis:'y2'}
    ];
    const range = rows.length ? [x[0], x[x.length-1]] :
      [days[first]+' 09:30:00', days[last]+' 16:00:00'];
    const layout = {...graph.layout, uirevision:`${days[first]}-${days[last]}`,
      xaxis:{...graph.layout.xaxis, range, autorange:false, rangeslider:{visible:false}},
      xaxis2:{...graph.layout.xaxis2, range, autorange:false, rangeslider:{visible:false}},
      yaxis:{...graph.layout.yaxis, autorange:true},
      yaxis2:{...graph.layout.yaxis2, autorange:true}};
    await Plotly.react(graph, traces, layout, {scrollZoom:true, displaylogo:false, responsive:true});
    summary(rows, selected);
  } catch (error) {
    status.textContent = '表示更新に失敗しました: '+error.message;
  } finally {busy=false; controls();}
}
apply.addEventListener('click', () => {
  const selected = days.map((d,i)=>({d,i})).filter(v=>v.d>=start.value && v.d<=end.value);
  if (!selected.length || selected.length>5) {
    status.textContent = '表示期間は1〜5営業日を指定してください。全期間は下の日足概要で確認できます。';
    return;
  }
  draw(selected[0].i, selected[selected.length-1].i);
});
previous.addEventListener('click',()=>{
  const count=last-first+1, a=Math.max(0,first-count); draw(a,Math.min(a+count-1,days.length-1));
});
next.addEventListener('click',()=>{
  const count=last-first+1, a=Math.min(first+count,Math.max(0,days.length-count));
  draw(a,Math.min(a+count-1,days.length-1));
});
latest.addEventListener('click',()=>draw(Math.max(0,days.length-5),days.length-1));
controls();
const initial=new Set(days.slice(first,last+1));
summary(payload.rows.filter(r=>initial.has(r[0])),initial);
"""


def page_html(data: pd.DataFrame, metadata: dict[str, Any], quality: dict[str, Any]) -> str:
    local = data.datetime_et.dt.strftime("%Y-%m-%d %H:%M:%S")
    utc = data.datetime.dt.strftime("%Y-%m-%d %H:%M UTC")
    rows = data[VALUES].copy()
    rows.insert(0, "date", data.date)
    rows.insert(1, "eastern", local)
    rows.insert(2, "utc", utc)
    payload = json.dumps(dict(rows=rows.to_numpy().tolist(), sessions=quality["sessions"]),
                         ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    detail = figure(data, quality).to_html(include_plotlyjs=True, full_html=False,
                                          div_id="intraday-detail", post_script=WINDOW_CONTROLLER,
                                          config={"scrollZoom": True, "displaylogo": False, "responsive": True})
    overview = overview_figure(data, quality).to_html(include_plotlyjs=False, full_html=False,
                                                     div_id="daily-overview",
                                                     config={"displaylogo": False, "responsive": True})
    identifier = html.escape(metadata["dataset_sha256"][:12])
    fetched = html.escape(metadata["fetched_at"])
    first, last = quality["sessions"][0]["date"], quality["sessions"][-1]["date"]
    return f'''<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AAPL 5分足・期間別表示</title><style>
body{{font-family:system-ui,sans-serif;margin:20px;color:#24334a}}button,input{{font:inherit;padding:8px;margin:4px}}
.controls{{display:flex;flex-wrap:wrap;align-items:center;gap:6px}}p{{line-height:1.6}}
</style></head><body><h1>AAPL 5分足</h1>
<p>Dataset {identifier} · 取得/検証日時 {fetched}<br>
構造検証のみ。終了ラベルは推定、正式な時刻定義と出来高の日足との差は要確認です。</p>
<h2>5分足の詳細（最大5営業日）</h2>
<div class="controls"><button id="previous-period">前の期間</button>
<label>開始<input id="period-start" type="date" min="{first}" max="{last}"></label>
<label>終了<input id="period-end" type="date" min="{first}" max="{last}"></label>
<button id="apply-period">表示</button><button id="next-period">次の期間</button>
<button id="latest-period">直近5営業日</button></div>
<p id="period-status" role="status" aria-live="polite"></p>
<script id="intraday-payload" type="application/json">{payload}</script>
{detail}<h2>全期間の概要（日足集計）</h2>
<p>保存済み5分足を日足に集計した概要です。上の5分足詳細とは足種が異なります。
全件を保持し、描画する5分足だけを制限しています。</p>{overview}</body></html>'''


def chart(base: Path) -> dict[str, Any]:
    data, metadata, quality = load(base)
    content = page_html(data, metadata, quality)
    paths = publish(base, {".html": content.encode("utf-8")})
    return dict(status="rendered", quality=quality["status"],
                total_bars=len(data), initial_detail_bars=len(detail_window(
                    data, session_dates=[s["date"] for s in quality["sessions"]])), paths=paths)


def yahoo(start: str, end: str) -> pd.DataFrame:
    yf = importlib.import_module("yfinance")
    raw = yf.download("AAPL", start=start,
                      end=(pd.Timestamp(end).date() + timedelta(days=1)).isoformat(),
                      interval="1d", auto_adjust=False, prepost=False, progress=False, threads=False)
    if raw.empty:
        raise DailyError("Yahoo Financeの応答が空です")
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.xs("AAPL", axis=1, level=-1)
    result = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    result.columns = VALUES
    result.insert(0, "date", pd.to_datetime(raw.index).strftime("%Y-%m-%d"))
    return result.reset_index(drop=True)


def external_compare(base: Path, reference: Path | None, use_yahoo: bool,
                     source: str) -> dict[str, Any]:
    data, metadata, quality = load(base)
    if use_yahoo:
        ref = yahoo(metadata["request_start"], metadata["effective_end"])
    elif reference is not None:
        ref = pd.read_csv(reference)
    else:
        raise DailyError("比較データ未指定")
    comparison = compare(aggregate(data, quality), ref)
    compared = comparison.loc[comparison.match_status == "compared"]
    price_deltas = compared[[col + "_delta" for col in VALUES[:4]]].abs()
    report = dict(source="Yahoo Finance via yfinance (auto_adjust=False)" if use_yahoo else source,
                  dataset_sha256=metadata["dataset_sha256"],
                  adjustment="unadjusted requested; upstream split conventions may differ",
                  compared_at=datetime.now(timezone.utc).isoformat(),
                  compared_days=len(compared), aggregate_only=int(comparison.match_status.eq("aggregate_only").sum()),
                  reference_only=int(comparison.match_status.eq("reference_only").sum()),
                  max_absolute_price_delta=float(price_deltas.max().max()) if len(compared) else None,
                  nonzero_price_delta_days=int(price_deltas.gt(0.000001).any(axis=1).sum()),
                  nonzero_volume_delta_days=int(compared.volume_delta.abs().gt(0).sum()),
                  quality=quality["status"], status="compared" if len(compared) else "no_overlap")
    paths = publish(base, {"_external_reference.csv": ref.to_csv(index=False).encode("utf-8"),
                           "_external_comparison.csv": comparison.to_csv(index=False).encode("utf-8"),
                           "_metadata.json": json_bytes({**metadata, "external_comparison": dict(
                               source=report["source"], status=report["status"],
                               report_suffix="_external_comparison_status.json")}),
                           "_external_comparison_status.json": json_bytes(report)})
    return {**report, "paths": paths}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["chart", "compare"])
    parser.add_argument("--dataset", type=Path, default=DEFAULT, help="拡張子なしの保存先")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reference-csv", type=Path)
    group.add_argument("--yahoo", action="store_true")
    parser.add_argument("--reference-source", default="external CSV; provider unspecified")
    args = parser.parse_args(argv)
    if args.command == "compare" and not (args.reference_csv or args.yahoo):
        parser.error("--reference-csvまたは--yahooを指定してください")
    try:
        result = chart(args.dataset) if args.command == "chart" else external_compare(
            args.dataset, args.reference_csv, args.yahoo, args.reference_source)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result["status"] == "no_overlap" else 0
    except Exception as error:
        failure = dict(status="error", command=args.command, error=str(error))
        if args.command == "compare":
            publish(args.dataset, {"_external_comparison_status.json": json_bytes(failure)})
        print(json.dumps(failure, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
