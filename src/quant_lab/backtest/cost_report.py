"""シナリオ横断のCSV・JSON・自己完結HTML。"""

import html
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
    write_cost_html(directory, rows, manifest)


def write_cost_html(
    directory: Path, rows: list[dict[str, Any]], manifest: dict[str, Any]
) -> None:
    """既存結果からHTMLのみ再生成する。計算・CSV・JSONは変更しない。"""

    def escape(value: object) -> str:
        return html.escape(str(value))

    def money(value: Any, *, precise: bool = False) -> str:
        if precise and value and abs(value) < 0.01:
            return f"${value:,.6f}"
        return f"${value:,.2f}"

    def table(headers: list[str], values: list[list[str]]) -> str:
        return (
            '<div class="table-wrap"><table><thead><tr>'
            + "".join(f'<th scope="col">{escape(h)}</th>' for h in headers)
            + "</tr></thead><tbody>"
            + "".join(
                "<tr>" + "".join(f"<td>{escape(v)}</td>" for v in row) + "</tr>"
                for row in values
            )
            + "</tbody></table></div>"
        )

    def details(title: str, value: Any) -> str:
        return f"<details><summary>{escape(title)}</summary><pre>{escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre></details>"

    provisional = any(
        r["evaluation_status"] != "formal" or not r["backtest_ready"] for r in rows
    )
    state = "暫定・診断専用" if provisional else "正式評価"
    summary = table(
        [
            "シナリオ",
            "ネット損益",
            "取引数",
            "勝率",
            "平均損益 / 取引",
            "PF",
            "最大下落額",
            "最終資金",
        ],
        [
            [
                str(r["name"]),
                money(r["net_pnl"]),
                str(r["total_trades"]),
                f"{r['win_rate']:.1%}",
                money(r["average_net_pnl"]),
                f"{r['profit_factor']:.2f}" if r["profit_factor"] is not None else "—",
                money(r["max_drawdown_usd"]),
                money(r["final_cash_usd"]),
            ]
            for r in rows
        ],
    )
    costs = table(
        [
            "シナリオ",
            "グロス損益",
            "委託手数料",
            "SEC",
            "FINRA",
            "CAT",
            "スプレッド",
            "スリッページ",
            "総コスト",
        ],
        [
            [str(r["name"])]
            + [
                money(r[k], precise=True)
                for k in [
                    "gross_pnl",
                    "commission",
                    "sec_fee",
                    "finra_fee",
                    "cat_fee",
                    "spread",
                    "slippage",
                    "total_cost",
                ]
            ]
            for r in rows
        ],
    )
    conditions = table(
        ["シナリオ", "片道スプレッド", "片道スリッページ"],
        [
            [
                str(r["name"]),
                f"{r['spread_bps_per_side']:g} bps",
                f"{r['slippage_bps_per_side']:g} bps",
            ]
            for r in rows
        ],
    )
    figure = go.Figure(
        go.Bar(
            x=[r["name"] for r in rows],
            y=[r["net_pnl"] for r in rows],
            marker_color=["#087f5b" if r["net_pnl"] >= 0 else "#c2413b" for r in rows],
            text=[money(r["net_pnl"]) for r in rows],
            textposition="outside",
            hovertemplate="%{x}<br>ネット損益 $%{y:,.2f}<extra></extra>",
        )
    )
    figure.update_layout(
        template="plotly_white",
        height=370,
        margin=dict(l=65, r=35, t=40, b=65),
        yaxis_title="ネット損益（USD）",
        xaxis_title=None,
        font=dict(size=14),
        yaxis=dict(zeroline=True, zerolinecolor="#64748b"),
    )
    graph = figure.to_html(
        full_html=False,
        include_plotlyjs=True,
        config={"responsive": True, "displaylogo": False},
    )
    reasons = list(
        dict.fromkeys(str(v) for r in rows for v in r.get("quality_reasons", []))
    )
    warnings = (
        "<ul>" + "".join(f"<li>{escape(v)}</li>" for v in reasons) + "</ul>"
        if reasons
        else "<p>品質上の未解決理由は記録されていません。</p>"
    )
    trade_note = (
        "条件により取引数が異なります。購入余力や約定価格の変化を反映した結果です。"
        if len({r["total_trades"] for r in rows}) > 1
        else "今回の比較では全条件の取引数が同じです。"
    )
    be = manifest["break_even"]
    intervals = "".join(
        f"<li>{escape(p['axis'])}: {escape(p['lower'])}〜{escape(p['upper'])} bps"
        f"（{escape(p['fixed_axis'])} = {escape(p['fixed_value'])} bps固定）</li>"
        for p in be.get("sign_change_intervals", [])
    )
    exact = ", ".join(str(v) for v in be.get("exact_scenarios", []))
    links = []
    for r in rows:
        run = Path(r["run_directory"])
        # 相対リンクで同じ結果フォルダ内の既存レポートへ移動する。
        relative = Path(os.path.relpath(run, directory))
        links.append(
            f"<li>{escape(r['name'])}：<a href='{escape(quote((relative / 'equity_curve.html').as_posix()))}'>資産推移</a> · <a href='{escape(quote((relative / 'trades.csv').as_posix()))}'>取引履歴CSV</a></li>"
        )
    quality = [
        dict(
            name=r["name"],
            backtest_ready=r["backtest_ready"],
            evaluation_status=r["evaluation_status"],
            warnings=r["warnings"],
            quality_reasons=r["quality_reasons"],
        )
        for r in rows
    ]
    content = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>取引コスト比較</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:#172b42;font:15px/1.7 system-ui,sans-serif}
main{max-width:1200px;margin:auto;padding:32px 24px}h1{font-size:28px;margin:8px 0}h2{font-size:20px;margin:0 0 16px}
p{margin:8px 0 16px}.muted{color:#52657b}.badge{display:inline-block;padding:4px 12px;background:#fff0cf;color:#754800;border-radius:8px;font-weight:700}
section{background:white;border:1px solid #dce4ed;border-radius:12px;padding:24px;margin:20px 0}.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}th,td{padding:12px 10px;border-bottom:1px solid #e2e8f0;text-align:right;white-space:nowrap}
th{background:#eef3f8;font-weight:600}th:first-child,td:first-child{text-align:left}tbody tr:hover{background:#f8fafc}
a{color:#075eb4}details{border-top:1px solid #e2e8f0;padding:14px 0}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.7 ui-monospace,monospace;background:#f7f9fc;padding:16px}
@media(max-width:640px){main{padding:16px 12px}section{padding:16px}h1{font-size:24px}}
</style></head><body><main>"""
    content += (
        f'<span class="badge">{state}</span><h1>取引コストで損益はどう変わるか</h1>'
    )
    content += '<p class="muted">同じ戦略・データを各費用条件で再実行した比較です。金額はUSDです。</p>'
    if provisional:
        content += "<p><strong>入力データの品質確認が未完了、または診断モードで実行した暫定結果です。正式な戦略評価には使用しないでください。</strong></p>"
    content += f"<section><h2>ネット損益の比較</h2>{graph}</section>"
    content += f'<section><h2>主要指標</h2>{summary}<p class="muted">{trade_note} 勝率は費用控除後。PFは総利益÷総損失、損失ゼロ・取引なしでは「—」。最大下落額は資産の過去最高値からの下落です。</p></section>'
    content += f'<section><h2>費用の内訳</h2>{costs}<p class="muted">ネット損益 = グロス損益 − 総コスト。表示は丸めていますが、計算値はCSV・JSONに保持しています。0.01ドル未満の費用は小数6桁で表示します。</p></section>'
    content += f"<section><h2>比較条件</h2>{conditions}<p>スプレッドはOHLCV参照価格に対する片道の実効負担、スリッページは追加の不利な価格差です。実測値ではありません。1 bps = 0.01%。zero_frictionにも規制関連費用は残ります。</p></section>"
    content += f"<section><h2>損益分岐の確認</h2><p>{escape(be['status'])}</p>"
    if exact:
        content += f"<p>損益ゼロの実行済み条件：{escape(exact)}</p>"
    if intervals:
        content += f"<ul>{intervals}</ul>"
    content += '<p class="muted">片方の費用条件を固定した実行済み点で確認しています。符号変化区間は候補であり、区間内の正確な閾値や連続性は保証しません。取引ゼロは分岐点に含みません。</p></section>'
    content += f"<section><h2>品質と計算の前提</h2>{warnings}"
    content += "<p>2026-11-21確認のWebull公表料金を過去の全期間に適用する試算であり、当時の実料金を再現していません。FINRAの免除転嫁、FINRAの丸め、CATの端数処理には未確認事項・モデル上の仮定があります。</p>"
    content += details("品質状態・警告の詳細", quality)
    content += details("公式料金・適用日・丸め規則・出典", manifest["fee_model"])
    content += details(
        "入力ファイル・ハッシュ・設定スナップショット",
        dict(
            artifacts=manifest["artifacts"],
            base_config=manifest.get("base_config"),
            comparison_config=manifest.get("comparison_config"),
            assumptions=manifest["assumptions"],
        ),
    )
    content += (
        '</section><section><h2>詳細レポート・データ</h2><p><a href="comparison.csv">比較CSV</a> · <a href="comparison.json">比較JSON</a></p><ul>'
        + "".join(links)
        + "</ul></section></main></body></html>"
    )
    (directory / "comparison.html").write_text(content, encoding="utf-8")
