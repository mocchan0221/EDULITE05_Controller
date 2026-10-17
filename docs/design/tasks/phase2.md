# Phase 2 タスク指示書 — 制御・安全・テレメトリ(Qt なし)

> 言語: ユーザーへの返答・報告は日本語で行う(CLAUDE.md「言語」)。サブエージェント間のやりとりは英語でもよい。

作成: Opus / 2026-10-02。実装担当: Sonnet(並列)。
前提: `CLAUDE.md`、`docs/design/architecture.md` §4.3–4.7・§6、`docs/design/ui_design.md` §3(状態モデル)・§12(停止の定義)、`docs/research/el05_protocol.md` §3(モード手順)・§8.0(実機確認済み事項)、Phase 1 の実装(`core/bus.py`, `core/device/motor.py`, `core/session.py`, `core/clock.py`, `sim/`)。

ゴール: Qt なしのコアだけで、**安全制限付きで各モードを運転できる**。CLI から LOW 段階で実機を回せる(HW-2)。Phase 3 で GUI(CoreBridge)がこの API の上に載る。

## 0. 原則
- 全指令は `SafetyGuard` を通る。迂回経路を作らない(`Motor` の生の書込み API は運転系パラメータ(iq_ref/spd_ref/loc_ref/run_mode/limit_*)を拒否し、`MotorService` 経由のみにする)
- **有効なモータには必ず周期的にフレームを送る**(制御ループが目標値を再送)→ モータ側の `canTimeout` を常に設定できる
- 停止の種類を混同しない: **運転停止**(目標を 0/現在位置保持にして有効のまま)/ **無効化**(type 4)/ **非常停止**(全台 type 4 を send_now + ラッチ + LOW へ)
- NaN / inf の指令は拒否(`ProtocolError` ではなく `SafetyError`、`core/errors.py` に追加)
- 時刻は `core.clock.now()`

## 1. インタフェース

### 1.1 出力段階と安全 `core/safety.py`
```python
class PowerTier(Enum): LOW = "low"; HIGH = "high"

@dataclass(frozen=True, slots=True)
class SoftLimits:                    # ユーザー設定(任意)。None は無制限(段階の上限のみ)
    position_min: float | None = None; position_max: float | None = None

class SafetyGuard:
    def __init__(self, profile: MotorProfile, tier: PowerTier = PowerTier.LOW, soft: SoftLimits = SoftLimits()): ...
    tier: PowerTier; limits: TierLimits        # 現在段階の上限(profile.tier(tier.value))
    def clamp_mit(self, cmd: MitCmd, state: MotorState) -> tuple[MitCmd, list[ClampNote]]
    def clamp_setpoint(self, cmd: SetpointCmd) -> tuple[SetpointCmd, list[ClampNote]]
    def motor_side_limits(self) -> dict[str, float]   # {"limit_torque":…, "limit_cur":…, "limit_spd":…}
```
- `MitCmd(position, velocity, kp, kd, torque)`: kp ≤ tier.kp、kd ≤ tier.kd、|torque| ≤ tier.torque、|velocity| ≤ tier.speed。さらに**最悪トルク制限**: `|kp·(p_set − p)| + |kd·(v_set − v)| + |t_ff| ≤ tier.torque` となるよう、まず t_ff、次に p_set を p 側へ、次に v_set を v 側へ寄せる(state は最新フィードバック)。ソフトリミットで p_set をクランプ
- `SetpointCmd(kind: Literal["velocity","current","position"], value, speed_limit=None, current_limit=None, accel=None)`: velocity → |v| ≤ tier.speed、current → |i| ≤ tier.current、position → ソフトリミット + speed_limit ≤ tier.speed
- `ClampNote(field, requested, applied, reason)`: UI の「低出力上限で制限中」表示に使う

### 1.2 指令型 `core/control/commands.py`
`MitCmd`、`SetpointCmd`、`HoldCmd`(= 現在位置保持。MIT モードなら kp/kd 既定で p_set=p、位置モードなら loc_ref=p)。すべて frozen dataclass。

### 1.3 `core/control/controller.py`(architecture.md §4.5 を確定)
```python
class Controller(ABC):
    name: ClassVar[str]; label: ClassVar[str]          # label は tr キー
    run_mode: ClassVar[RunMode]
    slots: ClassVar[tuple[str, ...]] = ("motor",)
    Params: ClassVar[type]                             # core/control/params.py の dataclass(既存。足りなければ追加)
    def __init__(self, params): self.params = params
    def update_params(self, params) -> None            # 運転中の目標変更(スレッド安全に差し替え)
    def on_start(self, states: dict[str, MotorState]) -> None
    def step(self, t: float, dt: float, states: dict[str, MotorState]) -> dict[str, Command]
    def on_stop(self, states) -> dict[str, Command]    # 運転停止時に 1 回送る指令(速度 0 / 現在位置保持 など)
```
組込み(`core/control/controllers/`): `MitDirect`, `Jog`(run_mode=VELOCITY。`set_direction(-1|0|+1)` で押下中だけ ±speed、0 で速度 0)、`Velocity`, `Current`, `Csp`, `Pp`, `SineTest`, `StepTest`(対象: position/velocity/current。run_mode は対象に応じて選ぶ)、`ZeroReturn`(CSP で 0 rad へ)。`Bilateral` は Phase 5。

### 1.4 `core/control/loop.py` — ControlLoop
```python
class ControlLoop:
    def __init__(self, bus: BusManager, guard: SafetyGuard, rate_hz: float = 200.0): ...
    def start(self) / stop(self)
    def attach(self, controller: Controller, assign: dict[str, Motor]) -> Handle   # 1 モータは 1 コントローラにのみ
    def detach(self, handle) -> None             # on_stop の指令を送ってから外す
    def keepalive(self, motor: Motor, cmd: Command) -> None   # コントローラ無しで有効なモータへ周期送信する指令(保持)
    stats -> LoopStats(rate_hz, jitter_ms_p99, overruns, last_dt)
```
- スレッド 1 本。`perf_counter` の絶対時刻で周期を刻む。Windows では開始時に `timeBeginPeriod(1)`(ctypes、終了時に `timeEndPeriod`)、各周期の残り 1 ms 未満はスピン
- 1 周期で全モータ分の指令を SafetyGuard → エンコード → `bus.send_many(…, Priority.CONTROL)`(BusManager に `send_many` が無ければ追加)
- MitCmd → type 1。SetpointCmd → type 18(spd_ref / iq_ref / loc_ref。speed_limit などは変化したときだけ書く)
- 例外はコントローラ単位で捕捉 → そのコントローラを外し、対象モータを無効化、イベント発行

### 1.5 `core/control/service.py` — MotorService(UI と CLI が使う唯一の運転 API)
```python
class UiState(Enum): OFFLINE, DISABLED, ENABLED, RUNNING, FAULT       # ui_design.md §3

class MotorService:
    def __init__(self, session: Session, rate_hz=200.0, tier=PowerTier.LOW): ...
    tier: PowerTier; estop_latched: bool
    def ui_state(self, motor_id) -> UiState
    def enable(self, motor_id, run_mode: RunMode) -> Future[None]
        # disabled のみ。stop → run_mode 書込 → モータ側 limit 書込(guard.motor_side_limits)→ canTimeout 書込
        # → (CSP/PP: loc_ref = mechPos 読出値)→ type 3 → keepalive(Hold)開始
    def start(self, motor_ids, controller: Controller) -> Future[None]   # enabled のみ。run_mode 一致を確認
    def update(self, motor_ids, params) -> None
    def stop_motion(self, motor_ids) -> Future[None]   # 運転停止: detach(on_stop 指令)→ keepalive(Hold)。enabled に戻る
    def disable(self, motor_ids, clear_fault=False) -> Future[None]
    def clear_fault(self, motor_id) -> Future[None]
    def estop(self) -> None             # 同期・即時: 全モータ type 4 を send_now → 全 detach → ラッチ → tier=LOW
    def release_estop(self) -> None     # ラッチ解除のみ。モータは disabled のまま
    def set_tier(self, tier) -> bool    # HIGH は全モータ disabled かつラッチなしのときだけ。LOW へはいつでも(有効なモータのモータ側 limit を即書換)
    events: "service.state_changed", "service.estop", "service.tier", "service.clamped", "service.fault", "service.comm_lost"
```
- **通信監視**: 有効なモータのフィードバックが `comm_timeout_s`(既定 0.25 s)来なければ、そのモータを無効化(send_now)して `comm_lost`。アダプタ切断(TransportError)なら全モータ offline・ラッチなし・tier=LOW
- **故障**: フィードバックの fault_bits ≠ 0 または type 21 → 該当モータの制御を外す → ui_state=FAULT。clear_fault は disabled+clear(type 4 B0=1)→ 故障が消えたら DISABLED
- **canTimeout**: 有効化時に `canTimeout = round(comm_timeout_s / 50e-6)` を書く(既定 0.5 s = 10000)。無効化時は 0 に戻さない(安全側)
- **テレメトリ**: 接続中は全モータの主動上報を ON(10 ms)。運転中は type 1/18 の応答 type 2 も来る。低速チャネル(iqf, VBUS, mechPos)は BACKGROUND 優先で 10 Hz ラウンドロビン

### 1.6 `core/telemetry/` — TelemetryHub
```python
class TelemetryHub:
    def __init__(self, capacity_s: float = 120.0, max_rate_hz: float = 1000.0): ...
    def register(self, name: str, unit: str, motor_id: int | None, kind: str, label: str) -> None
    def append(self, name: str, t: float, value: float) -> None          # 書込みは RX スレッド等 1 スレッドから
    def channels(self) -> list[ChannelInfo]
    def window(self, names: Sequence[str], seconds: float, until: float | None = None) -> dict[str, tuple[np.ndarray, np.ndarray]]   # (t, v) のコピー、t は clock.now() 基準の絶対時刻
    def latest(self, name) -> tuple[float, float] | None
class Recorder:  # CSV 記録(開始/停止、選択チャネル、ヘッダにプロファイル名・日時・段階)
```
- チャネル: `m{id}.pos`, `.pos_multi`, `.vel`, `.tq`, `.temp`, `.vbus`, `.iqf`, `.cmd.pos`, `.cmd.vel`, `.cmd.tq`, `.cmd.cur`(指令は ControlLoop が送った値)
- 固定長 numpy リングバッファ(チャネル毎に capacity_s × max_rate_hz)。`window` はロック下でコピー

### 1.7 CLI 追加(HW-2 用)`cli/main.py`
すべて `--yes` 必須、既定は LOW、終了時(正常・Ctrl+C・例外)に必ず運転停止 → 無効化:
- `jog ID --speed 1.0 --seconds 2 [--reverse]`
- `velocity ID --speed 1.0 --seconds 3`
- `current ID --iq 0.3 --seconds 2`
- `mit ID --kp 5 --kd 0.3 --position P --seconds 3` / `mit ID --damping --kd 0.5 --seconds 5`(手で回して減衰を感じる)
- `csp ID --position P --speed-limit 1.0`(現在位置からの相対 `--relative` も)
- `sine ID --target velocity --amplitude 1 --frequency 0.5 --seconds 6`
- 実行中は monitor と同じ 1 行表示 + クランプ発生時はその旨を表示。`--tier high` は受け付けない(Phase 2 の CLI では LOW のみ)

## 2. 担当と順序

| 担当 | 範囲 | 依存 |
|---|---|---|
| **F** | §1.1 safety + §1.2 commands + §1.6 telemetry + テスト | なし |
| **G** | §1.3 controller + controllers + §1.4 loop + テスト(Sim と FakeBus で) | F の commands/safety(F が先に commands.py と safety.py の骨格をコミット) |
| **H** | §1.5 service + 通信監視/故障/テレメトリ取得 + §1.7 CLI + 統合テスト(Sim 3 台: 個別・同時・全台 E-STOP・通信断・故障) | F, G |

並列: F →(G、H は F の骨格コミット後に service の骨組みから着手可)→ H 仕上げ。
受入: `python -m pytest` 全件、`ruff` クリーン。シミュレータで全 CLI 運転コマンドが動き、終了時に必ず無効化されることをテストで確認。

## 3. HW-2(Opus が CLI で実施。**ユーザーの了承を得てから**。LOW のみ、カメラで目視)
1. `velocity 127 --speed 0.5 --seconds 2` → カメラで回転方向と量を確認、終了後に停止
2. `jog` 正逆 1 rad/s × 1 s
3. `mit --damping` を有効にした状態で、ユーザーにハンドルを手で回してもらい減衰を確認
4. `csp --relative --position 0.5 --speed-limit 0.5` で小角度移動
5. 運転中に Ctrl+C / 非常停止相当(`estop`)→ 即停止を確認
6. 制御周期・往復遅延・ジッタを計測し、LOW/HIGH の数値と性能目標を見直す(architecture.md §7)
7. 運控モードで limit_torque が PD 出力を制限するか(Q-P11)を、手で外乱を与えて確認
