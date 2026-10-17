"""`CoreBridge`: 画面(`Bridge`)を本物のコア(`MotorService`)の上に実装する(phase3.md §2)。

スレッドの設計(GUI スレッドはコアを待たない):

    コアのスレッド(RX / TX / 制御ループ / 監視 / ワーカー)
        │ EventBus のコールバック・Future の done コールバック・フレームタップ
        ▼
    スレッド安全なキュー(`queue.SimpleQueue` / `deque`)  … 積むだけ。ここでは何も判断しない
        │
        ▼
    GUI スレッドの QTimer(16 ms ≒ 60 Hz)`_tick`
        ・キューを取り出して signal を発行(ログ・接続・E-STOP・出力段階 …)
        ・`state_updated`(MotorViewState)を約 60 Hz で発行
        ・`frame_logged`(最大 200 件/秒に間引き。落とした件数は `BusStats.frames_dropped`)
        ・`stats_updated` を 2 Hz で発行

GUI スレッドから呼ぶ API は、待ちが生じうる処理(接続・切断・スキャン・有効化・パラメータ読書き)を
すべてワーカーまたは Future に任せ、結果は `add_done_callback` → キュー経由で GUI スレッドに戻す。
同期で呼ぶのは待たずに戻るもの(`start` / `update` / `stop_motion` / `estop` / `set_tier`)だけ。

接続ごとに世代番号(`_gen`)を持ち、切断・再接続後に古い接続から届いた通知は捨てる。
"""

from __future__ import annotations

import dataclasses
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import catalog, tr
from edulite05.app.ports import list_serial_ports
from edulite05.app.viewmodels import (
    FAULT_KEYS,
    BusStats,
    ChannelInfo,
    ConnectionInfo,
    FaultDetail,
    FrameEntry,
    LogEntry,
    MotorInfo,
    MotorViewState,
    ParamDef,
    ParamValue,
    PowerLimits,
)
from edulite05.core import clock
from edulite05.core.control.controllers import CONTROLLERS
from edulite05.core.control.controllers.jog import Jog
from edulite05.core.control.service import (
    SERVICE_CLAMPED,
    SERVICE_COMM_LOST,
    SERVICE_CONTROL_ERROR,
    SERVICE_ESTOP,
    SERVICE_FAULT,
    SERVICE_MOTORS_CHANGED,
    SERVICE_STATE_CHANGED,
    SERVICE_TIER,
    MotorService,
    UiState,
)
from edulite05.core.device.profile import MotorProfile
from edulite05.core.events import (
    BUS_ERROR,
    CONNECTION_CHANGED,
    LOG,
    MOTOR_DISCOVERED,
    MOTOR_RETRY,
)
from edulite05.core.protocol.faults import (
    FaultInfo,
    describe_fault21,
    describe_feedback_faults,
)
from edulite05.core.protocol.ids import CommType, parse_id
from edulite05.core.protocol.private import (
    DecodeContext,
    FaultReport,
    Feedback,
    ParamResponse,
    UidResponse,
    decode,
)
from edulite05.core.protocol.scaling import uint_to_float
from edulite05.core.protocol.types import RunMode, unpack_value
from edulite05.core.safety import PowerTier
from edulite05.core.session import Session
from edulite05.core.telemetry import Recorder, TelemetryHub, motor_channel
from edulite05.core.telemetry.power import estimate_power
from edulite05.core.transport import registry
from edulite05.core.transport.base import CanFrame, CanTransport, Direction
from edulite05.core.transport.replay import motor_ids_in_log
from edulite05.plugin_api import runtime as plugin_runtime

log = logging.getLogger(__name__)

TICK_MS = 16  # GUI スレッドでキューを空にして状態を発行する周期(≒ 60 Hz)
STATS_PERIOD_S = 0.5
FRAME_RATE_LIMIT = 200  # frame_logged の上限 [件/秒]
FRAME_BURST = 50  # 間引きのバケツ(一度に通せる件数)
MAX_FRAMES_PER_TICK = 64
MAX_EVENTS_PER_TICK = 400
SCAN_IDS = range(1, 128)
MIN_PERIOD_MS = 2  # 送信周期の下限(実機は送信間隔 1 ms 以上が必要。hw_bench.md)
CMD_FRESH_S = 0.3  # 指令値(cmd.*)をこれより古ければ表示しない
VBUS_FRESH_S = 3.0  # 母線電圧(低速ポーリング ≒ 3 Hz)をこれより古ければ表示しない
GAP_S = 0.15  # 指令チャネルでこれ以上サンプルが空いたら線を切る(NaN を挟む)
DEFAULT_SIM_MOTORS = 2

# UI のチャネル接尾辞 → (TelemetryHub のチャネル接尾辞, ラベル tr キー, 単位, グラフ段, 指令か)
_CHANNELS: tuple[tuple[str, str, str, str, str, bool], ...] = (
    ("pos", "pos_multi", "channel.pos", "rad", "pos", False),
    ("vel", "vel", "channel.vel", "rad/s", "vel", False),
    ("torque", "tq", "channel.torque", "N·m", "torque", False),
    ("temp", "temp", "channel.temp", "℃", "temp", False),
    ("vbus", "vbus", "channel.vbus", "V", "vbus", False),
    ("current", "iqf", "channel.current", "A", "current", False),
    ("power", "power_est", "channel.power", "W", "power", False),
    ("cmd_pos", "cmd.pos", "channel.cmd_pos", "rad", "pos", True),
    ("cmd_vel", "cmd.vel", "channel.cmd_vel", "rad/s", "vel", True),
    ("cmd_torque", "cmd.tq", "channel.cmd_torque", "N·m", "torque", True),
    ("cmd_current", "cmd.cur", "channel.cmd_current", "A", "current", True),
)

# core の故障キー → UI の故障キー(FAULT_KEYS)。無いものは UI に出す別名を持つ
_FAULT_UI_KEY: dict[str, str] = {
    "undervoltage": "undervoltage",
    "overcurrent": "overcurrent",
    "overtemp": "overtemp",
    "encoder": "encoder",
    "stall": "overload",
    "uncalibrated": "uncalibrated",
    "motor_overtemp": "overtemp",
    "motor_overtemp_warn": "overtemp",
    "phase_a_overcurrent": "overcurrent",
    "phase_b_overcurrent": "overcurrent",
    "phase_c_overcurrent": "overcurrent",
    "encoder_uncalibrated": "uncalibrated",
    "driver_ic": "driver_ic",
    "overvoltage": "overvoltage",
    "hw_id": "hw_id",
    "position_init": "position_init",
}

# 故障注入(シミュレータ): UI の故障キー → (type 21 の fault ビット, type 2 の故障ビット or None)
_SIM_FAULT: dict[str, tuple[int, int | None]] = {
    "undervoltage": (2, None),
    "overcurrent": (4, None),
    "overtemp": (0, None),
    "encoder": (9, 3),
    "overload": (14, None),
    "uncalibrated": (7, None),
}

_ADAPTERS = {"usbcan": "robstride_at", "robstride_at": "robstride_at", "sim": "sim",
             "simulator": "sim", "pycan": "pycan", "replay": "replay"}
# 接続ラベル(`adapter.<名前>`)。ここに無いものは公式 USB-CAN
_ADAPTER_LABEL = {"sim": "adapter.simulator", "pycan": "adapter.pycan", "replay": "adapter.replay"}


def _ui_fault_key(info: FaultInfo) -> str:
    return _FAULT_UI_KEY.get(info.key, "unknown")


def _fmt_num(v: float) -> str:
    return f"{v:.4g}"


@dataclass(slots=True)
class _FaultRec:
    count: int
    first_t: float
    last_t: float


@dataclass(slots=True)
class _Conn:
    """1 回の接続が持つコアのオブジェクト。"""

    gen: int
    transport: CanTransport
    session: Session
    service: MotorService
    tap: _FrameTap
    sim: bool
    unsubs: list[Callable[[], None]] = field(default_factory=list)


class _FrameTap:
    """BusManager の framelog 差込口(`write` / `close`)に挿して、TX / RX フレームを横取りする。

    TX / RX スレッドから呼ばれる。ここでは間引き(トークンバケツ。毎秒 `rate` 件まで)と
    deque への追加だけを行い、整形(デコード)は GUI スレッドで行う。
    """

    def __init__(self, rate: int = FRAME_RATE_LIMIT, burst: int = FRAME_BURST) -> None:
        self._rate = float(rate)
        self._burst = float(burst)
        self._tokens = float(burst)
        self._last = time.perf_counter()
        self._lock = threading.Lock()
        self._q: deque[CanFrame] = deque(maxlen=2000)
        self.dropped = 0
        self.total = 0
        self.wall_offset = time.time() - clock.now()

    def write(self, frame: CanFrame) -> None:
        now = time.perf_counter()
        with self._lock:
            self.total += 1
            self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                self._q.append(frame)
            else:
                self.dropped += 1

    def close(self) -> None:  # framelog 互換
        return None

    def drain(self, limit: int) -> list[CanFrame]:
        out: list[CanFrame] = []
        q = self._q
        while q and len(out) < limit:
            try:
                out.append(q.popleft())
            except IndexError:
                break
        return out


class CoreBridge(Bridge):
    """MotorService の上に載る `Bridge`。`start()` で動き出し、終了時に `shutdown()`。"""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        kind: str = "hardware",
        sim_motors: int = DEFAULT_SIM_MOTORS,
        start_offline: bool = False,
        profile: MotorProfile | None = None,
        default_port: str | None = None,
    ) -> None:
        """`kind`: "simulator"(起動時にシミュレータへ自動接続。`start_offline` で止める)/
        "hardware"(未接続で起動し、接続フォームで選ぶ)。"""
        super().__init__(parent)
        self._kind = kind
        self._sim_n = max(1, int(sim_motors))
        self._start_offline = start_offline
        self._default_port = default_port
        self._profile = profile or MotorProfile.load_builtin("el05")
        self._ctx_host = 0xFD

        self._q: queue.SimpleQueue[tuple[Any, ...]] = queue.SimpleQueue()
        self._gen = 0
        self._conn: _Conn | None = None
        self._threads: list[threading.Thread] = []
        self._connecting = False
        self._scanning = False
        self._connected = False
        self._lost = False
        self._lost_reason = ""
        self._adapter = ""
        self._port = ""
        self._simulated = False
        self._read_only = False  # ログ再生(トランスポートが読み出し専用)。指令・書込は送らない
        self._last_cfg: dict[str, Any] = {}
        self._decode_ctx: DecodeContext | None = None

        self._motor_infos: list[MotorInfo] = []
        self._selected = 1
        self._states: dict[int, MotorViewState] = {}
        self._tier_cache = "low"
        self._tier_emitted = "low"
        self._estop_emitted = False

        self._fault_recs: dict[tuple[int, str], _FaultRec] = {}
        self._clamps: dict[int, str] = {}
        self._dirty: dict[int, set[int]] = {}
        self._dropped_logged = 0
        self._last_drop_log = 0.0

        self._recorder: Recorder | None = None
        self._rec_hub: TelemetryHub | None = None
        self._rec_path: Path | None = None

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._last_stats = 0.0
        self._last_tick_error = 0.0
        self._started = False

    # ================================================================== 起動・終了
    def start(self) -> None:
        """初期状態を通知してタイマを動かす。`kind="simulator"` なら自動でシミュレータへ接続。"""
        if self._started:
            return
        self._started = True
        self.connection_changed.emit(self.connection_info())
        self.motors_changed.emit([])
        self.selected_motor_changed.emit(self._selected)
        self.power_tier_changed.emit(self._tier_cache)
        self._timer.start()
        if self._kind == "simulator" and not self._start_offline:
            self.connect_adapter({"adapter": "sim", "n": self._sim_n})

    def shutdown(self) -> None:
        """タイマを止め、記録・接続を閉じる(同期。全モータへ stop を送ってから)。"""
        self._timer.stop()
        self._gen += 1
        self._stop_recording_quiet()
        conn, self._conn = self._conn, None
        plugin_runtime.set_service(None)
        self._connected = False
        if conn is not None:
            self._teardown(conn)
        for t in list(self._threads):
            t.join(5.0)

    # ================================================================== 問い合わせ
    @property
    def backend_kind(self) -> str:
        if self._connected or self._lost:
            return "simulator" if self._simulated else "hardware"
        return self._kind

    def connection_info(self) -> ConnectionInfo:
        return ConnectionInfo(
            self._connected, self._adapter, self._port, self._simulated, self._lost,
            self._read_only,
        )

    def motors(self) -> list[MotorInfo]:
        return list(self._motor_infos)

    def selected_motor(self) -> int:
        return self._selected

    def power_tier(self) -> str:
        svc = self._svc()
        return svc.tier.value if svc is not None else self._tier_cache

    def states(self) -> dict[int, MotorViewState]:
        return dict(self._states)

    def estop_latched(self) -> bool:
        svc = self._svc()
        return svc.estop_latched if svc is not None else False

    def power_limits(self) -> PowerLimits:
        svc = self._svc()
        tier = svc.tier.value if svc is not None else self._tier_cache
        lim = svc.guard.limits if svc is not None else self._profile.tier(tier)
        return PowerLimits(
            tier, lim.torque, lim.current, lim.speed, lim.kp, lim.kd,
            lim.kd_negative_max, lim.friction_comp_max,
        )

    def param_definitions(self) -> list[ParamDef]:
        out: list[ParamDef] = []
        for p in self._profile.params:
            if p.min is not None and p.max is not None:
                rng = f"{p.min:g} ~ {p.max:g}"
            else:
                rng = ""
            out.append(
                ParamDef(
                    index=p.index,
                    name=p.key,
                    dtype=p.dtype.value,
                    unit=p.unit,
                    range_text=rng,
                    rw="W/R" if p.writable else "R",
                    description=p.description or p.label,
                    default=p.default,
                    minimum=p.min,
                    maximum=p.max,
                )
            )
        return out

    def available_channels(self) -> list[ChannelInfo]:
        out: list[ChannelInfo] = []
        for info in self._motor_infos:
            for suffix, _core, label_key, unit, kind, command in _CHANNELS:
                out.append(
                    ChannelInfo(
                        f"m{info.id}.{suffix}", tr(label_key, id=info.id), unit, info.id, kind,
                        command,
                    )
                )
        return out

    def scope_data(
        self, names: list[str], window_s: float
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        svc = self._svc()
        if svc is None:
            return {}
        hub = svc.telemetry
        core_of: dict[str, tuple[str, bool]] = {}
        for name in names:
            mid_s, _, suffix = name.partition(".")
            for s, core_suffix, _l, _u, _k, command in _CHANNELS:
                if s == suffix and mid_s[:1] == "m" and mid_s[1:].isdigit():
                    core = motor_channel(int(mid_s[1:]), core_suffix)
                    if hub.has(core):
                        core_of[name] = (core, command)
                    break
        if not core_of:
            return {}
        now = clock.now()
        data = hub.window([c for c, _ in core_of.values()], window_s, until=now)
        out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for name, (core, command) in core_of.items():
            t, v = data[core]
            rel = t - now
            if command and t.size > 1:
                rel, v = _break_gaps(rel, v, GAP_S)
            out[name] = (rel, v)
        return out

    def clamp_note(self, motor_id: int) -> str:
        return self._clamps.get(motor_id, "")

    def clear_scope(self) -> None:
        svc = self._svc()
        if svc is not None:
            svc.telemetry.clear()

    def fault_details(self, motor_id: int) -> list[FaultDetail]:
        svc = self._svc()
        if svc is None:
            return []
        return self._details_of(svc, motor_id)

    # ================================================================== 接続
    def connect_adapter(self, cfg: dict[str, Any]) -> None:
        if self._connecting:
            self._log("warn", tr("core.busy_connecting"))
            return
        if self._connected:
            return
        self._last_cfg = dict(cfg)
        self._begin_connect(dict(cfg))

    def reconnect(self) -> None:
        if self._connecting:
            return
        self._begin_connect(dict(self._last_cfg or {"adapter": "sim", "n": self._sim_n}))

    def _begin_connect(self, cfg: dict[str, Any]) -> None:
        adapter = _ADAPTERS.get(str(cfg.get("adapter", "")).lower(), "robstride_at")
        port = str(cfg.get("port", "")) if adapter in ("robstride_at",) else ""
        if adapter == "robstride_at" and not port:
            ports = list_serial_ports()
            port = self._default_port or (ports[0].device if ports else "")
        if adapter == "robstride_at" and not port:
            self._log("error", tr("core.connect_failed", reason=tr("connection.no_port")))
            return
        if adapter == "pycan":
            interface = str(cfg.get("interface", "")).strip()
            if not interface:
                self._log("error", tr("core.connect_failed", reason=tr("connection.no_interface")))
                return
            channel = str(cfg.get("channel", "")).strip()
            port = f"{interface}:{channel}" if channel else interface
        elif adapter == "replay":
            path = str(cfg.get("path", "")).strip()
            if not path:
                self._log("error", tr("core.connect_failed", reason=tr("connection.no_log")))
                return
            port = Path(path).name  # ヘッダーなどにはファイル名だけ出す
        label = tr(_ADAPTER_LABEL.get(adapter, "adapter.usbcan"))
        self._log("info", tr("core.connecting", adapter=label, port=port or "-"))
        old, self._conn = self._conn, None
        self._gen += 1
        gen = self._gen
        self._connecting = True
        self._scanning = False
        self._stop_recording_quiet()
        if old is not None:
            # 古い接続は破棄(画面は未接続へ)。ワーカーで閉じてから新しい接続を作る
            self._reset_connection_state()
        thread = threading.Thread(
            target=self._connect_job,
            args=(gen, adapter, port, cfg, old, label),
            name="edulite05-connect",
            daemon=True,
        )
        self._threads.append(thread)
        thread.start()

    def _connect_job(
        self,
        gen: int,
        adapter: str,
        port: str,
        cfg: dict[str, Any],
        old: _Conn | None,
        label: str,
    ) -> None:
        """ワーカースレッド: 旧接続を閉じ → transport / Session / MotorService を作って開く。"""
        if old is not None:
            self._teardown(old)
        conn: _Conn | None = None
        try:
            host = int(cfg.get("host_id", 0xFD))
            if adapter == "sim":
                n = int(cfg.get("n", self._sim_n))
                opts: dict[str, Any] = {"n": n}
            elif adapter == "pycan":
                # python-can(registry の options_schema: interface / channel / bitrate)
                opts = {
                    "interface": str(cfg.get("interface", "")).strip(),
                    "channel": str(cfg.get("channel", "")).strip(),
                    "bitrate": int(cfg.get("bitrate", 1_000_000)),
                }
            elif adapter == "replay":
                # ログ再生(読み出し専用。registry の options_schema: path / speed / loop)
                opts = {
                    "path": str(cfg.get("path", "")).strip(),
                    "speed": float(cfg.get("speed", 1.0)),
                    "loop": bool(cfg.get("loop", False)),
                }
            else:
                opts = {"port": port}
                if "baudrate" in cfg:
                    opts["baudrate"] = int(cfg["baudrate"])
            transport = registry.create(adapter, **opts)
            session = Session(transport, self._profile, host_id=host)
            if adapter == "replay":
                # 再生ではスキャンの応答が返らない。ログに現れるモータをここで登録する
                for mid in motor_ids_in_log(opts["path"]):
                    session.motor(mid)
            tap = _FrameTap()
            session.bus.framelog = tap  # type: ignore[assignment]  # TX / RX を横取りする
            conn_unsubs = self._subscribe_events(session, gen)
            session.open()
            service = MotorService(session)
            service.open()
            conn = _Conn(gen, transport, session, service, tap, adapter == "sim", conn_unsubs)
        except Exception as e:  # noqa: BLE001 - 接続失敗は理由を出して未接続へ戻す
            log.exception("接続に失敗しました")
            if conn is not None:
                self._teardown(conn)
            self._post_call(gen, lambda e=e: self._on_connect_failed(gen, e), always=True)
            return
        self._post_call(gen, lambda: self._on_connected(conn, label, port, host), always=True)

    def _subscribe_events(self, session: Session, gen: int) -> list[Callable[[], None]]:
        unsubs: list[Callable[[], None]] = []
        for topic in (
            SERVICE_STATE_CHANGED,
            SERVICE_ESTOP,
            SERVICE_TIER,
            SERVICE_CLAMPED,
            SERVICE_FAULT,
            SERVICE_COMM_LOST,
            SERVICE_CONTROL_ERROR,
            SERVICE_MOTORS_CHANGED,
            MOTOR_RETRY,
            MOTOR_DISCOVERED,
            BUS_ERROR,
            CONNECTION_CHANGED,
            LOG,
        ):
            unsubs.append(
                session.events.subscribe(
                    topic, lambda p, t=topic: self._q.put(("ev", gen, time.time(), t, p))
                )
            )
        return unsubs

    def _on_connect_failed(self, gen: int, exc: Exception) -> None:
        if gen != self._gen:
            return
        self._connecting = False
        self._log("error", tr("core.connect_failed", reason=str(exc) or type(exc).__name__))
        self.connection_changed.emit(self.connection_info())

    def _on_connected(self, conn: _Conn, label: str, port: str, host: int) -> None:
        self._connecting = False
        if conn.gen != self._gen:  # 接続中に切断・再接続が要求された
            threading.Thread(target=self._teardown, args=(conn,), daemon=True).start()
            return
        self._conn = conn
        plugin_runtime.set_service(conn.service)  # プラグインの ctx.service
        self._connected = True
        self._lost = False
        self._lost_reason = ""
        self._adapter = label
        self._port = port
        self._simulated = conn.sim
        self._read_only = bool(getattr(conn.transport.caps, "read_only", False))
        self._ctx_host = host
        self._decode_ctx = DecodeContext(
            host, self._profile.ranges, self._profile.param_dtypes()
        )
        self._tier_cache = "low"
        self.connection_changed.emit(self.connection_info())
        self._log("info", tr("core.connected", adapter=label + (f" {port}" if port else "")))
        if self._read_only:
            self._log("info", tr("core.read_only"))
        self.scan()

    def disconnect_adapter(self) -> None:
        if not self._connected and not self._lost:
            return
        self._gen += 1
        self._stop_recording_quiet()
        conn, self._conn = self._conn, None
        plugin_runtime.set_service(None)
        self._reset_connection_state()
        if conn is not None:
            thread = threading.Thread(
                target=self._teardown, args=(conn,), name="edulite05-disconnect", daemon=True
            )
            self._threads.append(thread)
            thread.start()
        self._log("info", tr("core.disconnected"))

    def _reset_connection_state(self) -> None:
        """画面側を未接続の状態へ戻して通知する(コアの片付けは別スレッド)。"""
        was_latched = self._estop_emitted
        self._connected = False
        self._read_only = False
        self._lost = False
        self._lost_reason = ""
        self._scanning = False
        self._motor_infos = []
        self.colors.reset()  # 新しいセッション(色の枠を空ける)
        self._states = {}
        self._fault_recs = {}
        self._clamps = {}
        self._tier_cache = "low"
        self.state_updated.emit({})
        self.motors_changed.emit([])
        self.connection_changed.emit(self.connection_info())
        if self._tier_emitted != "low":
            self._tier_emitted = "low"
            self.power_tier_changed.emit("low")
        if was_latched:
            self._estop_emitted = False
            self.estop_changed.emit(False)

    def _teardown(self, conn: _Conn) -> None:
        """任意スレッド(ブロックしうる): 全モータへ stop → ループ・監視を止め → Session を閉じる"""
        try:
            conn.service.close()
        except Exception:  # noqa: BLE001
            log.exception("MotorService の終了に失敗しました")
        try:
            conn.session.close()
        except Exception:  # noqa: BLE001
            log.exception("Session の終了に失敗しました")
        for u in conn.unsubs:
            try:
                u()
            except Exception:  # noqa: BLE001
                pass

    def scan(self) -> None:
        conn = self._conn
        if conn is None or not self._connected:
            self._log("warn", tr("core.not_connected"))
            return
        if self._scanning:
            return
        if self._read_only:
            # ログ再生: 何も送らない。ログから登録済みのモータをそのまま見せる
            self._on_scan_done(sorted(conn.session.motors), None)
            return
        if any(s.ui_state in ("enabled", "running") for s in self._states.values()):
            self._log("warn", tr("core.scan_refused_active"))
            return
        self._scanning = True
        self._log("info", tr("core.scan_start"))
        gen = conn.gen
        try:
            fut = conn.session.scan(SCAN_IDS)
        except Exception as e:  # noqa: BLE001
            self._scanning = False
            self._log("error", tr("core.scan_failed", reason=str(e)))
            return

        def done(f: Any) -> None:
            exc = f.exception() if not f.cancelled() else RuntimeError("cancelled")
            ids = [] if exc is not None else sorted(m.motor_id for m in f.result())
            self._post_call(gen, lambda: self._on_scan_done(ids, exc))

        fut.add_done_callback(done)

    def _on_scan_done(self, ids: list[int], exc: BaseException | None) -> None:
        self._scanning = False
        if exc is not None:
            self._log("error", tr("core.scan_failed", reason=str(exc)))
            return
        model = self._profile.model
        self.colors.register(ids)  # 色は初登録順で固定(ID の値では決めない)
        self._motor_infos = [
            MotorInfo(i, f"{model} #{i}", model, self.colors.slot(i)) for i in ids
        ]
        for i in ids:
            self._dirty.setdefault(i, set())
        detail = f"({', '.join(str(i) for i in ids)})" if ids else ""
        self._log("info", tr("core.scan_done", n=len(ids), ids=detail))
        if ids and self._selected not in ids:
            self._selected = ids[0]
        self.motors_changed.emit(self.motors())
        self.selected_motor_changed.emit(self._selected)

    def select_motor(self, motor_id: int) -> None:
        if motor_id != self._selected and any(m.id == motor_id for m in self._motor_infos):
            self._selected = motor_id
            self._log("debug", tr("core.selected", id=motor_id))
            self.selected_motor_changed.emit(motor_id)

    # ================================================================== 運転
    def _svc(self) -> MotorService | None:
        conn = self._conn
        return conn.service if conn is not None else None

    def _need_service(self) -> MotorService | None:
        """モータへ送る操作の入口。未接続と、読み出し専用(ログ再生)のときは None(送らない)。"""
        svc = self._svc()
        if svc is None or not self._connected:
            self._log("warn", tr("core.not_connected"))
            return None
        if self._read_only:
            self._log("warn", tr("core.read_only_refused"))
            return None
        return svc

    def enable(self, ids: list[int], run_mode: int) -> None:
        svc = self._need_service()
        if svc is None:
            return
        for mid in ids:
            try:
                fut = svc.enable(mid, RunMode(int(run_mode)))
            except Exception as e:  # noqa: BLE001
                self._log_failed(mid, "core.what_enable", e)
                continue
            self._watch(fut, mid, "core.what_enable")

    def disable(self, ids: list[int]) -> None:
        """通常の無効化: 運転停止 → 減速を待つ → type 4(`stop_and_disable`)。"""
        svc = self._need_service()
        if svc is None:
            return
        for mid in ids:
            try:
                fut = svc.stop_and_disable(mid)
            except Exception as e:  # noqa: BLE001
                self._log_failed(mid, "core.what_disable", e)
                continue
            self._watch(fut, mid, "core.what_disable")

    def clear_fault(self, ids: list[int]) -> None:
        svc = self._need_service()
        if svc is None:
            return
        for mid in ids:
            try:
                fut = svc.clear_fault(mid)
            except Exception as e:  # noqa: BLE001
                self._log_failed(mid, "core.what_clear_fault", e)
                continue
            self._watch(fut, mid, "core.what_clear_fault")

    def estop(self) -> None:
        svc = self._svc()
        if svc is None:
            self._log("warn", tr("core.not_connected"))
            return
        try:
            svc.estop()
        except Exception as e:  # noqa: BLE001
            log.exception("E-STOP で例外")
            self._log("error", str(e))
        self._sync_estop_tier()

    def release_estop(self) -> None:
        svc = self._svc()
        if svc is not None:
            svc.release_estop()
            self._sync_estop_tier()

    def set_power_tier(self, tier: str) -> bool:
        svc = self._svc()
        if svc is None:
            return False
        try:
            ok = svc.set_tier(PowerTier(tier))
        except Exception:  # noqa: BLE001 - ラッチ中など(SafetyError)
            ok = False
        if not ok:
            self._log("warn", tr("core.tier_refused"))
            return False
        self._sync_estop_tier()
        return True

    def _sync_estop_tier(self) -> None:
        """同期 API の直後に、信号の取りこぼし・遅れが無いよう即時に反映する(重複は除く)。"""
        svc = self._svc()
        if svc is None:
            return
        self._emit_tier(svc.tier.value)
        self._emit_estop(svc.estop_latched)

    def _emit_tier(self, tier: str) -> None:
        self._tier_cache = tier
        if tier != self._tier_emitted:
            self._tier_emitted = tier
            self._log("info", tr("core.tier_changed", tier=tr(f"tier.{tier}.short")))
            self.power_tier_changed.emit(tier)

    def _emit_estop(self, latched: bool) -> None:
        if latched != self._estop_emitted:
            self._estop_emitted = latched
            self._log("warn" if latched else "info",
                      tr("core.estop" if latched else "core.estop_released"))
            self.estop_changed.emit(latched)

    def start_controller(
        self,
        name: str,
        assign: dict[str, int],
        params: object,
        *,
        continuous: bool = True,
        period_ms: int = 5,
    ) -> None:
        svc = self._need_service()
        if svc is None:
            return
        cls = CONTROLLERS.get(name)
        ids = _assigned_ids(cls, assign)  # 複数スロット(2台連動)は slots の順
        known = {m.id for m in self._motor_infos}
        if not ids or any(i not in known for i in ids):
            self._log("warn", tr("core.no_motor"))
            return
        mid = ids[0]
        if cls is None:
            self._log("error", tr("core.unknown_controller", name=name))
            return
        try:
            direction = 0
            p = dataclasses.replace(params)  # type: ignore[type-var]
            if name == "jog":  # UI は符号付きの速度で渡す。core の Jog は方向 × 速さ
                direction = (p.speed > 0) - (p.speed < 0)  # type: ignore[attr-defined]
                p = dataclasses.replace(p, speed=abs(p.speed))  # type: ignore[attr-defined,type-var]
            ctrl = cls(p)
            if isinstance(ctrl, Jog):
                ctrl.set_direction(direction)
            self._apply_period(svc, period_ms)
            fut = svc.start(ids if len(ids) > 1 else mid, ctrl)
        except Exception as e:  # noqa: BLE001
            self._log_failed(mid, "core.what_start", e)
            return
        self._watch(fut, mid, "core.what_start")

    def _apply_period(self, svc: MotorService, period_ms: int) -> None:
        """送信周期。ループは全モータ共通なので、他に運転中のモータが無いときだけ変える。"""
        if any(s.ui_state == "running" for s in self._states.values()):
            return
        want = max(int(period_ms), MIN_PERIOD_MS)
        if want != period_ms:
            self._log("debug", tr("core.period_limited", req=period_ms, min=MIN_PERIOD_MS))
        svc.loop.set_rate(1000.0 / want)

    def update_controller_params(self, name: str, assign: dict[str, int], params: object) -> None:
        svc = self._svc()
        ids = _assigned_ids(CONTROLLERS.get(name), assign)
        mid = ids[0] if ids else None
        if svc is None or mid is None:
            return
        try:
            p = dataclasses.replace(params)  # type: ignore[type-var]
            ctrl = svc.controller_of(mid)
            if name == "jog" and isinstance(ctrl, Jog):
                direction = (p.speed > 0) - (p.speed < 0)  # type: ignore[attr-defined]
                svc.update(mid, dataclasses.replace(p, speed=abs(p.speed)))  # type: ignore[attr-defined,type-var]
                ctrl.set_direction(direction)
            else:
                svc.update(mid, p)
        except Exception as e:  # noqa: BLE001 - 運転が終わった直後など(目標の更新は捨ててよい)
            log.debug("目標の更新を無視: %r", e)

    def stop_controller(self, name: str, assign: dict[str, int]) -> None:
        svc = self._svc()
        ids = _assigned_ids(CONTROLLERS.get(name), assign)
        mid = ids[0] if ids else None
        if svc is None or mid is None:
            return
        try:
            fut = svc.stop_motion(mid)
        except Exception as e:  # noqa: BLE001
            self._log_failed(mid, "core.what_stop", e)
            return
        self._watch(fut, mid, "core.what_stop")

    # ================================================================== パラメータ
    def read_all_params(self, motor_id: int) -> None:
        if self._read_only:
            return  # 読出しもモータへの要求になる。ログ再生では何も送らない(値は空のまま)
        svc = self._need_service()
        if svc is None:
            return
        self._log("info", tr("core.param_read_all", id=motor_id))
        try:
            fn = getattr(svc, "read_all_params", None)
            fut = fn(motor_id) if fn is not None else svc.motor(motor_id).read_all_params()
        except Exception as e:  # noqa: BLE001
            self._log_failed(motor_id, "core.what_param_read", e)
            return
        gen = self._gen

        def done(f: Any) -> None:
            exc = f.exception() if not f.cancelled() else RuntimeError("cancelled")
            self._post_call(gen, lambda: self._on_params_read(motor_id, exc))

        fut.add_done_callback(done)

    def _on_params_read(self, motor_id: int, exc: BaseException | None) -> None:
        if exc is not None:
            self._log_failed(motor_id, "core.what_param_read", exc)
        vals = self._param_values(motor_id)
        if exc is None:
            n = sum(1 for v in vals if v.value is not None)
            self._log("info", tr("core.param_read_done", id=motor_id, n=n))
        self.params_updated.emit(motor_id, vals)

    def write_param(self, motor_id: int, index: int, value: float) -> None:
        svc = self._need_service()
        if svc is None:
            return
        try:
            pd = self._profile.param(index)
        except KeyError:
            self._log("warn", tr("core.param_refused", index=index))
            return
        if not pd.writable:
            self._log("warn", tr("core.param_refused", index=index))
            return
        v: int | float = int(value) if pd.dtype.is_integer else float(value)
        try:
            fn = getattr(svc, "write_param", None)
            fut = fn(motor_id, pd.key, v) if fn is not None else svc.motor(motor_id).write_param(
                pd.key, v
            )
        except Exception as e:  # noqa: BLE001
            self._log_failed(motor_id, "core.what_param_write", e)
            self.params_updated.emit(motor_id, self._param_values(motor_id))
            return
        gen = self._gen

        def done(f: Any) -> None:
            exc = f.exception() if not f.cancelled() else RuntimeError("cancelled")
            self._post_call(gen, lambda: self._on_param_written(motor_id, pd.index, pd.key, v, exc))

        fut.add_done_callback(done)

    def _on_param_written(
        self, motor_id: int, index: int, key: str, value: float, exc: BaseException | None
    ) -> None:
        if exc is not None:
            self._log_failed(motor_id, "core.what_param_write", exc)
        else:
            self._dirty.setdefault(motor_id, set()).add(index)
            self._log("info", tr("core.param_written", id=motor_id, name=key, value=f"{value:g}"))
        self.params_updated.emit(motor_id, self._param_values(motor_id))

    def save_params(self, motor_id: int) -> None:
        svc = self._need_service()
        if svc is None:
            return
        try:
            fn = getattr(svc, "save_params", None)
            fut = fn(motor_id) if fn is not None else svc.motor(motor_id).save_params()
        except Exception as e:  # noqa: BLE001
            self._log_failed(motor_id, "core.what_param_save", e)
            return
        gen = self._gen

        def done(f: Any) -> None:
            exc = f.exception() if not f.cancelled() else RuntimeError("cancelled")
            self._post_call(gen, lambda: self._on_params_saved(motor_id, exc))

        fut.add_done_callback(done)

    def _on_params_saved(self, motor_id: int, exc: BaseException | None) -> None:
        if exc is not None:
            self._log_failed(motor_id, "core.what_param_save", exc)
            return
        # 変更済みの印は消さない(0x70xx は type 22 でも保存されず、電源を切ると戻る)
        self._log("info", tr("core.param_saved", id=motor_id))
        self.params_updated.emit(motor_id, self._param_values(motor_id))

    def _param_values(self, motor_id: int) -> list[ParamValue]:
        svc = self._svc()
        known: dict[int, int | float] = {}
        if svc is not None:
            try:
                known = dict(svc.motor(motor_id).params)
            except Exception:  # noqa: BLE001
                known = {}
        dirty = self._dirty.get(motor_id, set())
        out = []
        for p in self._profile.params:
            v = known.get(p.index)
            if isinstance(v, float):
                v = round(v, 6)
            out.append(ParamValue(p.index, p.key, v, p.unit, p.index in dirty))
        return out

    # ================================================================== ツール(保守)
    def run_tool(self, tool: str, motor_id: int, args: dict[str, Any]) -> None:
        """保守操作。MotorService の保守 API(担当 I)へ。存在しないものは「未実装」をログに出す。"""
        svc = self._need_service()
        title = tr(f"tool.{tool}.title")
        if svc is None:
            self.tool_finished.emit(tool, motor_id, False, tr("core.not_connected"))
            return
        detail = ", ".join(f"{k}={v}" for k, v in args.items() if not k.endswith("_text")) or "-"
        self._log("warn", tr("core.tool_run", id=motor_id, tool=title, detail=detail))
        try:
            fut = self._dispatch_tool(svc, tool, motor_id, args)
        except _Unimplemented:
            self._log("error", tr("core.tool_unimplemented", tool=title))
            msg = tr("core.tool_unimplemented", tool=title)
            self.tool_finished.emit(tool, motor_id, False, msg)
            return
        except Exception as e:  # noqa: BLE001
            self._log("error", tr("core.tool_failed", id=motor_id, tool=title, error=str(e)))
            self.tool_finished.emit(tool, motor_id, False, str(e))
            return
        gen = self._gen

        def done(f: Any) -> None:
            exc = f.exception() if not f.cancelled() else RuntimeError("cancelled")
            res = f.result() if exc is None else None
            self._post_call(gen, lambda: self._on_tool_done(tool, title, motor_id, exc, res))

        fut.add_done_callback(done)

    def _dispatch_tool(self, svc: MotorService, tool: str, mid: int, args: dict[str, Any]) -> Any:
        def need(name: str) -> Callable[..., Any]:
            fn = getattr(svc, name, None)
            if fn is None:
                raise _Unimplemented(name)
            return fn

        if tool == "set_zero":
            fn = getattr(svc, "set_zero", None)
            return fn(mid) if fn is not None else svc.motor(mid).set_zero()
        if tool == "active_report":
            on = bool(args.get("on", True))
            if on:  # 周期 = 10 + 5N ms(el05_protocol.md §8.0)。書込後も監視の上報 ON は維持される
                return need("set_report_period")(mid, 10 + 5 * int(args.get("period_n", 0)))
            return svc.motor(mid).set_active_report(False)
        if tool == "can_timeout":
            seconds = float(args.get("seconds", 0.0))
            fn = getattr(svc, "set_can_timeout", None)
            if fn is not None:
                return fn(mid, seconds)
            return svc.motor(mid).write_param("canTimeout", round(seconds / 50e-6))
        if tool == "set_can_id":
            return need("set_can_id")(mid, int(args["new_id"]))
        if tool == "set_baud":
            return need("set_baud")(mid, int(args["baud"]))
        if tool == "switch_protocol":
            return need("switch_protocol")(mid, args["protocol"])
        if tool == "restore_protocol":
            return need("restore_protocol")(mid, str(args["from_protocol"]))
        raise _Unimplemented(tool)

    def _on_tool_done(
        self, tool: str, title: str, mid: int, exc: BaseException | None, result: Any
    ) -> None:
        if exc is not None:
            self._log("error", tr("core.tool_failed", id=mid, tool=title, error=str(exc)))
            self.tool_finished.emit(tool, mid, False, str(exc))
            return
        message = str(getattr(result, "message", "") or "")
        level = "warn" if getattr(result, "needs_power_cycle", False) else "info"
        if message:
            self._log(level, tr("core.tool_result", id=mid, tool=title, message=message))
        else:
            self._log("info", tr("core.tool_done", id=mid, tool=title))
        self.tool_finished.emit(tool, mid, True, message)
        if tool == "restore_protocol":
            self.scan()  # 戻したモータが見つかるようになったかもしれない。台帳を取り直す
        elif tool in ("set_zero", "can_timeout", "active_report"):
            self.params_updated.emit(mid, self._param_values(mid))

    # ================================================================== 記録
    def start_recording(self, path: Path | str, channels: list[str] | None = None) -> None:
        svc = self._svc()
        if svc is None or not self._connected:
            self._log("warn", tr("core.recording_not_connected"))
            return
        if self._recorder is not None:
            self.stop_recording()
        hub = svc.telemetry
        names: list[str] = []
        for ui in channels if channels is not None else [c.name for c in self.available_channels()]:
            mid_s, _, suffix = ui.partition(".")
            for s, core_suffix, *_rest in _CHANNELS:
                if s == suffix and mid_s[1:].isdigit():
                    core = motor_channel(int(mid_s[1:]), core_suffix)
                    if hub.has(core):
                        names.append(core)
                    break
        if not names:
            self._log("warn", tr("core.recording_not_connected"))
            return
        rec = Recorder(hub)
        p = Path(path)
        rec.start(p, names, profile_name=self._profile.name, tier=self.power_tier())
        self._recorder, self._rec_hub, self._rec_path = rec, hub, p
        self._log("info", tr("core.recording_started", name=p.name))

    def stop_recording(self) -> Path | None:
        rec, path = self._recorder, self._rec_path
        self._recorder, self._rec_hub, self._rec_path = None, None, None
        if rec is None or path is None:
            return None
        rows = rec.stop()
        err = rec.error
        if err is not None:
            self._log("error", tr("core.recording_failed", error=str(err)))
        if rows <= 0:
            path.unlink(missing_ok=True)
            return None
        self._log("info", tr("core.recording_saved", name=path.name, rows=rows))
        return path

    def recording_rows(self) -> int:
        rec = self._recorder
        return rec.rows if rec is not None else 0

    def _stop_recording_quiet(self) -> None:
        try:
            self.stop_recording()
        except Exception:  # noqa: BLE001
            log.exception("記録の停止に失敗しました")

    # ================================================================== シミュレータ専用
    def inject_fault(self, motor_id: int, fault_key: str) -> None:
        conn = self._conn
        if conn is None or not conn.sim:
            self._log("warn", tr("core.sim_only"))
            return
        spec = _SIM_FAULT.get(fault_key)
        if spec is None:
            return
        bit, fb = spec
        try:
            conn.transport.inject_fault(motor_id, bit, feedback_bit=fb)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            self._log("error", str(e))
            return
        self._log("error", tr("core.fault_injected", id=motor_id, fault=tr(f"fault.{fault_key}")))

    def inject_comm_loss(self) -> None:
        """シミュレータの接続を閉じる(USB を抜いたのと同じ。送信が失敗して通信断になる)。"""
        conn = self._conn
        if conn is None or not conn.sim:
            self._log("warn", tr("core.sim_only"))
            return
        self._log("warn", tr("core.comm_loss_injected"))
        threading.Thread(target=conn.transport.close, daemon=True).start()

    # ================================================================== キュー(コア → GUI)
    def _post_call(self, gen: int, fn: Callable[[], None], *, always: bool = False) -> None:
        """任意スレッドから: GUI スレッドの次の tick で `fn()` を実行する。
        `gen` が現在の接続世代と違えば捨てる(`always=True` は除く)。"""
        self._q.put(("call", gen, always, fn))

    def _watch(self, fut: Any, mid: int, what_key: str) -> None:
        """Future の失敗を、完了時に GUI スレッドでログへ出す(待たない)。"""
        gen = self._gen

        def done(f: Any) -> None:
            if f.cancelled():
                return
            exc = f.exception()
            if exc is not None:
                self._post_call(gen, lambda: self._log_failed(mid, what_key, exc))

        fut.add_done_callback(done)

    def _log_failed(self, mid: int, what_key: str, exc: BaseException) -> None:
        msg = str(exc) or type(exc).__name__
        self._log("error", tr("core.op_failed", id=mid, what=tr(what_key), error=msg))

    # ================================================================== tick(GUI スレッド)
    def _tick(self) -> None:
        try:
            self._drain_queue()
            conn = self._conn
            if conn is None:
                return
            self._drain_frames(conn)
            self._publish_states(conn)
            now = time.perf_counter()
            if now - self._last_stats >= STATS_PERIOD_S:
                self._last_stats = now
                self._emit_stats(conn)
        except Exception:  # noqa: BLE001 - タイマを止めない(60 Hz で同じ例外が出続けないよう 1 秒に 1 回)
            now = time.perf_counter()
            if now - self._last_tick_error > 1.0:
                self._last_tick_error = now
                log.exception("CoreBridge: tick で例外")

    def _drain_queue(self) -> None:
        for _ in range(MAX_EVENTS_PER_TICK):
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                return
            try:
                if item[0] == "call":
                    _k, gen, always, fn = item
                    if always or gen == self._gen:
                        fn()
                else:
                    _k, gen, ts, topic, payload = item
                    if gen == self._gen:
                        self._handle_event(topic, payload, ts)
            except Exception:  # noqa: BLE001 - 1 件の失敗で tick を止めない
                log.exception("CoreBridge: 通知の処理で例外")

    def _handle_event(self, topic: str, p: Any, ts: float) -> None:
        if topic == SERVICE_STATE_CHANGED:
            self._on_state_changed(p, ts)
        elif topic == SERVICE_ESTOP:
            self._emit_estop(bool(p.get("latched")))
        elif topic == SERVICE_TIER:
            self._emit_tier(p["tier"].value)
        elif topic == SERVICE_CLAMPED:
            self._on_clamped(p)
        elif topic == SERVICE_FAULT:
            self._on_fault(p, ts)
        elif topic == SERVICE_COMM_LOST:
            self._on_comm_lost(p)
        elif topic == SERVICE_CONTROL_ERROR:
            ids = "・".join(str(i) for i in p.get("motor_ids", []))
            self._log("error", tr("core.control_error", ids=ids, name=p.get("controller", "?"),
                                  error=str(p.get("error", ""))), ts)
        elif topic == SERVICE_MOTORS_CHANGED:
            self._on_motors_renamed(p.get("renamed") or {})
        elif topic == MOTOR_RETRY:
            self._log("debug", tr("core.retry", id=p.get("motor_id", "?"),
                                  what=p.get("what", ""), attempt=p.get("attempt", "?")), ts)
        elif topic == MOTOR_DISCOVERED:
            self._log("info", tr("core.motor_found", id=p.get("motor_id")), ts)
        elif topic == BUS_ERROR:
            self._log("warn", tr("core.bus_error", where=p.get("where", ""),
                                 error=repr(p.get("error"))), ts)
        elif topic == LOG:
            self._log(str(p.get("level", "info")), str(p.get("message", "")), ts)
        elif topic == CONNECTION_CHANGED:
            if not p.get("connected", True) and self._connected and not self._lost:
                self._set_lost("transport")

    def _on_state_changed(self, p: dict[str, Any], ts: float) -> None:
        mid, state, prev, reason = p["motor_id"], p["state"], p.get("previous"), p.get("reason", "")
        if state is UiState.ENABLED:
            key = "core.st_stopped" if reason == "stop_motion" else "core.st_enabled"
            self._log("info", tr(key, id=mid), ts)
        elif state is UiState.RUNNING:
            ctrl = self._svc().controller_of(mid) if self._svc() is not None else None
            name = catalog().get(f"controller.{ctrl.name}", ctrl.name) if ctrl else "?"
            self._log("info", tr("core.st_running", id=mid, name=name), ts)
        elif state is UiState.DISABLED:
            if prev in (UiState.ENABLED, UiState.RUNNING):
                level = "info" if reason in ("disable", "estop") else "warn"
                self._log(level, tr("core.st_disabled", id=mid), ts)
            else:
                self._log("debug", tr("core.st_changed", id=mid, prev=_ui_name(prev),
                                      state=_ui_name(state), reason=reason), ts)
        elif state is UiState.FAULT:
            self._log("debug", tr("core.st_fault", id=mid), ts)
        elif state is UiState.OFFLINE:
            # 登録直後(前の状態なし)はまだ応答を待っているだけなので警告しない
            level = "debug" if prev is None else "warn"
            self._log(level, tr("core.st_offline", id=mid), ts)

    def _on_motors_renamed(self, renamed: dict[int, int]) -> None:
        """CAN ID の変更(保守操作)で、台帳・選択・未保存の印を新 ID へ付け替える。"""
        if not renamed:
            return
        model = self._profile.model
        ids = [renamed.get(m.id, m.id) for m in self._motor_infos]
        for old, new in renamed.items():
            self.colors.rename(old, new)  # 色は付け替えても変えない
        self._motor_infos = [
            MotorInfo(i, f"{model} #{i}", model, self.colors.slot(i)) for i in sorted(ids)
        ]
        self._selected = renamed.get(self._selected, self._selected)
        for old, new in renamed.items():
            if old in self._dirty:
                self._dirty[new] = self._dirty.pop(old)
        self.motors_changed.emit(self.motors())
        self.selected_motor_changed.emit(self._selected)

    def _on_clamped(self, p: dict[str, Any]) -> None:
        mid, notes = p["motor_id"], p.get("notes") or []
        if not notes:
            if self._clamps.pop(mid, None) is not None:
                self._log("info", tr("core.clamped_released", id=mid))
            return
        items = []
        for n in notes:
            reasons = "・".join(tr(f"core.clamp_{r}") for r in n.reason.split(","))
            items.append(tr("core.clamp_item", field=n.field, requested=_fmt_num(n.requested),
                            applied=_fmt_num(n.applied), reason=reasons))
        detail = "; ".join(items)
        if self._clamps.get(mid) != detail:
            self._clamps[mid] = detail
            self._log("info", tr("core.clamped", id=mid, detail=detail))

    def _on_fault(self, p: dict[str, Any], ts: float) -> None:
        mid = p["motor_id"]
        infos = describe_feedback_faults(int(p.get("fault_bits", 0))) + describe_fault21(
            int(p.get("fault", 0)), int(p.get("warning", 0))
        )
        keys = list(dict.fromkeys(_ui_fault_key(i) for i in infos))
        for k in keys:
            rec = self._fault_recs.get((mid, k))
            if rec is None:
                self._fault_recs[(mid, k)] = _FaultRec(1, ts, ts)
            else:
                rec.count += 1
                rec.last_t = ts
        names = "、".join(catalog().get(f"fault.{k}", k) for k in keys) or "?"
        self._log("error", tr("core.fault_occurred", id=mid, names=names), ts)

    def _on_comm_lost(self, p: dict[str, Any]) -> None:
        reason = str(p.get("reason", "transport"))
        if reason == "transport":
            self._log("error", tr("core.comm_lost_transport"))
        else:
            ids = "・".join(str(i) for i in p.get("motor_ids", []))
            self._log("error", tr("core.comm_lost_motors", ids=ids))
        self._set_lost(reason)

    def _set_lost(self, reason: str) -> None:
        if self._lost and self._lost_reason == "transport":
            return
        self._lost = True
        self._lost_reason = reason
        self._connected = False
        self.connection_changed.emit(self.connection_info())

    def _check_recovered(self, svc: MotorService) -> None:
        """モータ単位の通信断(timeout)は、全モータのフィードバックが戻れば自動で復帰する。"""
        if self._lost and self._lost_reason == "timeout":
            states = svc.states()
            if states and all(s is not UiState.OFFLINE for s in states.values()):
                self._lost = False
                self._lost_reason = ""
                self._connected = True
                self.connection_changed.emit(self.connection_info())
                self._log("info", tr("core.comm_recovered"))

    # ---- 状態の組立 ----
    def _publish_states(self, conn: _Conn) -> None:
        svc = conn.service
        self._check_recovered(svc)
        ui_states = svc.states()
        hub = svc.telemetry
        now = clock.now()
        out: dict[int, MotorViewState] = {}
        for info in self._motor_infos:
            mid = info.id
            ui_core = ui_states.get(mid, UiState.OFFLINE)
            ui = ui_core.value
            try:
                m = svc.motor(mid)
            except Exception:  # noqa: BLE001
                continue
            ms = m.state
            online = ui != "offline"
            faults: tuple[str, ...] = ()
            if ui_core is UiState.FAULT or ms.fault_bits:
                faults = tuple(self._fault_keys(svc, mid))
            if not faults:
                for k in [k for k in self._fault_recs if k[0] == mid]:
                    del self._fault_recs[k]
            running = ui_core is UiState.RUNNING
            ctrl = svc.controller_of(mid)
            cmd_p = cmd_v = cmd_t = None
            if running:
                cmd_p = _fresh(hub, motor_channel(mid, "cmd.pos"), now, CMD_FRESH_S)
                cmd_v = _fresh(hub, motor_channel(mid, "cmd.vel"), now, CMD_FRESH_S)
                cmd_t = _fresh(hub, motor_channel(mid, "cmd.tq"), now, CMD_FRESH_S)
            vbus = _fresh(hub, motor_channel(mid, "vbus"), now, VBUS_FRESH_S)
            rm = svc.run_mode_of(mid)
            if rm is None:
                rm = ms.run_mode
            out[mid] = MotorViewState(
                id=mid,
                online=online,
                mode=_MODE_KEY.get(int(ms.mode), "reset") if ms.mode is not None else "reset",
                run_mode=int(rm) if rm is not None else 0,
                position=ms.position,
                position_multi=ms.position_multi,
                velocity=ms.velocity,
                torque=ms.torque,
                temperature=ms.temperature,
                vbus=vbus,
                faults=faults,
                controller=ctrl.name if running and ctrl is not None else None,
                cmd_position=cmd_p,
                cmd_velocity=cmd_v,
                cmd_torque=cmd_t,
                ui_state=ui,  # type: ignore[arg-type]
                power=estimate_power(ms.torque, ms.velocity, self._profile.power_estimate)
                if online
                else None,
            )
        if not out and not self._states:
            return
        self._states = out
        self.state_updated.emit(out)

    def _fault_keys(self, svc: MotorService, mid: int) -> list[str]:
        keys = list(dict.fromkeys(_ui_fault_key(i) for i in svc.fault_details(mid)))
        order = {k: i for i, k in enumerate(FAULT_KEYS)}
        return sorted(keys, key=lambda k: order.get(k, 99))

    def _details_of(self, svc: MotorService, mid: int) -> list[FaultDetail]:
        infos = svc.fault_details(mid)
        by_key: dict[str, FaultInfo] = {}
        for i in infos:
            by_key.setdefault(_ui_fault_key(i), i)
        cat = catalog()
        hub = svc.telemetry
        now_wall = time.time()
        out: list[FaultDetail] = []
        order = {k: i for i, k in enumerate(FAULT_KEYS)}
        for key in sorted(by_key, key=lambda k: order.get(k, 99)):
            info = by_key[key]
            rec = self._fault_recs.get((mid, key))
            if rec is None:
                rec = self._fault_recs[(mid, key)] = _FaultRec(1, now_wall, now_wall)
            measured: float | None = None
            threshold: float | None = None
            unit = ""
            try:
                state = svc.motor(mid).state
            except Exception:  # noqa: BLE001
                state = None
            if key == "overtemp" and state is not None:
                measured, threshold, unit = (
                    round(state.temperature, 1), self._profile.limits.temp_fault, "℃"
                )
            elif key == "undervoltage":
                v = _fresh(hub, motor_channel(mid, "vbus"), clock.now(), VBUS_FRESH_S)
                measured, unit = (round(v, 1) if v is not None else None), "V"
            elif key == "overcurrent":
                v = _fresh(hub, motor_channel(mid, "iqf"), clock.now(), VBUS_FRESH_S)
                measured = round(v, 2) if v is not None else None
                threshold, unit = self._profile.limits.current, "A"
            causes = cat.get(f"fault.causes.{key}", "")
            out.append(
                FaultDetail(
                    key=key,
                    name=cat.get(f"fault.{key}", info.name),
                    description=cat.get(f"fault.desc.{key}", info.description),
                    measured=measured,
                    threshold=threshold,
                    unit=unit,
                    causes=tuple(c for c in causes.split("|") if c),
                    remedy=cat.get(f"fault.remedy.{key}", ""),
                    count=rec.count,
                    first_t=rec.first_t,
                    last_t=rec.last_t,
                )
            )
        return out

    # ---- フレーム・統計 ----
    def _drain_frames(self, conn: _Conn) -> None:
        for f in conn.tap.drain(MAX_FRAMES_PER_TICK):
            self.frame_logged.emit(self._frame_entry(conn.tap, f))
        dropped = conn.tap.dropped
        now = time.perf_counter()
        if dropped > self._dropped_logged and now - self._last_drop_log > 5.0:
            self._last_drop_log = now
            self._dropped_logged = dropped
            self._log("debug", tr("core.frames_dropped", n=dropped))

    def _frame_entry(self, tap: _FrameTap, f: CanFrame) -> FrameEntry:
        t = (f.timestamp or clock.now()) + tap.wall_offset
        direction = "TX" if f.direction is Direction.TX else "RX"
        try:
            text = self._describe_frame(f, direction)
        except Exception:  # noqa: BLE001 - 表示用の解釈の失敗は握りつぶす
            text = ""
        return FrameEntry(t, direction, f.can_id, bytes(f.data), text)

    def _describe_frame(self, f: CanFrame, direction: str) -> str:
        idf = parse_id(f.can_id)
        if direction == "TX":
            return self._describe_tx(f, idf.comm_type, idf.data2, idf.dest)
        ctx = self._decode_ctx
        if ctx is None:
            return ""
        msg = decode(f, ctx)
        if isinstance(msg, Feedback):
            return tr("frame.feedback", pos=f"{msg.position:.3f}", vel=f"{msg.velocity:.3f}",
                      tq=f"{msg.torque:.3f}", temp=f"{msg.temperature:.1f}",
                      mode=tr(f"mode.{_MODE_KEY.get(int(msg.mode), 'reset')}"))
        if isinstance(msg, ParamResponse):
            name = self._param_name(msg.index)
            if not msg.ok:
                return tr("frame.param_resp_fail", name=name)
            v = "-" if msg.value is None else (
                f"{msg.value:g}" if isinstance(msg.value, float) else str(msg.value))
            return tr("frame.param_resp", name=name, value=v)
        if isinstance(msg, UidResponse):
            return tr("frame.uid", id=msg.motor_id)
        if isinstance(msg, FaultReport):
            return tr("frame.fault_report", fault=msg.fault, warning=msg.warning)
        return ""

    def _param_name(self, index: int) -> str:
        try:
            return self._profile.param(index).key
        except KeyError:
            return f"0x{index:04X}"

    def _describe_tx(self, f: CanFrame, ct: int, data2: int, dest: int) -> str:
        d = bytes(f.data)
        r = self._profile.ranges
        if ct == CommType.GET_ID:
            return tr("frame.get_id", id=dest)
        if ct == CommType.MIT_CTRL and len(d) >= 8:
            p = int.from_bytes(d[0:2], "big")
            v = int.from_bytes(d[2:4], "big")
            kp = int.from_bytes(d[4:6], "big")
            kd = int.from_bytes(d[6:8], "big")
            return tr("frame.mit", p=f"{uint_to_float(p, *r.position, 16):.3f}",
                      v=f"{uint_to_float(v, *r.velocity, 16):.3f}",
                      kp=f"{uint_to_float(kp, *r.kp, 16):.2f}",
                      kd=f"{uint_to_float(kd, *r.kd, 16):.3f}",
                      t=f"{uint_to_float(data2, *r.torque, 16):.3f}")
        if ct == CommType.ENABLE:
            return tr("frame.enable")
        if ct == CommType.STOP:
            return tr("frame.clear_fault") if d and d[0] == 1 else tr("frame.stop")
        if ct == CommType.SET_ZERO:
            return tr("frame.set_zero")
        if ct == CommType.PARAM_READ and len(d) >= 2:
            return tr("frame.read_param", name=self._param_name(int.from_bytes(d[0:2], "little")))
        if ct == CommType.PARAM_WRITE and len(d) >= 8:
            idx = int.from_bytes(d[0:2], "little")
            dtype = self._profile.param_dtypes().get(idx)
            val = unpack_value(dtype, d[4:8]) if dtype is not None else "?"
            vs = f"{val:g}" if isinstance(val, float) else str(val)
            return tr("frame.write_param", name=self._param_name(idx), value=vs)
        if ct == CommType.SAVE:
            return tr("frame.save_params")
        if ct == CommType.ACTIVE_REPORT and len(d) >= 7:
            return tr("frame.report", state="ON" if d[6] == 1 else "OFF")
        return tr("frame.other", name=catalog().get(f"diag.type.{ct}", f"type {ct}"))

    def _emit_stats(self, conn: _Conn) -> None:
        try:
            bs = conn.session.bus.stats()
            ls = conn.service.loop.stats
        except Exception:  # noqa: BLE001
            return
        self.stats_updated.emit(
            BusStats(
                tx_fps=bs.tx_fps,
                rx_fps=bs.rx_fps,
                loop_hz=ls.rate_hz,
                jitter_ms=ls.jitter_ms_p99,
                parse_errors=bs.parse_errors,
                timeouts=bs.timeouts,
                retries=bs.retries,
                frames_dropped=conn.tap.dropped,
            )
        )

    # ---- ログ ----
    def _log(self, level: str, text: str, ts: float | None = None) -> None:
        self.log_message.emit(LogEntry(time.time() if ts is None else ts, level, text))


class _Unimplemented(Exception):
    """コアに対応する保守 API が無い。"""


_MODE_KEY = {0: "reset", 1: "cali", 2: "motor"}


def _ui_name(state: UiState | None) -> str:
    return "-" if state is None else state.value


def _fresh(hub: TelemetryHub, name: str, now: float, max_age: float) -> float | None:
    """チャネルの最新値(`max_age` 秒以内のものだけ)。"""
    try:
        got = hub.latest(name)
    except KeyError:
        return None
    if got is None or now - got[0] > max_age:
        return None
    return got[1]


def _assigned_ids(cls: type | None, assign: dict[str, int]) -> list[int]:
    """割り当て(スロット名 → モータ ID)をコントローラの slots の順に並べる。

    単一スロット(slots = ("motor",))は {"motor": id}、2台連動は {"leader": a, "follower": b}。
    スロットが揃わなければ "motor" だけを見る(無ければ空)。"""
    slots = tuple(getattr(cls, "slots", ("motor",))) if cls is not None else ("motor",)
    ids = [assign.get(slot) for slot in slots]
    if any(i is None for i in ids):
        m = assign.get("motor")
        return [m] if m is not None else []
    return [int(i) for i in ids if i is not None]


def _break_gaps(t: np.ndarray, v: np.ndarray, gap: float) -> tuple[np.ndarray, np.ndarray]:
    """サンプルが `gap` 秒以上空いたところへ NaN を挟み、グラフの線を切る。"""
    idx = np.flatnonzero(np.diff(t) > gap)
    if idx.size == 0:
        return t, v
    tt = np.insert(t, idx + 1, t[idx] + 1e-6)
    vv = np.insert(v, idx + 1, np.nan)
    return tt, vv
