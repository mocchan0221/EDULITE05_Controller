# EDULITE05_Controller — 開発規約(AI エージェント向け)

RobStride EduLite 05 (EL05) CAN アクチュエータを試運転・評価するローカル GUI ツール。

## 言語
- **ユーザーとのやりとり(このリポジトリで作業するセッションの返答・報告)はすべて日本語で行う。** サブエージェント同士の指示・報告は英語でもよい
- UI 文言・ドキュメント・コミットメッセージも日本語(既存の慣例どおり)

## まず読むもの
- 設計: `docs/design/architecture.md`(構成・層・安全設計)、`docs/design/implementation_plan.md`(Phase・タスク)
- タスク指示書: `docs/design/tasks/`(実装はここに書かれた範囲と受入基準に従う)
- 仕様: `docs/research/el05_protocol.md`(通信の正)、`el05_spec.md`、`can_transport.md`、`motorstudio_features.md`、`tech_stack.md`
- 一次資料: `docs/vendor/`(**編集禁止**。出典は `docs/vendor/README.md`)
- 文書の書き方: `docs/style.md`(文章規範と適用範囲・用語表)。**利用者向け文書・設計資料・UI 文言など、日本語の文章を書く/直すときはすべてこれに従う**

## 構成の原則
- `src/edulite05/core/` と `src/edulite05/sim/` は **Qt を import しない**(CLI・テストから単体で動くこと)
- GUI スレッドでブロッキング I/O や待機をしない。コアへの要求は Future / コールバック
- 組込みパネル・Controller もプラグイン API 経由で登録する(`src/edulite05/builtin/`)
- すべてのモータ指令は SafetyGuard を通す。出力段階(LOW/HIGH)の制限を迂回する経路を作らない
- モータ固有の数値(レンジ・既定値・パラメータ定義)はコードに直書きせず `profiles/*.toml` から読む
- UI 文字列は `tr("key")` 経由(カタログ `src/edulite05/app/i18n/ja.toml`)。既定は日本語 UI。英語は `en.toml`(キーと `{placeholder}` を ja と同じに保つ。キーを足したら両方に足す。`tests/test_i18n_en.py` が検査)。切替は設定ページ(次回起動で反映)・`--lang en`・環境変数 `EDULITE05_LANG`。幅が固定の部品(ナビ・チップ・セグメント)の英語は短く保つ
- UI v2: 仕様は `docs/design/ui_design.md`。色・寸法・フォントサイズは `src/edulite05/app/ui/tokens.py`(QSS は `ui/style.py` が生成)経由で、ページ・部品に直書きしない。共通部品は `app/ui/`、ページは `app/pages/`(`PAGES` に登録)。部品の確認は `uv run --frozen python -m edulite05 --gallery`
- 内部単位は SI(rad, rad/s, N·m, A, ℃)

## 実行・テスト
- 依存管理は `uv`。起動: `run.bat`(実機)/ `run_sim.bat`(本物のコア + シミュレータ)/ `uv run python -m edulite05 [--sim [N]] [--demo]`。画面は `app/core_bridge.py`(`CoreBridge`)が MotorService につなぐ。`--demo` は UI 開発用のダミー(`DemoBridge`)
- テスト: `uv run python -m pytest`(コミット前に全件パス必須)。GUI テストは `QT_QPA_PLATFORM=offscreen`
- プロジェクト(ソース)は共有フォルダ `\\192.168.100.103\Test\dev\EDULITE05_Controller` 上。bash では `//192.168.100.103/...` 表記
- **venv はローカルディスク**: 必ず `UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\edulite05\venv`(bash: `export UV_PROJECT_ENVIRONMENT="$LOCALAPPDATA/edulite05/venv"`)と `UV_LINK_MODE=copy` を設定してから uv を使う。run*.bat は自動で設定する。共有フォルダ上に `.venv` を作らないこと(import 40–60 s、pytest 全体 41 分かかった)
- テストの基盤(`tests/conftest.py`): GUI テストは autouse フィクスチャが (1) テスト中に作られたトップレベルのウィジェットを破棄する(pytest-qt の deleteLater はイベントループが無く配送されず、MainWindow が溜まって `theme_manager().apply()` が線形に遅くなる)、(2) 残ったスレッド(非 daemon / `edulite05-*` / `sim-*`)があれば失敗にする、(3) offscreen に OS のフォントを読み込む(無いと幅が実機より 300 px 大きく出る)、(4) `quitOnLastWindowClosed` を切る(`main()` を呼ぶテストの後で `QEventLoop` が即戻りになるのを防ぐ)。実時間待ちは `qtbot.waitUntil` で条件を待つ(`sleep` / 即時の assert にしない)。ブリッジ・MotorService・Session はテストの最後に必ず `shutdown` / `close` する
- プロジェクトが UNC パスのため、コンソールスクリプト(`uv run edulite05` 等)は使わず `uv run --frozen python -m <module>` 形式で実行する
- 重いモジュール(pyvista 等)は遅延 import にする
- 実機: 公式 USB-CAN(CH340)が COM7。机上の EL05 1 台(24 V 1 A 電源、未固定、ハンドル付き)。モータを動かす試験はユーザーの了承を得てから、出力段階 LOW の範囲で行う。`tools/camera_snapshot.py` で PC カメラから動作を目視確認できる(`docs/research/hw_bench.md`)

## コード規約
- Python 3.12、型ヒント必須、`dataclass(slots=True)` を多用、`ruff` 準拠
- コメント・docstring は日本語可。仕様由来の処理には出典を書く(例: `# el05_protocol.md §2.2`)
- 新たに判明した仕様・実機での事実は `docs/research/` の該当ファイルに追記する(チャットだけで終わらせない)

## git
- タスク単位でコミット。メッセージは `phaseN-M: 要約` 形式(日本語可)
- `user_data/`・`.venv/`・生成物はコミットしない
