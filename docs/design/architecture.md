# EL05 コントローラ GUI — ソフトウェア構成案

- 状態: **v0.2 (2026-10-01)** — ユーザー回答(アダプタ・複数台・出力 2 段階・言語)を反映済み。UI 配置は UI-1 チェックで確定予定。未決事項は `docs/design/open_questions.md`
- 根拠資料: `docs/research/` の 5 本(el05_spec / el05_protocol / motorstudio_features / can_transport / tech_stack)

---

## 1. 目的とスコープ

RobStride EduLite 05 (EL05) を PC から試運転・評価するためのローカル GUI。

**必須 (公式 MotorStudio 同等)**
接続・スキャン / 有効化・停止・E-STOP / JOG / 運控(MIT)・電流・速度・PP・CSP の各モード / ゼロ設定 / CAN ID 変更 /
パラメータ読み書き・Flash 保存 / 故障表示・クリア / 主動上報設定 / オシロ(波形表示・保存) / 通信ログ / プロトコル切替・ボーレート変更

**必須 (本ツール独自)**
リアルタイムグラフ(多ch、100–1000 Hz 取得、30–60 fps 描画) / リアルタイム 3D CG / プラグインによる機能追加 /
シミュレータ(実機なしで全機能動作) / 型式プロファイルに基づく安全制限

**当面スコープ外** (理由は `motorstudio_features.md` §2・§5)
OTA ファーム更新、磁気エンコーダ校正(非公開コマンド)、上位機パラメータ表 0x0000–0x304E の非公開読出(L91)。
→ 公式 MotorStudio と併用する前提。プラグインとして後付けできる構造にはしておく。

### 用語の注意
| 本書の用語 | 意味 |
|---|---|
| **MIT 制御 / 運控モード** | 独自プロトコル run_mode=0 + type 1 フレーム(p, v, Kp, Kd, t_ff の 5 値)。MotorStudio の「MIT」タブはこれ |
| **MIT プロトコル** | type 25 で切替える 11bit 標準フレームの別プロトコル。上とは別物(Phase 6) |

---

## 2. 技術スタック(決定案)

| 領域 | 採用 | 理由(詳細は tech_stack.md) |
|---|---|---|
| 言語/実行 | **Python 3.12 + uv**(`uv.lock` で固定) | 導入済み。1 コマンド起動、公式 SDK(MIT) の知見を流用可 |
| GUI | **PySide6 6.11 系**(LGPL) | ドッキング UI・成熟度。PyQt6 は GPL のため不採用 |
| グラフ | **pyqtgraph 0.14** | numpy 直結で高速。`clipToView` / downsample |
| 3D | **PyVista + pyvistaqt**(VTK)| GLB/STL 読込、姿勢は行列更新のみ。**遅延 import**、失敗時は 2D ダイヤル表示にフォールバック |
| シリアル/CAN | **pyserial**(公式 USB-CAN "AT")/ **python-can**(CANHUB, CANable, PCAN 等)| アダプタ差し替え可能に |
| 数値 | numpy | リングバッファ・解析 |
| 設定 | TOML(読込は stdlib `tomllib`)/ JSON(ユーザー設定の書込み)| 追加依存なし |
| テスト | pytest, pytest-qt(offscreen)| コア層は Qt 無しでテスト |
| STEP→メッシュ | cascadio + trimesh(**開発時のみ**実行するスクリプト)| 生成 GLB をリポジトリに同梱。アプリ実行時は不要 |

不採用: Web UI(Node 20.11 が Vite/Electron の要件未満、2 プロセス構成の複雑さ)。
ただしコア層を Qt 非依存にするので、将来 Web UI をプラグイン/別フロントとして追加する道は残る。

---

## 3. 全体構成

```
┌──────────────────────────── GUI 層 (Qt, main thread) ────────────────────────────┐
│ MainWindow(ドック)  ConnectionPanel  DevicePanel  ControlPanel(モード別タブ)       │
│ ScopePanel  Viewer3DPanel  ParamPanel  FaultPanel  LogPanel  ToolsPanel  …        │
│        ↑ 30–60 Hz で snapshot/リングバッファを pull、イベントは Qt signal に変換    │
├──────────────────────────── プラグイン層 ──────────────────────────────────────────┤
│ PluginManager ─ 組込みプラグイン(上記パネルも全部これで登録) / plugins/ / entry points │
├──────────────────────────── コア層 (Qt 非依存) ────────────────────────────────────┤
│ Session ─┬─ BusManager ─ RX thread / TX thread(優先度キュー) / 要求応答照合       │
│          ├─ Motor(ID ごと) ─ MotorState / 高レベル API / SafetyGuard              │
│          ├─ ControlLoop(制御スレッド, 固定周期) ─ Controller(JOG, MIT, sin, step…)│
│          ├─ TelemetryHub ─ チャネル登録・リングバッファ・Recorder(CSV)             │
│          └─ EventBus(スレッド安全 pub/sub)                                         │
│ protocol/ ─ 独自プロトコル codec・スケーリング・故障デコード・パラメータ定義          │
│ profiles/ ─ 型式プロファイル(EL05 のレンジ・安全既定値)                          │
├──────────────────────────── トランスポート層 ──────────────────────────────────────┤
│ CanTransport(抽象) ─ AtSerialTransport / PythonCanTransport / SimTransport / Replay│
└───────────────────────────────────────────────────────────────────────────────────┘
                                       ↓
                    公式 USB-CAN (CH340, 921600bps AT) / CANHUB / CANable / PCAN
                    SimTransport → MotorSimulator(フレームレベルで EL05 を模擬)
```

**原則**
1. **コア層は Qt を import しない**。GUI なしの CLI・テスト・将来の別フロントから同じコアを使う。
2. **GUI スレッドは絶対に待たない**。コアへの要求は `Future` / コールバックで返る。
3. **組込み機能もプラグイン API で実装する**(ドッグフーディング)。これで拡張性が実際に担保される。
4. **安全側に倒す**。全コマンドは SafetyGuard を通過し、終了・切断・例外時は全モータ停止。

---

## 4. 層ごとの設計

### 4.1 トランスポート層 `core/transport/`

`can_transport.md` §5 の推奨をそのまま採用。

```python
@dataclass(frozen=True, slots=True)
class CanFrame:
    can_id: int; data: bytes; extended: bool = True
    timestamp: float = 0.0          # time.monotonic() 基準
    channel: int = 0; direction: Direction = Direction.RX

class CanTransport(ABC):
    def open(self) -> None; def close(self) -> None; is_open: bool
    def send(self, f: CanFrame) -> None
    def send_many(self, fs: Sequence[CanFrame]) -> None   # AT は 1 フレームずつ最小 1 ms 間隔で書く(連結しない。hw_bench.md)
    def recv(self, timeout: float) -> CanFrame | None      # RX スレッドから呼ぶ
    capabilities: TransportCaps; def stats(self) -> TransportStats
```

| 実装 | 内容 | Phase |
|---|---|---|
| `AtSerialTransport` | 公式 USB-CAN。`41 54 | (id29<<3)|0x04 BE | DLC | data | 0D 0A`、921600bps。**AT 設定コマンドは送らない**。長さベースのパーサ + 1 バイト再同期 | 1 |
| `SimTransport` | 自前キュー + `MotorSimulator`(1 自由度慣性・粘性モデル、各 run_mode、type 0/1/2/3/4/6/7/17/18/21/22/24 応答、遅延・欠落・故障注入)| 1 |
| `ReplayTransport` | 記録した生フレームログを時刻通り再生 | 5 |
| `PythonCanTransport` | python-can の任意 interface(gs_usb/candle/slcan/pcan/socketcan/virtual)| 6 |

トランスポートは **レジストリ登録式**(プラグインが新アダプタを追加可能)。接続ダイアログは登録済みトランスポートの設定スキーマから自動生成する。

### 4.2 プロトコル層 `core/protocol/`

純粋関数のみ(I/O なし、状態なし)。`el05_protocol.md` のバイト例を **そのままユニットテストにする**。

- `ids.py` — 29bit ID の組立/分解 `make_id(type, data2, dest)`、`parse_id()`
- `scaling.py` — `float_to_uint / uint_to_float`(レンジはプロファイルから注入、EL05 固定値を埋め込まない)
- `private.py` — 各 type のエンコーダ(`enable()`, `stop(clear_fault)`, `mit_cmd(p,v,kp,kd,t)`, `read_param(idx)`, `write_param(idx, value, dtype)`, `set_zero()`, `set_can_id()`, `save()`, `set_baud()`, `set_active_report()`, `switch_protocol()`, `get_uid()`, `read_version()`)とデコーダ(`decode(frame) -> Feedback | ParamResponse | FaultReport | UidResponse | VersionResponse | Unknown`)
- `faults.py` — type 2 故障 6bit、type 21 fault/warning 32bit の意味付け(列挙 + 日本語説明)
- `params.py` — 0x70xx 実行時パラメータ定義表(index, name, dtype, unit, range, RW, 説明)。**データはプロファイル TOML 側**に置き、コードは読み込むだけ
- 注意点(el05_protocol.md より): type 1 のトルクは ID bit23..8、0 N·m = 0x7FFF / 型に応じた B4–B7 解釈(SDK の float 決め打ちは誤り)/ 主動上報は type 2 と 0x18 の両方を受理 / 角度は ±4π で周期ループ

### 4.3 デバイス層 `core/device/`

```python
class Motor:                       # BusManager が ID ごとに生成
    id: int; profile: MotorProfile; state: MotorState   # 最新値(ロック付き snapshot)
    # 非同期 API(すべて Future を返す)
    def enable() / stop() / clear_fault() / set_run_mode(mode) / set_zero()
    def read_param(name|idx) / write_param(name|idx, v) / save_params()
    def set_active_report(on, period_ms) / set_can_id(new) / read_version() / get_uid()
    # 周期指令(ControlLoop から呼ばれる、非ブロッキング)
    def command(cmd: MitCommand | SetpointCommand)
```

- **MotorState**: mode(Reset/Cali/Motor)、run_mode、position(折り返し生値と unwrap 後の多回転値 + 較正済みフラグ `multi_valid`。§4.3.2)、velocity、torque、temperature、fault bits、最終受信時刻、通信品質(受信レート、欠落)
- **SafetyGuard**: プロファイルのレンジ + ユーザー設定のソフトリミット(角度範囲・速度・トルク・電流)で全指令をクランプ/拒否。モード変更は停止中のみ許可(マニュアル注意事項)。
- **状態遷移の強制**: `set_run_mode` は内部で「stop → 確認 → write run_mode → (有効化は明示操作)」を行う。CSP/PP 有効化時は現在位置を loc_ref に先書きして急動作を防ぐ(el05_protocol.md §3.3 注意)。
- **MotorProfile** (`profiles/el05.toml`): P ±12.57 rad、V ±50 rad/s、T ±6 N·m、Kp 0–500、Kd 0–5、I ±11 A、温度閾値、GUI 既定値(JOG 速度、電流制限 等)。**RS00–06 もファイル追加だけで対応可能**にする。

#### 4.3.1 出力段階(Power Tier)— 2 段階の出力制限

| 段階 | 想定 | トルク | 電流 | 速度 | MIT Kp / Kd 上限 | 表示色 |
|---|---|---|---|---|---|---|
| **LOW**(既定) | 無固定・手で持って試せる | 0.5 N·m | 1.0 A | 5 rad/s | 20 / 1.0(負の Kd −0.05、摩擦補償 0.1 N·m) | 緑 |
| **HIGH** | 固定推奨 | 6 N·m(プロファイル上限) | 11 A | 50 rad/s | 500 / 5(負の Kd −0.2、摩擦補償 0.3 N·m) | 橙 |

(数値は初期案。`profiles/el05.toml` の `[power_tiers.*]` で定義し、HW-2 で実機調整する)

- **二重に制限する**:
  1. アプリ側: SafetyGuard が全指令(t_ff, iq_ref, spd_ref, limit 系, JOG 速度 等)を段階の上限でクランプ。MIT 指令は最新フィードバックから `|Kp·(p_set−p)| + |Kd·(v_set−v)| + |t_ff|` が上限を超えないよう p_set / v_set を現在値寄りに制限する。
  2. モータ側: 有効化時と段階変更時に `limit_torque (0x700B)`、`limit_cur (0x7018)`、`limit_spd (0x7017)` を段階の値で書込み(type 18、Flash 保存はしない)。運控モードに limit_torque が効くかは HW-2 で確認(`open_questions.md` Q-P11)。
- **MIT のホスト側項(負の Kd・摩擦補償)と暴走ガード**(phase4-fb-X): MIT フレームの kd は 0〜5 で負を送れない。
  そこで `MitCmd.kd < 0` と `MitCmd.friction_comp`(`MitParams`)は **PC 側で t_ff に畳み込む**(SafetyGuard の `clamp_mit`、毎周期、最新フィードバックの速度 v で計算):
  - `kd_fw = max(kd, 0)` をフレームに載せ、`kd_host = min(kd, 0)` は `t_ff += kd_host·(v_set − v)`(v_set = 0 なら `|kd_host|·v` で動きを助ける向き)
  - `t_ff += friction_comp·tanh(v / v_eps)`(`v_eps` = プロファイル `[mit_host] friction_v_eps` 0.05 rad/s。この個体はクーロン摩擦 ≈ 0.03 N·m・粘性 ≈ 0.014 N·m·s/rad、`hw_bench.md` HW-4)
  - 上限は出力段階ごと(`[power_tiers.*]` の `kd_negative_max`: LOW 0.05 / HIGH 0.2、`friction_comp_max`: LOW 0.1 / HIGH 0.3)。合算した t_ff を含めて最悪トルク
    `|kp·Δp| + |kd_fw·Δv| + |t_ff 合計| ≤ 段階のトルク` を満たすようにし(従来のクランプ順: t_ff を縮める → p_set → v_set)、`ClampNote` は従来どおり
  - **暴走ガード**(ヒステリシス。モータごとのラッチだけが SafetyGuard の状態): ホスト側項が有効なとき、|v| > `runaway_on`(0.8)× 段階の速度上限で、ホスト側の負の減衰と摩擦補償を捨て、
    `kd_fw ≥ guard_kd`(0.1。段階の kd 上限まで)にして制動し、|v| < `runaway_off`(0.4)× 段階の速度上限まで保つ。`ClampNote(reason="runaway_guard")`(field は kd / friction_comp)が
    `control.clamped` → GUI の「制限中」(`core.clamp_runaway_guard`)に流れる。ホスト側項が無い通常の MIT には効かない。ラッチは運転の開始・停止・無効化で解除する
  - **電流モードにも同じガード**(実機で判明: `run_mode=3` では `limit_spd`(0x7017)が効かず、LOW でも 23.9 rad/s(電圧限界)まで加速した)。
    `clamp_setpoint(cmd, state)` が `SetpointCmd("current")` に対し、|v| > 0.8 × 段階の速度上限で `iq_ref` を制動方向
    `−sign(v)·min(|iq|, i_max)` に置き換え、|v| < 0.4 × まで保つ(ヒステリシス。同じ `ClampNote(reason="runaway_guard")`、field = `value`)。
    制御周期 5 ms の離散化で、押す電流が大きいほど 1 周期の加速ぶんだけ超える(sim: 0.3 A・LOW でピークは上限の ≈ 1.0 倍。1 A なら 1 周期で ≈ 3.9 rad/s 増える)
  - 保証できないこと: 項は最新フィードバック(約 5 ms 周期)で計算するため、負の減衰が速い系では離散化の遅れで 0.8 を少し超えてから制動が効く(`tests/sim/test_sim_mit_host.py`: 200 Hz・LOW でピークは速度上限の ≈ 1.00 倍)
- **起動時は必ず LOW**。HIGH への変更は「全モータ停止中」かつ「固定を確認しました」チェック付きダイアログでのみ可能。LOW へはいつでも即時に下げられる。
- ツールバーに常時大きく表示(色 + 文字)。ステータスバーにも表示。
- 段階はセッション全体で共通(モータごとに変えない。単純さ優先)。

#### 4.3.2 位置の座標系と ±4π 付近の規則(phase4-fix-W)

実機の事実(`el05_protocol.md` §8.0、`hw_bench.md` HW-3b): type 2 フィードバックの位置は **±4π で折り返し**、
mechPos(0x7019)は **折り返さない多回転値**。MIT の p_set をモータ内部がどちらと比べるかは **未確認**
(`open_questions.md` Q-P12)。多回転が ±4π を超えた状態で、折り返し位置を p_set にして位置保持すると
8π の位置誤差になり、モータが引き戻される(シミュレータで再現: LOW でも −50 rad/s)。

- **多回転角の較正**: `MotorState.position_multi` は type 2 を展開した値で、セッション開始時に ±4π の外にいると
  k·8π ずれる。`Motor.sync_multi()` が mechPos を読み、展開オフセットを k = round((mechPos − 折り返し位置)/8π)·8π に
  合わせて `multi_valid = True` にする(冪等なので再送する)。実行する場面は、(1) 取り込み後の初回接触(`MotorService`
  の監視が停止中のモータに対し、較正できるまで 1 s ごとに試行)、(2) **`MotorService.enable` のたびに、モータが動けるように
  なる前**(失敗なら `SafetyError`「多回転位置(mechPos)を読み出せないため有効化できません」。未較正のまま有効化しない)、
  (3) ゼロ点設定後(`reset_position_unwrap(mechPos)`)。CSP/PP の `loc_ref`・位置保持・コントローラの目標は
  すべて多回転座標(= mechPos と同じ)。CLI の `monitor` も表示前に較正する。
- **`MitCmd.position` は多回転座標**。SafetyGuard(`clamp_mit`)が、プロファイルの `[position] mit_position_frame`
  に応じてコマンド座標へ変換し、最悪トルクを見積もる(`profiles/el05.toml`)。

  | `mit_position_frame` | 位置誤差の見積り | 変換 | ±4π 付近の規則 |
  |---|---|---|---|
  | `"unknown"`(既定。Q-P12 確認まで) | 折り返し位置・多回転位置の**両方**に対する大きい方 | そのまま(両者が一致する領域のみ有効) | 多回転の絶対値 > 4π − `wrap_margin` で **kp = 0** |
  | `"multi"` | 多回転位置 | そのまま | 同上 |
  | `"wrapped"` | 折り返し位置 | `p_cmd = p_wrapped + (p_set − p_multi)` | なし(境界をまたぐ 1 周期だけ誤差が跳びうる = 残余リスク) |

  kp = 0 にしたとき減衰(kd)と t_ff は段階の上限の範囲で残る。`ClampNote(field="kp", reason="position_wrap")` が
  `control.clamped` → `service.clamped` → GUI の「制限中」表示(`core.clamp_position_wrap`)に流れ、ログは区間に入った
  ときに 1 回(毎周期は出さない)。`wrap_margin` 既定 0.5 rad、折り返し半幅 `wrap_half` = 4π もプロファイルの値。
  p_set 自体も ±4π(`ranges.position`)にクランプされる。解消は「ゼロ点設定」(運転ページの位置カードの
  **[原点に設定]** から: 停止 → 無効化 → `set_zero`。`MotorService.set_zero` は type 6 の前と `reset_position_unwrap` の後の 2 回、
  位置系チャネル(`pos` / `pos_multi` / `cmd.pos`)へ NaN を追記して、座標が変わる跳びをグラフが縦線で結ばないようにする)。
  位置モードの目標は UI で「1 回転内(最短)」の角度として入力でき、`core/control/targets.py` の純関数
  `nearest_target(θ, p_multi) = p_multi + wrap_to_[−π,π)(θ − p_multi)` が多回転の目標に変換する(コントローラ・SafetyGuard は多回転のまま受け取る)。
- **HoldCmd(MIT)**: 多回転座標の現在位置を p_set にラッチして上の規則を通す。±4π 付近では減衰のみの保持に
  なる(意図した縮退)。**Bilateral** は差・目標を `position_multi` だけで計算し(折り返し位置は使わない)、
  p_set を多回転座標で出す。±4π 付近では 2 台とも kp = 0 になり、結合は減衰・速度追従のみ。
- **シミュレータ**: `SimMotor(mit_frame="multi"|"wrapped")`(既定 `"multi"` = 悲観側)。フィードバックは ±4π で
  折り返し、mechPos は多回転値(実機と同じ)。

### 4.4 BusManager `core/bus.py`

- **RX スレッド 1 本**: `transport.recv()` → decode → 種別で振分け(フィードバック → Motor.state 更新 + Telemetry 追記 / 応答 → Pending 解決 / 故障 → EventBus / 不明 → 生ログ)。
- **TX スレッド 1 本 + 優先度キュー**: `ESTOP > 周期制御 > パラメータ系 > バックグラウンドポーリング`。E-STOP は即時送出(キューを飛ばす経路も用意)。
- **要求応答照合**: `PendingKey = (channel, motor_id, comm_type, param_index)` → Future、単調時計で期限切れ。バージョン読出のように type 2 で返る応答は窓時間 + データパターンで判別。
- **スキャン**: ID 1–127 に type 0 を送り、応答を収集(非ブロッキング、進捗イベント)。
- **統計**: TX/RX fps、パース失敗、タイムアウト数、推定バス負荷 → ステータスバーへ。
- **生フレームログ**: 全 TX/RX を candump 互換テキストで任意記録(後で Replay 可能)。

### 4.5 制御ループ `core/control/`

固定周期スレッド(既定 200 Hz、設定で 50–1000 Hz)。Windows では `timeBeginPeriod(1)` + `perf_counter` 絶対時刻スケジューリング。オーバーラン・ジッタを統計化。

```python
class Controller(ABC):             # プラグインで追加可能
    name: str; run_mode: RunMode    # 必要な run_mode(0=MIT, 2=速度 …)
    slots: tuple[str, ...] = ("motor",)   # 割り当てるモータの役割。バイラテラルは ("leader", "follower")
    Params: type[dataclass]         # ← このスキーマから UI フォームを自動生成
    def on_start(self, states: dict[str, MotorState], p) -> None
    def step(self, t: float, dt: float, states: dict[str, MotorState], p) -> dict[str, Command]
    def on_stop(self) -> None
```

**複数モータ**: ControlLoop は複数の Controller インスタンス(それぞれ 1 台以上のモータに割当)を同じ周期で回し、1 周期分の全指令を `send_many` でまとめて送る。1 台のモータは同時に 1 つの Controller にしか割り当てられない。
対応範囲は「複数台を個別に/同時に動かす」「2 台のバイラテラル制御デモ」まで。運動学・URDF を使うアーム用途は対象外。

組込み Controller(= MotorStudio の各タブ + 改善):

| Controller | run_mode | 内容 |
|---|---|---|
| `MitDirect` | 0 | 5 値の直接指令(連続送信/単発) |
| `Jog` | 2 または 0 | 押している間だけ回転。速度・加速度・電流制限を設定可(公式は 1 rad/s 固定)|
| `Velocity` / `Current` / `CSP` / `PP` | 2 / 3 / 5 / 1 | 各モードの指令 |
| `Hold` / `Damping` / `Spring` | 0 | 現在位置保持、ダンパ、仮想ばね(MIT の Kp/Kd デモ) |
| `SineTest` / `StepTest` / `Chirp` | 任意 | 対象量(位置/速度/電流/トルク)に波形を重畳 |
| `ZeroReturn` | 5 | 緩やかに 0 rad へ戻る(公式 A 版の Zero Position モード相当) |
| `Bilateral` | 0 | 2 台(leader / follower)の位置・力を相互に結合するデモ。対称型(位置対称)から。Sim 2 台で開発可能 |

**テレメトリ取得戦略**(`can_transport.md` §4 の帯域計算に基づく):
- MIT 系: 送信した type 1 の応答 type 2 がそのままフィードバック(制御周期 = 取得周期)。
- 速度/電流/位置モード: ① 主動上報 type 24(10 ms 刻み、最大 100 Hz)② 目標値の再送で type 2 応答を得る「リフレッシュ取得」(〜500 Hz)を選択可。
- 低速チャネル: type 17 のラウンドロビンポーリング(iqf, VBUS, mechPos 等を 5–20 Hz)。

### 4.6 テレメトリ `core/telemetry/`

- **チャネル**: `"m1.pos"`, `"m1.vel"`, `"m1.tq"`, `"m1.temp"`, `"m1.cmd.pos"`, `"m1.p.iqf"` … を名前で登録。プラグインは派生チャネル(例: 電力 = トルク×速度)を追加できる。
- **リングバッファ**: チャネルごとに numpy 事前確保(既定 120 s × 1 kHz)、`(t, value)`、書込み 1 スレッド・読出はロック下でコピー(公式 SDK の半書込みバグを回避)。
- **Recorder**: 選択チャネルを CSV へ(開始/停止、トリガ条件付き開始は後続)。メタデータ(プロファイル、パラメータ、日時)をヘッダに。

#### 4.6.1 消費電力の概算(phase4-fb-X)

`core/telemetry/power.py` の純関数 `estimate_power(τ, ω, spec)`: `P_in = τ·ω + 1.5·R_ph·(|τ|/Kt)² + P_base`(負 = 回生)。係数はプロファイル
`[power_estimate]`(`kt` 0.69 N·m/Apk = el05_spec.md、`r_phase` 1.36 Ω = RS05 線間抵抗 2.72 Ω の半分、`p_base` 1.0 W)で、**すべて未較正**
(電流モードの実測は Iq 1 A で約 0.1 N·m と仕様の約 1/7、`open_questions.md` Q-P7 未解決。EL05 の相抵抗は未公表)。`MotorService` が
フィードバックごとに `m{id}.power_est`(W)へ生の値を追記し、運転ページの表示は内訳ごとに 0.3 s のローパス(`LowPass`)をかける。バス電流の実測ではない。

### 4.7 EventBus / Session

- `EventBus`: `subscribe(topic, cb)`、`publish(topic, payload)`。コアのスレッドから発火、GUI 側アダプタが Qt の QueuedConnection で main thread に移す。
- 主なトピック: `connection.changed`, `motor.discovered`, `motor.fault`, `motor.mode_changed`, `param.updated`, `estop`, `log`.
- `Session`: 上記一式の生成・破棄、**終了時フック**(`atexit` / Qt `aboutToQuit` / 例外フック → 全モータ stop → transport close)。

### 4.8 プラグイン層 `plugin_api/`

```python
class Plugin:
    id: str; name: str; version: str; requires_gui: bool = True
    def activate(self, ctx: PluginContext) -> None: ...
    def deactivate(self) -> None: ...

class PluginContext:
    session: Session          # bus, motors, control, telemetry, events
    ui: UiRegistry | None     # CLI 時は None
    settings: PluginSettings  # プラグイン専用の永続設定
    log: logging.Logger

class UiRegistry:
    add_panel(id, title, factory, area="right", tabbed_with=None)
    add_action(menu_path, text, callback, shortcut=None)
    add_toolbar_widget(factory)
    add_scope_preset(name, channels)
# コア側の拡張点(UI 不要)
session.control.register_controller(cls)
session.telemetry.register_channel(name, unit, source|func)
transport_registry.register(name, cls, config_schema)
profile_registry.add_path(dir)
```

- **発見**: ① `src/edulite05/builtin/`(組込み)② プロジェクト直下 `plugins/<name>/`(ユーザー追加、ファイルを置くだけ)③ entry point `edulite05.plugins`(pip 配布用)。
- 有効/無効は設定画面で切替。読込失敗はそのプラグインだけ無効化してログ表示(アプリは落とさない)。
- `plugins/example_hello/` を雛形として同梱。
- 将来: スクリプトコンソール(Python REPL に `session` を公開)、シーケンス実行(YAML/Python)もプラグインとして提供。

### 4.9 GUI 層 `app/`

`QMainWindow` + `QDockWidget`。レイアウトは保存・復元(「既定に戻す」あり)。

```
┌ Toolbar: [接続▼][スキャン] | Motor:[ID 1 ▼] mode:Reset run:MIT | [Enable][Stop] [■ E-STOP] ┐
├───────────────┬──────────────────────────────────────┬──────────────────────┤
│ 接続/デバイス  │  Scope(多段プロット)  /  3D ビュー     │ 制御パネル            │
│ - アダプタ設定 │  ch 選択・時間幅・停止・カーソル        │ [MIT][速度][電流]     │
│ - モータ一覧   │                                      │ [CSP][PP][JOG][試験] │
│ ステータス     │                                      │ スライダ+数値入力     │
│ 位置/速度/Tq   │                                      │ ソフトリミット表示    │
│ 温度/故障 LED  │                                      │                      │
├───────────────┴──────────────────────────────────────┴──────────────────────┤
│ [ログ] [CANフレーム] [パラメータ] [故障] [ツール(ID変更/ゼロ/上報/ボーレート/プロトコル)] │
└ Status: TX 200fps RX 200fps  load 12%  loop 200Hz jitter 0.3ms  sim/実機 ────────────┘
```

- **E-STOP**: ツールバーの赤ボタン + `Esc` / `Pause` キー(フォーカス位置によらずアプリ全体ショートカット)。`Space` はボタン押下・入力と衝突するため使わない。
- **危険操作の確認**: Flash 保存、ID 変更、ボーレート/プロトコル切替、ゼロ設定、工場出荷相当 → 確認ダイアログ(内容と「再投入が必要」等を明示)。
- **一時適用と保存の区別**: パラメータ表で「書込み済み(未保存)」セルを色分け、Flash 保存ボタンを明示(公式の不足点 §5-5)。
- **パラメータ表**: 0x70xx を一覧・一括読出・編集・書込・保存、JSON エクスポート/インポート、差分表示。
- **故障パネル**: type 2 の 6bit と type 21 の 32bit をデコード表示、履歴、ワンクリック clear。
- **Scope**: pyqtgraph。多段(位置/速度/トルク/温度 等)、ch 追加・色、時間幅、一時停止、カーソル、オートスケール、CSV 保存。後続でトリガ・FFT・ステップ応答評価。
- **3D ビュー**: `assets/models/el05.glb` をロータ(出力軸)/ステータに分割済みで読込。出力角でロータを回転、目標位置をゴースト表示、トルク矢印。PyVista 不可時は 2D ダイヤルへ。複数台接続時は横に並べて表示。
- **複数モータ**: デバイス一覧に接続中の全モータ(ID・状態・故障 LED)を表示。制御パネルは「選択中のモータ」を操作し、複数スロットの Controller(バイラテラル等)は割当 UI を出す。Scope のチャネルはモータ ID 付き(`m1.pos`, `m2.pos`)。E-STOP は常に全台。
- **言語**: 当面は日本語のみ。ただし UI 文字列は最初から `tr("key")` 経由で `app/i18n/ja.toml` に集約し、後から `en.toml` 等を追加するだけで切替可能にする。
- **単位**: 内部は SI(rad, rad/s, N·m, A)。表示は rad/deg 切替。

### 4.10 CLI `cli/`

GUI 前の実機立ち上げ・自動試験用。
`uv run edulite05-cli ports` / `scan --port COM5` / `info 1` / `param get 1 run_mode` / `param set …` / `enable 1` / `stop 1` / `monitor 1`(テキストでフィードバック表示)/ `--sim`

---

## 5. ディレクトリ構成

```
EDULITE05_Controller/
├─ README.md                 使い方(起動方法・構成・資料の場所)
├─ CLAUDE.md                 AI エージェント向け開発規約
├─ pyproject.toml / uv.lock
├─ run.bat / run_sim.bat     ダブルクリック起動(UNC パス対策で pushd)
├─ run.sh                    Linux 用
├─ src/edulite05/
│  ├─ __main__.py            `python -m edulite05`
│  ├─ core/
│  │  ├─ transport/  base.py  at_serial.py  sim.py  pycan.py  replay.py  registry.py
│  │  ├─ protocol/   ids.py  scaling.py  private.py  mit.py  faults.py  params.py
│  │  ├─ device/     motor.py  state.py  safety.py  profile.py
│  │  ├─ control/    loop.py  controller.py  controllers/*.py
│  │  ├─ telemetry/  channels.py  ringbuffer.py  recorder.py
│  │  ├─ bus.py  session.py  events.py  settings.py
│  ├─ sim/                   motor_model.py(EL05 の物理 + プロトコル応答)
│  ├─ plugin_api/            api.py  manager.py
│  ├─ builtin/               組込みプラグイン(各パネル・Controller の登録)
│  ├─ app/                   main.py  main_window.py  qt_bridge.py  theme.py  i18n.py
│  │  ├─ widgets/            共通部品(数値+スライダ、LED、ゲージ、自動フォーム)
│  │  └─ panels/             connection  device  control  scope  viewer3d  params  faults  log  tools
│  └─ cli/                   main.py
├─ profiles/el05.toml        型式プロファイル(レンジ・既定値・パラメータ定義)
├─ assets/models/            el05.glb(STEP から生成)
├─ plugins/example_hello/    ユーザープラグイン置き場 + 雛形
├─ tools/convert_step.py     STEP→GLB 変換(開発時のみ)
├─ tests/                    unit(protocol, at parser, safety) / integration(sim) / gui(smoke)
├─ user_data/                (gitignore)設定・ログ・録画 CSV
└─ docs/
   ├─ vendor/                一次資料(編集禁止、README に出典)
   ├─ research/              調査メモ(自分で要約)
   └─ design/                本書・実装計画・未決事項・(以後)ADR
```

---

## 6. 安全設計まとめ

| リスク | 対策 |
|---|---|
| 想定外の急動作 | SafetyGuard によるクランプ、CSP/PP 有効化前の loc_ref 先書き、モード変更は停止中のみ |
| GUI フリーズ・クラッシュで指令が残る | canTimeout(0x7028) を有効化時に設定するオプション(既定 ON 推奨、例 0.5 s)/ 終了・例外フックで全停止 |
| 通信断 | 受信途絶検知 → 状態「不明」表示・制御ループ停止 |
| 誤操作 | E-STOP 常設(Esc/Space)、危険操作の確認ダイアログ、スライダは既定で安全レンジ |
| 無固定のモータが反力で暴れる | 出力段階 LOW を既定(§4.3.1)、アプリ側 + モータ側の二重制限、HIGH は固定確認付きでのみ |
| MIT の負の Kd・摩擦補償による暴走 | PC 側の項は段階ごとの小さな上限(§4.3.1)、最悪トルクに合算して制限、|v| > 0.8 × 速度上限で項を捨て kd を上げて制動(ヒステリシス 0.4)|
| 回生による過電圧 | ダンピング/手回し時の警告表示(マニュアル記載)|
| 公式ツールの危険既定値(速度モード 27 A 等)| 型式プロファイルで上限 11 A に制限 |
| MIT 停止フレームとプロトコル切替フレームが 1 バイト違い | 定数フレームをコードに固定しテストで検証、プロトコル切替はツール画面の確認付き操作のみ |

---

## 7. 性能目標(計算値、実機で要検証)

| 項目 | 目標 |
|---|---|
| 制御ループ(1 台, 公式 USB-CAN)| 200 Hz 既定、500–1000 Hz 可 |
| フィードバック取得 | MIT 時 = 制御周期、他モード 100 Hz(上報)〜 500 Hz(リフレッシュ取得)|
| グラフ描画 | 30–60 fps、8ch × 120 s 表示でも操作が重くならない |
| 3D 描画 | 30 fps(姿勢行列の更新のみ)|
| E-STOP 押下 → 送出 | < 10 ms |
