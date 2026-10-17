# Phase 0 追加タスク — UI v2 全面再設計の実装

> 言語: ユーザーへの返答・報告は日本語で行う(CLAUDE.md「言語」)。サブエージェント間のやりとりは英語でもよい。

作成: Opus / 2026-10-01。実装担当: Sonnet。**仕様は `docs/design/ui_design.md`(v2)。これが唯一の正**。
迷ったら ui_design.md §2 の原則と §13 の受入チェックリストに従う。根拠資料: `docs/research/ui_design_guidelines.md`, `ui_domain_benchmarks.md`。

## 方針
- 機能は v1 モックと同じ。**見せ方・配置・操作の流れを作り直す**
- 再利用する: `app/bridge.py`(§11 の変更を加える)、`app/viewmodels.py`、`app/demo_bridge.py`(状態モデルに合わせて改修)、`app/demo_params.py`、`core/control/params.py`、`app/i18n/`、`app/model3d.py`、`app/ports.py`、3D/グラフの描画ロジック
- 作り直す: `app/theme.py` → `app/ui/tokens.py` + `app/ui/style.py`(QSS 生成)、`app/widgets/*` → `app/ui/*` の部品、`app/panels/*` → `app/pages/*`、`app/main_window.py`(シェル)。旧ファイルは移行後に削除(残骸を残さない)
- ページ・部品の中で**色・寸法・フォントサイズを直書きしない**(すべて tokens 経由)。文字列はすべて `tr()`(ja.toml)
- 実行は必ず `uv run --frozen python -m ...`(CLAUDE.md)。各段階で `python -m pytest` と `python -m ruff check .` を通してからコミット

---

## 段階 1: 土台(担当 F、1 エージェント)

1. **トークンとスタイル**: `app/ui/tokens.py`(ui_design.md §7 の全トークンを dark/light で。モータ色 §7.2、文字 §7.3、寸法 §7.4)、`app/ui/style.py`(トークンからアプリ全体の QSS を生成。テーマ切替で再適用)
2. **アイコン**: Lucide(ISC)の SVG を `assets/icons/` に必要分だけ取得(例: `https://unpkg.com/lucide-static@<pin>/icons/<name>.svg` を curl。バージョンを固定)し、`assets/icons/LICENSE` を同梱。`app/ui/icon.py` でテーマの文字色に着色した QIcon を生成(QSvgRenderer)。使うアイコン: ナビ(gauge, sliders-horizontal, stethoscope, wrench, settings)、状態(circle, circle-dot, play, triangle-alert, octagon-x)、操作(power, play, square, rotate-ccw, chevron-down/right, search, ellipsis, plug, refresh-cw, circle-dot(記録), pause, hand(押下保持)、info, lock)など
3. **部品** `app/ui/`: ui_design.md §6 の表のすべて(Button 各 variant、HoldButton、Segmented、StatusBadge、MotorChip、InfoBar、Toast、ParamRow(旧 ParamForm を作り直し。dataclass metadata から自動生成する機能は維持)、ReadoutCell(ゲージ付き)、Section、Expander、EmptyState、NavRail、ConfirmDialog)
4. **シェル** `app/main_window.py`: ヘッダー(§4: アプリ名・接続ボタン・モータチップ・出力段階セグメント・非常停止)、バナー領域(優先順位付きで複数積める)、NavRail + `QStackedWidget`、ステータスバー、ヘッダー右端 `⋯` メニュー。ページは `app/pages/` の各クラスを登録する方式(`app/pages/__init__.py` にページ一覧)。段階 1 では各ページを**スタブ**(見出しと「準備中」の EmptyState)にしておく
5. **Bridge / ViewModel の変更**: ui_design.md §11(ui_state、enable(ids, run_mode)/disable、estop ラッチ・解除、fault_details)。DemoBridge を状態モデルどおりに改修(未接続から始めるオプション `--start-offline` も追加し、接続→自動スキャンの流れを再現できるように)。非常停止 → ラッチ → LOW へ戻す、を実装
6. **部品ギャラリー**: `python -m edulite05 --gallery` で全部品をダーク/ライト並べて表示するウィンドウ。`tools/capture_ui_mock.py` を更新し、`docs/design/ui_mock_v2/` にギャラリーとシェルのスクリーンショット(1536×912)を保存(**v1 の画像 `docs/design/ui_mock/` は比較用に残す**)
7. **キーボード**: §9 のうちシェルが持つもの(Esc/Pause、Ctrl+1..5)
8. テスト: トークン(全トークンが dark/light 両方にある)、部品の基本動作(HoldButton の release/leave/focus-out、Segmented の無効化と理由ツールチップ)、シェルの起動・ページ切替・非常停止ラッチ/解除・バナー表示

段階 1 の完了報告には、ギャラリーとシェルのスクリーンショットのパスを含めること(Opus がレビューしてから段階 2 に進む)。

## 段階 2: ページ(担当 P1 / P2 / P3、並列。各自の担当ファイルだけを編集)

- **P1 運転ページ** `app/pages/operate/`(最大): ui_design.md §5.1 のすべて(読み値ストリップ、モニタ(グラフ・3D 切替、プリセット、凡例行、最新値タグ、同期カーソル、記録)、イベント行、インスペクタ(状態別の主ボタン、モード選択、目標、制限の折りたたみ、MIT プリセット、位置の動き方、JOG 押下保持、試験信号、2 台連動)、未接続/0 台の空状態)と §5.6 接続ポップオーバー(ヘッダーの接続ボタンから開く部品は `app/pages/operate/connection_popover.py` に置き、シェルからはそれを呼ぶだけにする)
- **P2 パラメータ・診断ページ** `app/pages/params.py`, `app/pages/diagnostics/`: §5.2、§5.3
- **P3 保守・設定ページ + ダイアログ** `app/pages/maintenance.py`, `app/pages/settings.py`, `app/ui/dialogs/`(HIGH 切替 §5.7、非常停止解除、保守の各ダイアログ、Flash 保存確認): §5.4、§5.5、§5.7

各担当: 自分のページのテスト(pytest-qt)と、スクリーンショット(ダーク 1536×912 + 主要な状態。ライト 1 枚)を `docs/design/ui_mock_v2/` に追加。シェル(`main_window.py`)・部品(`app/ui/*`)の変更が必要になったら**自分では直さず**、最終報告に「必要な変更」として書く(Opus が段階 3 でまとめて反映を指示する)。

## 段階 3: 統合とレビュー(Opus → 必要なら Sonnet に差し戻し)
ui_design.md §13 のチェックリストで確認。
