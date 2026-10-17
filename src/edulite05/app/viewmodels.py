"""GUI とコアの境界で受け渡す値型(すべて不変)。

パネルはこれらと `Bridge` の signal / メソッドだけを使う(architecture.md §4.9、phase0.md 0-5)。
内部単位は SI(rad, rad/s, N·m, A, ℃)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from edulite05.core.telemetry.power import PowerEstimate

# type 2 の故障ビット(bit21..16 を右シフトした 0..5)の順序と識別子。el05_protocol.md §2.3
FAULT_KEYS: tuple[str, ...] = (
    "undervoltage",  # bit0 低電圧
    "overcurrent",  # bit1 三相電流(過電流)
    "overtemp",  # bit2 過温
    "encoder",  # bit3 磁気エンコーダ
    "overload",  # bit4 ストール過負荷
    "uncalibrated",  # bit5 未校正
)

MODE_KEYS: tuple[str, ...] = ("reset", "cali", "motor")

# モータごとの UI 状態(ui_design.md §3)。バッジ・主ボタン・モード選択の可否はこれで決まる
UiState = Literal["offline", "disabled", "enabled", "running", "fault"]
UI_STATES: tuple[str, ...] = ("offline", "disabled", "enabled", "running", "fault")

# run_mode(0x7005)→ 短い表示キー。el05_protocol.md §3.1
RUN_MODE_KEYS: dict[int, str] = {0: "mit", 1: "pp", 2: "velocity", 3: "current", 5: "csp"}


@dataclass(frozen=True, slots=True)
class ConnectionInfo:
    connected: bool
    adapter: str = ""
    port: str = ""
    simulated: bool = False
    lost: bool = False  # 通信断(接続していたアダプタとの通信が途絶えた)。再接続を案内する
    # 読み出し専用(ログ再生)。モータへ指令・書込を送らない(画面は操作を無効にして理由を言う)
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class MotorInfo:
    id: int
    name: str
    profile_name: str = "EL05"
    # 色の枠(0 始まり。初登録順で固定、CAN ID 変更でも変わらない。ui_design.md §7.2)
    color_index: int = 0


@dataclass(frozen=True, slots=True)
class MotorViewState:
    id: int
    online: bool
    mode: str  # "reset" / "cali" / "motor"
    run_mode: int
    position: float  # rad(±4π に巻き戻した値)
    position_multi: float  # rad(多回転)
    velocity: float  # rad/s
    torque: float  # N·m
    temperature: float  # ℃
    vbus: float | None = None  # V
    faults: tuple[str, ...] = ()  # FAULT_KEYS の要素
    controller: str | None = None
    cmd_position: float | None = None
    cmd_velocity: float | None = None
    cmd_torque: float | None = None
    ui_state: UiState = "disabled"  # ui_design.md §3(offline/disabled/enabled/running/fault)
    power: PowerEstimate | None = None  # 消費電力の概算(瞬時。未較正。表示側でならす)


@dataclass(frozen=True, slots=True)
class FaultDetail:
    """発生中の故障 1 件の説明(診断ページ・バナー用。ui_design.md §5.3・§11)。"""

    key: str  # FAULT_KEYS の要素
    name: str  # 名称(例: 過温)
    description: str  # 平易な説明
    measured: float | None  # 測定値(無ければ None)
    threshold: float | None  # 閾値(無ければ None)
    unit: str  # 測定値・閾値の単位
    causes: tuple[str, ...]  # 考えられる原因
    remedy: str  # 対処
    count: int  # 同じ故障の発生回数(繰返しはまとめて件数で出す)
    first_t: float  # 初回発生時刻(epoch 秒)
    last_t: float  # 直近の発生時刻(epoch 秒)


@dataclass(frozen=True, slots=True)
class LogEntry:
    t: float  # epoch 秒
    level: str  # "info" / "warn" / "error" / "debug"
    text: str


@dataclass(frozen=True, slots=True)
class FrameEntry:
    t: float  # epoch 秒
    direction: str  # "TX" / "RX"
    can_id: int  # 29bit 拡張 ID
    data: bytes
    decoded_text: str = ""


@dataclass(frozen=True, slots=True)
class ParamValue:
    index: int
    name: str
    value: float | int | None
    unit: str = ""
    dirty: bool = False  # 書込み済み(0x70xx は運転用の値で、電源を切ると工場値に戻る)


@dataclass(frozen=True, slots=True)
class ParamDef:
    """0x70xx パラメータの静的定義(Phase 1 でプロファイル TOML から供給)。"""

    index: int
    name: str
    dtype: str  # "uint8" / "uint16" / "uint32" / "float"
    unit: str
    range_text: str
    rw: str  # "R" / "W/R"
    description: str
    default: float | int | None = None
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True, slots=True)
class BusStats:
    tx_fps: float = 0.0
    rx_fps: float = 0.0
    loop_hz: float = 0.0
    jitter_ms: float = 0.0
    parse_errors: int = 0  # 受信フレームの解釈失敗の累計(接続してから)
    timeouts: int = 0  # 応答タイムアウトの累計
    retries: int | None = None  # 冪等要求の再送の累計(None = 計測していない)
    frames_dropped: int = 0  # フレーム表示の間引きで省略した累計件数(CoreBridge。毎秒 200 件まで)


@dataclass(frozen=True, slots=True)
class ChannelInfo:
    name: str  # "m1.pos"
    label: str
    unit: str
    motor_id: int
    kind: str = "pos"  # グラフ段: "pos" / "vel" / "torque" / "temp"
    command: bool = False  # 指令値(破線で重ねる)


@dataclass(frozen=True, slots=True)
class PowerLimits:
    """現在の出力段階の上限(architecture.md §4.3.1)。"""

    tier: str  # "low" / "high"
    torque: float  # N·m
    current: float  # A
    speed: float  # rad/s
    kp: float
    kd: float
    # MIT のホスト側項(PC 側で計算する負の kd・摩擦補償)の上限。0 = 使えない
    kd_negative_max: float = 0.0  # kd の負側の上限(大きさ)
    friction_comp_max: float = 0.0  # 摩擦補償 [N·m]
