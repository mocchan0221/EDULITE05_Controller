# EDULITE05_Controller

RobStride EduLite 05 (EL05) CAN アクチュエータを試運転・評価するためのローカル GUI ツールです。
公式 USB-CAN アダプタ(または python-can 対応のアダプタ)経由でモータを接続し、状態のグラフ表示・3D 表示・制御・パラメータ読み書きを行います。シミュレータ(`--sim`)で実機なしでも動作を確認できます。

## 使い方

起動は、目的に応じて次の 3 通りです。

| 目的 | 起動方法 |
|---|---|
| 実機(公式 USB-CAN + EL05)をつなぐ | `run.bat` |
| モータ無しで試す(本物の制御コア + シミュレータ) | `run_sim.bat` |
| 画面の見た目だけ確認する(ダミーデータ) | `run.bat --demo` |

- 起動時の出力段階は「低出力」です。最初はそのまま、速度 0.5 rad/s 程度の低速から試してください。
- 非常停止は Pause キーです。モータが有効・運転中のときは Esc でも非常停止します。

画面の見かた、安全のしくみ、各モードの操作、CLI の使い方は、[利用者マニュアル(docs/manual.md)](docs/manual.md) にあります。

## 必要なもの
- [uv](https://docs.astral.sh/uv/)(Python 3.12 は uv が自動で用意します)
  - Windows: `winget install --id=astral-sh.uv -e` または `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

## 起動方法
| 方法 | 内容 |
|---|---|
| `run.bat` をダブルクリック | 実機モード |
| `run_sim.bat` をダブルクリック | シミュレータ(`--sim`。本物の制御コア + 模擬モータ) |
| `uv run --frozen python -m edulite05 [--sim]` | コマンドラインから起動 |
| `uv run --frozen python -m edulite05.cli --help` | CLI(読み出し・運動・シーケンス。マニュアル §10) |
| `./run.sh` | Linux 用 |

初回は依存パッケージの取得(`uv sync` 相当)で時間がかかります。

## 開発
```
uv sync              # 依存インストール(dev 依存を含む)
uv run python -m pytest   # テスト(GUI テストは QT_QPA_PLATFORM=offscreen)
uv run ruff check    # Lint
```

## UI モックの確認
`run.bat --demo` で UI モック(ダミーデータで動く本番と同じ画面構成)が起動します。UI は v2 への全面再設計中です(仕様: `docs/design/ui_design.md`、タスク: `docs/design/tasks/phase0_ui_v2.md`)。
- v2 のスクリーンショットは `docs/design/ui_mock_v2/`、v1 は比較用に `docs/design/ui_mock/` に残しています(再生成: `uv run --frozen python tools/capture_ui_mock.py`)。
- 部品ギャラリー: `uv run --frozen python -m edulite05 --gallery`(全部品をダーク/ライトで確認。右上で切替)。
- 起動直後はモータ 1 が試験信号(正弦波)を実行するデモ動作をします(`--no-demo` で無効化)。`--start-offline` で未接続から始め、ヘッダーの接続ボタン → 接続 → 自動スキャンの流れを確認できます。
- 非常停止は `Pause` キー(モータが有効・運転中は `Esc` も。ラッチされ、バナーから確認つきで解除)。出力段階の高出力への切替は確認ダイアログ付きで、全モータ無効のときだけ可能です。ページ切替は `Ctrl+1` から、ナビの上から順。
- ウィンドウの大きさとテーマは `user_data/ui_state.ini` に保存されます。

## ディレクトリ構成
```
src/edulite05/
  core/         通信・プロトコル・デバイス・制御・テレメトリ(Qt 非依存)
  sim/          シミュレータ(Qt 非依存)
  plugin_api/   プラグイン API
  builtin/      組込みプラグイン(パネル・Controller)
  app/          GUI(ui = 共通部品とトークン / pages / i18n。widgets・panels は v1 の残り)
  cli/          CLI
profiles/       型式プロファイル(TOML)
assets/models/  3D モデル(el05.glb は含めていない。下の「3D モデル」)
assets/icons/   Lucide アイコン(ISC。LICENSE 同梱)
tools/          開発用スクリプト
tests/          テスト
docs/           設計・調査・一次資料
user_data/      設定・ログ(gitignore)
```
詳細は `docs/design/architecture.md` を参照してください。

## 資料の場所
`docs/README.md` から辿れます(設計、調査メモ、ベンダー一次資料)。開発規約は `CLAUDE.md`。

メーカーの一次資料(PDF・STEP・参照コード)はリポジトリに含めていません。取得元の一覧は `docs/vendor/README.md` にあります。

## 3D モデル
3D 表示に使う `assets/models/el05.glb` は、メーカー公開の STEP から変換したものなのでリポジトリに含めていません。ファイルが無い場合は簡易モデル(円柱)で表示します。

作るときは、`docs/vendor/README.md` の出典から `el05.stp` を取得して `docs/vendor/el05/` に置き、次を実行します。

```
uv run --no-project --with cascadio --with trimesh --with numpy python tools/convert_step.py
```

## ライセンス
MIT License(`LICENSE`)。`assets/icons/` の Lucide アイコンは ISC License(同梱の LICENSE)。
