# quant-lab

GMOコイン外国為替FX Public APIからUSD/JPYの最新レートを取得し、コンソール表示、CSVへの履歴保存、および任意のSlack通知を行うスクリプトです。Public APIを利用するため、GMOコインのAPIキーやSecretは不要です。

取得先は `GET https://forex-api.coin.z.com/public/v1/ticker` です。詳細は[GMOコイン外国為替FX APIドキュメント](https://api.coin.z.com/fxdocs/)を参照してください。

## レート項目

| 項目 | 意味 |
| --- | --- |
| `bid` | 現在の売値 |
| `ask` | 現在の買値 |
| `rate` | bidとaskの仲値（`(bid + ask) / 2`） |
| `spread` | 売値と買値の差（`ask - bid`） |

計算には浮動小数点誤差を避けるため `Decimal` を使用します。APIの基準時刻はUTCとして解析し、日本時間の日付を `rate_date` として保存します。

## 実行方法

依存関係を同期してスクリプトを実行します。

```bash
uv sync --locked
uv run python main.py
```

最新レートは `data/usd_jpy.csv` に追記されます（`data` ディレクトリがなければ自動作成されます）。同じ `rate_date` の行がすでに存在する場合は重複保存しません。

## CSV履歴

CSVは次の列で構成されます。

| 列 | 説明 |
| --- | --- |
| `fetched_at` | 処理を実行したUTC時刻 |
| `rate_date` | API基準時刻を日本時間に変換した日付 |
| `rate` | USD/JPYの仲値 |
| `bid` | USD/JPYの売値 |
| `ask` | USD/JPYの買値 |
| `spread` | `ask - bid` で計算した差 |
| `source_timestamp` | APIが返した基準時刻（UTC） |
| `market_status` | 市場ステータス（`OPEN` または `CLOSE`） |

旧形式（`fetched_at,rate_date,rate`）のCSVは、次回保存時に既存の3項目を維持したまま新形式へ安全に移行します。旧行の追加項目は空欄になります。

## Slack通知

Slack通知を利用する場合だけ、Bot tokenと通知先チャンネルを設定します。

```bash
export SLACK_BOT_TOKEN="xoxb-your-bot-token"
export SLACK_CHANNEL="#alerts"
uv run python main.py
```

どちらかが未設定の場合もレート取得とCSV保存は実行され、Slack通知だけをスキップします。コンソールと通知には仲値、bid、ask、spread、APIの基準時刻、市場ステータスが含まれます。

## テストとCI

外部APIやSlackには接続せず、モックを使うunittestを実行します。

```bash
uv run python -m unittest discover -v
```

GitHub Actionsは `main` へのpush、`main` 向けPull Request、手動実行時に同じテストを実行します。平日の日次レート取得ワークフロー（日本時間9:00）も継続し、更新されたCSVをコミットします。
