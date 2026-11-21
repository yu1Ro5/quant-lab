"""シナリオ横断のCSV・JSON・自己完結HTML。"""

import html
import json
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go  # type: ignore[import-untyped]


def write_cost_reports(
    directory: Path, rows: list[dict[str, Any]], manifest: dict[str, Any]
) -> None:
    frame = pd.DataFrame(rows)
    frame.to_csv(directory / "comparison.csv", index=False)
    document = dict(manifest, results=rows)
    (directory / "comparison.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    figure = go.Figure(
        go.Bar(x=[r["name"] for r in rows], y=[r["net_pnl"] for r in rows])
    )
    figure.update_layout(
        title="シナリオ別ネット損益", xaxis_title="Scenario", yaxis_title="USD"
    )
    provisional = any(
        r["evaluation_status"] != "formal" or not r["backtest_ready"] for r in rows
    )
    state = "暫定・診断専用" if provisional else "正式評価"
    columns = [
        k for k in rows[0] if k not in ("exclusions", "warnings", "quality_reasons")
    ]
    table = frame[columns].to_html(index=False, escape=True)
    notes = html.escape(
        json.dumps(
            dict(
                assumptions=manifest["assumptions"],
                fee_model=manifest["fee_model"],
                artifacts=manifest["artifacts"],
                break_even=manifest["break_even"],
                quality=[
                    dict(
                        name=r["name"],
                        warnings=r["warnings"],
                        quality_reasons=r["quality_reasons"],
                    )
                    for r in rows
                ],
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    content = figure.to_html(full_html=True, include_plotlyjs=True)
    content = content.replace(
        "<body>", f"<body><h1>{state}：取引コスト比較（USD）</h1>{table}", 1
    )
    content = content.replace(
        "</body>", f"<h2>前提・入力・品質・損益分岐</h2><pre>{notes}</pre></body>"
    )
    (directory / "comparison.html").write_text(content, encoding="utf-8")
