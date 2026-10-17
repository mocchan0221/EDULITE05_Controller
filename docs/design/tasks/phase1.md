# Phase 1 タスク指示書 — コア通信(Qt なし)

> 言語: ユーザーへの返答・報告は日本語で行う(CLAUDE.md「言語」)。サブエージェント間のやりとりは英語でもよい。

作成: Opus / 2026-10-02。実装担当: Sonnet(並列)。
前提: `CLAUDE.md`(venv はローカル: `UV_PROJECT_ENVIRONMENT`、`python -m` 形式)、`docs/design/architecture.md` §4.1–4.4・§4.7・§4.10、**通信仕様の正は `docs/research/el05_protocol.md`**、アダプタは `docs/research/can_transport.md` §2・§5。

ゴール: `uv run --frozen python -m edulite05.cli --port COM7 scan` で実機の EL05 を見つけ、UID・バージョン・全 0x70xx パラメータを読み、主動上報のフィードバックを表示できる(HW-1)。シミュレータでも同じ CLI が動く。**このフェーズではモータを回すコマンドは作らない**(有効化/無効化のみ。回転指令は Phase 2 で SafetyGuard と一緒に入れる)。

## 0. 共通ルール
- `core/` と `sim/` は Qt を import しない。純粋な部分(プロトコル・パーサ)は I/O なし・状態なし
- 数値はすべてプロファイル(`profiles/el05.toml`)から。EL05 の値をコードに直書きしない
- 仕様由来の処理には出典コメント(例 `# el05_protocol.md §2.2`)
- 時刻は `core.clock.now()`(= `time.perf_counter()`、秒、float)。Windows の Python 3.12 では `time.monotonic()` の分解能が約 15.6 ms しかないため使わない(2026-10-02 変更)
- スレッド間の結果受け渡しは `concurrent.futures.Future`
- 例外: トランスポート系は `TransportError`、プロトコル系は `ProtocolError`、要求のタイムアウトは `RequestTimeout`(すべて `core/errors.py`)

---

## 1. インタフェース定義(このとおりに実装する。変更が必要なら最終報告で提案)

### 1.1 `core/transport/base.py`
```python
class Direction(enum.Enum): RX = "rx"; TX = "tx"

@dataclass(frozen=True, slots=True)
class CanFrame:
    can_id: int                 # 29bit(extended)/ 11bit(standard)
    data: bytes                 # 0..8 バイト
    extended: bool = True
    timestamp: float = 0.0      # monotonic
    channel: int = 0
    direction: Direction = Direction.RX
    def with_meta(self, *, timestamp=None, direction=None) -> "CanFrame"  # dataclasses.replace の薄いラッパ

@dataclass(frozen=True, slots=True)
class TransportCaps:
    name: str; hw_timestamp: bool; bus_state: bool; standard_frames: bool; max_fps_hint: int

@dataclass(slots=True)
class TransportStats:            # 実装側が更新、読む側は snapshot() のコピーを使う
    tx_frames: int = 0; rx_frames: int = 0; parse_errors: int = 0; resyncs: int = 0
    io_errors: int = 0; last_rx: float | None = None

class CanTransport(ABC):
    caps: TransportCaps
    @abstractmethod
    def open(self) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    @property
    @abstractmethod
    def is_open(self) -> bool: ...
    @abstractmethod
    def send(self, frame: CanFrame) -> None: ...
    def send_many(self, frames: Sequence[CanFrame]) -> None: ...   # 既定は send を繰り返す。AT は 1 write に連結
    @abstractmethod
    def recv(self, timeout: float) -> CanFrame | None: ...          # RX スレッド専用。timeout で None
    @abstractmethod
    def stats(self) -> TransportStats: ...                           # コピーを返す
```
`core/transport/registry.py`: `register(name, factory, describe)`、`create(name, **kwargs) -> CanTransport`、`available() -> list[TransportInfo(name, label, options_schema)]`。組込み: `"robstride_at"`(AtSerialTransport)、`"sim"`(SimTransport)。

### 1.2 `core/transport/at_serial.py`(公式 USB-CAN、can_transport.md §2)
- `encode_at(frame: CanFrame) -> bytes`: `b"AT" + struct.pack(">I", (can_id << 3) | 0x04) + bytes([len]) + data + b"\r\n"`(拡張)。**標準フレーム**は `(can_id << 21)`、IDE=0 で推定実装し、関数 docstring に「未検証(Q-T1)」と明記
- `class AtFrameParser`: `feed(chunk: bytes) -> list[CanFrame]`。`AT` 探索 → DLC ≤ 8 確認 → 全長が揃うまで待つ → 末尾 `0D 0A` 検証 → bit2 で拡張/標準判定。失敗時は 1 バイト進めて再同期(`parse_errors`/`resyncs` を数える)。データ中の `41 54`/`0D 0A` で誤動作しないこと
- `class AtSerialTransport(CanTransport)`: `__init__(port: str, baudrate: int = 921600, read_chunk: int = 4096)`。`serial.Serial(port, baudrate, timeout=0.005, write_timeout=0.2, exclusive=True)`、open 直後に `reset_input_buffer()`。**AT 設定コマンドは送らない**。`recv` は内部バッファ(deque)から返し、空なら `read(in_waiting or 1)` → parser。`send_many` は 1 回の `write`。送信フレームは `direction=TX`、受信は `RX` + monotonic 時刻
- `list_ports() -> list[PortInfo(device, description, vid, pid, preferred: bool)]`(1A86:7523 を preferred、先頭に)

### 1.3 `core/transport/framelog.py`
candump `-L` 互換の 1 行 1 フレーム: `(1696000000.123456) at0 1200FD01#0570000001000000`(TX は行末に ` T`)。`FrameLogWriter(path)`(スレッド安全、`write(frame)`)、`read_framelog(path) -> Iterator[CanFrame]`。

### 1.4 `core/protocol/`(純粋関数)
- `ids.py`: `class CommType(IntEnum)`(GET_ID=0, MIT_CTRL=1, FEEDBACK=2, ENABLE=3, STOP=4, SET_ZERO=6, SET_CAN_ID=7, PARAM_READ=17, PARAM_WRITE=18, FAULT=21, SAVE=22, BAUD=23, ACTIVE_REPORT=24, PROTOCOL=25, VERSION_ALT=26)、`make_id(comm_type, data2, dest) -> int`、`parse_id(can_id) -> IdFields(comm_type, data2, dest)`
- `scaling.py`: `float_to_uint(x, lo, hi, bits) -> int`(クランプ込み)、`uint_to_float(u, lo, hi, bits) -> float`
- `types.py`: `class DType(Enum)`: UINT8/UINT16/UINT32/INT16/FLOAT、`pack_value(dtype, value) -> bytes(4)`(LE、B4 から詰める)、`unpack_value(dtype, b4: bytes) -> int|float`;`class RunMode(IntEnum)`: OPERATION=0, PP=1, VELOCITY=2, CURRENT=3, CSP=5;`class MotorMode(IntEnum)`: RESET=0, CALI=1, MOTOR=2;`@dataclass Ranges(position, velocity, torque, kp, kd)`(各 `(lo, hi)`)
- `private.py` エンコーダ(すべて `CanFrame` を返す、`host` 既定は呼び出し側で渡す):
  `get_uid(motor, host)`, `enable(motor, host)`, `stop(motor, host, clear_fault=False)`, `mit_command(motor, host, ranges, position, velocity, kp, kd, torque)`, `read_param(motor, host, index)`, `write_param(motor, host, index, dtype, value)`, `set_zero(motor, host)`, `set_can_id(motor, host, new_id)`, `save_params(motor, host)`, `set_baud(motor, host, code)`, `set_active_report(motor, host, on: bool)`, `switch_protocol(motor, host, protocol: int)`, `read_version(motor, host, alt=False)`(type 4 / alt=True で type 26)
- `private.py` デコーダ: `decode(frame, ctx: DecodeContext) -> Message`。`DecodeContext(host_id: int, ranges: Ranges, param_dtypes: Mapping[int, DType])`
  - `Message` = `Feedback | ParamResponse | FaultReport | UidResponse | Unknown`(frozen dataclass、すべて `motor_id` と `frame` を持つ)
  - `Feedback(motor_id, comm_type(2|24), mode: MotorMode, fault_bits: int, position, velocity, torque, temperature)`
  - `ParamResponse(motor_id, index, ok: bool, value: int|float|None, raw: bytes)`(dtype 不明なら value=None)
  - `FaultReport(motor_id, fault: int, warning: int)`、`UidResponse(motor_id, uid: bytes)`(dest==0xFE)、`Unknown(frame, reason)`
  - 宛先(bit7..0)が `ctx.host_id` でも 0xFE でもないフレームは `Unknown(reason="not for host")`
  - `try_parse_version(frame) -> VersionInfo | None`(type 2 の ID で data[0:3] == 00 C4 56 のとき。版数は B3–B6 を `".".join` した文字列 + raw)
- `faults.py`: `FEEDBACK_FAULTS`(6bit の (bit, key, 日本語名, 説明))、`FAULT21_BITS`、`WARNING21_BITS`、`describe_feedback_faults(bits) -> list[FaultInfo]`、`describe_fault21(fault, warning) -> list[FaultInfo]`(出典 el05_protocol.md §2.3/§2.11)
- **テスト**: el05_protocol.md 内の全バイト例(type 0/1/3/4/6/7/17/18/22/23/24/25、バージョン読出、AT 例 `41 54 90 07 E8 0C 08 05 70 00 00 01 00 00 00 0D 0A`)を、そのまま期待値にする

### 1.5 `profiles/el05.toml` と `core/device/profile.py`
```toml
[motor]
model = "EL05"
name = "RobStride EduLite 05"
[ranges]            # type 1/2 のスケーリング(el05_protocol.md §1.5)
position = [-12.57, 12.57]
velocity = [-50.0, 50.0]
torque = [-6.0, 6.0]
kp = [0.0, 500.0]
kd = [0.0, 5.0]
[limits]            # 物理上限
current = 11.0
speed = 50.0
torque = 6.0
temp_fault = 135.0
[power_tiers.low]   # architecture.md §4.3.1
torque = 0.5
current = 1.0
speed = 5.0
kp = 20.0
kd = 1.0
[power_tiers.high]
torque = 6.0
current = 11.0
speed = 50.0
kp = 500.0
kd = 5.0
[[params]]          # el05_protocol.md §5.2 の全行
index = 0x7005
key = "run_mode"
label = "動作モード"
group = "指令"      # 制御ゲイン / 制限 / 指令 / 状態 / 設定(app/pages/params/groups.py と合わせる)
dtype = "uint8"
unit = ""
min = 0
max = 5
default = 0
access = "rw"       # rw / r
description = "0 運控, 1 PP, 2 速度, 3 電流, 5 CSP"
```
`MotorProfile.load(path) / load_builtin("el05")`、`profile.param(index_or_key) -> ParamDef`、`profile.param_dtypes() -> dict[int, DType]`、`profile.tier(name) -> TierLimits`。UI の `demo_params.py` / `pages/params/groups.py` の日本語名・グループは**プロファイルを正**として揃える(UI 側の差し替えは Phase 3)。

### 1.6 `core/events.py`
`EventBus`: `subscribe(topic: str, cb) -> Unsubscribe`、`publish(topic, payload)`(呼び出しスレッドで同期実行、コールバック例外はログして握りつぶす)。トピック定数を同ファイルに。

### 1.7 `core/bus.py` — BusManager
```python
class Priority(IntEnum): ESTOP = 0; CONTROL = 1; REQUEST = 2; BACKGROUND = 3

class BusManager:
    def __init__(self, transport: CanTransport, ctx: DecodeContext, events: EventBus | None = None, framelog: FrameLogWriter | None = None): ...
    def start(self) -> None          # transport.open() 済み前提。RX スレッドと TX スレッドを起動
    def stop(self) -> None           # スレッド停止(join)。transport は閉じない
    def send(self, frame, priority=Priority.REQUEST) -> None            # 非ブロッキング(TX キュー)
    def send_now(self, frames: Sequence[CanFrame]) -> None              # E-STOP 用。キューを飛ばして即送信(TX ロックのみ)
    def request(self, frame, match: Callable[[Message], bool], timeout: float = 0.1,
                priority=Priority.REQUEST, raw_match: Callable[[CanFrame], bool] | None = None) -> Future[Message]
    def subscribe(self, cb: Callable[[Message], None]) -> Unsubscribe  # RX スレッドから呼ばれる
    def scan(self, ids: Iterable[int] = range(1, 128), gap_s=0.002, settle_s=0.3) -> Future[list[UidResponse]]
    def stats(self) -> BusStats      # tx/rx fps(直近 1 s), queue 長, timeouts, parse_errors(transport から)
```
- TX: 優先度付きキュー(heapq + Condition、同優先度は FIFO)
- RX: `transport.recv(0.02)` → decode → (raw_match / match を持つ pending を先に照合)→ subscribers → events
- request の期限切れは `RequestTimeout` で Future を失敗させる(監視は RX スレッドのループ内で monotonic 判定)
- 例外で落ちない(ループ内で捕捉 → events に error)

### 1.8 `core/device/state.py` / `motor.py`
```python
@dataclass(frozen=True, slots=True)
class MotorState:
    motor_id: int; online: bool; mode: MotorMode | None; run_mode: RunMode | None; fault_bits: int
    position: float; position_multi: float; velocity: float; torque: float; temperature: float
    last_rx: float | None; rx_rate: float   # 直近 1 s のフィードバック数

class Motor:
    def __init__(self, bus: BusManager, motor_id: int, profile: MotorProfile, host_id: int): ...
    state: MotorState                     # 最新(Feedback を受けるたびに置換。読み出しはロック不要の参照取得)
    uid: bytes | None; version: str | None
    def get_uid(self) -> Future[UidResponse]
    def read_version(self) -> Future[VersionInfo]          # type 4 方式 → タイムアウトなら type 26 を試す
    def read_param(self, key_or_index) -> Future[ParamResponse]
    def read_all_params(self) -> Future[dict[int, ParamResponse]]   # プロファイルの全 params を順に
    def write_param(self, key_or_index, value) -> Future[Feedback]  # dtype はプロファイルから。範囲外は ProtocolError
    def enable(self) -> Future[Feedback]; def disable(self, clear_fault=False) -> Future[Feedback]
    def set_run_mode(self, mode: RunMode) -> Future[Feedback]       # disabled のときのみ(違えば ProtocolError)
    def set_active_report(self, on: bool, period_ms: int | None = None) -> Future[None]
    def save_params(self) -> Future[Feedback]; def set_zero(self) -> Future[Feedback]
    # set_can_id / set_baud / switch_protocol は Phase 4(保守)で追加。今は作らない
```
- `position_multi`: type 2 の角度は ±4π で周期ループ → 前回値との差が半周期を超えたら周期(= range 幅)を加減して unwrap
- 応答の照合キー: 送信先モータ ID + 期待する通信タイプ(+ param index)。type 2 応答はモータ ID 一致のみで照合(同時に複数要求を出さない: Motor 内で要求を直列化するロックを持つ)

### 1.9 `core/session.py`
`Session(transport, profile, host_id=0xFD, framelog_path=None)`: `open()`(transport.open + bus.start)、`close()`(**接続中の全モータへ stop を send_now** → bus.stop → transport.close)、`scan() -> Future[list[Motor]]`(見つかった ID で Motor を生成し `motors` に登録)、`motors: dict[int, Motor]`、`events: EventBus`。`with Session(...) as s:` 対応。`atexit` で close。

### 1.10 `sim/`(フレームレベルの EL05 シミュレータ)
- `sim/motor_model.py`: `SimMotor(motor_id, profile, uid)`: 状態(mode, run_mode, params 辞書(プロファイル既定値), 位置・速度・トルク・温度、active_report on/period、fault bits)、`handle(frame) -> list[CanFrame]`(応答)、`step(dt)`(1 自由度: J·dω = τ − b·ω、τ はモード別: MIT は PD 則、速度は PI で limit_cur まで、電流は Kt·iq、CSP/PP は位置計画 + 速度制御。数値はそれらしい値でよいが、**プロトコル応答は el05_protocol.md と完全一致**させる)、主動上報フレームの生成
- `sim/transport.py`: `SimTransport(motors: list[SimMotor], latency_s=0.0005, drop_rate=0.0, physics_hz=1000)` は CanTransport 実装。内部スレッドで物理を回し、送信フレームを各 SimMotor に配って応答を受信キューへ
- 故障注入: `SimMotor.inject_fault(bit)` / `clear`
- CLI と統合テストは SimTransport で実機と同じ流れが通ること

### 1.11 `cli/main.py`(`python -m edulite05.cli`。`__main__.py` も用意)
共通オプション: `--port COM7` | `--sim [N]`(N 台のシミュレータ、既定 1)、`--host 0xFD`、`--log frames.log`、`--timeout`。
サブコマンド:
- `ports` — COM 一覧(preferred に ★)
- `scan [--ids 1-127]` — 見つかった ID と UID を表示
- `info ID` — UID、バージョン、現在の mode/fault
- `params ID [--group G] [--json]` — 全 0x70xx を表(index, key, label, value, unit)
- `param get ID KEY|INDEX` / `param set ID KEY|INDEX VALUE [--save]` — set は **書込可能かつ「状態」以外**、かつ範囲内のみ。`run_mode` 等も可(モータは回らない)
- `enable ID` / `disable ID [--clear-fault]`
- `monitor ID [--rate HZ] [--seconds S]` — 主動上報を ON にして(終了時 OFF)、位置・速度・トルク・温度・mode・故障を 1 行で更新表示。Ctrl+C で上報 OFF + disable
- `raw ID TYPE DATA2 HEXDATA --i-know-what-im-doing` — デバッグ用の生送信(既定は拒否)
出力は日本語、終了コード: 成功 0 / タイムアウト 2 / その他 1。

---

## 2. 担当と順序

| 担当 | 範囲 | 依存 |
|---|---|---|
| **A** | §1.4 protocol + §1.5 profile(toml とローダ)+ `core/errors.py` + テスト | なし |
| **B** | §1.1 base + registry、§1.2 AT、§1.3 framelog + テスト | なし(CanFrame は B が作る。A は B の base.py ができるまで同じ定義で書いてよい → B が先に base.py だけコミットする) |
| **C** | §1.10 sim | A, B |
| **D** | §1.6 events、§1.7 bus、§1.8 motor/state、§1.9 session + テスト(テストは C 完成前はループバックの FakeTransport で) | A, B |
| **E** | §1.11 CLI + Sim 統合テスト(`tests/integration/`) | C, D |

並列: (A, B) → (C, D) → E。各担当は自分のファイルのみ編集、コミットは `phase1-<担当>: …` + `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`。受入: `python -m pytest` 全件(GUI 含む)パス、`python -m ruff check .` クリーン。

## 3. HW-1(Opus が CLI で実施。モータは回さない)
`ports` → `scan` → `info` → `params` → `monitor`(手でハンドルを回して値が変わること)→ `enable` / `disable`(回らないことをカメラで確認)。生フレームは `user_data/hw1_frames.log` に記録し、結果を `docs/research/el05_protocol.md` §8.3 と `open_questions.md` に反映する。
