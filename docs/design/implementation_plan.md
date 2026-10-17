# EL05 コントローラ GUI — 実装計画

- 状態: **v0.2 (2026-10-01)** — ユーザー回答を反映。Phase 0 着手
- 前提: `docs/design/architecture.md`、未決事項は `docs/design/open_questions.md`

## 0. 進め方

### 役割分担
| 作業 | 担当モデル | 具体的な中身 |
|---|---|---|
| 要件整理・仕様解釈・設計・インタフェース定義 | **Opus 5.5** | 各 Phase の前に「タスク指示書」(インタフェース・受入基準・参照資料の箇所)を書く |
| コーディング・テスト作成 | **Sonnet 5.5** | 指示書どおりに実装し、テストを通す。独立したタスクは並列エージェントで |
| Phase 終了時のレビュー | **Opus 5.5** | 仕様(el05_protocol.md)との突合せ、安全面・スレッド安全性の確認、指摘は Sonnet に差し戻す |
| 実機試験の結果解析・不具合原因調査 | **Opus 5.5** | ログ(生フレーム)から原因を切り分け、修正方針を決める |

例外: 数十行程度の修正や、指示を書くほうが手間な小修正は Opus が直接行う場合がある(その都度報告)。

### 各 Phase の共通ルール
1. Opus が指示書 `docs/design/tasks/phaseN_*.md` を作る(何を・どこに・受入基準・参照すべき資料の節)
2. Sonnet が実装 → `uv run pytest` がすべて通ることを必須とする
3. Opus がレビュー → 差し戻し or 完了
4. 仕様上の新事実(実機で判明したこと等)は **必ず `docs/research/` に追記**(チャットだけで終わらせない)
5. 実機チェックポイント(HW-n)ではユーザーに試験手順を渡し、結果をもらう

---

## Phase 0: 土台づくり + 技術スパイク

| # | タスク | 担当 | 成果物 / 受入基準 |
|---|---|---|---|
| 0-1 | プロジェクト雛形 | Sonnet | `pyproject.toml`(依存固定)、`uv.lock`、`src/edulite05/` の空パッケージ、`run.bat`/`run_sim.bat`/`run.sh`、`.gitignore`、`README.md`、`CLAUDE.md` |
| 0-2 | GUI スタック互換確認 | Sonnet | PySide6 6.11 + pyqtgraph 0.14 + pyvistaqt で「1 kHz の sin を 8ch 描画 + GLB を回転表示」する使い捨てデモが 60 fps 近くで動く。不具合があればバージョンを調整して記録(tech_stack.md の R-リスク検証) |
| 0-3 | UNC パス上での起動確認 | Sonnet | `\\192.168.100.103\...` 上で `run.bat` ダブルクリック起動できる(`pushd` で UNC 対策、`UV_LINK_MODE=copy`)。`.venv` は**共有フォルダ上**(ユーザー決定)。起動時間を記録し、遅ければ `UV_PROJECT_ENVIRONMENT` でローカルへ移す手順を README に書く |
| 0-4 | STEP → GLB 変換 | Sonnet | `tools/convert_step.py`:部品名デコード、ロータ/ステータ分割、回転軸を原点へ移動、ねじ削除/簡略化 → `assets/models/el05.glb`(< 2 MB 目安)。手順を `docs/research/el05_model.md` に記録 |

| 0-5 | **UI モック** | Sonnet | 本番と同じ `app/` 構成で MainWindow・全ドック・ツールバー(E-STOP、出力段階表示)・制御タブ・Scope・3D・パラメータ表・ログを配置。データは**ダミー生成器**で動かす(機能は未接続でよい)。ダミー 2 台の複数モータ表示を含む。文字列は `tr()` 経由 |

並列化: 0-1 → (0-2, 0-3, 0-4 を並列) → 0-5

### ★ UI-1: UI デザイン・配置のユーザーチェック
`run_sim.bat` でモックを起動して確認してもらう。指摘を反映して UI 配置を確定(`architecture.md` §4.9 を更新)してから Phase 1 以降へ進む。
モックの画面部品は Phase 3 でそのまま本物のデータにつなぐ(作り直さない)。

---

## Phase 1: コア通信(Qt なし)

| # | タスク | 担当 | 受入基準 |
|---|---|---|---|
| 1-0 | インタフェース定義書 | **Opus** | `CanFrame`/`CanTransport`/decode 結果型/`MotorProfile`/`Motor` API のシグネチャと振る舞いを確定 |
| 1-1 | protocol 層 | Sonnet | `el05_protocol.md` の全バイト例(type 0/1/3/4/6/7/17/18/22/23/24/25、バージョン読出、MIT 停止/切替定数)がユニットテストで一致 |
| 1-2 | `AtSerialTransport` | Sonnet | パーサ単体テスト:マニュアル実例、分割受信、ノイズ混入、データ中の `41 54`/`0D 0A`、不正 DLC から再同期 |
| 1-3 | `MotorSimulator` + `SimTransport` | Sonnet | type 0/1/2/3/4/6/7/17/18/21/22/24 に応答、各 run_mode の簡易物理、遅延・欠落・故障注入 |
| 1-4 | `profiles/el05.toml` + ローダ | Sonnet | レンジ・安全既定値・0x70xx パラメータ定義(el05_protocol.md §5.2 全行)|
| 1-5 | `BusManager` + `Motor` + `EventBus` + `Session` | Sonnet | Sim 上で:スキャン → UID 取得 → パラメータ読書 → 有効化 → 停止 が非同期 API で動く統合テスト。タイムアウト・応答照合のテスト |
| 1-6 | CLI | Sonnet | `ports / scan / info / param get|set / enable / stop / monitor` が `--sim` と実機で動作 |
| 1-R | レビュー | **Opus** | 仕様突合せ、スレッド安全性 |

並列化: 1-0 → (1-1, 1-2, 1-4 並列) → 1-3 → 1-5 → 1-6

### ★ HW-1: 実機での通信確認(ユーザー作業、CLI のみ・モータは回さない)
公式 USB-CAN(or 手元のアダプタ)で `scan`, `info`(UID・バージョン), `param get`(全 0x70xx 読出), 主動上報の受信を確認し、生フレームログを保存。
→ Opus が解析し、`open_questions.md` の Q-P1〜(バージョン読出方式、上報の type、0x70xx 読出範囲 等)を解決して research に反映。

---

## Phase 2: 制御・安全・テレメトリ(Qt なし)

| # | タスク | 担当 | 受入基準 |
|---|---|---|---|
| 2-0 | Controller/SafetyGuard/Telemetry 仕様 | **Opus** | インタフェースと安全ルール(クランプ・停止条件・モード遷移)を確定 |
| 2-1 | `SafetyGuard` + モード遷移ロジック | Sonnet | 範囲外指令のクランプ/拒否、運転中のモード変更拒否、CSP/PP 有効化前の loc_ref 先書き をテスト |
| 2-2 | `ControlLoop` + 組込み Controller | Sonnet | MitDirect / Jog / Velocity / Current / CSP / PP / Hold / Damping / Spring / Sine / Step / ZeroReturn。Sim 上で各モードが期待どおり動く統合テスト。周期・ジッタ統計 |
| 2-3 | Telemetry(チャネル・リングバッファ・Recorder)| Sonnet | 1 kHz × 8ch 書込み中の並行読出で破損なし、CSV 出力 |
| 2-4 | テレメトリ取得戦略 | Sonnet | 上報(type 24)/ リフレッシュ取得 / type 17 ポーリングを切替可能 |
| 2-5 | 終了・切断時の全停止、canTimeout 自動設定 | Sonnet | 例外・Ctrl+C・transport 切断で stop が送られることをテスト |
| 2-6 | 出力段階(LOW/HIGH)| Sonnet | architecture.md §4.3.1。起動時 LOW、HIGH は停止中のみ、アプリ側クランプ(MIT は PD 項込みの最大トルクを制限)+ モータ側 limit 書込み をテスト |
| 2-7 | 複数モータ | Sonnet | Sim 3 台で、個別制御・同時制御(1 周期で send_many)・全台 E-STOP の統合テスト |
| 2-R | レビュー | **Opus** | |

並列化: 2-0 → (2-1, 2-3 並列) → (2-2, 2-4, 2-6) → (2-5, 2-7)

### ★ HW-2: CLI での低速回転試験(ユーザー作業、無負荷・固定した状態で)
LOW 段階で `enable` → 速度 1 rad/s → 停止、MIT で Kd ダンピング、CSP 小角度移動。ループ周期・往復遅延を計測 → 性能目標の見直し。
出力段階の制限が実際に効くか(特に運控モードで limit_torque が効くか = Q-P11)を確認し、LOW/HIGH の数値を調整。HIGH の試験は固定してから。

---

## Phase 3: GUI MVP

| # | タスク | 担当 | 受入基準 |
|---|---|---|---|
| 3-0 | GUI 骨格・Qt ブリッジ仕様 + 画面仕様 | **Opus** | パネル一覧、各パネルの項目と既定値(motorstudio_features.md §3 を元に)|
| 3-1 | プラグイン API + マネージャ(組込み用)| Sonnet | 組込みパネルはすべて `builtin/` のプラグインとして登録される |
| 3-2 | MainWindow・ドック・ツールバー・E-STOP・ステータスバー | Sonnet | レイアウト保存/復元、Esc/Space で E-STOP |
| 3-3 | 接続・デバイスパネル | Sonnet | アダプタ選択(COM 列挙、CH340 優先)、Sim 選択、スキャン結果一覧 |
| 3-4 | 制御パネル(モード別タブ、Controller の Params から自動フォーム)| Sonnet | 全組込み Controller を GUI から操作可能 |
| 3-5 | ステータス表示(数値・故障 LED)+ Scope | Sonnet | 8ch・1 kHz 取得・60 fps 描画、一時停止・時間幅・CSV 保存 |
| 3-6 | ログ・CAN フレームモニタ | Sonnet | TX/RX を 16 進 + デコード結果で表示、フィルタ |
| 3-7 | GUI スモークテスト | Sonnet | pytest-qt offscreen で起動 → Sim 接続 → 有効化 → 速度指令 → 停止 |
| 3-R | レビュー + 使い勝手確認 | **Opus** + ユーザー | |

並列化: 3-0 → 3-1 → 3-2 → (3-3, 3-4, 3-5, 3-6 並列) → 3-7

### ★ HW-3: GUI での実機試運転(ユーザー作業)
全モード・JOG・E-STOP の実機確認。フィードバックをもらって UI 調整。

---

## Phase 4: 機能拡充(MotorStudio 同等の残り + 3D)

| # | タスク | 担当 |
|---|---|---|
| 4-1 | 3D ビューパネル(GLB 表示・出力角連動・目標ゴースト・トルク矢印・2D フォールバック)| Sonnet |
| 4-2 | パラメータパネル(一覧・一括読出・編集・書込・Flash 保存・JSON 入出力・差分)| Sonnet |
| 4-3 | 故障パネル(デコード表示・履歴・クリア)| Sonnet |
| 4-4 | ツールパネル(CAN ID 変更、ゼロ設定、上報設定、canTimeout、ボーレート変更、プロトコル切替・CANopen/MIT からの復帰)— すべて確認ダイアログ付き | Sonnet |
| 4-5 | Scope 強化(カーソル、トリガ、FFT、ステップ応答評価)| Sonnet |
| 4-R | レビュー | Opus |

### ★ HW-4: パラメータ保存・ID 変更・ゼロ点の永続性確認(open_questions の Q-P5 等)

---

## Phase 5: 拡張性の仕上げ

| # | タスク | 担当 |
|---|---|---|
| 5-1 | 外部プラグイン読込(`plugins/`、entry points)、有効/無効 UI、雛形 `example_hello` + 開発ガイド `docs/design/plugin_guide.md` | Sonnet(ガイドの構成は Opus)|
| 5-2 | スクリプトコンソール(`session` を公開した Python REPL パネル)| Sonnet |
| 5-3 | シーケンス実行(ステップ列を YAML/Python で定義、実行・記録)| Sonnet |
| 5-4 | 生フレームログ記録 + `ReplayTransport` | Sonnet |
| 5-5 | 言語切替(英語カタログ追加、設定画面で切替)— 必要になったら | Sonnet |
| 5-6 | バイラテラル制御デモ(2 スロット Controller をプラグインとして実装 = 複数モータ拡張の見本)| Sonnet(制御則の設計は Opus)|

---

## Phase 6: オプション(必要に応じて)

- `PythonCanTransport`(CANHUB / CANable / PCAN)と複数チャネル(将来メジャーな変換器を追加する場合)
- MIT プロトコル(11bit 標準フレーム)対応 — AT アダプタの標準フレーム形式を実機確認後
- Web UI フロント(コア層を共有)
- OTA ファーム更新(`docs/vendor/common/OTA*.pdf` を精査したうえで、十分な安全策とともに)

---

## マイルストーンまとめ

| MS | 内容 | 実機 |
|---|---|---|
| M0 | 雛形・スパイク・UI モック完了 → **UI-1 チェック** | 不要 |
| M1 | CLI で実機と通信 | HW-1 |
| M2 | CLI で実機を安全に回せる | HW-2 |
| **M3** | **GUI で MotorStudio の主要機能 + グラフ(ここで一旦使える状態)** | HW-3 |
| M4 | 3D・パラメータ・故障・ツール(MotorStudio 同等) | HW-4 |
| M5 | プラグイン・スクリプト・シーケンス | 任意 |
