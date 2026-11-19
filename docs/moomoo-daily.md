# AAPL日足の取得・保存・チャート

## 検証結果（2026-11-19更新）

### Macでの取得・生成結果

保存済みのCSV・Parquet・メタデータを照合し、2026-01-02〜2026-11-17の
AAPL日足221件（`AuType.NONE`）を確認した。期待営業日数も221件で、日付は昇順、
重複なし、CSVとParquetのOHLCVは一致し、既存の品質検証を通過した。
実データのHTMLとPNGが生成され、利用者のMacブラウザでHTML表示を確認済み。
PNGもレビュー時に確認した。外部データソースとの日別OHLCVの完全一致は未検証。

Macでの`probe`実行結果（2026-11-19 01:14 JST）:

| 要求期間 | 結果 | 件数 | 実際の取得範囲 |
| --- | --- | ---: | --- |
| 2025-01-01〜01-15 | sampled | 9 | 2025-01-02〜01-15 |
| 2020-01-01〜01-15 | sampled | 10 | 2020-01-02〜01-15 |
| 2010-01-01〜01-15 | sampled | 10 | 2010-01-04〜01-15 |
| 2006-01-01〜01-15 | empty | 0 | なし |

APIエラーは報告されていない。確認できた最古は2010-01-04だが、全履歴の最古ではない。
2006年の空応答の原因、2007〜2009年、その他の未要求期間は未確認。

### 初回の環境制約（2026-11-18時点の記録）

CLIとオフライン単体テストを追加した時点では、チャットのサンドボックスからは
`127.0.0.1:11111` への接続が `PermissionError: [Errno 1] Operation not permitted`
となり、実データ取得と過去データ検証には未到達。OpenD自体の停止や認証失敗を意味しない。
`fetch` と `probe` の実行はいずれも終了コード2、`stage=connection`。
依存追加も `pypi.org` の許可リスト拒否となったため、SDK・Plotly・Kaleidoの導入と
HTML/PNG出力はその時点のチャット環境では実検証できなかった。
その後のMacでの成功は上記と区別する。架空データの成果物は生成していない。

検証結果はunittest 132件中131件成功・Plotly未導入により1件スキップ。
`ruff check .`、`mypy --no-incremental .`、`git diff --check` は成功。
既存テスト117件も変更前に成功を確認した。

利用者から、Macでログイン済みOpenDと5分足156件の取得成功が報告されている。
5分足の結果は、上記の日足221件の取得結果とは別の検証である。

## Macでの実行

リポジトリのルートで実行する。Python 3.12、uv、ログイン済みの公式moomoo OpenDが必要。
接続先は `127.0.0.1:11111` 固定。IDやパスワードをCLIへ渡す必要はない。
OpenDの認証状態・設定・ログはリポジトリ外に置く。発注・取引アンロックは行わない。

```bash
uv sync --locked --python 3.12

# SDKとグラフ依存はpyproject.toml/uv.lockに含まれる
uv run --locked python -m quant_lab.stock.daily fetch \
  --from 2026-01-01 --to 2026-11-18

# 保存済み実データから再生成。API接続・SDK・ログインは不要
uv run --locked python -m quant_lab.stock.chart \
  data/stock/aapl_daily_2026.parquet

open data/stock/aapl_daily_2026.html
```

CSVを使う場合はchartの入力を `data/stock/aapl_daily_2026.csv` に替える。
HTMLはPlotlyのJavaScriptを内包するため、出力後はネット接続なしで開ける。
ローソク足・出来高・終値線を縦に配置し、横軸を共有する。マウスのホバーで値を確認し、
ドラッグ・ホイール・期間ボタン・スライダーで表示範囲を変更できる。

PNG出力にはKaleidoとChrome/Chromiumが必要。既存のChromeが検出されなければ
`BROWSER_PATH` に実行ファイルの絶対パスを指定する。例:

```bash
export BROWSER_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
```

Chromeがない場合は利用者がインストールしてからグラフコマンドを再実行する。
PNG生成に失敗してもHTMLが保存済みならそのパスをエラーに表示し、終了コード2を返す。
古いPNGが存在する場合、それは今回の再生成に成功した成果物ではない。

`uv`は通常どおり実行し、キャッシュ書込権限エラーが出た場合だけ
`UV_CACHE_DIR=/tmp/quant-lab-uv-cache uv ...` で再実行する。

## ファイルと検証

既定の保存先は、既存の株式取得と同じGit管理対象外の `data/stock/`。
`fetch --output /absolute/path/aapl_daily_2026` で拡張子なしの保存先を変更できる。

| ファイル | 内容 |
| --- | --- |
| `aapl_daily_2026.csv` | UTF-8、日付昇順、`date,open,high,low,close,volume` |
| `aapl_daily_2026.parquet` | 同じデータと、バックテスト用のUTC付き`datetime`列 |
| `aapl_daily_2026_metadata.json` | 提供元、銘柄、足、調整方式、取得日時、要求期間、有効終了日、実期間、件数、タイムゾーン、期待営業日数、SDKバージョン、品質結果 |
| `aapl_daily_2026.html` | 操作可能な3段チャート |
| `aapl_daily_2026.png` | 同チャートの静止画 |

CSVの`date`は米国東部の取引日。Parquetの`datetime`はその日の東部時間0時を
UTCへ変換したもの（取引日識別用であり、バー確定時刻ではない）。
既存の `quant_lab.backtest.data.load_parquet` で読み込める。
Yahoo Finance等との比較にはCSVの`date`を使い、未調整価格と調整後価格を混同しない。

`request_trading_days` で祝日・土日・短縮取引日を考慮する。通常日は東部時間16時、
`MORNING`の短縮日は13時より前には当日を含めない。指定終了日より新しい日付は取得しない。
取引日カレンダーは臨時休場を完全には反映しないため、欠落時は自動補間せずエラーにする。

`request_history_kline` の次ページキーがなくなるまで取得する。
日付重複、欠損、非有限値、価格の非正値、high/lowの矛盾、負のvolume、
取引日カレンダーとの過不足は保存前に拒否する。許容差で見逃さず期待営業日数との完全一致を要求する。
行は昇順に整列するが、株価・出来高や欠損バーは補正しない。
元応答の日時順序はメタデータの`source_dates_ascending`に記録する。

CSV・Parquet・メタデータは同じディレクトリ内の一時領域に全出力し、旧ファイルを
退避してから置き換える。書込や置換の例外時には、更新済みファイルを旧版に戻す。
復元自体が失敗した場合は退避ファイルを残し、その場所をエラーで通知する。
複数ファイルの置換全体がOS上で原子的になるわけではなく、強制終了・電源断や
同じ保存先への並行実行に対する整合性保証はない。同じ保存先への取得は直列に実行する。

成功時の標準出力JSONに件数、最初・最後の日付、先頭5件、末尾5件、保存パスを含める。
API権限・履歴枠・頻度制限エラーは成功にせず停止し、部分データは保存しない。
再試行はOpenDの表示とエラー原因を確認してから行う。ページ間は2.1秒空ける。
接続、SDK読込、ハンドシェイク、認証、取得処理のどこで停止したかを`stage`で表示する。
`--timeout 120`（既定値）でSDKの接続待ちを含むプロセス全体を制限する。

## 過去データの少量検証

```bash
uv run --locked python -m quant_lab.stock.daily probe
```

2025・2020・2010・2006年の各1月1〜15日について、先頭ページ最大10本だけを取得する。
結果は `data/stock/aapl_history_probe.json` に保存する。
各リクエスト期間、取得件数、最初・最後の確認日、空応答やAPIエラーを個別に記録する。
1件でもエラーがあれば、報告ファイルを残して終了コード2を返す。
`earliest_confirmed` はこの検証で確認できた最古の日付であり、全履歴の最古ではない。
`absolute_earliest` は未確定を示す `null`。他の月・年、2006年より前は未検証として残す。
権限エラーや空応答から「それ以前には履歴がない」とは結論しない。

## テスト

```bash
uv run python -m unittest discover -v
uv run ruff check .
uv run mypy --no-incremental .

# Plotly構造の追加テスト
uv run --locked python -m unittest tests.stock.test_chart -v
```

APIはモックし、テスト専用の値は一時ディレクトリだけに保存する。
Plotlyは通常依存であり、未導入ならテストは失敗する。実APIやPNGの描画成功は単体テストだけで保証しない。

仕様参照:

- [moomoo履歴K線](https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-history-kline.html)
- [moomoo取引日カレンダー](https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-trading-days.html)
- [Plotly HTML出力](https://plotly.com/python/interactive-html-export/)
- [Plotly PNG出力とChrome](https://plotly.com/python/static-image-export/)
