# quant-lab

[![CI](https://github.com/yu1Ro5/quant-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/yu1Ro5/quant-lab/actions/workflows/ci.yml)

米国株・日本株の市場データ取得と、品質検証・再現性を重視したバックテストのための個人リサーチ基盤。実発注は行わず、売買はすべて仮想取引として検証する。

## 全体像

| 領域 | 内容 | 詳細 |
| --- | --- | --- |
| データ取得 | moomooの米国株（日足・5分足）、yfinanceの日本株5分足 | [日足](docs/moomoo-daily.md) / [5分足](docs/moomoo-intraday.md) |
| 品質検証 | 欠損・重複・時刻・立会の検査と、正式評価を止める品質ゲート | [5分足の品質判定](docs/backtest-data-quality.md) |
| バックテスト | YAML駆動の米国株バックテスト、日本株バックテストMVP | [米国株](docs/backtest-us.md) / [日本株MVP](docs/backtest-japanese-mvp.md) |
| 比較実験 | Exit比較・コスト比較 | [米国株バックテスト](docs/backtest-us.md) |

## クイックスタート

依存関係を同期し、コミット済みのサンプルデータで日本株バックテストMVPを実行する。サンプルは売買フローを確認するための架空データです。

```bash
uv sync --locked

uv run python -m quant_lab.backtest \
  examples/data/japanese_stock_sample.parquet
```

想定出力:

```text
Parquet読込成功: 7件
総取引回数: 2
勝率: 50.00%
総損益: -25.00円
```

## 設計原則

- **品質ゲート**: 全必須検査が pass しなければ正式評価を禁止する。未確認のデータは `--diagnostic` を明示した診断でのみ扱う
- **先読み禁止**: 戦略は確定済みの足だけで判断し、約定は次足以降にする
- **再現性**: runごとに入力・コードのハッシュと確定設定を保存し、過去のrunを上書きしない
- **評価規律**: IS/OOSを分け、OOSは候補を確定してから開く
- **安全境界**: 実発注はしない。moomooは読み取り専用で使い、認証情報と実取得データはGitに入れない

## 主な使い方

### 日本株5分足を取得する

```bash
uv run python -m quant_lab.stock.fetch \
  --symbol 9434 \
  --from 2026-07-28 \
  --to 2026-07-29
```

東証銘柄コードと期間を指定し、yfinanceから5分足OHLCVを取得して `data/stock/` へParquetで保存する。5分足は直近60日以内に制限される。

### 米国株バックテストを実行する

```bash
# 品質検証のみ
uv run python -m quant_lab.backtest --config configs/experiments/aapl_orb_30m.yaml --quality-only
# 全必須品質検査passのデータだけ正式評価
uv run python -m quant_lab.backtest --config configs/experiments/aapl_orb_30m.yaml
```

設定・約定モデル・結果の仕様は [米国株バックテスト](docs/backtest-us.md) を参照してください。

## ドキュメント

- [米国株バックテスト](docs/backtest-us.md): YAML設定、戦略の追加、約定モデル、結果と再現性、Exit比較
- [日本株バックテストMVP](docs/backtest-japanese-mvp.md): 入力仕様とサンプルデータの再生成
- [5分足の品質判定](docs/backtest-data-quality.md): 品質検査と ready 判定の根拠
- [moomooの日足取得](docs/moomoo-daily.md) / [米国株5分足取得](docs/moomoo-intraday.md)
- [moomoo OpenAPI動作確認](docs/moomoo-openapi-verification.md): 動作確認の記録と利用方針
- [開発・検証ガイド](docs/development.md): CIとローカル検証、テスト構成、レビューとマージ判断
- [エージェントIssueライフサイクル](docs/agent-issue-lifecycle.md): Issue駆動の自律開発の状態遷移と排他仕様
- [Webull OpenAPIの設計整理](docs/webull-openapi-design.md)

## セットアップと検証

- Python 3.12以上と [uv](https://docs.astral.sh/uv/) が必要です。依存関係は `pyproject.toml` に集約し、`uv.lock` で固定します
- グラフの期間切替を含む開発テストには Node.js 24 が必要です

```bash
uv sync --locked
uv run python -m unittest discover -v
```

CI（GitHub Actions）は main への push・PR・手動実行で ruff、mypy、全単体テストを実行します。判定条件は [開発・検証ガイド](docs/development.md#ciとローカル検証) を参照してください。

## 注意

- 個人の研究・バックテスト用途を想定しています
- yfinanceはYahoo Financeの公式ライブラリではありません。データの利用条件と正確性は利用者自身で確認してください
- moomooの利用方針（MacとCodex Cloudの範囲、読み取り専用の徹底）は [moomoo OpenAPI動作確認](docs/moomoo-openapi-verification.md#利用方針) を参照してください
