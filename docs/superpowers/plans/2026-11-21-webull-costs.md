# Webull cost comparison implementation plan

Goal: Issue #38とユーザー指定のSTEP 1〜9を実装する。
Architecture: 既存Brokerに固定料金モデルを追加し、単発runnerを各シナリオで実行する。品質ゲートは変更しない。
Tech stack: Python / Decimal / YAML / pandas / Plotly。
Spec: GitHub Issue #38および依頼文。米国株現物買い、USD決済、注文接続なし。

- [x] broker_fees.py: 2026-11-21のWebull公表料金スナップショット。Decimalによる料金・境界・丸めテスト。
- [x] config/execution/models/metrics: 固定手数料互換、買付余力、売却費用、費用内訳、現金整合テスト。
- [x] cost_sensitivity/cost_report/CLI: ベース固定、摩擦のみ変更、毎回Engine実行、入力・品質根拠ハッシュ整合、CSV/JSON/HTML、符号反転区間テスト。
- [x] README: 料金適用日・公式情報・仮定・診断ゲート・実行例・出力を説明。
- [x] 全unittest / ruff / mypyを実行し独立ブランチでPR作成。

Review focus: 買付時CATによる余力不足、取引ゼロの偽の分岐点、料金ページ間の矛盾、比較途中の入力変更、非数値・重複シナリオを検証する。
