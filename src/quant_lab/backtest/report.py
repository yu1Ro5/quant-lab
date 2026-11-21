"""自己完結するCSV/JSON/HTMLレポートの出力。"""

import html
import json
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go  # type: ignore[import-untyped]

from .metrics import experiment_metrics
from .models import CompletedTrade, ExperimentResult


def write_reports(
    directory: Path,
    result: ExperimentResult,
    initial_capital: float,
    *,
    quality: dict[str, Any] | None = None,
    diagnostic: bool = False,
) -> None:
    metrics = experiment_metrics(result, initial_capital)
    if quality is not None:
        metrics["backtest_ready"] = quality["backtest_ready"]
        metrics["evaluation_status"] = "provisional" if diagnostic else "formal"
        metrics["quality_reasons"] = quality["reasons"]
    columns = [f.name for f in fields(CompletedTrade)]
    pd.DataFrame([asdict(t) for t in result.trades], columns=columns).to_csv(
        directory / "trades.csv", index=False
    )
    (directory / "metrics.json").write_text(
        json.dumps(
            metrics,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    curve = equity_figure(result)
    notes = html.escape(
        json.dumps(
            {"warnings": result.warnings, "exclusions": result.exclusions},
            ensure_ascii=False,
            indent=2,
        )
    )
    document = curve.to_html(full_html=True, include_plotlyjs=True)
    if quality is not None:
        state = "暫定・診断専用" if diagnostic else "正式評価"
        document = document.replace(
            "<body>",
            f"<body><h1>{state}</h1><p>backtest_ready={quality['backtest_ready']}; 詳細: quality.md / quality.json</p>",
            1,
        )
    document = document.replace("<body>", "<body>" + summary_table(metrics), 1)
    document = document.replace(
        "</body>", f"<h2>警告・除外日</h2><pre>{notes}</pre></body>"
    )
    (directory / "equity_curve.html").write_text(document, encoding="utf-8")


def equity_figure(result: ExperimentResult) -> go.Figure:
    """同じ取引の資産推移をコスト前後で比較する。"""
    curve = go.Figure()
    for key, name, dash in [
        ("gross_equity", "コスト控除前", "dash"),
        ("equity", "コスト控除後", "solid"),
    ]:
        curve.add_trace(
            go.Scatter(
                x=[p["time"] for p in result.equity],
                y=[p[key] for p in result.equity],
                mode="lines",
                name=name,
                line=dict(dash=dash),
                hovertemplate="%{x}<br>$%{y:,.2f}<extra>%{fullData.name}</extra>",
            )
        )
    curve.update_layout(
        title="確定足終値での時価評価：コスト控除前・控除後",
        xaxis_title="UTC",
        yaxis_title="USD",
        xaxis_rangeslider_visible=True,
        hovermode="x unified",
    )
    return curve


def summary_table(metrics: dict[str, Any]) -> str:
    """JSONと同じ集計値から、説明付きサマリーを作る。"""

    def money(value: float) -> str:
        return f"${value:,.2f}"

    factor = metrics["profit_factor"]
    factor_label = (
        f"{factor:.2f}"
        if factor is not None
        else "算出不可（損失ゼロ）"
        if metrics["profit_factor_status"] == "no_losses"
        else "算出不可（取引なし・損益ゼロ）"
    )
    rows = [
        (
            "初期資金 → 最終残高",
            f"{money(metrics['initial_capital_usd'])} → {money(metrics['final_cash_usd'])}",
            "資金の最終的な増減",
        ),
        (
            "コスト控除後の総損益",
            money(metrics["net_pnl"]),
            "手数料などを引いた最終的な成績",
        ),
        (
            "コスト控除前の総損益",
            money(metrics["gross_pnl"]),
            "同じ取引のコストを除いた売買損益",
        ),
        (
            "総取引コスト",
            money(metrics["total_cost"]),
            "手数料・スリッページ・スプレッド概算の合計",
        ),
        (
            "取引回数",
            f"{metrics['total_trades']:,}回",
            "購入から決済まで完了した取引数",
        ),
        ("勝率", f"{metrics['win_rate']:.2%}", "コスト控除後に利益になった取引の割合"),
        (
            "Profit Factor",
            factor_label,
            "ネット総利益÷ネット総損失。1を超えると利益超過",
        ),
        (
            "最大ドローダウン",
            f"{money(metrics['max_drawdown_usd'])} / {metrics['max_drawdown_fraction']:.2%}",
            "控除後の確定足終値資産の過去最高値からの最大低下額・率",
        ),
    ]
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        "<style>.summary{margin:24px;font-family:sans-serif}.summary table{border-collapse:collapse;width:100%}"
        ".summary th,.summary td{border:1px solid #ddd;padding:10px;text-align:left}.summary th{background:#f0f4fa}</style>"
        '<section class="summary"><h1>バックテスト結果サマリー（USD）</h1>'
        "<table><thead><tr><th>表示項目</th><th>今回の値</th><th>分かること</th></tr></thead><tbody>"
        + body
        + "</tbody></table><p>控除前の線は、控除後と同じ取引に発生済みコストを戻した比較です。"
        "コストゼロで売買判断・購入余力を再計算した別実験ではありません。"
        "保有中は未発生の売却費用を含みません。</p></section>"
    )
