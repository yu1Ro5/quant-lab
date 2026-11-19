# quant-lab

moomoo OpenAPIの初回検証記録と再開手順は
[moomoo OpenAPI 動作確認](docs/moomoo-openapi-verification.md)を参照してください。
初回は環境制約で中断しましたが、その後MacでOpenD接続とデータ取得に成功しています。

GMOコイン外国為替FX Public APIを使ったUSD/JPY監視と、Parquetを使った単一銘柄バックテストMVPを収録しています。

USD/JPY監視では、日次・時間別の履歴保存、変動比較、Slack通知を行います。Public APIのため、GMOコインのAPIキーやSecretは不要です。

レートは `GET https://forex-api.coin.z.com/public/v1/ticker`、市場ステータスは `GET https://forex-api.coin.z.com/public/v1/status` から取得します。価格計算には `Decimal` を使い、APIの基準時刻はUTC、日本時間の日付を日次履歴の `rate_date` として扱います。

## moomoo AAPL日足

OpenDに接続してAAPL日足をCSV・Parquetに保存し、保存データからローソク足・出来高・
終値推移のHTML/PNGを生成するCLIを追加しました。
[実行方法と検証状況](docs/moomoo-daily.md)を参照してください。
Macで日足221件の取得とHTML/PNG生成を確認済みです。
遡及検証では2010年1月4日まで取得できました（全履歴の最古は未確定）。

## moomoo 米国株5分足

AAPLの2026年初から確定済み営業日までの5分足を取得・検証し、CSV/Parquet、
日足比較表、ローソク足・出来高のHTMLを保存できます。
MacのOpenDで17,316本を取得し、構造上の欠落・重複なしを確認しました。
出来高の日足との差と足の正式な時刻定義は要確認です。
HTMLは最大5営業日の詳細を切り替える軽量表示と、全期間の日足集計概要に分けています。
[実行方法・品質検証・遡及結果・利用条件](docs/moomoo-intraday.md)を参照してください。

## 必要環境とセットアップ

- Python 3.12以上
- [uv](https://docs.astral.sh/uv/)
- 実行時依存: `requests`、`slack-sdk`、`yfinance`、`numpy`、`pandas`、`pyarrow`、`moomoo-api`、`plotly`、`kaleido`

lock済みの依存関係を同期します。

```bash
uv sync --locked
```

依存宣言は`pyproject.toml`に集約し、解決済みバージョンは`uv.lock`で固定します。
moomoo用の別requirementsファイルや`--with`指定は不要です。
グラフの期間切替を含む開発テストにはNode.jsが必要です（CIはNode.js 24）。

GitHub Actionsの`CI`はmainへのpush・PR・手動実行でruff、mypy、全単体テストを実行します。
株式取得・米国株・バックテストも検証する汎用CIで、テストが1件でもスキップされた場合は失敗します。
`USDJPY Hourly Monitor`は別workflowの手動実行専用監視処理で、CIではありません。

## moomooによる米国株データ取得の利用方針

米国株データの取得・開発支援には、用途に応じて次を使い分けます。これは運用方針であり、既存のデータ取得処理やバックテストをMCPへ置き換えるものではありません。

| 用途 | 使用するもの | 役割 |
| --- | --- | --- |
| 大量の履歴データ取得 | Python SDK + OpenD | 取得枠・頻度制限・ページングを管理し、再現可能な入力データを用意する |
| API調査・コード生成 | 公式 `moomooapi` Skill | API仕様・引数・サンプルを参照し、SDKを使うコードの開発を支援する |
| 少量の相場情報の直接照会 | 公式MCP | AIエージェントからローソク足などを読み取り専用で照会する |
| 5分足バックテスト・ORB開発 | SDKで取得したOHLCV + バックテストコード | 時刻・取引セッション・価格調整条件を揃えたデータで検証する。Skillsは実装支援に使う |

### MacとCodex Cloudの利用範囲

Macでは、ログイン済みの既存OpenD（`127.0.0.1:11111`）とPython SDKを使用します。SkillsはAPI資料と実行スクリプトを提供し、相場取得スクリプトはSDK経由でOpenDへ接続します。一方、MCPはOAuthで認証するリモートAPIであり、MacのOpenDを経由しません。

Macのユーザー設定はリポジトリに含めません。

- 公式Skills：`~/.agents/skills/moomooapi/`、`~/.agents/skills/install-moomoo-opend/`
- MCP設定：`~/.codex/config.toml` の `[mcp_servers.moomoo]`
- MCPエンドポイント：`https://mcp.moomoo.com/mcp`

Codex CloudにはMacのSkills・MCP設定・認証状態は引き継がれません。Cloudの `127.0.0.1` はCloud自身を指すため、MacのOpenDには接続できません。Cloudでは人工データやモックを用いて、取得後の処理とバックテストを開発・検証します。MCPをCloudから使用する構成は、別途設定・認証・接続確認が必要であり、現時点では未検証です。

### 読み取り専用での利用

MCPのOAuthスコープは `quote:read` のみに限定し、`quote:write`、`trade:read`、`trade:write` は許可しません。Codex側でも、確認済みの読み取り専用相場ツールだけを `enabled_tools` で許可します。ツール一覧に注文用ツールが表示されることと、その操作権限があることは別です。

公式Skillsには注文用コードとOpenDのインストール手順も含まれます。MCPのスコープ制限はSDKには適用されないため、Skillsでは相場取得だけを指定し、本番・模擬のいずれも注文の送信・訂正・取消や取引のアンロックを行いません。既存OpenD・SDKの再インストールや初期化も行いません。

APIキー、OAuthトークン、パスワード、実取得データはGitへコミットしません。取得データを比較するときは、銘柄・日付・時間帯・価格調整条件・出来高の単位を揃え、取得できなかった項目は未検証として扱います。

Codexでの依頼例：

```text
$moomooapi 既存OpenD 127.0.0.1:11111を使って、
US.AAPLの2026-11-18の5分足OHLCVを、通常取引時間・無調整で3本取得してください。
再インストールや注文操作は行わないでください。
```

```text
moomoo MCPのquote_history_klineで、US.AAPLの2026-11-18の日足を
無調整で1本取得してください。相場参照ツールだけを使用してください。
```

2026-11-19のMacローカル検証では、Codexによる公式Skillsの認識、既存OpenD経由のAAPLの5分足取得、日本のmoomoo IDによる `quote:read` のみのOAuth認証、MCP経由の日足取得を確認しました。同じ日付・無調整の日足OHLCVは両経路で一致しましたが、売買代金 `turnover` は異なり、差の原因は未検証です。

公式資料：

- [moomoo OpenD Skills](https://openapi.moomoo.com/moomoo-api-doc/jp/intro/ai.html)
- [moomoo MCP概要](https://open.moomoo.com/mcp-docs/overview)
- [moomoo MCP認証・スコープ](https://open.moomoo.com/mcp-docs/authentication)
- [Codex Skills](https://developers.openai.com/codex/skills/)
- [Codex MCP設定](https://developers.openai.com/codex/mcp/)

## 日本株5分足OHLCV

4桁の東証銘柄コードと期間を指定し、yfinanceから5分足OHLCVを取得してParquetへ保存します。`--to`で指定した日も取得対象に含まれます。

```bash
uv run python -m quant_lab.stock.fetch \
  --symbol 9434 \
  --from 2026-07-28 \
  --to 2026-07-29
```

| 引数 | 内容 |
| --- | --- |
| `--symbol` | 4桁の東証銘柄コード。内部で`.T`を付けて取得 |
| `--from` | 取得開始日（`YYYY-MM-DD`） |
| `--to` | 取得終了日（`YYYY-MM-DD`、当日を含む） |

yfinanceの5分足は直近60日以内に制限されます。出力先は`data/stock/{symbol}_{from}_{to}_5m.parquet`で、`datetime`、`open`、`high`、`low`、`close`、`volume`の6列を日本時間の昇順で保存します。

保存した件数と先頭行は次のように確認できます。

```bash
uv run python -c "import pandas as pd; p='data/stock/9434_2026-07-28_2026-07-29_5m.parquet'; df=pd.read_parquet(p); print(f'{len(df):,}件'); print(df.head())"
```

yfinanceはYahoo Financeの公式ライブラリではありません。本機能は個人の研究・バックテスト用途を想定しています。データの利用条件と正確性は利用者自身で確認してください。

## prepare / deliver CLI

定期実行では、取得・永続化とSlack送信を分けてください。

```bash
export QUANT_LAB_DATA_DIR="/absolute/path/to/quant-lab-data/data"

uv run python main.py prepare \
  --envelope /tmp/usdjpy-delivery.json

# この間に日次CSV、時間別CSV、状態JSONをcommit/pushする

uv run python main.py deliver \
  --envelope /tmp/usdjpy-delivery.json

# deliverのJSON出力で state_commit_required=true の場合だけ
# alert_state.jsonを再度commit/pushする
```

`prepare` は次を順に行います。

1. 両方の閾値を検証する
2. APIからtickerとmarket statusを取得する
3. 1時間前比と日次比を計算する
4. 日次CSV、時間別CSV、アラート状態を更新する
5. 指定した一時パスへdelivery envelopeを原子的に出力する

`prepare` はSlack APIを呼びません。したがって、最初のデータcommit/pushが成功した後だけ `deliver` を呼ぶ構成にできます。`deliver` はenvelope内の完成済みメッセージを読み、Slack APIを最大1回呼びます。

引数なしの `uv run python main.py` は後方互換モードです。一時envelopeを使い、同一プロセスで `prepare`、`deliver` の順に実行します。Slack認証情報がなければ従来どおり通知だけをスキップして終了コード0になります。データを別リポジトリへcommitする運用では、必ず明示的な `prepare` / `deliver` を使用してください。

### CLI終了コードと構造化出力

各サブコマンドは、最終行に1行のJSONを標準出力へ出します。

| 終了コード | コマンド | 意味 |
| ---: | --- | --- |
| `0` | prepare | envelope生成と可能なデータ・状態更新に成功 |
| `2` | prepare | 設定、API、日次データなどのエラー。Slackは未送信 |
| `0` | deliver | Slack送信成功。JSONの `state_commit_required` を確認 |
| `3` | deliver | Slack送信失敗、または認証情報不足 |
| `4` | deliver | envelopeが読めない、またはschema不正 |
| `5` | deliver | 古いenvelope・並行deliveryを送信前に拒否、またはSlack送信後の強いアラート状態更新に失敗 |

prepare成功例:

```json
{"daily_saved": true, "delivery_kind": "strong_alert", "envelope_path": "/tmp/usdjpy-delivery.json", "hourly_saved": true, "state_healthy": true, "state_updated": true, "status": "prepared"}
```

deliver成功例:

```json
{"alert_state_path": "/data/alert_state.json", "delivery_kind": "strong_alert", "state_commit_required": true, "status": "sent"}
```

通常通知の成功では `state_commit_required` は `false` です。終了コード5の場合はJSONの `status` を確認してください。`delivery_rejected` は古いenvelopeまたは並行deliveryをSlack送信前に拒否した状態、`sent_state_update_failed` はSlack送信済みですがpendingを消せず、次回に重複送信される可能性がある状態です。

## 環境変数

| 変数 | 必須 | 既定値・意味 |
| --- | --- | --- |
| `QUANT_LAB_DATA_DIR` | 任意 | `data`。3つのデータファイルの基準ディレクトリ |
| `USD_JPY_DAILY_HISTORY_PATH` | 任意 | `${QUANT_LAB_DATA_DIR}/usd_jpy.csv` を個別上書き |
| `USD_JPY_HOURLY_HISTORY_PATH` | 任意 | `${QUANT_LAB_DATA_DIR}/usd_jpy_hourly.csv` を個別上書き |
| `USD_JPY_ALERT_STATE_PATH` | 任意 | `${QUANT_LAB_DATA_DIR}/alert_state.json` を個別上書き |
| `USD_JPY_HOURLY_ALERT_THRESHOLD_PERCENT` | 任意 | `0.3` |
| `USD_JPY_DAILY_ALERT_THRESHOLD_PERCENT` | 任意 | `1.0` |
| `SLACK_BOT_TOKEN` | deliver時に必須 | Slack Bot token |
| `SLACK_CHANNEL` | deliver時に必須 | 通知先channel |

個別パスは `QUANT_LAB_DATA_DIR` より優先され、すべて実行時に解決されます。日次閾値では、既存環境との互換性のため、新しい日次変数が未設定の場合に限って旧 `USD_JPY_ALERT_THRESHOLD_PERCENT` も読みます。

閾値は単位がパーセントの正の有限な数です。未設定、空文字、空白だけなら既定値を使用します。不正数値、`NaN`、`Infinity`、0、負数はAPI取得・CSV更新・Slack送信より前に拒否します。判定は丸め前の `Decimal` に対する `abs(percent) >= threshold` です。

## 比較と通知

通知には既存のmid、bid、ask、spread、API基準日時、market statusを残し、次を追加します。

- 1時間前比: 現在の `source_timestamp` より45分以上90分以下前の候補から、60分前に最も近い値。同距離なら新しい `source_timestamp`
- 日次比: 現在の `rate_date` より前にある最新の有効な異なる `rate_date`
- 比較基準: 1時間比較ではUTCの `source_timestamp`、日次比較では `rate_date`
- 候補なし: 比較種別ごとに `比較データなし`

CSVの行順や暦上の単純な前日には依存しません。不正日付、非有限値、0以下のレート、未来の時間別データなどは比較候補から除外します。変動額・率・円安/円高の方向と閾値判定には丸め前の値を使い、表示時だけ小数点以下2桁へ丸めます。

両比較が同時に強いアラート対象なら、Slackメッセージは1件へ統合されます。market statusが `CLOSE` の場合もCSV保存と通常通知は行いますが、新しい強いアラートは作りません。

## データファイル

### `usd_jpy.csv`

既存の日次履歴です。同じ `rate_date` は重複保存しません。

```csv
fetched_at,rate_date,rate,bid,ask,spread,source_timestamp,market_status
```

旧形式 `fetched_at,rate_date,rate` も読み込み、次回保存時に既存値を維持して現行列へ移行します。

### `usd_jpy_hourly.csv`

UTCの1時間bucketごとに最初の成功値だけを保存します。bucketは `fetched_at` ではなく `source_timestamp` を1時間単位へ切り下げて決定し、直近90日を保持します。現在値の90日前と同時刻の行は残します。

```csv
fetched_at,source_timestamp,bucket_start_utc,rate,bid,ask,spread,market_status
```

更新は同じディレクトリの一時ファイルからの置換です。既知の列と一致しないヘッダー、列欠損、bucket矛盾、重複bucketを検出した場合は自動初期化しません。その実行では時間比較・時間CSV更新を止め、可能な日次保存・状態処理・通常通知を継続します。

### `alert_state.json`

version 1のschemaです。

```json
{
  "version": 1,
  "comparisons": {
    "hourly": {
      "is_active": false,
      "last_notified_at": null
    },
    "daily": {
      "is_active": false,
      "last_notified_at": null
    }
  },
  "pending_alert": null
}
```

未送信の強いアラートがある場合、`pending_alert` は次の形です。

```json
{
  "event_id": "決定的な24文字の公開ID",
  "occurred_at": "2026-07-26T02:17:00+00:00",
  "triggered_comparisons": ["hourly"],
  "message": "Slackへ送る完成済みメッセージ",
  "delivery_claim": {
    "claim_id": "送信処理ごとの一意なID",
    "claimed_at": "2026-07-26T02:18:00+00:00"
  }
}
```

`delivery_claim` はdeliverがSlack送信前に排他的に記録する任意フィールドです。有効なclaimがある間は、同じenvelopeの別deliverを送信前に拒否し、並行prepareもpendingを置き換えません。送信失敗時はclaimを解除し、処理が異常終了しても15分後に再取得できます。

比較状態は1時間・日次で独立し、クールダウンは3時間です。閾値未満に戻った後の再超過は3時間以内でも新規イベントになります。`CLOSE` と比較不能では既存の `is_active` を解除しません。`last_notified_at` は強いSlackアラートの送信成功後だけ更新します。

強いアラートの送信失敗では最新1件の `pending_alert` が残り、次回prepareのenvelopeで通常通知より優先されます。新しい強いアラートが発生すれば古いpendingを最新の内容で置き換えます。通常通知は再送状態を作りません。

状態JSONが壊れている、versionが未知、必須型が不正な場合はファイルを上書きせず、強いアラートを抑制します。可能なCSV保存と通常通知は継続し、標準エラーへ修復が必要な旨を出します。

## delivery envelope

envelopeはrunnerの一時領域へ置き、Gitへcommitしません。schemaはversion 1です。

```json
{
  "version": 1,
  "delivery_kind": "strong_alert",
  "message": "Slackへ送る完成済みメッセージ",
  "event_id": "決定的な24文字の公開ID",
  "triggered_comparisons": ["hourly", "daily"],
  "alert_state_path": "/absolute/path/to/data/alert_state.json",
  "prepared_at": "2026-07-26T02:17:00+00:00"
}
```

`delivery_kind` は `normal` または `strong_alert` です。通常通知では `event_id` が `null`、`triggered_comparisons` が空配列になります。token、channel ID、ユーザー情報は含みません。

## quant-lab-data workflowからの利用

prepare成功後の最初のcommitでは、次だけを明示的にstageします。

```text
data/usd_jpy.csv
data/usd_jpy_hourly.csv
data/alert_state.json
```

deliver成功後、JSONの `state_commit_required` が `true` の場合だけ `data/alert_state.json` を再度commitします。envelopeはcommitしません。強いアラート失敗時はpendingを残すため、最初のcommit済み状態を変更しません。通常通知失敗時も追加commitはありません。

旧 `quant-lab` workflowはscheduleを停止し、`workflow_dispatch` による手動実行専用になりました。定期実行は `quant-lab-data` へ移行中です。`quant-lab-data` 側のscheduleは移行確認後に別作業で有効化するため、現時点ではまだ有効化していません。

最小構成例です。これは後続の `quant-lab-data` 側で作るworkflowの参考です。

```yaml
jobs:
  monitor:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    env:
      QUANT_LAB_DATA_DIR: ${{ github.workspace }}/data-repo/data
      SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
      SLACK_CHANNEL: ${{ secrets.SLACK_CHANNEL }}
      USD_JPY_HOURLY_ALERT_THRESHOLD_PERCENT: ${{ vars.USD_JPY_HOURLY_ALERT_THRESHOLD_PERCENT }}
      USD_JPY_DAILY_ALERT_THRESHOLD_PERCENT: ${{ vars.USD_JPY_DAILY_ALERT_THRESHOLD_PERCENT }}
    steps:
      - uses: actions/checkout@<verified-sha>
        with:
          path: data-repo
      - uses: actions/checkout@<verified-sha>
        with:
          repository: yu1Ro5/quant-lab
          ref: <verified-40-character-quant-lab-commit-sha>
          path: app
      - uses: astral-sh/setup-uv@<verified-sha>
      - run: uv sync --locked --project "$GITHUB_WORKSPACE/app"
      - id: prepare
        run: |
          uv run --project "$GITHUB_WORKSPACE/app" \
            python "$GITHUB_WORKSPACE/app/main.py" prepare \
            --envelope "$RUNNER_TEMP/usdjpy-delivery.json"
      - name: Commit prepared data and state
        working-directory: data-repo
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/usd_jpy.csv data/usd_jpy_hourly.csv data/alert_state.json
          git diff --cached --quiet || {
            git commit -m "Update USD/JPY monitoring data"
            git push
          }
      - id: deliver
        run: |
          uv run --project "$GITHUB_WORKSPACE/app" \
            python "$GITHUB_WORKSPACE/app/main.py" deliver \
            --envelope "$RUNNER_TEMP/usdjpy-delivery.json" \
            | tee "$RUNNER_TEMP/deliver-result.json"
      - name: Commit delivered alert state when required
        working-directory: data-repo
        run: |
          if jq -e '.state_commit_required == true' \
            "$RUNNER_TEMP/deliver-result.json" >/dev/null; then
            git add data/alert_state.json
            git diff --cached --quiet || {
              git commit -m "Mark USD/JPY alert delivered"
              git push
            }
          fi
```

実際のworkflowでは、checkout/setup Actionを検証済みcommit SHAへ固定し、`concurrency` を設定してください。prepareが失敗した場合や最初のpushが失敗した場合はdeliver stepへ進めない構成にします。

## 日本株バックテストMVP

単一銘柄のOHLCV Parquetを読み込み、サンプル戦略、外部へ注文を送らないダミーBroker、結果集計までを順番に実行します。目的は戦略の利益ではなく、バックテストの一連の流れを再現できることです。

日本株関連の製品コードは `src/quant_lab/`、バックテストのテストは `tests/backtest/`、株価取得のテストは `tests/stock/` に置いています。既存のUSD/JPY監視コードとテストは、従来の実行方法を維持するためリポジトリ直下に残しています。

### 入力データ

Parquetには次のカラムが必要です。余分なカラムがあっても実行できます。

| カラム | 意味 |
| --- | --- |
| `datetime` | ローソク足の日時 |
| `open` | 始値 |
| `high` | 高値 |
| `low` | 安値 |
| `close` | 終値 |
| `volume` | 出来高 |

日時順でないデータは読込時に並べ替えます。欠損、重複日時、0以下の価格、負の出来高、OHLCの矛盾は実行前にエラーになります。

### サンプルを実行する

`examples/data/japanese_stock_sample.parquet` はコミット済みなので、依存関係の同期後すぐに実行できます。これは実在銘柄の履歴ではなく、売買フローを確認するための架空データです。

```bash
uv sync --locked

uv run python -m quant_lab.backtest \
  examples/data/japanese_stock_sample.parquet
```

想定出力は次のとおりです。

```text
Parquet読込成功: 7件
総取引回数: 2
勝率: 50.00%
総損益: -25.00円
```

サンプル戦略は、株を持っていないときに「当日の終値が前日の高値を上回る」と翌日に1株買い、株を持っているときに「当日の終値が前日の安値を下回る」と翌日に売ります。

たとえば2026年7月2日の終値1,030円が、7月1日の高値1,020円を上回った場合、7月2日の取引終了後に買うと決め、7月3日の始値1,040円で買ったことにします。終値を確認した後で同じ終値に戻って買う計算にしないためです。

データ最終日の2026年7月9日まで株を持っている場合は、集計を完結させるため、その日の終値1,055円で売ったことにします。この最終売却はバックテスト上の便宜であり、将来の実売買にそのまま適用する仕様ではありません。

### Parquetを再生成する

生成プログラムと、生成済みのサンプル用・テスト用ParquetをすべてGitで管理します。次のコマンドは両方を再生成します。

```bash
uv run python scripts/generate_backtest_parquet.py
```

生成先は次の2ファイルです。

```text
examples/data/japanese_stock_sample.parquet
tests/fixtures/backtest_sample.parquet
```

任意の場所へ生成する場合は `--sample-output` と `--fixture-output` を指定します。

ダミーBrokerは内部状態を更新するだけで、証券会社へ注文を送りません。複数銘柄、複数時間足、空売り、手数料、税金、グラフ、最適化、外部発注APIはこのMVPの対象外です。

## テストと検証

外部APIとSlackはmockし、標準ライブラリの `unittest` を使います。

```bash
uv run python -m unittest discover -v
uv run python -m compileall -q .
uv run ruff check .
uv run mypy .
git diff --check
```

## 日足KLine

`daily_kline.py` はGMOコイン外国為替FX Public APIの `GET /public/v1/klines` から、指定した年範囲のUSD/JPY日足を取得します。BIDとASKを個別に取得し、同じ `openTime` のデータだけを結合します。

```bash
uv run python daily_kline.py --from-year 2023 --to-year 2026
```

保存先の既定値は `data/usd_jpy_1day.csv` で、`--output` により変更できます。

```bash
uv run python daily_kline.py \
  --from-year 2025 \
  --to-year 2026 \
  --output data/usd_jpy_1day_recent.csv
```

CSVは次の列で、UTCの `open_time` 昇順に保存します。同じ `open_time` の既存行は内容が変わった場合だけ更新し、BID/ASKの時刻不一致はエラーにします。

```csv
open_time,bid_open,bid_high,bid_low,bid_close,ask_open,ask_high,ask_low,ask_close
```
