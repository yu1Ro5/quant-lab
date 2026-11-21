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
- 実行時依存: `requests`、`slack-sdk`、`yfinance`、`numpy`、`pandas`、`pyarrow`、`moomoo-api`、`plotly`、`kaleido`、`PyYAML`、`exchange-calendars`

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

## YAML駆動の米国株バックテスト

Entry・Exit・Sizingを独立に指定して、保存済み5分足Parquetから実験を再実行できます。
初期戦略は30分ORB、固定率の損切り/利確、固定1株です。外部API・OpenDへの接続はなく、
すべてメモリ内の仮想取引です。旧日本株MVPの位置引数CLIと公開クラスも維持しています。

```bash
uv sync --locked
# 品質検証のみ。現在のAAPLは未検証項目があるため終了コード2
uv run python -m quant_lab.backtest --config configs/experiments/aapl_orb_30m.yaml --quality-only
# 暫定値の診断再現（正式評価ではない）
uv run python -m quant_lab.backtest --config configs/experiments/aapl_orb_30m.yaml --diagnostic
# 全必須品質検査passのデータだけ正式評価
uv run python -m quant_lab.backtest --config configs/experiments/aapl_orb_30m.yaml

# 既存MVP
uv run python -m quant_lab.backtest examples/data/japanese_stock_sample.parquet
```

入力の既定パスは`data/stock/aapl_5m_2026.parquet`。
別worktreeや別ディレクトリに保存している場合は、YAMLの`data.path`をその絶対パスに変更してください。
**入力/出力の相対パスは実行時のカレントディレクトリ基準**です。
個人用設定や実データ入り結果はGitへ追加しないでください。例えば`results/local.yaml`へ
設定をコピーすれば、設定も既定のGit除外ディレクトリに置けます。

### 設定例と検証

完全なサンプルは[configs/experiments/aapl_orb_30m.yaml](configs/experiments/aapl_orb_30m.yaml)。

```yaml
experiment:
  name: aapl_orb_30m
data:
  symbol: US.AAPL
  path: data/stock/aapl_5m_2026.parquet
  timeframe: 5m
  timezone: America/New_York
  bar_timestamp: end
  adjustment: none
period:
  start: "2026-01-01"
  end: "2026-11-18"
entry:
  strategy: orb
  params:
    opening_range_minutes: 30
exit:
  strategy: fixed_stop
  params:
    stop_loss_pct: 0.5
    take_profit_pct: 1.0
    close_at_market_end: true
sizing:
  strategy: fixed_shares
  params:
    shares: 1
execution:
  commission_usd_per_side: 0.10
  slippage_bps_per_side: 2
  spread_bps_per_side: 0
portfolio:
  initial_capital_usd: 5000
  max_trades_per_day: 1
output:
  directory: results
```

必須セクション/キー、未知キー、YAML重複キー、未知strategy、未知パラメータ、型違い、
NaN/Infinity、負のコスト、期間逆転を説明付きエラー（終了コード1）にします。
日付は引用符付き`YYYY-MM-DD`文字列、timezoneはIANA名、足種は`5m`のみです。
株数と1日最大取引数は1〜1,000,000の整数（boolや小数は禁止）、初期資金は正の有限値。
ORB期間は5〜180分の5分単位、損切率は0より大きく100%未満、利確率は正の有限値。
片道bpsの合計は10,000未満、`close_at_market_end`は当日決済を守るため`true`のみです。
実験名は英数字・`_`・`-`の1〜100文字。`output`省略時は`results`。
各組込戦略の`params: {}`は実装の既定値を使い、結果設定には適用した既定値も保存します。

### 責務分離と戦略の追加

| 担当 | ファイル | 契約 |
| --- | --- | --- |
| Entry | `strategies/entry/base.py`, `strategies/entry/orb.py` | `on_close(history, session)`：確定済み履歴→買いシグナル |
| Exit | `strategies/exit/base.py`, `strategies/exit/fixed_stop.py` | `on_bar(bar, position)`：保護注文のOHLC評価→決済価格/理由/時刻 |
| Sizing | `strategies/sizing/base.py`, `strategies/sizing/fixed_shares.py` | `size(cash, price, signal)`：株数。0は見送り |
| 仮想Execution/Broker | `execution.py` | 実約定価格、現金、余力、コスト |
| Engine | `intraday_engine.py` | 次足注文、ポジション、部品の連携、日末の決済保証 |
| 入力・立会 | `sessions.py` | timezone/足ラベル正規化、XNYS立会、欠損日の除外 |
| Config / Runner | `config.py`, `registry.py`, `runner.py` | YAML検証、安全な戦略解決、実験実行、識別情報 |
| Metrics | `metrics.py` | 成績集計 |
| Report | `report.py` | CSV/JSON/HTML出力 |

新戦略ファイルを`strategies/entry/`・`strategies/exit/`・`strategies/sizing/`の
対応するフォルダへ追加し、そのフォルダの`base.py`にあるProtocolを実装してください。
例えば新Entryを追加したら`registry.py`へ明示的にimportし、
`ENTRY.register("new_entry", NewEntry)`を1行追加します。
あとはYAMLの`entry.strategy`と`params`を変更するだけで、Engine/Runnerは無改修です。
Exit/Sizingもそれぞれ`EXIT`/`SIZING`へ登録できます。
任意モジュール名のYAML指定、`eval`、自動探索importは行いません。
戦略コンストラクタがパラメータの型/範囲を検証します（組込戦略は`validation.py`を共有）。
Python APIでは`ExperimentEngine(config, entry=..., exit=..., sizing=...)`で個別注入できます。
テスト用の戦略で個別注入とYAML registry経由の両方を自動検証しています。

Entryには当日現在までの確定足と立会予定時刻だけを渡し、`session.bars`は空にします。
Sizingには次足の始値に基づく約定予定価格を渡します。
Exitは、足開始時点で既知の建値から設定した保護注文を評価する契約です。
差し替えExitでも、終値を見てその足の過去の安値で売るなどの先読みは禁止です。
状態を持つ戦略は実験ごとに新規インスタンスを使い、日付リセットも実装してください。
Engineは具体的な戦略名やORBの数値には依存しません。

### 入力時刻・立会・欠損の扱い

必須列は`datetime, open, high, low, close, volume`。追加列は使用しません。
価格はUSD、出来高は元の単位を保持し、整数化しません。timezone付き時刻はその絶対時刻を
UTCへ変換し、naiveな時刻は**YAMLのtimezone**でlocalizeします。
DSTの曖昧/存在しない時刻、重複、非有限値、欠損、非正価格、OHLC矛盾を拒否します。
開始/終了ラベルは`bar_timestamp: start/end`で明示し、各5分区間の開始/終了へ正規化します。

市場日は`exchange_calendars`の`XNYS`を使用します。
[ライブラリのカレンダーAPI](https://github.com/gerrymanoim/exchange_calendars/blob/master/exchange_calendars/calendar_utils.py)
から取得する立会時刻で、米国東部09:30〜16:00、DST、休日、短縮13:00などを扱います。
実行にネット接続は不要です。将来の臨時休場・時間変更はライブラリ更新が必要になる場合があります。
期間の開始日・終了日は東部時間の日付で両端を含みます。

期間内の全営業日を列挙し、通常立会の足だけを使います。時間外・休日の足は警告付きで除外します。
通常日は78本、13時終了日は42本。寄り付き6本、日中の足、最終足のいずれかに欠損がある、
または5分グリッド外の足がある場合は**その日全体を取引対象外**とし、除外日/理由を記録します。
完全にデータがない営業日も除外日です。補間や「最後に見えた価格で決済」はしません。
この事前品質フィルタは後から分かる欠損も用いるため、稼働中の売買停止への対応は再現しません。
比較時には除外日の違いを確認してください。

moomoo取得データの`datetime`はUTCで、観測上の09:35〜16:00から終了ラベルと推定されています。
`interval_start_utc`はその仮説による派生値で、今回の入力では使いません。
`adjustment: none/forward/backward`は入力データの申告条件で、バックテスト側では再調整しません。
隣接する`<stem>_metadata.json`があればハッシュ・銘柄・足種・判明している条件を照合し、
不一致なら拒否します。Issue #37以降は全必須検査がpassでなければ正式評価を禁止します。
メタデータ欠如や正式仕様未確認はunknownとし、`--diagnostic`を明示した診断だけ暫定表示付きで許可します。
`backtest_ready`は毎回検証根拠から計算し、入力metadataのフラグは流用しません。
11月18日の1分足→5分足OHLC全78本一致は終了ラベルを強く支持しますが、正式定義と出来高差分は未解決です。
構造検証成功を品質保証とは扱いません。
[品質検査・根拠ファイル・日足比較・実データ調査・再実行手順](docs/backtest-data-quality.md)を参照してください。

### 約定モデルと損益

30分ORBでは09:30〜10:00の確定済み6本からHigh/Lowを計算します。
10:00以降に**開始する**足の確定終値がHighを厳密に超えたらシグナルとし、次足始値で買います。
例えば終了ラベル10:05の足を確認したら、10:05開始（10:10終了）の足の始値で買います。
このためCSVの`signal_time`と`entry_time`は境界で同じ時刻になり得ますが、参照する足は別です。
立会最終足への新規購入は禁止です。上限1回なら、その日の約定済み購入は1回だけです。
余力不足の購入は拒否し、次のシグナルで余力が足りれば購入できる場合があります。

ストップ/ターゲットは**買いの実約定価格**を基準とし、エントリーした足内でも判定します。
始値がストップ以下ならその始値、足内でストップに達したらストップ水準で決済します。
同じ足で利確・損切りが両方成立したら、始値がターゲット以上の場合も含め、保守的に損切り優先。
ストップに達していない足では、始値がターゲット以上ならその始値、足内到達ならターゲット水準です。
残った建玉は通常立会最終足の終値で決済し、翌営業日へ持ち越しません。
始値での決済時刻は足開始、足内到達の時刻は正確に分からないため足終了で記録します。

買値=`基準価格 × (1 + (slippage_bps + spread_bps)/10000)`、
売値=`基準価格 × (1 - (slippage_bps + spread_bps)/10000)`。
commissionは約定ごとの固定USD（株数によらず片道1回）です。
slippageとspreadは共に基準価格に対する独立した片道概算で、乗算しません。
入力が既に売買気配の差を含む場合はspreadを0にするなど、同じ摩擦の二重計上を避けてください。
初期の手数料/2bpsは比較実験用の仮定で、証券会社の実料金を表しません。

`gross_pnl`は売買基準価格差×株数、`slippage`/`spread`は両側基準代金合計×各bps、
`commission`は両側の固定手数料、`net_pnl = gross_pnl - total_cost`です。
現金から買付実代金と片道手数料を先に控除し、余力を超える購入は行いません。
決済時に売却実代金から片道手数料を引いて戻します。借入・空売りはありません。
価格計算はfloatで行うため、残高と集計損益に浮動小数の丸め誤差が生じる場合があります。

OHLCVでは到達順序・板・実際のスプレッド・流動性・部分約定・取引停止を復元できません。
固定bpsは板による価格形成を再現せず、利確にも不利な調整を適用する簡易モデルです。
最大ドローダウンは下記の確定足終値系列によるもので、足内の最大逆行は含みません。
複数銘柄・税金・為替換算・最適化・実発注は対象外です。

### 結果と再現性

毎回`results/<実験名>_<UTC時刻マイクロ秒>_<ランダムID>/`を排他的に作成し、過去runを上書きしません。
出力途中の失敗時は`FAILED`、品質ゲートでの実行拒否時は`BLOCKED`を残します。

| 成果物 | 内容 |
| --- | --- |
| `config.yaml` | 実行時確定設定。入力/出力は絶対パス、組込戦略の既定値も展開 |
| `trades.csv` | 全取引、東部取引日、symbol、ORB High/Low、シグナル/約定時刻、株数、基準/実約定価格、決済理由、コスト前/後USD損益と内訳。ゼロ取引でもヘッダーを保存 |
| `metrics.json` | 取引回数、勝率、グロス/コスト/ネット、平均ネット、Profit Factor、最大DD（USDと比率）、営業日/取引日/ノートレード日/除外日、警告 |
| `equity_curve.html` | 上部に表示項目・今回の値・分かることの3列表。Plotly内包の資産推移にコスト控除前・控除後の2本を表示。警告・除外日も表示 |
| `quality.json` / `quality.md` | 全必須品質検査のpass/fail/unknown、入力・根拠、日別欠損・除外日、ready判定。拒否時も保存 |
| `daily_comparison.csv` | 通常立会の集計OHLCV、日足参照、生差分・相対差。条件未確認はunknown |
| `run_metadata.json` | UTC実行時刻、入力SHA-256/サイズ/行数、隣接metadataのSHA-256、Git commit/変更状態、実装コードのSHA-256、Python/依存バージョン、カレンダー、仮定 |

CLIの成功ログには、結果ディレクトリに加えて`実験結果HTML：<絶対パス>`を表示します。
HTML上部には初期資金→最終残高、純損益、グロス損益、総コスト、取引回数、勝率、
Profit Factor、最大ドローダウンを表示します。
控除前の線は、控除後と同じ取引・評価時点へ発生済みコストを戻した比較系列です。
保有中は購入時の手数料・スリッページ・スプレッドだけを戻し、売却費用は決済時に加えます。
コストゼロで売買判断・購入余力・利確損切り水準を再計算した別実験ではありません。
両系列は初期資金を含む確定足終値時価評価で、未発生の売却費用は含みません。

勝率はネット損益が正の取引割合、Profit Factorはネット利益合計÷ネット損失絶対値です。
損失ゼロならJSONの非標準Infinityを使わず`null`、`profit_factor_status`で
`no_losses`（利益あり）または`no_trades_or_flat`を区別します。
最大DDの比率は各時点の過去最高資産に対する低下率。ノートレード日は有効営業日のうち取引がない日で、除外日を含みません。

同一の入力バイト列・設定・コード・依存関係で`trades.csv`と`metrics.json`は再現します。
実行ID/時刻・HTML内部IDは実行ごとに異なります。入力とハッシュは同じ読込バイト列から生成します。
未コミット開発中はGit SHAだけでコードを再現できないため、Git変更状態と実装ハッシュも確認し、
比較に使う版のコード・lockファイルを別途保存してください。

外部API不要のE2E検証は`uv run python -m unittest tests.backtest.test_experiment_e2e -v`。
固定の人工OHLCVを一時Parquetへ保存し、YAMLから実際のCLIプロセスを起動して、
取引・期待損益・5種類の成果物・再実行時の再現性を確認します。部品のモックは使用しません。
ゼロ取引のCLI検証も維持しています。

テストは`tests/backtest/`内で次のように分離しています。

- `fixtures.py`：共通の人工OHLCV・設定・実コンポーネント実行ヘルパー
- `test_experiment_config.py`：YAML設定検証
- `test_intraday_engine.py`：時系列・建玉管理・部品連携
- `test_entry.py` / `test_exit.py` / `test_sizing.py`：各戦略
- `test_execution.py`：仮想約定・コスト・現金
- `test_sessions.py`：立会・DST・時刻ラベル・欠損データ
- `test_registry.py`：戦略登録と差し替え
- `test_experiment_metrics.py`：実験の成績集計
- `test_runner.py`：結果保存・入力識別・品質警告
- `test_experiment_e2e.py`：CLIから成果物までの一気通貫検証

既存MVPのテストも維持しています。全テストは`uv run python -m unittest discover -v`で実行します。

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

## Webull日本・米国株コスト比較（Issue #38）

```bash
uv run python -m quant_lab.backtest --compare-costs configs/comparisons/aapl_orb_webull.yaml --diagnostic
```

既存の単発`--config`と旧Parquet CLIは維持します。ベース実験のEntry/Exit/Sizing、
期間、資金、最大取引数を固定し、各シナリオでEngine/SimulationBrokerを再実行します。
現物整数株の買いポジション、事前保有USDでの決済のみ。売買ごとの為替費用は加算しません。
指値待機・未約定・部分約定、ADR管理費、税金は対象外です。

### 公式料金とモデル上の仮定

**確認日2026-11-21**の公表料金を`webull_jp_us_equity`として固定します。
2026年1月のデータにも「現在の料金で過去の戦略を実行したと仮定する試算」として適用し、
当時の実料金・口座請求額は再現しません。

| 費用 | USD料金 | 方向・適用日 |
| --- | --- | --- |
| 委託手数料 | 0 | 売買とも。2026-07-27 17:30以降の約定から無料化 |
| SEC | 約定代金×0.00002060、最低0.01、セント切上げ | 売却のみ。2026-04-02現地約定から |
| FINRA | 株数×0.000195、最低0.01、上限9.79 | 売却のみ。2026-01-01以降の公表基本料金 |
| CAT | 取引所:株数×0.000003、店頭:株数×0.00000003 | 売買とも。2026-05-01現地約定から |

SECはサービス概要の最低額表記と変更告知に差があるため、変更告知の最低0.01と
セント切上げを採用します。FINRAの端数処理は掲載資料で確認できないため、
**セント切上げを仮定**します。CATの最低額・上限・丸めは記載がないため、
**最低額・上限を設けず精密な積を保持する仮定**です。料金はDecimalで計算します。

FINRA公式規則では2026-10-01〜12-31にTAF免除がある一方、Webullサービス概要は
株式の基本料金とオプションの免除を記載しています。株式口座への免除転嫁は未確認です。
本モデルはWebull掲載の株式基本料金を固定する保守的な仮定で、実請求を断定しません。
CAT経路はOHLCVから不明のため、サンプルは取引所と仮定します。

公式資料（2026-11-21確認）:

- [料金表・USDへの事前両替](https://www.webull.co.jp/pricing)
- [米国株サービス概要](https://www.webull.co.jp/products/us-stocks/service)
- [無料化開始](https://www.webull.co.jp/blog/350)
- [CAT変更](https://www.webull.co.jp/blog/319)
- [SEC変更・最低額](https://www.webull.co.jp/blog/300)
- [FINRA変更告知](https://www.webull.co.jp/blog/283)
- [FINRA年末TAF免除規則](https://www.sec.gov/files/rules/sro/finra/2026/34-106409-ex5.pdf)

### スプレッドとスリッページ

spread bpsはフルスプレッドではなく、OHLCV参照価格に対する**片道の実効負担**です。
始値を実測Bid/Ask中間価格とは扱いません。Slippageは追加で不利に動く価格差です。
それぞれ参照価格×株数×bps/10000で独立計算し、買付価格を上げ、売却価格を下げます。
価格反映済みの費用を現金から再控除しません。規制費用は仮想約定価格・数量から計算します。

| Scenario | 片道spread bps | 片道slippage bps |
| --- | ---: | ---: |
| zero_friction | 0 | 0 |
| low | 0.5 | 0.5 |
| baseline | 1 | 2 |
| high | 2 | 5 |

市場実測値ではなく比較の仮定です。zero_frictionにも規制費用は残ります。
購入余力・取引数・実約定価格基準のExit水準が変わり得るため、単純な履歴の費用引き直しはしません。
損益分岐は再実行済み条件のゼロまたは符号反転区間として示し、区間内の存在・一意性・
将来利益を保証しません。取引ゼロは損益分岐としません。範囲内になければ見つからずと表示します。

出力はGit管理外の`results/`配下です。比較CSV/JSON/HTML（表とネット損益グラフ）と、
各シナリオのtrades.csv、metrics.json、equity_curve.html、config.yaml、品質・実行metadataを保存します。
入力・品質根拠ハッシュと設定を記録し、比較開始時に入力と品質根拠のバイト列を保存し、保存済み入力の変更は拒否します。
品質ゲートは単発と同一で、未検証入力は正式評価を拒否します。明示的な`--diagnostic`でのみ
「暫定・診断専用」として扱います。AAPLの時刻定義・出来高単位・日足照合の未解決事項は維持します。
