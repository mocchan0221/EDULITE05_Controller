# Phase 0 タスク指示書 — 土台・技術スパイク・UI モック

> 言語: ユーザーへの返答・報告は日本語で行う(CLAUDE.md「言語」)。サブエージェント間のやりとりは英語でもよい。

作成: Opus / 2026-10-01。実装担当: Sonnet。
前提として `CLAUDE.md`、`docs/design/architecture.md`(特に §2, §4.3.1, §4.9, §5)を読むこと。

ゴール: `run_sim.bat` をダブルクリックすると、**本番と同じ画面構成の UI モック**がダミーデータで動く状態にし、ユーザーに UI デザイン・配置を確認してもらう(UI-1)。
モックの画面部品は Phase 3 でそのまま本物につなぐ。**作り捨てにしないこと**(ただし 0-2 のスパイクは使い捨て)。

---

## 0-1 プロジェクト雛形(担当 A)

- `pyproject.toml`
  - `name = "edulite05"`, `requires-python = ">=3.12,<3.13"`, build backend `hatchling`, src レイアウト(`src/edulite05`)
  - 依存: `PySide6>=6.11,<6.12`, `pyqtgraph==0.14.0`, `numpy>=2`, `pyserial>=3.5`, `pyvista>=0.49`, `pyvistaqt>=0.13`
    (python-can は Phase 6 まで入れない)
  - dev 依存(`[dependency-groups] dev`): `pytest`, `pytest-qt`, `ruff`
  - scripts: `edulite05 = "edulite05.app.main:main"`, `edulite05-cli = "edulite05.cli.main:main"`(CLI は Phase 1。今は "未実装" と表示して終了するだけでよい)
  - ruff 設定(line-length 100, target py312)、pytest 設定(`testpaths = ["tests"]`, `qt_api = "pyside6"`)
- `uv lock` → `uv.lock` をコミット
- パッケージの骨組み(空の `__init__.py`): `core/{transport,protocol,device,control,telemetry}`, `sim`, `plugin_api`, `builtin`, `app/{widgets,panels,i18n}`, `cli`。`src/edulite05/__main__.py` は `app.main:main` を呼ぶ
- `run.bat` / `run_sim.bat`(`run_sim.bat` は `--sim` 付き)
  - UNC パス対策: 先頭で `pushd "%~dp0"`、最後に `popd`
  - `set UV_LINK_MODE=copy`(uv キャッシュ C: → 共有フォルダへのハードリンク不可のため)
  - `uv` が無ければインストール方法を表示して `pause`
  - `uv run --frozen edulite05 %*`、異常終了時は `pause` してエラーを読めるようにする
- `run.sh`(Linux 用、同等)
- `.gitattributes`: `* text=auto eol=lf`、`*.bat text eol=crlf`、PDF/STEP/GLB/STL は `binary`
- `README.md`(日本語): 概要、起動方法(run.bat / run_sim.bat / uv run)、必要なもの(uv)、ディレクトリ構成、資料の場所(`docs/README.md`)、venv をローカルへ移す方法(`UV_PROJECT_ENVIRONMENT`)
- `tests/test_smoke.py`: パッケージが import できること

## 0-3 UNC 上での起動確認(担当 A、0-1 の直後)

- 共有フォルダ上で `uv sync` → 初回・2 回目の所要時間、`uv run python -c "import PySide6, pyqtgraph, pyvista"` の所要時間を計測
- cmd.exe 経由で `run_sim.bat` が UNC パスから起動できることを確認(`cmd /c` で実行し、モック未実装なら仮の空ウィンドウで可)
- 結果を `docs/research/tech_stack.md` の末尾に「## 検証結果: 共有フォルダ上の venv(2026-10-xx)」として追記

## 0-4 STEP → GLB 変換(担当 B、0-1 と並列可)

- `tools/convert_step.py`: `uv run --with cascadio --with trimesh python tools/convert_step.py` で実行(**pyproject には依存を追加しない**)
- 入力 `docs/vendor/el05/el05.stp`(Creo 出力、mm、11 solid)。`tech_stack.md` の STEP 節に事前調査結果あり:
  - 部品名は Creo エンコードされた中国語(输出轴=出力軸、机壳=ハウジング、齿圈=リングギア、軸受、ピン、ねじ …)→ デコードして英語/日本語名に
  - 回転軸は Z 軸に平行で (−12.6, 3.6) mm 付近を通る → **回転軸を原点・Z 軸に一致**させる(実際の軸位置は円筒面から算出して確認)
  - ねじ 4 本が三角形の約 69% → 削除か大幅簡略化
- 出力: `assets/models/el05.glb`(単位 m、目安 2 MB 未満)。ノードを **`rotor`(出力軸と一緒に回る部品)と `stator`(それ以外)の 2 グループ**に分ける。どの部品を rotor にしたかは根拠とともに記録
- 可能なら確認用に `assets/models/preview.png`(pyvista の off_screen で描画)
- 記録: `docs/research/el05_model.md`(部品一覧・デコード名・rotor/stator 分類・軸位置・変換手順・三角形数)

## 0-2 + 0-5 GUI スタック確認と UI モック(担当 C、0-1 完了後)

### 0-2 スパイク(最初に 30 分程度で)
- `tools/spikes/stack_check.py`(使い捨て、コミットは可): pyqtgraph で 8ch × 1 kHz の sin を 60 fps 描画 + pyvistaqt で円柱を回転。PySide6 6.11 + pyqtgraph 0.14.0 の組合せで問題が出たら回避策/バージョン調整を行い `tech_stack.md` の検証結果に追記

### 0-5 UI モック

#### 画面とコアの境界 — `app/bridge.py`(**この形を Phase 3 でも維持する**)

パネルは `Bridge` の signal とメソッド**だけ**を使う(コア層やモックを直接触らない)。

```python
class Bridge(QObject):                       # 抽象。モックは DemoBridge、Phase 3 で CoreBridge
    connection_changed = Signal(object)      # ConnectionInfo(connected, adapter, port, simulated)
    motors_changed     = Signal(list)        # list[MotorInfo(id, name, profile_name)]
    state_updated      = Signal(dict)        # {motor_id: MotorViewState} … 約 60 Hz
    power_tier_changed = Signal(str)         # "low" | "high"
    selected_motor_changed = Signal(int)
    log_message        = Signal(object)      # LogEntry(t, level, text)
    frame_logged       = Signal(object)      # FrameEntry(t, direction, can_id, data, decoded_text)
    params_updated     = Signal(int, list)   # motor_id, list[ParamValue(index, name, value, unit, dirty)]
    stats_updated      = Signal(object)      # BusStats(tx_fps, rx_fps, loop_hz, jitter_ms)

    def available_channels(self) -> list[ChannelInfo]           # name "m1.pos", label, unit, motor_id
    def scope_data(self, names: list[str], window_s: float) -> dict[str, tuple[np.ndarray, np.ndarray]]
    def power_limits(self) -> PowerLimits                        # 現在段階の上限(torque, current, speed, kp, kd)
    # 操作(モックはログ出力 + それらしい状態変化のみ)
    def connect_adapter(self, cfg: dict) / disconnect_adapter() / scan()
    def select_motor(self, motor_id: int)
    def enable(self, ids: list[int]) / stop(self, ids: list[int]) / estop()
    def set_power_tier(self, tier: str) -> bool
    def start_controller(self, name: str, assign: dict[str, int], params: object) / update_controller_params(...) / stop_controller(...)
    def read_all_params(self, motor_id) / write_param(self, motor_id, index, value) / save_params(self, motor_id)
```

- `MotorViewState` 等の値型は `app/viewmodels.py` に `@dataclass(frozen=True, slots=True)` で定義
  (`MotorViewState`: id, online, mode("reset"/"cali"/"motor"), run_mode(int), position, position_multi, velocity, torque, temperature, vbus(None 可), faults(tuple[str,...]), controller(str|None), cmd_position/cmd_velocity/cmd_torque(None 可))
- `app/demo_bridge.py`: ダミー 2 台(ID 1, 2)。内部で 1 kHz 相当のサンプルを numpy リングバッファ(120 s)に生成(QTimer 16 ms ごとにまとめて生成)。enable/stop/start_controller で波形の振る舞いが変わる程度でよい。ダミーの CAN フレームとログも流す
- `--sim` のとき DemoBridge を使う(Phase 1 以降で本物の Sim に差し替え)。`--sim` なしでも今は DemoBridge で起動し、ステータスバーに「モック」と明示

#### 制御パラメータの自動フォーム — `app/widgets/param_form.py`
- dataclass のフィールド metadata(`label`=tr キー, `unit`, `min`, `max`, `step`, `decimals`, `limit`=出力段階の上限キー "torque"/"current"/"speed"/"kp"/"kd")から「ラベル + スライダ + 数値入力 + 0 リセット」行を自動生成
- `limit` 指定の項目は `Bridge.power_limits()` で範囲を動的に制限(段階変更時に即反映、範囲外の値はクランプ)
- 各モードの Params dataclass は `src/edulite05/core/control/params.py` に置く(**ロジック無しの dataclass のみ**。Phase 2 で Controller 実装がこれを使う)
  - `MitParams`(position, velocity, kp, kd, torque_ff)/ `VelocityParams`(velocity, current_limit, accel)/ `CurrentParams`(iq)/ `CspParams`(position, speed_limit)/ `PpParams`(position, velocity, accel)/ `JogParams`(speed, accel)/ `SineParams`(target 量, amplitude, frequency, offset)/ `StepParams`
  - 既定値・範囲は `docs/research/motorstudio_features.md` §3 と `el05_protocol.md` §3、`architecture.md` §4.3.1 に従う(例: 速度モードの電流制限の既定値は 27 A ではなく LOW 段階の上限以下)

#### 画面構成(`architecture.md` §4.9 の図のとおり。QMainWindow + QDockWidget)

- **ツールバー**(左→右): [接続/切断] アダプタ表示 | [スキャン] | 操作対象モータ ▼ | 状態チップ(mode / run_mode / 故障有無)| [有効化] [停止] | **出力段階トグル**(LOW=緑「低出力(無固定可)」/ HIGH=橙「高出力(要固定)」、HIGH への切替は「モータを固定しました」チェック付き確認ダイアログ、全停止中のみ)| 右端に大きな赤い **[非常停止]**
- **左ドック**
  - 「接続」: アダプタ種別(公式 USB-CAN / シミュレータ)、COM ポート ▼ + 更新(`serial.tools.list_ports`、VID:PID 1A86:7523 を優先表示)、CAN 1 Mbps(表示のみ)、ホスト ID 0xFD
  - 「モータ一覧」: 表(ID、名前、状態 LED、mode、温度、故障)。行選択 = 操作対象の切替
  - 「状態」: 操作対象の大きな数値表示(位置 rad/deg 切替・多回転、速度、トルク、温度、母線電圧)+ 故障 6 bit の LED(低電圧・過電流・過温・エンコーダ・過負荷・未校正)
- **中央**: タブ「グラフ」「3D」
  - グラフ: 縦に積んだ 4 段(位置 / 速度 / トルク / 温度、指令値は破線で重ねる)。横にチャネル一覧(チェック + 色)、時間幅 ▼(2/5/10/30/60 s)、[一時停止] [オートスケール] [クリア] [CSV 保存](モックでは保存も実装してよい)。`clipToView`・downsample を有効に
  - 3D: `assets/models/el05.glb` があれば rotor/stator を読込み、無ければ円柱 2 つで代用。rotor を操作対象の出力角で回転、目標角のゴースト(半透明)、トルク矢印。複数台は横並び。PyVista の import や初期化に失敗したら 2D ダイヤル表示(QPainter)にフォールバック
- **右ドック**「制御」: タブ [運控(MIT)] [速度] [電流] [位置 CSP] [位置 PP] [JOG] [試験信号]
  - 各タブ: ParamForm + [開始] [停止] + (MIT のみ)連続送信/単発、送信周期 ▼
  - JOG タブ: 大きな [◀ 押している間 逆転] [押している間 正転 ▶]
  - 上部に「このモードは停止中にのみ切替可能」等の注意を小さく
- **下ドック**: タブ [ログ] [CAN フレーム] [パラメータ] [故障] [ツール]
  - CAN フレーム: 時刻・方向・ID(16 進)・type・データ(16 進)・デコード結果の表、一時停止・フィルタ(type / モータ)・クリア。行数上限あり(リングバッファ)
  - パラメータ: `el05_protocol.md` §5.2 の 0x70xx 全行(index, 名称, 値, 単位, 範囲, R/W, 説明)。未保存の書込み値は色分け。[全読出] [選択を書込] [Flash 保存] [エクスポート] [インポート]
  - 故障: デコード済み故障の一覧 + 履歴 + [故障クリア]
  - ツール: グループボックスで [CAN ID 変更] [ゼロ点設定] [主動上報 ON/OFF・周期] [CAN タイムアウト] [CAN ボーレート変更] [プロトコル切替 / CANopen・MIT からの復帰]。モックでは確認ダイアログまで実装し、実行はログ出力のみ
- **ステータスバー**: 接続状態 | TX/RX fps | 制御ループ Hz・ジッタ | 出力段階 | 「モック」/「シミュレータ」/「実機」
- **メニュー**: ファイル(記録開始/停止、終了)/ 表示(各パネルの表示切替、レイアウトを初期化、テーマ ライト/ダーク)/ ツール(プラグイン管理 … 無効表示)/ ヘルプ(資料フォルダを開く、バージョン)
- **ショートカット**: `Esc` と `Pause` = 非常停止(`Qt.ApplicationShortcut`)
- **レイアウト保存**: `QMainWindow.saveState/restoreState` を `user_data/ui_state.ini` に(`QSettings` の IniFormat)
- **見た目**: Fusion スタイル、ダーク既定 + ライト切替。フォントは Windows で "Yu Gothic UI"(無ければ "Meiryo UI")。数値表示は等幅(例 "Consolas")。非常停止ボタンと出力段階表示は遠くからでも分かる大きさ・色に
- **文字列**: すべて `tr("key")`(`app/i18n/__init__.py` に `tr()`、カタログ `app/i18n/ja.toml`。キー未定義ならキー文字列を返し警告ログ)

#### テスト
- `tests/gui/test_mock_window.py`(pytest-qt, offscreen): 起動 → 3 秒程度 DemoBridge を回す → 全ドックが存在 → 出力段階トグル(ダイアログはモンキーパッチで自動承認)→ Esc で estop が呼ばれる → 終了
- `tests/test_param_form.py`: limit 付きフィールドが段階の上限でクランプされる

---

## 共通の受入基準
- `uv run pytest` 全件パス、`uv run ruff check` 指摘なし
- `run_sim.bat` をエクスプローラーからダブルクリック(UNC)で UI モックが起動する
- 各担当は自分の担当ファイルだけをコミット(`phase0-N: …`)。`index.lock` で失敗したら少し待って再試行
- 最終報告: 作ったもの、確認結果、スクリーンショット(`user_data/` ではなく `docs/design/ui_mock/` に PNG で保存しコミット)、未解決事項
