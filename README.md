# quant-lab

GMOコイン外国為替FX Public APIからUSD/JPYの最新レートを取得し、コンソール表示、CSVへの履歴保存、および任意のSlack通知を行うスクリプトです。Public APIを利用するため、GMOコインのAPIキーやSecretは不要です。

レートと基準時刻は `GET https://forex-api.coin.z.com/public/v1/ticker`、市場ステータスは `GET https://forex-api.coin.z.com/public/v1/status` から取得します。詳細は[GMOコイン外国為替FX APIドキュメント](https://api.coin.z.com/fxdocs/)を参照してください。

## 日足KLine

GMOコイン外国為替FX Public APIの `GET /public/v1/klines` から、指定した年範囲のUSD/JPY日足を取得できます。BIDとASKを個別に取得し、同じ `openTime` のデータだけを結合します。APIが返す価格文字列は `Decimal`、ミリ秒単位のUnix timestampである `openTime` はUTCのタイムゾーン付き `datetime` として扱います。

日足の `interval=1day` では、APIの `date` パラメータに日付ではなく4桁の年（`YYYY`）を指定します。次の例は2023年から2026年までを年の昇順で取得します。

```bash
uv run python daily_kline.py --from-year 2023 --to-year 2026
```

保存先の既定値は `data/usd_jpy_1day.csv` です。別の保存先は `--output` で指定できます。

```bash
uv run python daily_kline.py \
  --from-year 2025 \
  --to-year 2026 \
  --output data/usd_jpy_1day_recent.csv
```

実行結果には取得件数、追加件数、更新件数を表示します。既存CSVは維持しながら `open_time` をキーに新規行を追加し、同じ時刻の内容が変わった場合は更新します。保存時はUTCの `open_time` 昇順に並べ、同一時刻を重複保存しません。BIDとASKの時刻が一致しない場合は、欠損した行を作らずエラーにします。

CSVは最新レート履歴とは分けて保存し、次の列で構成されます。

```csv
open_time,bid_open,bid_high,bid_low,bid_close,ask_open,ask_high,ask_low,ask_close
```

`open_time` はUTCのISO 8601形式です。OHLCは、BIDとASKそれぞれについて始値（`open`）、高値（`high`）、安値（`low`）、終値（`close`）をAPIの意味のまま保存します。BID/ASKから根拠の不明確な仲値高値・安値は生成しません。

## レート項目

| 項目 | 意味 |
| --- | --- |
| `bid` | 現在の売値 |
| `ask` | 現在の買値 |
| `rate` | bidとaskの仲値（`(bid + ask) / 2`） |
| `spread` | 売値と買値の差（`ask - bid`） |

計算には浮動小数点誤差を避けるため `Decimal` を使用します。APIの基準時刻はUTCとして解析し、日本時間の日付を `rate_date` として保存します。

## 前回比

現在の仲値は、暦上の前日ではなく、CSV内で今回の `rate_date` より前にある直近の有効な仲値と比較します。そのため、土日・祝日などで日付が空いていても、保存済みの直近レートが比較対象です。同じ `rate_date` の行は比較に使用しません。

- 変動額: `現在のrate - 直近のrate`
- 変動率: `(現在のrate - 直近のrate) / 直近のrate × 100`
- 変動額が正の場合: 円安
- 変動額が負の場合: 円高
- 変動額が0の場合: 変化なし

計算と方向判定には丸め前の `Decimal` 値を使い、通知へ表示するときだけ小数点以下2桁に丸めます。CSV内の日付が不正な行や、`rate` が `Decimal` に変換できない行、非有限値、0以下の行は比較対象から除外します。該当する有効な過去行がなければ、エラーにはせず `前回比: 比較データなし` と表示し、方向は表示しません。

## 実行方法

依存関係を同期してスクリプトを実行します。

```bash
uv sync --locked
uv run python main.py
```

最新レートは `data/usd_jpy.csv` に追記されます（`data` ディレクトリがなければ自動作成されます）。同じ `rate_date` の行がすでに存在する場合は重複保存しません。

## 日足チャート

既存の日足取得処理が保存する `data/usd_jpy_1day.csv` を使い、USD/JPYの直近30営業日のClose価格を折れ線グラフで確認できます。新しいAPIリクエストは行わず、BIDとASKのCloseの仲値を日付順に表示します。30件未満やデータがない場合にも対応しています。

初回は、先に表示対象の日足データを取得してからチャートを起動します。

```bash
uv run python daily_kline.py --from-year 2023 --to-year 2026
uv run streamlit run chart_app.py
```

日足CSVがまだ作成されていない場合、画面にも先に実行する取得コマンドを表示します。ブラウザで表示された `USD/JPY` 画面は幅に合わせて伸縮し、日付とClose価格のグリッド、ポイントごとのツールチップを表示します。標準テーマはダークモードです。

別のCSVを確認する場合は、既存の日足CSVと同じ形式のファイルを環境変数で指定できます。

```bash
USD_JPY_KLINE_PATH="data/usd_jpy_1day_recent.csv" \
  uv run streamlit run chart_app.py
```

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
| `source_timestamp` | ticker APIの `responsetime` が示す基準時刻（UTC） |
| `market_status` | 市場ステータス（`OPEN` または `CLOSE`） |

旧形式（`fetched_at,rate_date,rate`）のCSVは、次回保存時に既存の3項目を維持したまま新形式へ安全に移行します。旧行の追加項目は空欄になります。

## Slack通知

Slack通知を利用する場合だけ、Bot tokenと通知先チャンネルを設定します。

```bash
export SLACK_BOT_TOKEN="xoxb-your-bot-token"
export SLACK_CHANNEL="#alerts"
uv run python main.py
```

どちらかが未設定の場合もレート取得とCSV保存は実行され、Slack通知だけをスキップします。コンソールと通知には仲値、bid、ask、spread、APIの基準時刻、市場ステータス、前回比が含まれます。

### 変動アラート

前回比の変動率が設定した閾値以上になった場合、通常のSlack通知を警告見出しと設定閾値で強調します。判定式は `abs(change_percent) >= threshold` で、円安方向の上昇と円高方向の下落の両方が対象です。判定には `RateChange.percent` の丸め前の `Decimal` 値をそのまま使い、閾値と完全に等しい場合もアラートになります。比較データがない場合は通常通知です。

閾値は環境変数 `USD_JPY_ALERT_THRESHOLD_PERCENT` で指定します。単位はパーセント、デフォルト値は `1.0` です。未設定、空文字、空白だけの場合はデフォルト値を使用します。設定値は正の有限数である必要があり、`0` 以下、不正な文字列、`NaN`、`Infinity`、`-Infinity` は設定エラーになります。不正値をデフォルト値へ置き換えることはありません。

ローカルでは次のように設定します。

```bash
export USD_JPY_ALERT_THRESHOLD_PERCENT="0.5"
uv run python main.py
```

GitHub Actionsでは、リポジトリの **Settings > Secrets and variables > Actions > Variables** から、Repository Variableとして `USD_JPY_ALERT_THRESHOLD_PERCENT` を登録してください。未登録時にワークフローへ空文字が渡された場合も、デフォルト値 `1.0` で動作します。

通常通知の例:

```text
USD/JPY 仲値: 162.74
bid: 162.73
ask: 162.75
spread: 0.02
基準時刻: 2026-07-21T00:00:00+00:00
市場ステータス: OPEN
前回比: +0.82円（+0.51%）
方向: 円安
```

アラート通知の例:

```text
⚠️ USD/JPY変動アラート
USD/JPY 仲値: 164.10
bid: 164.09
ask: 164.11
spread: 0.02
基準時刻: 2026-07-22T00:00:00+00:00
市場ステータス: OPEN
前回比: +1.82円（+1.12%）
方向: 円安
設定閾値: 1.00%
```

比較できる過去データがない場合、末尾は次の形式になります。

```text
前回比: 比較データなし
```

## テストとCI

外部APIやSlackには接続せず、既存方針に合わせて `unittest` とモックを使うテストを実行します。

```bash
uv run python -m unittest discover -v
uv run ruff check .
uv run mypy .
```

GitHub Actionsは `main` へのpush、`main` 向けPull Request、手動実行時に同じテストを実行します。

### 定期監視ワークフロー

最新レート監視はGitHub Actionsで1時間ごとに実行されます。cronはUTC基準で、実行時間帯はUTCの月曜0:17から金曜23:17までです。JSTでは月曜9:17から土曜8:17までの毎時実行となり、GitHub Actionsから手動実行することもできます。

各実行では、`USD_JPY_ALERT_THRESHOLD_PERCENT` が適用され、通常通知または変動アラート通知のどちらか1回をSlackへ送信します。比較対象は1時間前のレートではなく、CSV内にある直近の異なる過去日付の有効レートです。

CSVは引き続き1日1件です。JSTでその日最初に成功した実行だけがレートを保存し、同日2回目以降の実行ではCSV保存とcommitをスキップします。CSVに変更がある場合だけ、ワークフローが更新をcommitします。
