"""保存済み日足CSV/ParquetからインタラクティブHTMLとPNGを生成する。"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from quant_lab.stock.daily import NY, DailyError, normalize


def load_daily(path: Path) -> pd.DataFrame:
    data = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    if "date" not in data or data.empty:
        raise DailyError("date列を持つ日足CSV/Parquetが必要です")
    dates = pd.to_datetime(data.date, format="%Y-%m-%d", errors="raise")
    if dates.isna().any():
        raise DailyError("日付が欠損しています")
    raw = data.assign(time_key=dates.dt.strftime("%Y-%m-%d 00:00:00"))
    result = normalize(raw, dates.dt.strftime("%Y-%m-%d").tolist(),
                       dates.min().date(), dates.max().date())
    if "datetime" in data:
        actual = pd.to_datetime(data.datetime, utc=True)
        if not (actual.dt.tz_convert(NY).dt.strftime("%Y-%m-%d").to_numpy()
                == dates.dt.strftime("%Y-%m-%d").to_numpy()).all():
            raise DailyError("dateとdatetimeが一致しません")
    return result


def build_figure(data: pd.DataFrame) -> Any:
    go = importlib.import_module("plotly.graph_objects")
    make_subplots = importlib.import_module("plotly.subplots").make_subplots
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        row_heights=[0.5, 0.2, 0.3], vertical_spacing=0.05,
                        subplot_titles=["AAPL 日足 / AuType.NONE", "出来高", "終値推移"])
    x = data.date
    fig.add_trace(go.Candlestick(x=x, open=data.open, high=data.high,
                                 low=data.low, close=data.close, name="OHLC"), row=1, col=1)
    fig.add_trace(go.Bar(x=x, y=data.volume, name="Volume"), row=2, col=1)
    fig.add_trace(go.Scatter(x=x, y=data.close, mode="lines", name="Close (USD)",
                             hovertemplate="%{x}<br>Close: $%{y:.2f}<extra></extra>"),
                  row=3, col=1)
    fig.update_layout(height=1000, template="plotly_white", hovermode="x unified",
                      title="US.AAPL · moomoo · 通常立会 · 未調整", showlegend=False)
    fig.update_yaxes(title_text="USD", row=1, col=1)
    fig.update_yaxes(title_text="Shares", row=2, col=1)
    fig.update_yaxes(title_text="USD", row=3, col=1)
    fig.update_xaxes(type="date", rangeslider_visible=False)
    fig.update_xaxes(rangeslider_visible=True, rangeselector=dict(buttons=[
        dict(count=1, label="1M", step="month", stepmode="backward"),
        dict(count=3, label="3M", step="month", stepmode="backward"),
        dict(step="all", label="All")]), row=3, col=1)
    return fig


def render(source: Path, output: Path | None = None) -> dict[str, Any]:
    data = load_daily(source)
    fig = build_figure(data)
    base = output or source.with_suffix("")
    html, png = Path(str(base) + ".html"), Path(str(base) + ".png")
    html.parent.mkdir(parents=True, exist_ok=True)
    # JSを埋め込み、ブラウザでネット接続なしに開けるHTMLを保存する。
    fig.write_html(str(html), include_plotlyjs=True, full_html=True,
                   config={"scrollZoom": True, "displaylogo": False})
    try:
        fig.write_image(str(png), width=1500, height=1000, scale=1)
    except Exception as error:
        raise DailyError(f"HTML保存済み: {html}。PNG生成失敗（Kaleido/Chromeを確認）: {error}") from error
    return dict(status="rendered", count=len(data), html=str(html), png=str(png))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, help="拡張子なしの保存先")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(render(args.input, args.output), ensure_ascii=False))
        return 0
    except (OSError, ImportError, ValueError) as error:
        print(json.dumps(dict(status="error", error=str(error)), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
