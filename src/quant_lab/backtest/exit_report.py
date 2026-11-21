"""保存済み比較結果から、人が読むためのHTMLだけを生成する。"""

import html
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import plotly.graph_objects as go  # type: ignore[import-untyped]

COLORS = ["#2563eb", "#d97706", "#0d9488", "#9333ea", "#db2777"]
LABELS = {
    "fixed_stop": "固定率",
    "trailing_stop": "トレーリング",
    "time_exit": "時間決済",
}


def write_exit_html(
    directory: Path, rows: list[dict[str, Any]], manifest: dict[str, Any]
) -> None:
    def escape(value: Any) -> str:
        return html.escape(str(value))

    def money(value: Any) -> str:
        return "—" if value is None else f"${value:,.2f}"

    def ratio(value: Any) -> str:
        return "—" if value is None else f"{value:.2f}"

    def table(headers: list[str], values: list[list[Any]]) -> str:
        return (
            '<div class="scroll"><table><thead><tr>'
            + "".join(f"<th>{escape(h)}</th>" for h in headers)
            + "</tr></thead><tbody>"
            + "".join(
                "<tr>" + "".join(f"<td>{escape(v)}</td>" for v in row) + "</tr>"
                for row in values
            )
            + "</tbody></table></div>"
        )

    def details(title: str, value: Any) -> str:
        return f"<details><summary>{escape(title)}</summary><pre>{escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre></details>"

    runs = manifest.get("runs", [])
    names = list(dict.fromkeys(r["name"] for r in rows))
    colors = {name: COLORS[i % len(COLORS)] for i, name in enumerate(names)}
    configs = {r["name"]: r["config"] for r in runs}
    labels = {
        name: LABELS.get(configs.get(name, {}).get("exit", {}).get("strategy"), name)
        for name in names
    }

    def label(name: str) -> str:
        return f"{labels[name]}（{name}）"

    provisional = any(
        r.get("evaluation_status") != "formal" or not r.get("backtest_ready")
        for r in rows
    )
    base = manifest.get("original_base_config", {})
    symbol = base.get("data", {}).get("symbol", "銘柄未記録")
    cards = []
    for name in names:
        component = configs.get(name, {}).get("exit", {})
        params = component.get("params", {})
        conditions = (
            [f"損切り {params['stop_loss_pct']:g}%"]
            if "stop_loss_pct" in params
            else []
        )
        for key, title, unit in [
            ("take_profit_pct", "利確", "%"),
            ("trailing_stop_pct", "追従幅", "%"),
            ("max_holding_minutes", "最大保有", "分"),
            ("exit_time_et", "指定時刻", " ET"),
        ]:
            if params.get(key) is not None:
                conditions.append(f"{title} {params[key]}{unit}")
        conditions.append("当日引けまでに全決済")
        cards.append(
            f'<article style="border-top:4px solid {colors[name]}"><h3>{escape(label(name))}</h3><p>{escape(" / ".join(conditions))}</p></article>'
        )
    sections = []
    graphs = []
    breakdowns = []
    common_warnings = list(
        dict.fromkeys(str(w) for row in rows for w in row.get("warnings", []))
    )
    js_included = False
    for period, title in [
        ("in_sample", "開発期間（IS）"),
        ("out_of_sample", "評価期間（OOS）"),
    ]:
        selected = [r for r in rows if r["period"] == period]
        if not selected:
            continue
        heading = f"{title}｜{selected[0]['start']}〜{selected[0]['end']}"
        fixed = next(
            (
                r
                for r in selected
                if configs.get(r["name"], {}).get("exit", {}).get("strategy")
                == "fixed_stop"
            ),
            None,
        )
        if (
            sum(
                configs.get(r["name"], {}).get("exit", {}).get("strategy")
                == "fixed_stop"
                for r in selected
            )
            != 1
        ):
            fixed = None
        values = [
            [
                label(r["name"]),
                money(r["net_pnl"]),
                money(r["net_pnl"] - fixed["net_pnl"]) if fixed else "—",
                f"{r['win_rate']:.1%}",
                f"{r['total_trades']:,}",
                ratio(r["profit_factor"]),
                money(r["max_drawdown_usd"]),
            ]
            for r in selected
        ]
        warning = (
            '<p class="notice">サンプル不足：取引30件未満の候補があります。30件以上でも統計的優位性を保証しません。</p>'
            if any(r["total_trades"] < 30 for r in selected)
            else ""
        )
        sections.append(
            f"<h3>{escape(heading)}</h3>{warning}"
            + table(
                [
                    "売り方",
                    "ネット損益",
                    "固定率との差",
                    "勝率",
                    "取引数",
                    "PF",
                    "最大下落額",
                ],
                values,
            )
        )
        pnl = go.Figure()
        dd = go.Figure()
        for row in selected:
            run = Path(row["run_directory"])
            curve = pd.read_csv(run / "equity.csv")
            equity = curve["equity"]
            initial = row["initial_capital_usd"]
            peaks = equity.cummax().clip(lower=initial)
            for figure, y in [(pnl, equity - initial), (dd, equity - peaks)]:
                figure.add_trace(
                    go.Scatter(
                        x=curve["time"],
                        y=y,
                        mode="lines",
                        name=label(row["name"]),
                        line=dict(color=colors[row["name"]]),
                        hovertemplate="%{x}<br>$%{y:,.2f}<extra>%{fullData.name}</extra>",
                    )
                )
        graph_html = []
        for figure, graph_title in [(pnl, "累積ネット損益"), (dd, "ドローダウン")]:
            figure.update_layout(
                title=graph_title,
                template="plotly_white",
                height=350,
                margin=dict(l=65, r=25, t=55, b=55),
                yaxis_title="USD",
                xaxis_title="日時（UTC）",
                hovermode="x unified",
                legend=dict(orientation="h", y=1.15),
                yaxis=dict(zeroline=True),
            )
            graph_html.append(
                f"<h4>{graph_title}</h4>"
                + figure.to_html(
                    full_html=False,
                    include_plotlyjs=True if not js_included else False,
                    config=dict(responsive=True, displaylogo=False),
                )
            )
            js_included = True
        graphs.append(
            f'<h3>{escape(heading)}</h3><p class="muted">各候補は{money(selected[0]["initial_capital_usd"])}で独立開始。別期間との連続運用ではありません。</p>'
            + "".join(graph_html)
        )
        costs = table(
            [
                "売り方",
                "グロス損益",
                "委託手数料",
                "SEC",
                "FINRA",
                "CAT",
                "スプレッド",
                "スリッページ",
                "総費用",
                "ネット損益",
            ],
            [
                [label(r["name"])]
                + [
                    f"${r[k]:,.6f}" if k in ("cat_fee",) else money(r[k])
                    for k in [
                        "gross_pnl",
                        "commission",
                        "sec_fee",
                        "finra_fee",
                        "cat_fee",
                        "spread",
                        "slippage",
                        "total_cost",
                        "net_pnl",
                    ]
                ]
                for r in selected
            ],
        )
        characteristics = table(
            ["売り方", "平均利益", "平均損失", "利益/損失比"],
            [
                [
                    label(r["name"]),
                    money(r["average_profit"]),
                    money(r["average_loss"]),
                    ratio(r["profit_loss_ratio"]),
                ]
                for r in selected
            ],
        )
        groups = []
        reasons = {
            "stop_loss": "損切り",
            "take_profit": "利確",
            "trailing_stop": "追従ストップ",
            "time_exit": "時間決済",
            "market_close": "当日引け",
        }
        for key, group_title in [
            ("exit_reason_performance", "決済理由別"),
            ("monthly_performance", "月別"),
        ]:
            grouped = [
                [
                    label(r["name"]),
                    reasons.get(g["group"], g["group"]),
                    g["total_trades"],
                    f"{g['win_rate']:.1%}",
                    money(g["gross_pnl"]),
                    money(g["total_cost"]),
                    money(g["net_pnl"]),
                ]
                for r in selected
                for g in r.get(key, [])
            ]
            groups.append(
                f"<h4>{group_title}</h4>"
                + (
                    table(
                        [
                            "売り方",
                            "決済理由" if key.startswith("exit") else "月",
                            "取引数",
                            "勝率",
                            "グロス損益",
                            "費用",
                            "ネット損益",
                        ],
                        grouped,
                    )
                    if grouped
                    else "<p>該当する取引はありません。</p>"
                )
            )
        links = []
        for row in selected:
            relative = quote(
                Path(os.path.relpath(row["run_directory"], directory)).as_posix()
            )
            links.append(
                f'<li>{escape(label(row["name"]))}：<a href="{relative}/equity_curve.html">個別レポート</a> · <a href="{relative}/trades.csv">取引CSV</a></li>'
            )
        breakdowns.append(
            f"<h3>{escape(heading)}</h3><h4>取引の特徴</h4>{characteristics}<h4>費用内訳</h4>{costs}"
            + "".join(groups)
            + "<ul>"
            + "".join(links)
            + "</ul>"
        )
    css = """*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:#18283e;font:15px/1.7 system-ui,sans-serif}main{max-width:1180px;margin:auto;padding:32px 24px}h1{font-size:28px}h2{font-size:21px}h3{margin-top:28px}section,article{background:white;border:1px solid #dce3ed;border-radius:12px;padding:24px}section{margin:24px 0}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}.cards h3{margin:0}.muted{color:#526277}.badge,.notice{background:#fff0cf;color:#754800;padding:8px 14px;border-radius:8px}.badge{display:inline-block;font-weight:700}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{padding:12px 10px;border-bottom:1px solid #e2e8f0;text-align:right;white-space:nowrap}th{background:#edf2f8}th:first-child,td:first-child{text-align:left}details{border-top:1px solid #e2e8f0;padding:16px 0}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}a{color:#175cb6}nav a{margin-right:16px}@media(max-width:640px){main{padding:16px 10px}section,article{padding:16px}h1{font-size:24px}}"""
    state = "暫定・診断専用" if provisional else "品質ゲート通過（未使用性は別途確認）"
    body = f'<span class="badge">{state}</span><h1>{escape(symbol)}｜ORBの売り方を比較</h1><p>買い条件は30分ORB・1株。売り方による損益・勝率・下落リスクの違いを確認します。</p><p class="notice">評価期間（OOS）という名称だけでは未使用性を意味しません。2026年1月〜11月18日は成績確認済みです。公表料金の固定試算で、過去の実請求額を再現しません。</p><nav><a href="#summary">比較結果</a><a href="#curves">損益と下落</a><a href="#details">成績の内訳</a><a href="#evidence">根拠・ダウンロード</a></nav>'
    body += (
        '<section><h2>比較している売り方</h2><div class="cards">'
        + "".join(cards)
        + '</div><p class="muted">共通条件：初期資金5,000 USD・1日最大1取引・Webull日本米国株料金・片道spread 1 bps / slippage 2 bps。</p></section>'
    )
    body += (
        '<section id="summary"><h2>主要な比較結果</h2>'
        + "".join(sections)
        + '<p class="muted">勝率は費用控除後PnLが正の取引の割合。PFはネット総利益÷ネット総損失。損失なしのPFは「—」。固定率が一意に指定されない場合、差は「—」。最大下落額は確定足終値資産の過去最高値からの下落です。過去の成績は将来を保証しません。</p></section>'
    )
    body += (
        '<section id="curves"><h2>損益と下落の推移</h2>'
        + "".join(graphs)
        + '</section><section id="details"><h2>成績の違いの内訳</h2><p class="muted">平均利益・平均損失はネットPnL。該当取引なしは0、損失なしの利益/損失比は「—」。金額は表示上丸めています。精密値はCSV/JSONを参照してください。</p>'
        + "".join(breakdowns)
        + "</section>"
    )
    body += (
        '<section id="evidence"><h2>詳細・再現性情報</h2><p><a href="comparison.csv">比較CSV</a> · <a href="comparison.json">比較JSON</a> · <a href="comparison_config.yaml">比較設定YAML</a></p>'
        + details("警告一覧（共通内容は重複を除去）", common_warnings)
        + details(
            "期間・候補ごとの除外日",
            [
                dict(
                    period=r["period"],
                    name=r["name"],
                    exclusions=r.get("exclusions", []),
                )
                for r in rows
            ],
        )
        + details("有効設定・品質・実装情報", runs)
        + details("入力ファイルとハッシュ", manifest.get("artifacts", {}))
        + details("料金の出典・仮定・未確認事項", manifest.get("fee_model", {}))
        + "</section>"
    )
    (directory / "comparison.html").write_text(
        f'<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ORB Exit比較</title><style>{css}</style></head><body><main>{body}</main></body></html>',
        encoding="utf-8",
    )
