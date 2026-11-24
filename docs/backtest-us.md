# 米国株バックテスト

YAMLで Entry・Exit・Sizing を指定し、保存済み5分足Parquetから実験を再実行するバックテストの仕様です。初期戦略は30分ORB、固定率の損切り/利確、固定1株。外部API・OpenDへの接続はなく、すべてメモリ内の仮想取引です。

```bash
uv sync --locked
# 品質検証のみ。入力Parquetが必要
uv run python -m quant_lab.backtest --config configs/experiments/aapl_orb_30m.yaml --quality-only
# 暫定値の診断再現（正式評価ではない）
uv run python -m quant_lab.backtest --config configs/experiments/aapl_orb_30m.yaml --diagnostic
# 全必須品質検査passのデータだけ正式評価
uv run python -m quant_lab.backtest --config configs/experiments/aapl_orb_30m.yaml
```

入力の既定パスは `data/stock/aapl_5m_2026.parquet` で、実行前に取得済みのParquetを配置します。入力/出力の相対パスは実行時のカレントディレクトリ基準です。終了コードを含む品質ゲートの判定条件は [5分足の品質判定](backtest-data-quality.md) を参照してください。

## 設定例と検証

完全なサンプルは [configs/experiments/aapl_orb_30m.yaml](../configs/experiments/aapl_orb_30m.yaml) です。

設定は厳密に検証し、必須セクション/キーの欠落、未知キー、YAML重複キー、未知の strategy やパラメータ、型違い、NaN/Infinity、負のコスト、期間の逆転は、理由付きエラー（終了コード1）で実行前に拒否します。各項目の型・範囲の正はサンプル設定と検証テストです。特に次の規則は見落としやすいものです。

- 日付は引用符付き `YYYY-MM-DD` 文字列、timezone はIANA名、足種は `5m` のみ
- `close_at_market_end` は当日決済を守るため `true` のみ
- 各組込戦略の `params: {}` は実装の既定値を使い、結果設定には適用した既定値も保存する
- `output` 省略時は `results`

## 責務分離と戦略の追加

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

新戦略は `strategies/entry/`・`strategies/exit/`・`strategies/sizing/` の対応フォルダへ追加し、そのフォルダの `base.py` のProtocolを実装します。`registry.py` へ明示的にimportして `ENTRY.register("new_entry", NewEntry)` のように1行登録すれば、YAMLの `strategy` と `params` の変更だけで使え、Engine/Runnerは無改修です（Exit/Sizingも `EXIT`/`SIZING` へ同様）。任意モジュール名のYAML指定、`eval`、自動探索importは行いません。パラメータの型/範囲はコンストラクタが検証します（組込戦略は `validation.py` を共有）。Python APIでは `ExperimentEngine(config, entry=..., exit=..., sizing=...)` で個別注入でき、個別注入とregistry経由の両方をテストで検証しています。

契約上の要点: Entryには確定足と立会予定時刻だけを渡し（`session.bars` は空）、Sizingには次足始値に基づく約定予定価格を渡します。Exitは足開始時点で既知の建値から設定した保護注文を評価し、先読みは禁止です。状態を持つ戦略は実験ごとに新規インスタンスを使い、日付リセットも実装します。Engineは具体的な戦略名やORBの数値には依存しません。

## 入力時刻・立会・欠損の扱い

必須列は`datetime, open, high, low, close, volume`。追加列は使用しません。
価格はUSD、出来高は元の単位を保持し、整数化しません。timezone付き時刻はその絶対時刻を
UTCへ変換し、naiveな時刻は**YAMLのtimezone**でlocalizeします。
DSTの曖昧/存在しない時刻、重複、非有限値、欠損、非正価格、OHLC矛盾を拒否します。
開始/終了ラベルは`bar_timestamp: start/end`で明示し、各5分区間の開始/終了へ正規化します。

市場日は `exchange_calendars` の `XNYS` を使い、米国東部09:30〜16:00、DST、休日、短縮13:00などを扱います（[カレンダーAPI](https://github.com/gerrymanoim/exchange_calendars/blob/master/exchange_calendars/calendar_utils.py)）。実行にネット接続は不要です。将来の臨時休場・時間変更はライブラリ更新が必要になる場合があります。期間の開始日・終了日は東部時間の日付で両端を含みます。

期間内の全営業日を列挙し、通常立会の足だけを使います。時間外・休日の足は警告付きで除外します。
通常日は78本、13時終了日は42本。寄り付き6本、日中の足、最終足のいずれかに欠損がある、
または5分グリッド外の足がある場合は**その日全体を取引対象外**とし、除外日/理由を記録します。
完全にデータがない営業日も除外日です。補間や「最後に見えた価格で決済」はしません。
この事前品質フィルタは後から分かる欠損も用いるため、稼働中の売買停止への対応は再現しません。
比較時には除外日の違いを確認してください。

moomoo取得データの`datetime`はUTCで、観測上の09:35〜16:00から終了ラベルと推定されています。
`interval_start_utc`はその仮説による派生値で、今回の入力では使いません。
`adjustment: none/forward/backward`は入力データの申告条件で、バックテスト側では再調整しません。

入力に隣接する `<stem>_metadata.json` がある場合は、ハッシュ・銘柄・足種・判明している条件を照合し、不一致なら拒否します。品質検査の項目・根拠・ready判定の詳細は [5分足の品質判定](backtest-data-quality.md) を参照してください。

## 約定モデルと損益

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

## 結果と再現性

毎回`results/<実験名>_<UTC時刻マイクロ秒>_<ランダムID>/`を排他的に作成し、過去runを上書きしません。
出力途中の失敗時は`FAILED`、品質ゲートでの実行拒否時は`BLOCKED`を残します。

| 成果物 | 内容 |
| --- | --- |
| `config.yaml` | 実行時確定設定。入力/出力は絶対パス、組込戦略の既定値も展開 |
| `trades.csv` | 全取引。取引日、symbol、ORB High/Low、シグナル/約定時刻、株数、基準/実約定価格、決済理由、損益とコスト内訳。ゼロ取引でもヘッダーを保存 |
| `metrics.json` | 取引回数、勝率、グロス/コスト/ネット、平均ネット、Profit Factor、最大DD（USDと比率）、営業日/取引日/ノートレード日/除外日、警告 |
| `equity_curve.html` | Plotly内包の資産推移（コスト控除前・後の2本）。警告・除外日も表示 |
| `run_metadata.json` | UTC実行時刻、入力SHA-256/サイズ/行数、Git commit/変更状態、実装コードのSHA-256、Python/依存バージョン、仮定 |

CLIの成功ログには、結果ディレクトリに加えて実験結果HTMLの絶対パスを表示します。HTMLには初期資金→最終残高、純損益、グロス損益、総コスト、取引回数、勝率、Profit Factor、最大ドローダウンを表示し、コスト控除前/後の2系列を比較できます。控除前の系列は、控除後と同じ取引に発生済みコストを戻したもので、コストゼロで売買判断を再計算した別実験ではありません。

指標の定義: 勝率はネット損益が正の取引の割合。Profit Factorはネット利益合計÷ネット損失絶対値で、損失ゼロなら `null` とし、`profit_factor_status` で `no_losses`（利益あり）または `no_trades_or_flat` を区別します（JSONの非標準Infinityは使いません）。最大DDの比率は各時点の過去最高資産に対する低下率で、確定足終値系列によるものとし、足内の最大逆行は含みません。ノートレード日は有効営業日のうち取引がない日で、除外日を含みません。

品質レポート（`quality.json` / `quality.md`）と `daily_comparison.csv` の内容は [5分足の品質判定](backtest-data-quality.md) を参照してください。

同一の入力バイト列・設定・コード・依存関係で `trades.csv` と `metrics.json` は再現します。実行ID/時刻・HTML内部IDは実行ごとに異なります。未コミット開発中はGit SHAだけでコードを再現できないため、Git変更状態と実装ハッシュも確認し、比較に使う版のコード・lockファイルは別途保存してください。

## ORB Exit比較

```bash
uv run --locked python -m quant_lab.backtest --compare-exits configs/comparisons/aapl_orb_exits.yaml
# 品質未確認データの診断は --diagnostic を追加（正式評価には使用不可）
```

`base_config` は比較YAMLからの相対パス、入力データと出力先は実行ディレクトリ基準です。既存の単発YAML、`--config`、旧Parquet CLI、`--compare-costs` も引き続き使え、単発でも `exit.strategy` に `fixed_stop` / `trailing_stop` / `time_exit` を指定できます。

Exit比較はYAMLに列挙した候補だけを同じ ExperimentEngine で実行します（総当たりや、Exitとコストの組合せ最適化はしません）。共通条件は ORB 30分・1株・5000 USD・1日最大1取引、Webull日本米国株モデル（委託手数料0、`fee_schedule: "2026-11-21"`、`cat_route: exchange`）、片道 spread 1 bps・slippage 2 bps。元の単発YAMLは変更せず、各実行の全有効設定を保存します。固定率候補もこの条件で再実行するため、旧条件の結果は比較基準にしません。料金は Issue #41 と同じ公表料金の固定試算で、実請求額は再現しません。仮定と公式出典は各結果に保存します。

**Exitの約定規則**（価格には共通Brokerが spread・slippage・料金を適用）:

- **固定率**: 既存挙動を維持。初期候補は損切り0.5%・利確1%。始値が損切り以下なら始値で決済し、同足で利確・損切りが両方成立したら損切りを優先します
- **トレーリング**: 初期候補は初期損切り0.5%・追従幅0.5%。実約定価格から初期ラインを置き、始値ギャップ→足内安値の順に判定します。ラインは下げず、同足の高値を過去の安値へ遡って適用しません。未決済なら当足高値で更新したラインを次足へ反映します。決済理由は `trailing_stop`
- **時間Exit**: 損切り0.5%に加え、`max_holding_minutes`（正の5分単位）または `exit_time_et`（ETの5分境界）の一方を必須指定します。保有時間は買付実約定時刻から計測し、期限以降は最初に利用可能な足の始値で決済します（始値が損切り以下なら損切り優先）。指定時刻後の新規買付は同じ始値で即時決済し、往復費用を負担します
- **共通**: 最終利用足まで保有した場合は最終足終値で `market_close` とし、日跨ぎしません。欠損足があるセッションは除外され、欠損価格の補間約定は行いません

**期間と評価の運用**: `periods.in_sample` と `periods.out_of_sample` には開始日・終了日を含む `YYYY-MM-DD` を指定し、IS終了日より後にOOSを置きます（重複・逆順は拒否）。各候補・各期間は独立した初期資金と新しい戦略インスタンスで開始し、IS/OOSは合算評価しません。OOSは候補を確定してから開き、結果を見て調整しません。なお日付分割だけではデータの未使用性は証明できません。各候補・期間で取引30件未満ならサンプル不足を警告します（30件以上でも統計的優位性は保証しません）。

**出力と指標**: `results/exit_comparison_*/` に `comparison.csv`・`comparison.json`・`comparison.html` と、入力・metadata・品質根拠のスナップショットを保存します。入力ハッシュは全実行で照合します。`runs/` の各実行には有効設定、識別情報、品質状態、除外日と、`trades.csv`、`metrics.json`、資産推移HTML、理由別・月別CSVを保存します。指標の基本定義は「結果と再現性」と同じで、比較では次が加わります。勝ちは費用控除後PnL>0、負けは<0、ゼロはどちらにも含めず勝率の分母には含めます。ネットPnLはグロス−委託手数料−SEC−FINRA−CAT−spread−slippage。理由別・月別集計は取引日のET日付で行います。
