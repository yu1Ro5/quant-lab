# 5分足の品質判定（Issue #37）

## 正式評価と診断

`python -m quant_lab.backtest --config ...`は毎回Parquetとmetadataを読み直し、
全必須検査が`pass`の場合だけ正式評価する。`fail`と`unknown`はどちらも遮断する。
従来のYAMLは変更なしで読み込めるが、未検証データがそのまま正式評価になる従来の挙動は廃止した。
旧日本株MVPの位置引数CLIは従来の簡易シミュレーション用であり、この正式評価ゲートの対象ではない。

```bash
# 品質レポートのみ（OpenD不要）。検査完了・ready=trueは0、falseは2、入力読込等の失敗は1。
uv run python -m quant_lab.backtest --config configs/experiments/aapl_orb_30m.yaml --quality-only

# 正式評価。品質不適合ならレポートを残して終了コード1。取引結果は生成しない。
uv run python -m quant_lab.backtest --config configs/experiments/aapl_orb_30m.yaml

# 明示的な診断。必ずprovisional/暫定表示を残す。
uv run python -m quant_lab.backtest --config configs/experiments/aapl_orb_30m.yaml --diagnostic
```

元データが別worktreeにある場合は個人設定を`results/local.yaml`へコピーし、
`data.path`を保存済みParquetの絶対パスへ変更する。参照・根拠も含む相対パスはカレントディレクトリ基準。
SDK・Pythonの再インストールや相場データの再取得は、保存データの検証には不要。

診断でも、schema・OHLCV・日時重複/順序・metadataの明白な矛盾・指定した根拠ファイルの読込失敗は拒否する。
欠損営業日は全日除外する。時間外/休日の足は警告して除外し、正式評価のcalendar検査は失敗になる。
欠損足の補間、ゼロ出来高の自動除去、小数出来高の整数化、日足との差を消す価格修正は行わない。

## 検査と判定

| 検査 | 検証内容 |
| --- | --- |
| schema | 必須6列、重複列、数値型（bool/文字列数値は不可）。日時はdatetime型か解釈可能な文字列 |
| ohlcv | 欠損・非有限・非正価格・負出来高・OHLC大小関係。ゼロ/小数出来高は件数を記録 |
| timestamps | 入力順序・重複。awareは瞬間をUTC変換、naiveは設定timezoneでlocalize。DSTの曖昧/存在しない時刻は拒否 |
| metadata | Parquet SHA-256、銘柄、K_5M、NONE/QFQ/HFQ、regular、件数、先頭/末尾、存在する保存TZ・要求/有効期間・銘柄列との整合 |
| calendar | exchange-calendarsのXNYS、東部09:30〜16:00、DST、休日、13:00等の短縮立会、時間外/休日足 |
| session_grid | 期間内の全営業日。78本/短縮42本、全欠損時刻、グリッド外、最初6本、最終足。全欠損日も検出 |
| timestamp_semantics等 | 入力に結び付くレビュー済みのデータ契約の根拠（下記） |
| finalization | 確定足取得方式の根拠、および検証時点より未来/未確定のバーがないこと |
| daily_comparison | 全OHLCVの日別集計、参照値、生差分/相対差、片側欠損日、比較条件・許容差 |

`backtest_ready`は全検査の論理積。入力metadataの`backtest_ready`は判定に採用しない。
metadata整合性と提供元仕様の実証は別の検査である。metadataがない/必要キー不足なら`unknown`。
構造検査の成功だけではreadyにならない。完全セッションだけで診断を続けても、元入力の欠損検査は失敗のまま残る。
セッション構築・足の開始/終了への正規化・ORBは既存実装を再利用している。

## 根拠ファイルの契約と信頼境界

任意の`quality`セクションを追加できる（省略時`mode: formal`）。
指定したファイルがない/壊れている場合は失敗として記録する。

```yaml
quality:
  mode: formal                    # formal / diagnostic のみ
  evidence_path: results/evidence.json
  daily_reference_path: results/daily_reference.csv
  daily_metadata_path: results/daily_reference_metadata.json
```

`evidence.json`は**根拠を実際にレビューした担当者が作成するローカルの信頼入力**。
プログラムは出典内容の真偽を自動証明しない。自己申告metadataやURLだけから根拠を自動生成してpassにしてはならない。
提供元文書・サポート回答・取得コード・生応答との照合などの具体的な観測と確認者を残す。
不足する項目は省略または`status: unknown`とする。下記は形式例であり実データの承認済み証拠ではない。

```json
{
  "dataset_sha256": "対象ParquetのSHA-256",
  "metadata_sha256": "隣接metadataのSHA-256",
  "assertions": {
    "timestamp_semantics": {
      "status": "unknown",
      "value": "end",
      "source": "提供元の仕様URLまたはサポート回答の所在",
      "observation": "終了ラベル仮説は実測と整合するが、公式の明記は確認できていない",
      "reviewed_by": "確認担当者",
      "reviewed_at": "確認日時"
    }
  }
}
```

次の全assertionが必要。`status: pass`、期待値、空でない`source/observation/reviewed_by/reviewed_at`、
入力とmetadata両方のハッシュ一致を必要とする。入力改訂時は根拠を見直す。
`fail`は失敗のまま保持する。`pass`という単独フラグや元のreadyフラグでは通過しない。

| assertion | 期待値 | 確認対象 |
| --- | --- | --- |
| timestamp_semantics | 設定のstart/end | 足ラベルと区間の対応 |
| native_timezone | America/New_York | 米国株の元時刻とDST対応 |
| adjustment | 設定のnone/forward/backward | 元の取得条件・調整方式 |
| volume_unit | shares | 株数単位・精度・5分足/日足での同一性 |
| session_scope | regular | 通常立会の定義と取得条件 |
| finalization | closed_bars_only | 未確定足の除外方法・取得時刻 |
| provenance | reviewed_acquisition | 取得コード・生応答・保存Parquetの対応 |

日足CSVの必須列は`date,open,high,low,close,volume`。dateはタイムゾーンなしの東部取引日。
独立提供元を推奨し、moomoo K_DAYも参照として指定可能だが独立ソースとは区別する。
5分足は通常立会内だけを集計する。欠損日の部分集計もcomplete=falseとして残し、一致扱いにはしない。
参照にない日・参照だけの日はouter joinで残す。参照がない場合は架空の値を作らずunknownとする。

日足metadataは`dataset_sha256`（参照CSV）、`symbol`、`timeframe: "1d"`、
`adjustment`、`session: "regular"`、`volume_unit: "shares"`、`timezone: "America/New_York"`、
`source`、`observation`、`reviewed_by`、`reviewed_at`を必要とする。
これらが揃い、5分足の時刻/セッション/調整/出来高/出所の根拠も通過した場合だけ比較条件を検証済みとする。
不一致はfail、不足はunknown。条件不足でも数値差は記録するが、原因を断定しない。
`prepost=False`や`auto_adjust=False`だけでオークション・出来高配信範囲・分割調整まで同一とは認定しない。

許容差の既定値は価格・出来高とも絶対差0。非ゼロにするには日足metadataに
`tolerance: {price_absolute: ..., volume_absolute: ..., reason: ..., reviewed_by: ...}`を指定する。
理由のない緩和はunknown。NaN/Infinity/負値/boolは失敗。単なる数値型floatであることは任意の許容差の根拠にならない。
提供元の表示精度や丸め規則が確認できたときに根拠とともに指定し、差分そのものは常に残す。

## 出力と再現性

既存の`results/<実験名>_<UTC時刻>_<ID>/`へ保存する。過去runを上書きしない。
`--quality-only`および正式評価拒否時にも`config.yaml`と次のファイルを残す。

- `quality.json`: 検証時刻、入力/metadata/参照ハッシュ、適用仕様・依存バージョン、根拠、全検査の三状態、日別欠損、除外日、警告/エラー、判定理由。
- `quality.md`: 検査表と根拠、日別グリッド/除外日/警告、日足比較要約。
- `daily_comparison.csv`: 全日・全OHLCVの集計値/参照値/生差分/相対差/complete/一致状況。
- `BLOCKED`: 実行が品質ゲートで拒否された場合。取引CSV/成績HTMLは生成しない。

同一の過去データ・metadata・根拠・参照・設定・依存バージョンでは検査判定が同じになる。
実行日時とrun IDは毎回変わる。未確定足かどうかの判定は記録された検証時点に依存する。
診断の`metrics.json`と`run_metadata.json`には`evaluation_status: provisional`、
`backtest_ready`と根拠を記録し、HTMLにも「暫定・診断専用」と警告を表示する。
診断を指定した結果は、品質が通っていてもprovisionalとする。

## 2026-11-20 JSTのローカル調査

最新mainの`24e64d5`を基準に、既存OpenD `127.0.0.1:11111`と既存Python環境を使用した。
取得処理の再実装・再インストール・取引Contextや注文操作は行っていない。

### 時刻・調整・出来高

[公式履歴K線API](https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-history-kline.html)では
米国株のtime_keyはUS Eastern、volumeはfloat、extended_time=Falseは時間外を含めない。
[調整種別](https://openapi.moomoo.com/moomoo-api-doc/en/quote/quote.html#autype)では
NONEはActual、QFQはforward、HFQはbackward。既存取得コードはNONEを明示している。
SDKのnaive文字列は東部時間としてlocalizeしDSTを適用、保存ParquetはUTC-awareである。
保存生応答の全OHLCVと時刻変換がParquet全件に一致することも確認した。

11月18日の読取専用SDK再照会では1分足390本（09:31〜16:00）、5分足78本（09:35〜16:00）。
1分足を右閉区間・終了ラベルで5本ずつ集計すると、全78本のOHLCが完全一致し、
Volumeの最大絶対差は浮動小数の加算差約9.31e-10。
**09:35は09:30〜09:35の終了ラベルという解釈を強く支持する観測**だが、
履歴API資料と公式Skill内でstart/endの明記は確認できなかった。したがってtimestamp_semanticsはunknownを維持する。
5分足の正式な確定配信時刻・訂正保証も未確認。既存収集処理は引け15分後まで待ち、
今回の保存対象は取得時より前に終了したセッションだけである。

小数出来高を保存した足は5,850本。float型の公式記載は単位・配信対象の同一性までは証明しないため、
volume_unitはunknown。株数単位の慣例や一定倍率による補正を仮定しない。
MCPの同日5分足照会は正常ステータスだったが空配列で、実データ取得成功とは扱っていない。
SDKの行情枠確認後、同一銘柄1日分の1分足/5分足/日足を直列照会し、全ページ完了を確認した。

### 日足比較と暫定ORB

保存済み5分足17,316本、2026-01-02〜11-18の222セッションは構造検査成功、欠損・除外日0。
参照条件の完全な同一性は未確認のため、下記の比較は数値差の観測であり正式照合成功ではない。

| 参照 | OHLCの最大絶対差（USD） | Volume差 |
| --- | --- | --- |
| 保存済みmoomoo K_DAY | O/H/C=0、L=0.04（4月20日の1日） | 222/222日、最大78,608,713 |
| 保存済みYahoo日足 | O=0.00501465、H=0.00500488、L=0.03999146、C=0.00501099 | 222/222日、最大78,608,763 |
| 11月18日のSDK再照会K_DAY | O/H/L/C=0 | 日足が5分足合計より7,596,629.568818多い |

許容差は0のまま。4月20日のLow差と出来高差の原因は未確定。
市場範囲・取引条件・オークション・訂正・単位の違いは候補であり、特定済み原因として扱わない。
[公式FAQ Q7](https://openapi.moomoo.com/moomoo-api-doc/en/qa/quote.html)は
APIのリアルタイム米国株行情がNasdaqとNYSE Arcaの上流を組み合わせると説明している。
これは配信範囲を調べる手掛かりだが、履歴K_5MとK_DAYの今回の差を説明する直接の根拠ではない。
提供元へ同一日の生応答・集計差分を添えて確認する必要がある。

診断ORBは元の設定・同一Parquetで、108取引、勝率40.7407%、グロス+$16.6613、
コスト$34.5250、ネット−$17.8637、Profit Factor 0.826313、除外0日を再現した。
正式な再評価は未実施。`backtest_ready=false`の理由は時刻意味・出来高単位・日足照合がunknownであること。

### ORBとAcceptance Criteria

確定09:30〜10:00の6本でレンジを作り、次の足の確定終値で突破を確認し、次足始値で約定する。
`signal_time`（前足終了）と`entry_time`（次足開始）は同じ境界時刻になり得る。
start/endラベル同値、足内Highだけの突破を拒否、未来足変更で過去エントリー不変、
当日引け決済、最終足欠損時は全日除外をfixtureで確認する。

| #37 AC | 実装・確認状況 |
| --- | --- |
| 時刻/TZ/調整/単位の根拠 | 公式記述と観測を文書化。不明なラベル正式定義・単位はunknown維持 |
| DST/短縮/休日/欠損/重複/OHLC | 自動検査とfixtureテスト |
| 日足比較レポート | 数値差/条件/許容差/unknownをJSON・CSV・Markdownで出力 |
| ORBの時間整合性 | 人工データで確認。既存約定モデルは変更なし |
| 再現可能な品質判定 | 同じ入力ハッシュ/条件で再検査。readyフラグを流用しない |
| 未検証の正式評価禁止 | default formalゲート、明示診断のみprovisional |
| テスト・README | 再実行方法と根拠契約を記載。検証コマンドは下記 |

```bash
uv run python -m unittest discover -v
uv run ruff check .
uv run mypy --no-incremental .
git diff --check
```

今回の検証結果はunittest 201件成功・skip 0、Ruff成功、mypy 73 source files成功、`git diff --check`成功。

既存環境を再利用する場合は`UV_PROJECT_ENVIRONMENT=/既存repo/.venv`と`uv run --no-sync`、
別worktreeのコードを確実に使うには`PYTHONPATH=src`を併用できる。
uvキャッシュ権限エラー時だけAGENTS.mdに従い`UV_CACHE_DIR=/tmp/quant-lab-uv-cache`を追加する。
検証成果物・取得データ・個人設定は`results/`に保存しGitへ追加しない。

## 追加・変更ファイル

| 区分 | ファイル |
| --- | --- |
| 追加・品質検証 | `src/quant_lab/backtest/quality.py`, `src/quant_lab/backtest/quality_daily.py` |
| 変更・連携 | `src/quant_lab/backtest/config.py`, `src/quant_lab/backtest/runner.py`, `src/quant_lab/backtest/cli.py`, `src/quant_lab/backtest/report.py` |
| 追加・テスト | `tests/backtest/test_quality.py`, `tests/backtest/test_quality_gate.py`, `tests/backtest/test_orb_timing.py` |
| 変更・回帰テスト | `tests/backtest/test_runner.py`, `tests/backtest/test_experiment_e2e.py`（明示診断へ変更） |
| 変更・設定/文書 | `configs/experiments/aapl_orb_30m.yaml`, `README.md`, `docs/moomoo-intraday.md` |
| 追加・文書 | `docs/backtest-data-quality.md` |

既存の取得パイプライン・ORB/約定ロジック・依存関係は変更していない。
実相場と個人用設定はGit対象外の`results/`にのみ出力した。
