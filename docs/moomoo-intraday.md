# moomoo 米国株5分足のデータ基盤

## 実測結果（2026-11-19、MacのOpenD）

`127.0.0.1:11111`の行情ログイン済みOpenDに接続して実行した。
SDK 10.12.7208、Plotly 7.1.0を使用。取引Context、注文、取引アンロックは使用していない。

- US.AAPL、K_5M、AuType.NONE、通常立会のみ。
- 2026-01-02 09:35 ET〜2026-11-18 16:00 ET、222営業日、17,316本。
- 期待17,316本と一致。重複、OHLCV欠損、負の出来高、OHLC矛盾、立会時間外、欠落足は0。
- SDKから小数の出来高が返る足は5,850本。型は公式資料でもfloatなので丸めていない。
- 終了ラベルという仮説で全セッションの5分グリッドと整合。正式な時刻定義は未確認。
- 実データからローソク足・出来高のHTMLを生成した。JavaScriptを内包し、ネット接続不要。
  ブラウザ操作ツールは`file://`を許可しないため、このチャットでHTMLの目視表示までは確認できていない。

### HTMLの軽量化

年間17,316本を一括描画したHTMLは利用者のMacで応答停止したため、表示方法を変更した。
初期表示は直近5営業日・390本。日付指定、前後の期間、直近5営業日のボタンで切り替える。
表示を最大5営業日に制限し、軸を狭めるだけでなくPlotlyへ渡す行自体を差し替える。
描画するローソク足と出来高は各最大390本で、全期間レンジスライダーは使用しない。
全期間の概要は別のグラフで日足集計222本として表示し、5分足とは足種が異なることを明記する。

CSV/Parquetと品質報告は全件のまま維持する。HTML内にも全件を保持し、期間切替時の通信は行わない。
Plotly本体と元データは読み込むため、HTML容量が描画本数と同じ割合で小さくなるわけではない。
実際のブラウザ応答時間の改善はMacで確認が必要で、速度の保証値は設定していない。
再生成後は、応答停止している古いタブを閉じ、新しいHTMLを開き直す。

**構造検証の成功と、バックテストに適したデータであることは区別する。**
メタデータの`quality_scope`は`structural_validation_only`、`backtest_ready`は`false`。
時刻の正式な意味と以下の比較差分は確認が必要。データを補正して一致させてはいない。

### 日足との比較

5分足を東部時間の日付ごとに、最初のOpen・最大High・最小Low・最後のClose・Volume合計へ集計した。
全日が78本あり、集計日足は222行。外部照合はYahoo Financeをyfinanceの
`auto_adjust=False`、`prepost=False`で取得して行った。

| 比較項目 | 結果 |
| --- | --- |
| Yahooとの日付対応 | 222日、片側だけの日付0 |
| YahooとのOpen/High/Close差 | 最大約0.0051ドル |
| YahooとのLow差 | 最大約0.03999ドル |
| 価格差が0.0101ドルを超えた日 | 1日（2026-04-20のLow） |
| YahooとのVolume差 | 全222日。最大絶対差78,608,763株 |
| moomoo K_DAYとのOHLC差 | Open/High/Closeは一致。2026-04-20のLowが0.04ドル差 |
| moomoo K_DAYとのVolume差 | 全222日。最大絶対差78,608,713株 |

Volume差の原因は未確定。集計対象、配信範囲、オークション・時間外取引等の口径差の可能性はあるが、
どれかと断定できる証拠は得ていない。出来高を使う戦略へ投入する前に提供元へ確認する必要がある。
比較CSVには全OHLCVの両値と生の差分を残す。`compared`は照合処理済みであり一致保証ではない。
YahooのCloseも、株式分割などの表示・調整規則が提供元間で異なる可能性がある。

### 遡及調査

各年の1月2〜8日のみ（最大7日間、1ページ）を順に調査し、最初の空応答で停止した。

| 年・期間 | 本数 | 結果 |
| --- | ---: | --- |
| 2025年1月 | 390 | 取得・構造検証成功 |
| 2024年1月 | 390 | 同上 |
| 2023年1月 | 312 | 同上 |
| 2022年1月 | 390 | 同上 |
| 2021年1月 | 390 | 同上 |
| 2020年1月 | 390 | 同上 |
| 2019年1月 | 390 | 同上 |
| 2018年1月 | 0 | API正常応答、空データ |
| 2018-11-19〜11-25（境界追加調査） | 276 | 11-19 09:35〜11-23 13:00 ETを取得・構造検証成功 |

**確認できた最古は2018-11-19 09:35 ET（14:35 UTC）。**
2018-11-22の休場と11-23の短縮日（42本）もカレンダーと一致した。
全履歴の最古としては未確定で、未要求の期間は確認していない。
公式資料は60分足以下を過去8年間に制限しており、今回の空応答と境界の成功はこれと整合する。
時間の経過とともに提供範囲は変化するので、この最古確認日は固定の保証ではない。

## Macでの再実行

```bash
uv sync --locked --python 3.12

# 2026年初〜実行時点で確定した最終営業日。データ取得と検証のみ。
uv run --locked \
  python -m quant_lab.stock.intraday fetch --from 2026-01-01

# 保存済みParquetからHTML再生成（OpenD接続不要）
uv run --locked \
  python -m quant_lab.stock.intraday_chart chart

open data/stock/aapl_5m_2026.html

# 外部日足取得と比較（OpenD接続不要）
uv run --locked \
  python -m quant_lab.stock.intraday_chart compare --yahoo

# 2026年分を取得した後、1週間ずつ遡る。初期値は2025・2024のみ。
uv run --locked \
  python -m quant_lab.stock.intraday probe --years 2025 2024

# 取得範囲の境界付近を最大7日間だけ調査
uv run --locked \
  python -m quant_lab.stock.intraday probe --window-start 2018-11-19
```

検証日と同じ終了指定は`fetch --from 2026-01-01 --to 2026-11-19`。
実行日当日の足は、通常16時・短縮13時の終了から15分以上経過したセッションのみ含める。
まだ確定していない場合は前営業日まで。指定終了日が将来でも未来の足は作らない。

`fetch/probe --output /absolute/path/aapl_5m_2026`は拡張子なしの保存先。
`chart/compare --dataset /absolute/path/aapl_5m_2026`で同じデータセットを指定する。
取得元のParquet SHA-256をメタデータと品質報告で検証する。
HTMLは生成時のスナップショットで、データ識別子・取得時刻を表示する。再取得後はchartも再実行する。
外部比較状態は再取得時に未実施へ戻し、新しいParquetと古い比較結果を混同しない。

Yahooが利用できない場合は、未調整の日足を次のUTF-8 CSV形式で用意できる。
日付は米国東部の取引日で、必須列は`date,open,high,low,close,volume`。

```bash
uv run --locked \
  python -m quant_lab.stock.intraday_chart compare \
  --reference-csv /absolute/path/reference.csv --reference-source '提供元と調整方式'
```

今回のYahoo比較ファイルを再利用する場合は
`--reference-csv data/stock/aapl_5m_2026_external_reference.csv`を指定する。
外部取得エラーは`_external_comparison_status.json`に残し、比較成功として扱わない。

## 保存ファイル（すべてGit管理対象外）

既定の共通接頭辞は`data/stock/aapl_5m_2026`。

| 接尾辞 | 内容 |
| --- | --- |
| `.parquet` / `.csv` | 正規化したOHLCV、UTCのdatetime、ETのdatetime_et、取引日、推定interval_start_utc |
| `_raw.parquet` | SDK生応答（時刻・OHLCVを補正しない） |
| `_metadata.json` | 提供元・足種・調整・要求/有効期間・取得日時・SDK・時刻解釈・利用条件確認状況・データハッシュ |
| `_quality.json` | 日別期待本数・実本数・欠落時刻・ゼロ/小数出来高件数・構造検証結果 |
| `_daily.csv` | 5分足からの日足集計。欠落日の集計はcomplete=false |
| `_moomoo_daily_comparison.csv` | moomooの日足APIとの比較 |
| `_external_reference.csv` | 外部比較に使った日足 |
| `_external_comparison.csv` | 外部日足との全OHLCV差分・対応状態 |
| `_external_comparison_status.json` | 比較元・日時・データハッシュ・差分概要 |
| `.html` | 最大5営業日の5分足詳細と、全期間の日足集計概要。ET軸でUTCもホバー表示 |
| `_history_probe.json` | 年ごとの少量調査結果 |
| `_history_boundary_probe.json` | 指定窓の境界調査結果 |
| `_probe_START_END_HASH.parquet` / `_probe_START_END_HASH_quality.json` | 調査窓の開始・終了日とデータハッシュを含む証拠データ・品質報告 |
| `_last_failure.json` | 直近の失敗記録。後の成功時も履歴として残り得る |
| `_failed_raw.parquet` / `_failed_quality.json` | 品質不正時の生データと理由。通常の成功成果物とは別 |

今回の初回検証では小数出来高への不要な整数制限で一度停止した。
公式のfloat型に合わせてその制限を外し、保存済み生データを使って再検証した。
`fetch --raw-source data/stock/aapl_5m_2026_raw.parquet`で同じ再検証が可能。
再取得は行わず、カレンダーと比較用の日足だけを照会する。メタデータに生データのパス・更新日時を残す。

依存は`pyproject.toml`に集約し、`uv.lock`でバージョンを固定する。
`uv sync --locked`でSDK・Plotly・Kaleidoも導入され、別requirementsファイルは不要。
遡及調査の保存名は開始日・終了日・内容ハッシュを含むため、別の窓や同じ窓の改訂データが
過去レポートの証拠を上書きしない。旧形式の既存ファイルはそのまま保持し、新規取得から適用する。
各調査レポートに実際の保存パスと完全なSHA-256を記録する。

CSV/Parquetの`datetime`はSDKのラベル時刻をUTC変換したもの。
`datetime_et`はDSTを反映した東部時間、`interval_start_utc`は終了ラベル仮説で5分を引いた派生値。
既存バックテストの`load_parquet`で読めるが、時刻意味・出来高差分の解決前に適合保証はしない。

## 品質・障害時の扱い

- ページキーがなくなるまで取得。循環、最大200ページ超過は停止する。
- 呼出間隔は最低1.1秒。全APIを直列化し、30秒あたり30回未満に抑える。
- 頻度制限・一時的ネットワークエラーは5/10/20秒待って最大3回再試行。同じページキーを維持する。
- 権限・履歴枠不足・ログイン不足は再試行しない。SDK全体は子プロセスで`--timeout 300`秒に制限。
- 欠損、重複、非有限値、非正価格、OHLC矛盾、負の出来高、グリッド外・立会時間外は検出する。
- 欠落足は売買停止や配信欠落の可能性を区別できないため`needs_review`として欠落時刻を保存する。
  売買停止を自動認定せず、ゼロ出来高の足も勝手に除去しない。
- 終了コード0は構造検証成功/処理完了、2は失敗、fetchの3は欠落を含む要確認データ。
  終了コード0でも時刻定義・日足比較・利用権の確認が完了したという意味ではない。
- 出力は同じディレクトリに全準備してから置換し、例外時は旧ファイルを復元。
  復元できなければ旧版退避先を表示する。強制終了・電源断・同じ保存先への並行実行は保証対象外。

## 公式仕様・利用条件の確認

[履歴K線の公式資料](https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-history-kline.html)は
米国株のtime_keyを米国東部時間とし、出来高の型をfloatと説明している。
開始/終了のどちらのラベルかを明示する文言は確認できなかった。
実データの09:35〜16:00（短縮日は13:00）から終了ラベルと推定し、公式確認は未確認と記録する。
同資料の制限は60分足以下が過去8年、履歴APIの初回リクエストが30秒あたり最大60回。
[カレンダーAPI](https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-trading-days.html)は30秒あたり最大30回、
過去10年で、臨時休場を除外しない。したがって突然の休場も欠落として要確認になる場合がある。

[日本向け利用規約・相場情報利用規約](https://www.moomoo.com/ja/terms/japan)の検索索引では、
個人利用目的と、提供元の事前同意なしの相場情報の保存・第三者提供等に対する制約を確認した。
全文ページの直接取得は403だったため、契約地域・契約種別に応じたAPI保存許諾の範囲は最終確認できていない。
APIが技術的に動くことは、あらゆる保存・商用利用・再配布の許可を意味しない。
成果物はローカルの個人研究用とし、公開リポジトリや外部サービスへ送らない。
長期保存・公開・商用利用の前には、自身に適用される最新契約と相場情報プロバイダーの条件を確認する。
この実装で利用規約の同意やアカウント設定変更は行っていない。

## テスト

```bash
# 通常依存にPlotlyを含む。Node.jsも導入して全実行
uv run --locked python -m unittest discover -v
uv run ruff check .
uv run mypy --no-incremental .
git diff --check
```

依存統合・証拠保存名修正後の実行結果: 153件成功、スキップ0。ruff・mypy成功。
APIはモックし、タイムゾーン/DST、半日、未確定日除外、欠落非補間、不正OHLCV、
ページング、再試行上限、恒久エラー、保存のロールバック、ハッシュ不一致、Plotly構造を検証した。
期間指定・前後移動・過大な表示期間の拒否は、Node上でDOM/PlotlyをモックしてJavaScriptの挙動も検証した。
Plotly未導入時はグラフテストが失敗する。
Nodeがないローカル環境ではJavaScript挙動のテストがスキップされる。
CIはNode.js 24を明示し、単体テストのスキップが1件でもあれば失敗として扱う。
uvのキャッシュ書込権限エラー時のみ、AGENTS.mdに従い
`UV_CACHE_DIR=/tmp/quant-lab-uv-cache uv ...`で再実行する。
