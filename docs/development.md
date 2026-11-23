# 開発・検証ガイド

## 検証の分類

| 分類 | 対象 | 外部接続・副作用 |
| --- | --- | --- |
| 単体 | 計算、設定、データ品質判定など | 人工データ・一時ディレクトリを使用する |
| モック結合 | 取得・保存・CLI・レポート間の連携 | 外部API・OpenD・通知をmockし、ネットワークや実データ更新を行わない |
| 実接続 | SDK/OpenDの応答、権限、取得枠、実データの意味 | 許可された読み取り専用の取得に限り、対象・期間・取得量・費用を確認する |

実接続を伴うworkflowの手動実行は、通知・永続データ更新などの副作用も確認し、依頼範囲で許可されている場合に限る。

## CIとローカル検証

検証の正本は [CI workflow](../.github/workflows/ci.yml)。Python 3.12とNode.js 24を用意し、次を実行する。依存関係やlint規則はプロジェクト設定を参照し、この文書へ転記しない。

```bash
uv sync --locked
uv run --locked python -c "import moomoo; import plotly; import kaleido; import yfinance"
uv run --locked ruff check .
uv run --locked mypy .
uv run --locked python -m unittest discover -v
```

CIは全単体テストを実行し、skipが1件でもあれば失敗する。ローカルでも出力のskipを確認する。CIと同じ終了判定が必要な場合は、workflowの `Run unittest without skips` のPythonコードを使用する。文書のみの変更などで検証を省略する場合は、PRに対象と理由を記載し、未実施を成功とは報告しない。

変更に応じたローカル追加検証と、PRのCI成功は別に確認する。main向けPRでは現在の `CI / test` が全チェックを実行する。ブランチ保護によるrequired check設定はworkflowだけでは確定できないため、マージ時にGitHubの要求も確認する。

パッケージ構成・build設定・配布CLIを変更する場合はwheelを生成し、checkout外の隔離環境でインストール・import・該当CLIの起動を確認する。通常の計算・文書変更で毎回行う必要はない。Parquetの列名は既存の入出力スキーマとテストを正本とし、外部入力を含めた一律の改名は行わない。

## OpenD関連PRの実機確認と保留条件

- SDK/OpenD呼び出し、引数、ページング、取引セッション指定、応答の変換、時刻・価格調整・出来高単位の解釈を変更した場合は、Macの既存OpenDで実機確認する。
- 文書のみ、人工データで完結する計算・表示など、接続契約や実データの解釈に影響しない変更では実接続は不要。PRに判断理由を記載する。
- 実機確認では銘柄・期間・時間帯・調整条件・取得件数と検証結果を記録する。認証情報、個人設定、実取得データはPRやGitへ含めない。利用方針は [README](../README.md#moomooによる米国株データ取得の利用方針) とAGENTS.mdに従う。
- 実機確認が必要なのに接続先・権限・取得枠がない場合、mockの成功で代替完了しない。ローカルで可能な検証まで進め、PRをDraftにして未検証項目とMacでの確認手順・再開条件を記載する。注文や取引認証情報で解決しない。

## レビューとマージ判断

実API不要のPRは、Issueの完了条件、関連検証、必要なCI、レビュー指摘の対応が揃った時点でマージ判断へ進める。必要な実機確認が残るPR、必須チェックが失敗したPR、未対応の重要な指摘があるPRは保留する。レビューの解決状態と修正・検証の実態を照合し、返信・スレッド解決は依頼範囲で行う。

## 方針の参照先

共通の不変条件はAGENTS.md、領域別の手順は該当する開発文書、機械的なチェックはCIで管理する。規則変更時は参照先との整合を確認する。

| 公式資料 | 採用する方針 | 公開日・更新日 |
| --- | --- | --- |
| [OpenAI: AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md) | 簡潔な規則と適用範囲、CIとの役割分担 | 本文で確認できず |
| [OpenAI: CodexのGitHubレビュー](https://learn.chatgpt.com/docs/third-party/github) | 影響のある固有規則を優先し、機械的チェックはCIへ委ねる | 本文で確認できず |
| [AGENTS.md公式](https://agents.md/) | 人間向けREADMEを補完し、必要な領域へ規則を配置する | 本文で確認できず |
| [GitHub: PRテンプレート](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/creating-a-pull-request-template-for-your-repository) | `.github/pull_request_template.md`への配置とIssue・変更内容の提示 | 本文で確認できず |
| [GitHub: IssueとPRの関連付け](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue) | 完了するIssueへのclosing keyword | 本文で確認できず |
| [GitHub CLI: gh pr create](https://cli.github.com/manual/gh_pr_create) | `--fill`はcommit情報による補完。必要な本文は`--body-file`等で明示する | 本文で確認できず |

全資料の確認日: 2026-11-23。確認日を公開日・更新日とは扱わない。これらは一般的な運用指針であり、検証コマンドや実機確認条件はこのリポジトリのCI・接続契約に合わせた方針である。
