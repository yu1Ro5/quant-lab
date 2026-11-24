# moomoo OpenAPI 動作確認

以下は初回の環境制約を記録した履歴です。その後、MacでOpenD接続、日足221件の取得、
HTML/PNG生成、2010年までの少量遡及取得に成功しています。
最新の検証結果は [AAPL日足の取得・保存・チャート](moomoo-daily.md) を参照してください。

## 2026-11-18 の実測結果

**判定: 環境制約により検証中断。取得成功ではなく、認証待ち段階にも未到達。**

このチャットに接続されている実行環境はローカルMac (`Darwin arm64`) であり、
Codex CloudのLinux実行環境ではない。Cloudシェルは利用できなかった。
したがって以下の結果からCloudでのOpenD起動可否は結論できない。

| 段階 | 結果と根拠 |
| --- | --- |
| リポジトリ | `origin` は `https://github.com/yu1Ro5/quant-lab.git`。開始時の作業ツリーはクリーン。`AGENTS.md`、依存定義、既存取得処理、バックテスト読込処理、CIを確認 |
| Python / uv | 既存 `.venv` のPython 3.12.13、uv 0.10.12を確認 |
| 依存同期 | `uv sync --locked --python 3.12` は既定キャッシュへの書込権限で失敗。指示に従い `UV_CACHE_DIR=/tmp/quant-lab-uv-cache` で再実行したところ、`files.pythonhosted.org` への通信を環境の許可リストが拒否。オフライン再実行も `curl-cffi==0.15.0` のキャッシュ不足で失敗 |
| 公式SDK導入 | `UV_CACHE_DIR=/tmp/quant-lab-uv-cache uv pip install --python .venv/bin/python moomoo-api` は `pypi.org` への通信拒否で失敗。SDKは未導入 |
| Linux版OpenD入手 | 公式資料とダウンロードページはWeb参照で確認。シェルから `https://www.moomoo.com/download/OpenAPI` を取得する操作は同ホストへの通信拒否で失敗。パッケージ取得・展開・実行は未実施 |
| Linux実行基盤 | ホストはMac。`docker info` もDockerソケットへの権限エラー。Linux版を実行できる基盤を確認できず |
| ローカル11111 | Pythonの `socket.create_connection(('127.0.0.1', 11111), timeout=3)` は `PermissionError: [Errno 1] Operation not permitted`。APIハンドシェイク未実施。接続拒否・OpenD不在・認証失敗とは判定しない。`lsof` に待受表示はなかったが、他プロセスの可視性は保証されない |
| moomooログイン | 未実施。認証情報は要求・入力・保存していない |
| US.AAPL 5分足 | 未取得。実データのParquet、件数、時間帯、欠損、OHLCV妥当性は未検証 |

実行した検証:

| コマンド | 結果 |
| --- | --- |
| `.venv/bin/python -m unittest discover -v` | `Ran 101 tests` / `FAILED (errors=1)`。100件成功、`tests.stock.test_fetch` が `ModuleNotFoundError: No module named 'yfinance'` で読込失敗。株式取得テスト群は実行できていない |
| `.venv/bin/ruff check .` | `All checks passed!` |
| `.venv/bin/mypy .` | `yfinance` の `import-not-found` 1件で失敗 |
| `git diff --check` | 成功 |

依存同期失敗のため、上記テストは既存 `.venv` の実行結果であり、lock済みの
完全な環境でのテスト成功を示さない。既存テスト・取得機能・依存定義は変更していない。
取得成功後の手順確定、取得スクリプトとテスト追加は未実施。

## 再開する環境

Cloudでの検証には、実際のCodex Cloud Linuxワーカーと、Pythonパッケージ配布元・
公式OpenD配布元・OpenDの接続先への通信が可能な環境が必要。
Web検索で資料が読めても、シェルやOpenDの通信が許可されているとは限らない。
11111への接続も、実行環境のローカルソケット制限を解消してから再検証する。

認証を伴う最初の確認は、利用者自身が操作できるローカルMacで行うと切り分けやすい。
公式のMac版OpenDを使用し、対話ログインと追加認証を完了し、同じMacからSDKで接続する。
Macでの成功をCloudでの成功として扱わない。MacのlocalhostはCloudのlocalhostとは異なる。

## 再実行手順（以下は未検証の再開手順）

### 依存環境

まず既存機能が正常な環境を確立する。

```bash
uv sync --locked --python 3.12
uv run python -m unittest discover -v
uv run ruff check .
uv run mypy .
```

`AGENTS.md` に従い、uvのキャッシュへの書込権限エラーが発生した場合だけ、
該当コマンドを `UV_CACHE_DIR=/tmp/quant-lab-uv-cache uv ...` で再実行する。
通信拒否やキャッシュ不足をコードの失敗として扱わない。

現在はSDKも`pyproject.toml`と`uv.lock`に含まれる。依存同期後に読み込みを確認できる。

```bash
uv run --locked python -c 'from importlib.metadata import version; import moomoo; print(version("moomoo-api"))'
```

実行に成功したらSDKのバージョンも記録する。依存を変更する際は既存依存との互換性を確認し、
`pyproject.toml` と `uv.lock` を同時に更新し、既存テストを再実行する。

### OpenD入手・起動・認証

1. [公式ダウンロード](https://www.moomoo.com/download/OpenAPI)から、対象OSの
   **moomooのコマンドラインOpenD**を選択する。Linux版とMac版、GUI版を取り違えない。
2. リポジトリ外の利用者専用ディレクトリへ展開する。Linuxでは `file ./OpenD` で
   実行形式とCPUを確認し、実行環境と一致することを確認する。
3. 同梱 `OpenD.xml` と `Appdata.dat` を保持する。API待受は `127.0.0.1:11111` に限定し、
   不要なTelnet・WebSocket待受を有効にしない。
4. 展開ディレクトリでLinuxなら `./OpenD`、Macなら
   `./OpenD.app/Contents/MacOS/OpenD` を利用者のプライベートな端末から起動する。
   実ファイル名は配布パッケージを確認する。
5. 利用者自身が対話プロンプトでログインし、必要な追加認証を完了する。
   最新版の認証方式は同梱ヘルプと公式資料も確認する。

[公式コマンドライン資料](https://openapi.moomoo.com/moomoo-api-doc/en/opend/opend-cmd.html)では
10.10以降は対話ログインが案内されている。古いXMLの説明を根拠にID・パスワードを
設定ファイルへ書き込まない。パスワード記憶は選択しない。

ID・パスワード・追加認証コードはチャット、コード、コマンド引数、シェル履歴、
ログ、Gitへ保存しない。認証時の端末録画・ログ転送を無効にし、OpenDの設定・ログ・
認証状態ファイルはリポジトリ外に置く。環境変数に置くだけではOpenDが読み込むとは
限らないため、未確認の認証用環境変数は使用しない。
Cloudで安全な対話入力が提供されない場合はログインを中断し、Macで実施する。

### 接続・取得・検証の順序

次の各段階を独立に記録する。TCP接続成功だけではSDK接続やログイン成功としない。

1. SDK導入後、TCP接続が可能か確認する。
2. `moomoo.OpenQuoteContext(host='127.0.0.1', port=11111)` を作成し、
   `get_global_state()` の戻り値と行情ログイン状態を確認する。使用後は必ず `close()` する。
   SDK処理が応答しない場合に備えて、プロセス単位の実行時間制限も設ける。
3. 行情APIのみを利用する。取引Context作成、取引アンロック、発注APIは使用しない。
4. `request_trading_days(code='US.AAPL', ...)` の取引日一覧を使い、米国東部時間の
   当日を除いた直近5取引日を選ぶ。単純な平日計算にせず、祝日・短縮取引日を扱う。
   当日は終了済みでも除外する保守的な定義とし、採用した5日を記録する。
5. `request_history_kline` で `US.AAPL`、`KLType.K_5M`、`AuType.NONE`、
   `extended_time=False` を指定し、対象日の範囲を取得する。
   `page_req_key` がなくなるまでページ取得し、各応答の `RET_OK` を確認する。
   権限・履歴枠・頻度制限エラーを空データの成功として扱わない。
6. 米国株の `time_key` は米国東部時間として解釈し、`America/New_York` を付与して
   UTCへ変換する。naive日時をそのままUTCとして読ませない。
7. `datetime, open, high, low, close, volume` の6列へ変換し、既存の
   `quant_lab.backtest.data.validate_data` で欠損、重複、有限値、正の価格、非負出来高、
   high/lowとopen/closeの大小関係を検証する。
8. 日別に、通常日は09:30以上16:00未満、短縮日は公式日程の終了時刻未満の
   5分間隔と比較し、欠損・余分な時刻・日付ごとの件数を記録する。
   通常日は78本、5日すべて通常日なら390本が比較基準。欠損を補完して成功にしない。
9. 全検証成功時だけ `data/stock/` 以下へParquet保存し、既存の
   `quant_lab.backtest.data.load_parquet` で読み戻す。保存後の件数・最初と最後の時刻・
   採用した5日・タイムゾーン・調整方式・SDK/OpenDバージョンも記録する。

API仕様の根拠:

- [履歴K線・ページング・米国東部時間](https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-history-kline.html)
- [取引日カレンダー](https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-trading-days.html)

実データ取得後に、この手順を再実行可能な取得モジュールへまとめ、ページング、
権限エラー、接続失敗、DST、祝日・短縮日、欠損・重複・不正OHLCV、Parquet読戻しの
テストを追加する。mockによるテスト成功と実データ取得成功は区別する。

## 利用方針

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


Codex CloudにはMacのSkills・MCP設定・認証状態は引き継がれません。Cloudの `127.0.0.1` はCloud自身を指すため、MacのOpenDには接続できません。Cloudでは人工データやモックを用いて、取得後の処理とバックテストを開発・検証します。MCPをCloudから使用する構成は、別途設定・認証・接続確認が必要であり、現時点では未検証です。

### 読み取り専用での利用

MCPのOAuthスコープは `quote:read` のみに限定し、`quote:write`、`trade:read`、`trade:write` は許可しません。Codex側でも、確認済みの読み取り専用相場ツールだけを `enabled_tools` で許可します。ツール一覧に注文用ツールが表示されることと、その操作権限があることは別です。

公式Skillsには注文用コードとOpenDのインストール手順も含まれます。MCPのスコープ制限はSDKには適用されないため、Skillsでは相場取得だけを指定し、本番・模擬のいずれも注文の送信・訂正・取消や取引のアンロックを行いません。既存OpenD・SDKの再インストールや初期化も行いません。

APIキー、OAuthトークン、パスワード、実取得データはGitへコミットしません。取得データを比較するときは、銘柄・日付・時間帯・価格調整条件・出来高の単位を揃え、取得できなかった項目は未検証として扱います。

公式資料：

- [moomoo OpenD Skills](https://openapi.moomoo.com/moomoo-api-doc/jp/intro/ai.html)
- [moomoo MCP概要](https://open.moomoo.com/mcp-docs/overview)
- [moomoo MCP認証・スコープ](https://open.moomoo.com/mcp-docs/authentication)
- [Codex Skills](https://developers.openai.com/codex/skills/)
- [Codex MCP設定](https://developers.openai.com/codex/mcp/)
