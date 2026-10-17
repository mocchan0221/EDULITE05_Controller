"""モック用 Bridge(ダミーデータ)。Phase 3 で `CoreBridge` に置き換える。

- ダミーのモータ 2 台(ID 1, 2)。1 kHz 相当のサンプルを numpy リングバッファ(120 s)へ生成する
  (QTimer 16 ms ごとにその間の分をまとめて生成)。
- 各モードは「質量-粘性」1 自由度モデル + 単純な制御則で、波形がそれらしく変わる程度の実装。
  実機のモデルではない(Phase 1 の MotorSimulator とは無関係)。
- 状態モデル(ui_design.md §3): offline → disabled →(有効化)enabled →(運転開始)running、
  故障は fault。
  有効化 / 無効化 / 運転開始 / 運転停止 / 非常停止(ラッチ)/ 出力段階で挙動が変わる。
- ダミーの CAN フレームとログも流す。フレームの符号化は el05_protocol.md §1〜2 に沿うが、
  表示用の簡易版(本物のエンコーダは Phase 1 の `core/protocol`)。
"""

from __future__ import annotations

import dataclasses
import math
import random
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer

from edulite05.app.bridge import Bridge
from edulite05.app.demo_params import EL05_PARAM_DEFS
from edulite05.app.i18n import tr
from edulite05.app.state_csv import write_state_csv
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
from edulite05.core.control.params import (
    PARAMS_BY_CONTROLLER,
    BilateralParams,
    CspParams,
    CurrentParams,
    JogParams,
    MitParams,
    PpParams,
    SineParams,
    StepParams,
    VelocityParams,
)
from edulite05.core.device.profile import PowerEstimateSpec
from edulite05.core.telemetry.power import estimate_power

_POWER_SPEC = PowerEstimateSpec()  # 消費電力の概算(既定 = profiles/el05.toml と同じ未較正の値)

# architecture.md §4.3.1 の初期案(本来は profiles/el05.toml の [power_tiers.*])
TIERS: dict[str, PowerLimits] = {
    "low": PowerLimits("low", torque=0.5, current=1.0, speed=5.0, kp=20.0, kd=1.0,
                       kd_negative_max=0.05, friction_comp_max=0.1),
    "high": PowerLimits("high", torque=6.0, current=11.0, speed=50.0, kp=500.0, kd=5.0,
                        kd_negative_max=0.2, friction_comp_max=0.3),
}

SAMPLE_HZ = 1000
BUFFER_S = 120
_RING = SAMPLE_HZ * BUFFER_S
_TWO_PI = 2.0 * math.pi
_P_RANGE = 4.0 * math.pi  # type 2 の角度は ±4π(el05_protocol.md §2.3)
_HOST_ID = 0xFD

# ダミー物理パラメータ(表示用。実機の値ではない)
_J = 0.005  # 慣性 [kg m²]
_B = 0.01  # 粘性 [N m s/rad](通電中)
_B_OFF = 0.08  # 非通電時の摩擦(減速機の引きずり。止めると速やかに止まる)
_KT = 0.5  # トルク定数 [N m/A]
_KV = 0.35  # 速度ループゲイン
_KPP = 12.0  # 位置→速度ゲイン

_CHANNEL_KINDS: tuple[tuple[str, str, str, str, bool], ...] = (
    # suffix, label key, unit, kind, command
    ("pos", "channel.pos", "rad", "pos", False),
    ("vel", "channel.vel", "rad/s", "vel", False),
    ("torque", "channel.torque", "N·m", "torque", False),
    ("temp", "channel.temp", "℃", "temp", False),
    ("vbus", "channel.vbus", "V", "vbus", False),
    ("cmd_pos", "channel.cmd_pos", "rad", "pos", True),
    ("cmd_vel", "channel.cmd_vel", "rad/s", "vel", True),
    ("cmd_torque", "channel.cmd_torque", "N·m", "torque", True),
)


def _u16(x: float, lo: float, hi: float) -> int:
    x = min(max(x, lo), hi)
    return int((x - lo) * 65535.0 / (hi - lo))


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _wrap(theta: float) -> float:
    """±4π の周期ループ(type 2 の角度表現)。"""
    return (theta + _P_RANGE) % (2 * _P_RANGE) - _P_RANGE


@dataclass(slots=True)
class _Ctrl:
    name: str
    params: Any
    continuous: bool
    period_ms: int
    t: float = 0.0
    holding: bool = False  # True = 運転停止後/有効化直後の保持(UI 上は running ではなく enabled)
    # 2台連動(bilateral): 自分の役割・相手の ID・2 台で共有する位置差 {"d": 目標, "d0": 開始時}
    role: str = ""
    partner: int = 0
    link: dict[str, float] | None = None


@dataclass(slots=True)
class _FaultRec:
    """発生中の故障 1 種の記録(件数・時刻・測定値)。"""

    count: int
    first_t: float
    last_t: float
    measured: float | None
    threshold: float | None
    unit: str


# 故障注入時のダミー測定値(測定値, 閾値, 単位)。el05_protocol.md §2.3 の閾値(12 V / 135 ℃)に合わせる
_FAULT_DEMO: dict[str, tuple[float | None, float | None, str]] = {
    "undervoltage": (10.8, 12.0, "V"),
    "overcurrent": (13.2, 11.0, "A"),
    "overtemp": (138.2, 135.0, "℃"),
    "encoder": (None, None, ""),
    "overload": (None, None, ""),
    "uncalibrated": (None, None, ""),
}


@dataclass(slots=True)
class _Motor:
    """1 台分のダミー物理状態。"""

    id: int
    temp: float
    theta: float = 0.0  # 多回転 [rad]
    omega: float = 0.0
    tau: float = 0.0  # 計測トルク
    vbus: float = 24.0
    enabled: bool = False
    run_mode: int = 0
    faults: set[str] = field(default_factory=set)
    ctrl: _Ctrl | None = None
    w_ramp: float = 0.0
    p_plan: float = 0.0
    v_plan: float = 0.0
    cmd_p: float | None = None
    cmd_v: float | None = None
    cmd_t: float | None = None
    clamp: str = ""  # 出力段階の上限で指令を制限している間の内容(状態の帯の「制限中」)
    zero_gap: bool = False  # 原点を設定した直後: 次のグラフのサンプルを NaN にして線を切る
    runaway: bool = False  # MIT の負の kd / 摩擦補償の暴走ガード中(SafetyGuard の代役)
    fault_info: dict[str, _FaultRec] = field(default_factory=dict)

    def mode(self) -> str:
        return "motor" if self.enabled else "reset"


class DemoBridge(Bridge):
    """ダミーデータで動く Bridge。`start()` を呼ぶと動き出す。"""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        kind: str = "mock",
        demo_motion: bool = True,
        seed: int | None = None,
        start_offline: bool = False,
    ) -> None:
        """`start_offline=True` なら未接続から始める(接続 → 自動スキャンの流れを再現できる)。"""
        super().__init__(parent)
        self._kind = kind
        self._demo_motion = demo_motion
        self._demo_pending = demo_motion
        self._start_offline = start_offline
        self._estop = False
        self._lost = False
        self._last_cfg: dict[str, Any] = {}
        self._rng = random.Random(seed)
        self._connected = False
        self._adapter = ""
        self._port = ""
        self._simulated = kind != "hardware"
        self._motors: dict[int, _Motor] = {}
        self._motor_infos: list[MotorInfo] = []
        self._selected = 1
        self._tier = "low"
        self._states: dict[int, MotorViewState] = {}
        self._params: dict[int, dict[int, float | int]] = {}
        self._dirty: dict[int, set[int]] = {}
        self._defs = {d.index: d for d in EL05_PARAM_DEFS}

        # リングバッファ
        self._n = 0  # 生成済みサンプル総数
        self._t_ring = np.zeros(_RING, dtype=np.float64)
        self._rings: dict[str, np.ndarray] = {}
        self._wall0 = 0.0
        self._counts = {"tx": 0, "rx": 0}
        self._parse_errors = 0  # 接続してからの累計(通信断の再現で増える)
        self._timeouts = 0
        self._frame_accum = 0.0

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(500)
        self._stats_timer.timeout.connect(self._emit_stats)
        self._last_stats = time.perf_counter()
        self._started = False
        # CSV 記録(状態更新ごとの行を溜め、停止時に書く)
        self._rec_path: Path | None = None
        self._rec_t0 = 0.0
        self._rec_rows: list[tuple[float, dict[int, MotorViewState]]] = []

    # ------------------------------------------------------------------ 起動
    def start(self) -> None:
        """初期状態(既定は接続済み・モータ 2 台。`start_offline` なら未接続)を通知して開始する。"""
        if self._started:
            return
        self._started = True
        self._wall0 = time.perf_counter()
        if self._start_offline:
            self._connected = False
            self._adapter = ""
            self._port = ""
        else:
            self._adapter = tr("adapter.simulator")
            self._port = ""
            self._connected = True
            self._create_motors()
        self.connection_changed.emit(self.connection_info())
        self.motors_changed.emit(self.motors())
        self.selected_motor_changed.emit(self._selected)
        self.power_tier_changed.emit(self._tier)
        self._log("info", tr("demo.started"))
        if self._connected and self._demo_pending:
            self._begin_demo_motion()
        self._timer.start()
        self._stats_timer.start()
        self._publish_states()

    def shutdown(self) -> None:
        self._timer.stop()
        self._stats_timer.stop()

    def _create_motors(self) -> None:
        self._motors = {1: _Motor(1, temp=29.0), 2: _Motor(2, temp=31.5)}
        self.colors.register(sorted(self._motors))  # 色は初登録順で固定
        self._motor_infos = [
            MotorInfo(i, tr("demo.motor_name", id=i), "EL05", self.colors.slot(i))
            for i in sorted(self._motors)
        ]
        for info in self._motor_infos:
            self._params[info.id] = {d.index: d.default for d in EL05_PARAM_DEFS}  # type: ignore[misc]
            self._dirty[info.id] = set()
        for mid in self._motors:
            for suffix, *_ in _CHANNEL_KINDS:
                self._rings.setdefault(f"m{mid}.{suffix}", np.full(_RING, np.nan, np.float32))

    def _begin_demo_motion(self) -> None:
        self._demo_pending = False
        self._log("info", tr("demo.motion_started", id=1))
        m = self._motors[1]
        m.enabled = True
        m.run_mode = 2
        m.ctrl = _Ctrl(
            "sine",
            SineParams(target="velocity", amplitude=2.5, frequency=0.25, offset=0.0),
            True,
            5,
        )

    # ------------------------------------------------------------------ 問い合わせ
    @property
    def backend_kind(self) -> str:
        return self._kind

    def connection_info(self) -> ConnectionInfo:
        return ConnectionInfo(
            self._connected, self._adapter, self._port, self._simulated, self._lost
        )

    def motors(self) -> list[MotorInfo]:
        return list(self._motor_infos)

    def selected_motor(self) -> int:
        return self._selected

    def power_tier(self) -> str:
        return self._tier

    def states(self) -> dict[int, MotorViewState]:
        return dict(self._states)

    def power_limits(self) -> PowerLimits:
        return TIERS[self._tier]

    def estop_latched(self) -> bool:
        return self._estop

    def fault_details(self, motor_id: int) -> list[FaultDetail]:
        m = self._motors.get(motor_id)
        if m is None:
            return []
        out: list[FaultDetail] = []
        for key in FAULT_KEYS:
            rec = m.fault_info.get(key)
            if key not in m.faults or rec is None:
                continue
            out.append(
                FaultDetail(
                    key=key,
                    name=tr(f"fault.{key}"),
                    description=tr(f"fault.desc.{key}"),
                    measured=rec.measured,
                    threshold=rec.threshold,
                    unit=rec.unit,
                    causes=tuple(tr(f"fault.causes.{key}").split("|")),
                    remedy=tr(f"fault.remedy.{key}"),
                    count=rec.count,
                    first_t=rec.first_t,
                    last_t=rec.last_t,
                )
            )
        return out

    def param_definitions(self) -> list[ParamDef]:
        return list(EL05_PARAM_DEFS)

    def available_channels(self) -> list[ChannelInfo]:
        out: list[ChannelInfo] = []
        for info in self._motor_infos:
            for suffix, label_key, unit, kind, command in _CHANNEL_KINDS:
                out.append(
                    ChannelInfo(
                        f"m{info.id}.{suffix}",
                        tr(label_key, id=info.id),
                        unit,
                        info.id,
                        kind,
                        command,
                    )
                )
        return out

    def scope_data(
        self, names: list[str], window_s: float
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        total = self._n
        m = min(total, _RING, int(window_s * SAMPLE_HZ))
        if m < 2:
            return {}
        idx = (np.arange(total - m, total) % _RING).astype(np.intp)
        t = self._t_ring[idx]
        rel = t - t[-1]
        out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for name in names:
            ring = self._rings.get(name)
            if ring is not None:
                out[name] = (rel, ring[idx])
        return out

    def clear_scope(self) -> None:
        self._n = 0
        for ring in self._rings.values():
            ring.fill(np.nan)
        self._t_ring.fill(0.0)
        self._wall0 = time.perf_counter()

    # ------------------------------------------------------------------ 記録
    def start_recording(self, path: Path | str, channels: list[str] | None = None) -> None:
        self._rec_path = Path(path)
        self._rec_t0 = time.perf_counter()
        self._rec_rows = []

    def stop_recording(self) -> Path | None:
        path, rows = self._rec_path, self._rec_rows
        self._rec_path, self._rec_rows = None, []
        if path is None or not rows:
            return None
        write_state_csv(path, rows)
        return path

    def recording_rows(self) -> int:
        return len(self._rec_rows) if self._rec_path is not None else 0

    # ------------------------------------------------------------------ 接続・スキャン
    def connect_adapter(self, cfg: dict[str, Any]) -> None:
        if self._connected:
            return
        self._last_cfg = dict(cfg)
        adapter = str(cfg.get("adapter", "simulator"))
        port = str(cfg.get("port", ""))
        self._log("info", tr("demo.connecting", adapter=adapter, port=port or "-"))
        QTimer.singleShot(350, lambda: self._finish_connect(adapter, port))

    def reconnect(self) -> None:
        self.connect_adapter(self._last_cfg or {"adapter": "simulator"})

    def _finish_connect(self, adapter: str, port: str) -> None:
        self._connected = True
        self._lost = False
        self._adapter = tr("adapter.simulator") if adapter == "simulator" else tr("adapter.usbcan")
        self._port = "" if adapter == "simulator" else port
        self._simulated = True
        self._wall0 = time.perf_counter() - self._n / SAMPLE_HZ
        self.connection_changed.emit(self.connection_info())
        self._log("info", tr("demo.connected"))
        self.scan()

    def disconnect_adapter(self) -> None:
        if not self._connected and not self._lost:
            return
        for m in self._motors.values():
            m.enabled = False
            m.ctrl = None
        self._connected = False
        self._lost = False
        self._parse_errors = self._timeouts = 0
        self._motors = {}
        self._motor_infos = []
        self.colors.reset()
        self._states = {}
        self.state_updated.emit({})
        self.motors_changed.emit([])
        self.connection_changed.emit(self.connection_info())
        self._log("info", tr("demo.disconnected"))

    def scan(self) -> None:
        if not self._connected:
            self._log("warn", tr("demo.not_connected"))
            return
        self._log("info", tr("demo.scan_start"))
        for mid in (1, 2, 3):
            self._frame("TX", self._id(0, _HOST_ID, mid), bytes(8), tr("frame.get_id", id=mid))
        QTimer.singleShot(600, self._finish_scan)

    def _finish_scan(self) -> None:
        if not self._connected:
            return
        if not self._motors:
            self._create_motors()
            self._wall0 = time.perf_counter() - self._n / SAMPLE_HZ
            if self._demo_pending:
                self._begin_demo_motion()
        for mid in sorted(self._motors):
            uid = bytes([0x6B, 0x02, 0x22, 0x14, 0x83, 0xB2, 0x68, mid])
            self._frame("RX", self._id(0, mid, 0xFE), uid, tr("frame.uid", id=mid))
        self._log("info", tr("demo.scan_done", n=len(self._motors)))
        self.motors_changed.emit(self.motors())
        self.selected_motor_changed.emit(self._selected)

    def select_motor(self, motor_id: int) -> None:
        if motor_id in self._motors and motor_id != self._selected:
            self._selected = motor_id
            self._log("debug", tr("demo.selected", id=motor_id))
            self.selected_motor_changed.emit(motor_id)

    # ------------------------------------------------------------------ 操作
    def _hold_ctrl(self, m: _Motor, run_mode: int, prev: object | None = None) -> _Ctrl:
        """有効化直後・運転停止後の「保持」コントローラ(指令 0 / 現在位置保持)。"""
        th = m.theta
        if isinstance(prev, MitParams) or (prev is None and run_mode == 0):
            kp = prev.kp if isinstance(prev, MitParams) else 0.0
            kd = prev.kd if isinstance(prev, MitParams) else 0.0
            c = _Ctrl("mit", MitParams(position=th, velocity=0.0, kp=kp, kd=kd, torque_ff=0.0),
                      True, 5, holding=True)
        elif isinstance(prev, PpParams) or (prev is None and run_mode == 1):
            vel = prev.velocity if isinstance(prev, PpParams) else PpParams().velocity
            acc = prev.accel if isinstance(prev, PpParams) else PpParams().accel
            c = _Ctrl("pp", PpParams(position=th, velocity=vel, accel=acc), True, 5, holding=True)
        elif isinstance(prev, CspParams) or (prev is None and run_mode == 5):
            lim = prev.speed_limit if isinstance(prev, CspParams) else CspParams().speed_limit
            c = _Ctrl("csp", CspParams(position=th, speed_limit=lim), True, 5, holding=True)
        elif isinstance(prev, CurrentParams) or (prev is None and run_mode == 3):
            c = _Ctrl("current", CurrentParams(iq=0.0), True, 5, holding=True)
        else:  # 速度・JOG
            c = _Ctrl("velocity", VelocityParams(velocity=0.0), True, 5, holding=True)
        m.p_plan, m.v_plan, m.w_ramp = m.theta, m.omega, m.omega
        return c

    def enable(self, ids: list[int], run_mode: int) -> None:
        if self._estop:
            self._log("warn", tr("demo.estop_blocked"))
            return
        for mid in ids:
            m = self._motors.get(mid)
            if m is None:
                continue
            if m.faults:
                self._log("warn", tr("demo.enable_blocked_fault", id=mid))
                continue
            m.enabled = True
            m.run_mode = run_mode
            m.ctrl = self._hold_ctrl(m, run_mode)
            self._log("info", tr("demo.enabled", id=mid))
            self._frame("TX", self._id(18, _HOST_ID, mid), self._w_float(0x7005, float(run_mode)),
                        tr("frame.write_param", name="run_mode", value=str(run_mode)))
            self._frame("TX", self._id(3, _HOST_ID, mid), bytes(8), tr("frame.enable"))
            self._feedback_frame(m)
        self._publish_states()

    def disable(self, ids: list[int]) -> None:
        for mid in ids:
            m = self._motors.get(mid)
            if m is None:
                continue
            m.enabled = False
            m.ctrl = None
            self._log("info", tr("demo.disabled", id=mid))
            self._frame("TX", self._id(4, _HOST_ID, mid), bytes(8), tr("frame.stop"))
            self._feedback_frame(m)
        self._publish_states()

    def estop(self) -> None:
        for m in self._motors.values():
            m.enabled = False
            m.ctrl = None
            self._frame("TX", self._id(4, _HOST_ID, m.id), bytes(8), tr("frame.stop"))
        newly = not self._estop
        self._estop = True
        if self._tier != "low":
            self._apply_tier("low")
        self._log("warn", tr("demo.estop"))
        self._publish_states()
        if newly:
            self.estop_changed.emit(True)

    def release_estop(self) -> None:
        if not self._estop:
            return
        self._estop = False
        self._log("info", tr("demo.estop_released"))
        self._publish_states()
        self.estop_changed.emit(False)

    def clear_fault(self, ids: list[int]) -> None:
        for mid in ids:
            m = self._motors.get(mid)
            if m is None:
                continue
            m.faults.clear()
            m.fault_info.clear()
            self._log("info", tr("demo.fault_cleared", id=mid))
            self._frame(
                "TX", self._id(4, _HOST_ID, mid), bytes([1, 0, 0, 0, 0, 0, 0, 0]),
                tr("frame.clear_fault"),
            )
        self._publish_states()

    def inject_fault(self, motor_id: int, fault_key: str) -> None:
        m = self._motors.get(motor_id)
        if m is None or fault_key not in _FAULT_DEMO:
            return
        now = time.time()
        measured, threshold, unit = _FAULT_DEMO[fault_key]
        rec = m.fault_info.get(fault_key)
        if rec is None:
            m.fault_info[fault_key] = _FaultRec(1, now, now, measured, threshold, unit)
        else:
            rec.count += 1
            rec.last_t = now
        m.faults.add(fault_key)
        m.enabled = False
        m.ctrl = None
        self._log("error", tr("demo.fault_injected", id=motor_id, fault=tr(f"fault.{fault_key}")))
        self._feedback_frame(m)
        self._publish_states()

    def inject_comm_loss(self) -> None:
        if not self._connected:
            return
        for m in self._motors.values():
            m.enabled = False
            m.ctrl = None
        self._connected = False
        self._lost = True
        self._timeouts += self._rng.randint(3, 9)
        self._parse_errors += self._rng.randint(0, 2)
        if self._tier != "low":
            self._apply_tier("low")
        self._log("error", tr("demo.comm_lost"))
        self._publish_states()
        self.connection_changed.emit(self.connection_info())

    def set_power_tier(self, tier: str) -> bool:
        if tier not in TIERS:
            return False
        if tier == self._tier:
            return True
        if tier == "high" and (
            self._estop or not self._connected or any(m.enabled for m in self._motors.values())
        ):
            self._log("warn", tr("demo.tier_refused"))
            return False
        self._apply_tier(tier)
        return True

    def _apply_tier(self, tier: str) -> None:
        """段階を実際に切り替える(検証は呼び出し側)。"""
        self._tier = tier
        self._log("info", tr("demo.tier_changed", tier=tr(f"tier.{tier}.short")))
        # モータ側の制限も更新(type 18 で limit_torque / limit_cur / limit_spd)
        lim = TIERS[tier]
        if self._connected:
            for m in self._motors.values():
                for idx, val in ((0x700B, lim.torque), (0x7018, lim.current), (0x7017, lim.speed)):
                    self._params[m.id][idx] = val
                    self._frame("TX", self._id(18, _HOST_ID, m.id), self._w_float(idx, val),
                                tr("frame.write_param", name=self._defs[idx].name,
                                   value=f"{val:g}"))
        self.power_tier_changed.emit(tier)

    def start_controller(
        self,
        name: str,
        assign: dict[str, int],
        params: object,
        *,
        continuous: bool = True,
        period_ms: int = 5,
    ) -> None:
        if name == "bilateral":
            self._start_bilateral(assign, params, period_ms)
            return
        mid = assign.get("motor")
        m = self._motors.get(mid) if mid is not None else None
        if m is None:
            self._log("warn", tr("demo.no_motor"))
            return
        if name not in PARAMS_BY_CONTROLLER:
            self._log("error", tr("demo.unknown_controller", name=name))
            return
        if self._estop:
            self._log("warn", tr("demo.estop_blocked"))
            return
        if m.faults:
            self._log("warn", tr("demo.enable_blocked_fault", id=m.id))
            return
        if not m.enabled:  # 運転開始は有効(enabled)なモータからのみ
            self._log("warn", tr("demo.start_needs_enabled", id=m.id))
            return
        if m.ctrl is not None and not m.ctrl.holding and m.ctrl.name != name:
            self._log("warn", tr("demo.mode_switch_refused", id=m.id))
            return
        m.ctrl = _Ctrl(name, dataclasses.replace(params), continuous, period_ms)  # type: ignore[type-var]
        m.run_mode = {"mit": 0, "pp": 1, "velocity": 2, "current": 3, "csp": 5, "jog": 2}.get(
            name, m.run_mode
        )
        m.p_plan, m.v_plan, m.w_ramp = m.theta, m.omega, m.omega
        self._log("info", tr("demo.controller_started", id=m.id, name=tr(f"controller.{name}")))
        self._publish_states()

    def _start_bilateral(self, assign: dict[str, int], params: object, period_ms: int) -> None:
        """2台連動の代役: 2 台を MIT 相当で結び、core の Bilateral と同じ式で結合する。"""
        lead = self._motors.get(assign.get("leader", -1))
        fol = self._motors.get(assign.get("follower", -1))
        if lead is None or fol is None or lead is fol:
            self._log("warn", tr("demo.no_motor"))
            return
        if self._estop:
            self._log("warn", tr("demo.estop_blocked"))
            return
        for m in (lead, fol):
            if m.faults:
                self._log("warn", tr("demo.enable_blocked_fault", id=m.id))
                return
            if not m.enabled:
                self._log("warn", tr("demo.start_needs_enabled", id=m.id))
                return
        d0 = fol.theta - lead.theta
        shared = {"d": d0, "d0": d0}
        for m, role, other in ((lead, "leader", fol), (fol, "follower", lead)):
            m.run_mode = 0
            m.ctrl = _Ctrl(
                "bilateral", dataclasses.replace(params), True, period_ms,  # type: ignore[type-var]
                role=role, partner=other.id, link=shared,
            )
        name = tr("controller.bilateral")
        self._log("info", tr("demo.controller_started", id=lead.id, name=name))
        self._publish_states()

    def update_controller_params(self, name: str, assign: dict[str, int], params: object) -> None:
        if name == "bilateral":
            for key in ("leader", "follower"):
                m = self._motors.get(assign.get(key, -1))
                if m is not None and m.ctrl is not None and m.ctrl.name == name:
                    m.ctrl.params = dataclasses.replace(params)  # type: ignore[type-var]
            return
        m = self._motors.get(assign.get("motor", -1))
        if m is not None and m.ctrl is not None and m.ctrl.name == name:
            m.ctrl.params = dataclasses.replace(params)  # type: ignore[type-var]

    def stop_controller(self, name: str, assign: dict[str, int]) -> None:
        """運転停止: 速度・電流は 0 へ、位置・MIT は現在位置保持へ移行して enabled に戻る。"""
        if name == "bilateral":
            for key in ("leader", "follower"):
                b = self._motors.get(assign.get(key, -1))
                if b is not None and b.ctrl is not None and not b.ctrl.holding:
                    b.ctrl = self._hold_ctrl(b, 0)
            self._log("info", tr("demo.controller_stopped", id=assign.get("leader", 0),
                                 name=tr("controller.bilateral")))
            self._publish_states()
            return
        m = self._motors.get(assign.get("motor", -1))
        if m is None or m.ctrl is None or m.ctrl.holding:
            return
        self._log("info", tr("demo.controller_stopped", id=m.id, name=tr(f"controller.{name}")))
        p = m.ctrl.params
        if isinstance(p, (SineParams, StepParams)):  # 試験信号は対象量に応じた保持へ
            run_mode = {"position": 5, "velocity": 2, "current": 3, "torque": 3}[p.target]
            m.ctrl = self._hold_ctrl(m, run_mode)
        else:
            m.ctrl = self._hold_ctrl(m, m.run_mode, p)
        self._publish_states()

    # ------------------------------------------------------------------ パラメータ
    def read_all_params(self, motor_id: int) -> None:
        if motor_id not in self._params:
            return
        self._log("info", tr("demo.param_read_all", id=motor_id))
        for d in EL05_PARAM_DEFS[:6]:
            self._frame("TX", self._id(17, _HOST_ID, motor_id),
                        struct.pack("<H6x", d.index), tr("frame.read_param", name=d.name))
        QTimer.singleShot(250, lambda: self._emit_params(motor_id))

    def write_param(self, motor_id: int, index: int, value: float) -> None:
        d = self._defs.get(index)
        if d is None or motor_id not in self._params or "W" not in d.rw:
            self._log("warn", tr("demo.param_write_refused", index=f"0x{index:04X}"))
            return
        if d.dtype != "float":
            value = int(value)
        self._params[motor_id][index] = value
        self._dirty[motor_id].add(index)
        self._log("info", tr("demo.param_written", id=motor_id, name=d.name, value=f"{value:g}"))
        self._frame("TX", self._id(18, _HOST_ID, motor_id), self._w_float(index, float(value)),
                    tr("frame.write_param", name=d.name, value=f"{value:g}"))
        self._emit_params(motor_id)

    def save_params(self, motor_id: int) -> None:
        if motor_id not in self._params:
            return
        self._log("info", tr("demo.param_saved", id=motor_id))
        self._frame("TX", self._id(22, _HOST_ID, motor_id), bytes(range(1, 9)),
                    tr("frame.save_params"))
        self._emit_params(motor_id)

    def _emit_params(self, motor_id: int) -> None:
        m = self._motors.get(motor_id)
        live: dict[int, float] = {}
        if m is not None:
            live = {0x7019: m.theta, 0x701A: m.tau / _KT, 0x701B: m.omega, 0x701C: m.vbus}
        vals = []
        for d in EL05_PARAM_DEFS:
            v = live.get(d.index, self._params[motor_id].get(d.index, d.default))
            if isinstance(v, float):
                v = round(v, 6)
            vals.append(ParamValue(d.index, d.name, v, d.unit, d.index in self._dirty[motor_id]))
        self.params_updated.emit(motor_id, vals)

    def run_tool(self, tool: str, motor_id: int, args: dict[str, Any]) -> None:
        """モック: ログとフレームを出すだけ(ゼロ点設定のみ位置を 0 にする)。"""
        detail = ", ".join(f"{k}={v}" for k, v in args.items())
        self._log("warn", tr("demo.tool_executed", tool=tr(f"tool.{tool}.title"), id=motor_id,
                             detail=detail or "-"))
        types = {"set_can_id": 7, "set_zero": 6, "active_report": 24, "can_timeout": 18,
                 "set_baud": 23, "switch_protocol": 25, "restore_protocol": 25}
        self._frame("TX", self._id(types.get(tool, 18), _HOST_ID, motor_id), bytes(8),
                    tr(f"tool.{tool}.title"))
        m = self._motors.get(motor_id)
        if tool == "set_zero":
            ok = m is not None and not m.enabled
            if ok:
                m.theta = 0.0
                m.p_plan = 0.0
                m.zero_gap = True  # グラフの線を 1 サンプル切る(0 への跳びを線で結ばない)
            # 実機(MotorService.set_zero)と同じく、停止中のモータだけに効く
            self.tool_finished.emit(
                tool, motor_id, ok, tr("demo.zero_done") if ok else tr("demo.zero_refused")
            )
        else:
            self.tool_finished.emit(tool, motor_id, True, "")

    # ------------------------------------------------------------------ 内部: ログ・フレーム
    def _log(self, level: str, text: str) -> None:
        self.log_message.emit(LogEntry(time.time(), level, text))

    def _frame(self, direction: str, can_id: int, data: bytes, text: str = "") -> None:
        self._counts["tx" if direction == "TX" else "rx"] += 1
        self.frame_logged.emit(FrameEntry(time.time(), direction, can_id, bytes(data), text))

    @staticmethod
    def _id(ctype: int, data2: int, dest: int) -> int:
        return ((ctype & 0x1F) << 24) | ((data2 & 0xFFFF) << 8) | (dest & 0xFF)

    @staticmethod
    def _w_float(index: int, value: float) -> bytes:
        return struct.pack("<H2xf", index, value)

    def _feedback_frame(self, m: _Motor) -> None:
        mode = {"reset": 0, "cali": 1, "motor": 2}[m.mode()]
        bits = 0
        for i, key in enumerate(
            ("undervoltage", "overcurrent", "overtemp", "encoder", "overload", "uncalibrated")
        ):
            if key in m.faults:
                bits |= 1 << i
        can_id = (2 << 24) | (mode << 22) | (bits << 16) | (m.id << 8) | _HOST_ID
        pos = _wrap(m.theta)
        data = struct.pack(
            ">HHHH",
            _u16(pos, -_P_RANGE, _P_RANGE),
            _u16(m.omega, -50.0, 50.0),
            _u16(m.tau, -6.0, 6.0),
            int(m.temp * 10),
        )
        text = tr("frame.feedback", pos=f"{pos:.3f}", vel=f"{m.omega:.3f}", tq=f"{m.tau:.3f}",
                  temp=f"{m.temp:.1f}", mode=tr(f"mode.{m.mode()}"))
        self._frame("RX", can_id, data, text)

    def _command_frame(self, m: _Motor) -> None:
        c = m.ctrl
        if c is None:
            return
        p = c.params
        if isinstance(p, MitParams):
            lim = TIERS[self._tier]
            tff = _clamp(p.torque_ff, -lim.torque, lim.torque)
            data = struct.pack(
                ">HHHH",
                _u16(p.position, -12.57, 12.57),
                _u16(_clamp(p.velocity, -lim.speed, lim.speed), -50.0, 50.0),
                _u16(_clamp(p.kp, 0, lim.kp), 0.0, 500.0),
                _u16(_clamp(p.kd, 0, lim.kd), 0.0, 5.0),
            )
            can_id = (1 << 24) | (_u16(tff, -6.0, 6.0) << 8) | m.id
            text = tr("frame.mit", p=f"{p.position:.3f}", v=f"{p.velocity:.3f}",
                      kp=f"{p.kp:.2f}", kd=f"{p.kd:.3f}", t=f"{tff:.3f}")
            self._frame("TX", can_id, data, text)
            return
        idx, name, val = {
            "velocity": (0x700A, "spd_ref", m.cmd_v),
            "current": (0x7006, "iq_ref", m.cmd_t),
            "csp": (0x7016, "loc_ref", m.cmd_p),
            "pp": (0x7016, "loc_ref", m.cmd_p),
        }.get(c.name, (0x700A, "spd_ref", m.cmd_v))
        v = 0.0 if val is None else val
        self._frame("TX", self._id(18, _HOST_ID, m.id), self._w_float(idx, v),
                    tr("frame.write_param", name=name, value=f"{v:.3f}"))

    # ------------------------------------------------------------------ 内部: 物理
    def _tau_max(self, lim: PowerLimits, current_limit: float | None) -> float:
        cur = lim.current if current_limit is None else min(current_limit, lim.current)
        return min(lim.torque, _KT * cur)

    def _velocity_loop(self, m: _Motor, w_target: float, accel: float, tmax: float,
                       dt: float) -> float:
        step = accel * dt
        m.w_ramp += _clamp(w_target - m.w_ramp, -step, step)
        m.cmd_v = m.w_ramp
        return _clamp(_KV * (m.w_ramp - m.omega), -tmax, tmax)

    def _position_loop(self, m: _Motor, p_ref: float, speed_limit: float, tmax: float,
                       dt: float) -> float:
        w = _clamp(_KPP * (p_ref - m.theta), -speed_limit, speed_limit)
        m.cmd_p = p_ref
        return self._velocity_loop(m, w, 400.0, tmax, dt)

    def _signal(self, p: Any, t: float) -> float:
        if isinstance(p, SineParams):
            return p.offset + p.amplitude * math.sin(_TWO_PI * p.frequency * t)
        if isinstance(p, StepParams):
            return p.high if (t % p.period) < p.period / 2 else p.low
        return 0.0

    @staticmethod
    def _clamp_text(p: Any, lim: PowerLimits) -> str:
        """`limit` 付きの項目が段階の上限を超えていれば「項目 値 → 上限」(SafetyGuard の代役)。"""
        notes: list[str] = []
        if not dataclasses.is_dataclass(p):
            return ""
        for f in dataclasses.fields(p):
            key = f.metadata.get("limit")
            v = getattr(p, f.name, None)
            if not key or not isinstance(v, (int, float)):
                continue
            cap = float(getattr(lim, key))
            neg = f.metadata.get("neg_limit")  # 負側に別の上限がある項目(MIT の kd)
            if neg and v < 0.0:
                cap = float(getattr(lim, neg))
            if abs(v) > cap + 1e-9:
                notes.append(f"{f.name} {v:g} → {math.copysign(cap, v):g}")
        return "・".join(notes)

    def clamp_note(self, motor_id: int) -> str:
        m = self._motors.get(motor_id)
        return m.clamp if m is not None else ""

    def _bilateral_torque(
        self, m: _Motor, c: _Ctrl, p: BilateralParams, dt: float, lim: PowerLimits
    ) -> float:
        """core の Bilateral と同じ構成(位置差を保ち、相手の位置・速度へ Kp・Kd で寄る)。"""
        other = self._motors.get(c.partner)
        link = c.link
        if other is None or link is None:
            return 0.0
        if c.role == "leader":  # 目標位置差の更新は 1 周期に 1 回(リーダ側で)
            target = link["d0"] + p.offset if p.offset_mode == "keep" else p.offset
            step = max(0.0, min(p.converge_speed, lim.speed)) * dt
            link["d"] += _clamp(target - link["d"], -step, step)
        d = link["d"]
        kp, kd = _clamp(p.stiffness, 0.0, lim.kp), _clamp(p.damping, 0.0, lim.kd)
        if c.role == "leader" and p.mode == "master_slave":
            tau = -_clamp(p.leader_damping, 0.0, lim.kd) * m.omega  # 手で動かせる
        else:
            p_set = other.theta + d if c.role == "follower" else other.theta - d
            tau = kp * (p_set - m.theta) + kd * (other.omega - m.omega) + p.force_ff * other.tau
            m.cmd_p, m.cmd_v = p_set, other.omega
        return _clamp(tau, -lim.torque, lim.torque)

    def _step_motor(self, m: _Motor, dt: float, lim: PowerLimits) -> None:
        m.cmd_p = m.cmd_v = m.cmd_t = None
        tau_cmd = 0.0
        c = m.ctrl
        live = m.enabled and c is not None and not c.holding
        m.clamp = self._clamp_text(c.params, lim) if live and c is not None else ""
        if m.enabled and c is not None:
            c.t += dt
            p = c.params
            if isinstance(p, MitParams):
                kp = _clamp(p.kp, 0.0, lim.kp)
                kd = _clamp(p.kd, -lim.kd_negative_max, lim.kd)
                fc = _clamp(p.friction_comp, 0.0, lim.friction_comp_max)
                v_set = _clamp(p.velocity, -lim.speed, lim.speed)
                tff = _clamp(p.torque_ff, -lim.torque, lim.torque)
                # PC 側の項(負の kd・摩擦補償)と暴走ガード(SafetyGuard の代役。ヒステリシス付き)
                if kd < 0.0 or fc > 0.0:
                    if abs(m.omega) > 0.8 * lim.speed:
                        m.runaway = True
                    elif abs(m.omega) < 0.4 * lim.speed:
                        m.runaway = False
                else:
                    m.runaway = False
                if m.runaway:
                    kd, fc = max(kd, 0.1), 0.0
                tff += min(kd, 0.0) * (v_set - m.omega) + fc * math.tanh(m.omega / 0.05)
                kd = max(kd, 0.0)
                tau_cmd = kp * (p.position - m.theta) + kd * (v_set - m.omega) + tff
                tau_cmd = _clamp(tau_cmd, -lim.torque, lim.torque)
                m.cmd_p, m.cmd_v = p.position, v_set
            elif isinstance(p, VelocityParams):
                tmax = self._tau_max(lim, p.current_limit)
                tau_cmd = self._velocity_loop(
                    m, _clamp(p.velocity, -lim.speed, lim.speed), p.accel, tmax, dt)
            elif isinstance(p, CurrentParams):
                iq = _clamp(p.iq, -lim.current, lim.current)
                tau_cmd = _clamp(_KT * iq, -lim.torque, lim.torque)
            elif isinstance(p, CspParams):
                tau_cmd = self._position_loop(
                    m, p.position, min(p.speed_limit, lim.speed), self._tau_max(lim, None), dt)
            elif isinstance(p, PpParams):
                err = p.position - m.p_plan
                v_des = math.copysign(
                    min(min(p.velocity, lim.speed), math.sqrt(2 * max(p.accel, 0.1) * abs(err))),
                    err,
                )
                a = max(p.accel, 0.1) * dt
                m.v_plan += _clamp(v_des - m.v_plan, -a, a)
                m.p_plan += m.v_plan * dt
                tau_cmd = self._position_loop(m, m.p_plan, lim.speed, self._tau_max(lim, None), dt)
                m.cmd_p = p.position
            elif isinstance(p, BilateralParams):
                tau_cmd = self._bilateral_torque(m, c, p, dt, lim)
            elif isinstance(p, JogParams):
                tmax = self._tau_max(lim, p.current_limit)
                tau_cmd = self._velocity_loop(
                    m, _clamp(p.speed, -lim.speed, lim.speed), p.accel, tmax, dt)
            elif isinstance(p, (SineParams, StepParams)):
                s = self._signal(p, c.t)
                tgt = p.target
                if tgt == "position":
                    tau_cmd = self._position_loop(m, s, lim.speed, self._tau_max(lim, None), dt)
                elif tgt == "velocity":
                    tau_cmd = self._velocity_loop(
                        m, _clamp(s, -lim.speed, lim.speed), 100.0, self._tau_max(lim, None), dt)
                elif tgt == "current":
                    tau_cmd = _clamp(_KT * _clamp(s, -lim.current, lim.current),
                                     -lim.torque, lim.torque)
                else:
                    tau_cmd = _clamp(s, -lim.torque, lim.torque)
            m.cmd_t = tau_cmd
        # 物理(半陰的オイラー)
        tau_drive = tau_cmd if m.enabled else 0.0
        m.omega += (tau_drive - (_B if m.enabled else _B_OFF) * m.omega) / _J * dt
        m.theta += m.omega * dt
        m.tau = tau_drive
        # 温度(時定数 60 s、発熱は τ² に比例)
        target_t = 26.0 + 18.0 * (m.tau / 0.5) ** 2
        m.temp += (target_t - m.temp) * dt / 60.0
        m.vbus = 24.0 - 0.15 * abs(m.tau)

    # ------------------------------------------------------------------ 内部: タイマ
    def _tick(self) -> None:
        if not self._connected or not self._motors:
            return
        now = time.perf_counter()
        target = int((now - self._wall0) * SAMPLE_HZ)
        k = target - self._n
        if k <= 0:
            return
        if k > 400:  # 停滞後は追い付かず飛ばす
            self._wall0 += (k - 400) / SAMPLE_HZ
            k = 400
        lim = TIERS[self._tier]
        dt = 1.0 / SAMPLE_HZ
        names = [f"m{mid}.{s}" for mid in self._motors for s, *_ in _CHANNEL_KINDS]
        buf = {n: np.empty(k, dtype=np.float32) for n in names}
        gauss = self._rng.gauss
        nan = float("nan")
        for i in range(k):
            for mid, m in self._motors.items():
                self._step_motor(m, dt, lim)
                pre = f"m{mid}."
                if m.zero_gap:
                    buf[pre + "pos"][i] = nan
                    m.zero_gap = False
                else:
                    buf[pre + "pos"][i] = m.theta + gauss(0, 0.0004)
                buf[pre + "vel"][i] = m.omega + gauss(0, 0.004)
                buf[pre + "torque"][i] = m.tau + gauss(0, 0.004)
                buf[pre + "temp"][i] = m.temp
                buf[pre + "vbus"][i] = m.vbus + gauss(0, 0.01)
                buf[pre + "cmd_pos"][i] = nan if m.cmd_p is None else m.cmd_p
                buf[pre + "cmd_vel"][i] = nan if m.cmd_v is None else m.cmd_v
                buf[pre + "cmd_torque"][i] = nan if m.cmd_t is None else m.cmd_t
        idx = (self._n + np.arange(k)) % _RING
        self._t_ring[idx] = (self._n + np.arange(k)) / SAMPLE_HZ
        for n in names:
            self._rings[n][idx] = buf[n]
        self._n += k
        self._publish_states()
        # フレームは間引いて流す(約 10 組/s/台)
        self._frame_accum += k
        if self._frame_accum >= 100:
            self._frame_accum = 0.0
            for m in self._motors.values():
                if m.ctrl is not None and m.enabled:
                    self._command_frame(m)
                    self._feedback_frame(m)

    def _ui_state(self, m: _Motor) -> str:
        if not self._connected:
            return "offline"
        if m.faults:
            return "fault"
        if m.ctrl is not None and not m.ctrl.holding and m.enabled:
            return "running"
        return "enabled" if m.enabled else "disabled"

    def _publish_states(self) -> None:
        states: dict[int, MotorViewState] = {}
        for mid, m in self._motors.items():
            ui_state = self._ui_state(m)
            running = ui_state == "running"
            states[mid] = MotorViewState(
                id=mid,
                online=self._connected,
                mode=m.mode(),
                run_mode=m.run_mode,
                position=_wrap(m.theta),
                position_multi=m.theta,
                velocity=m.omega,
                torque=m.tau,
                temperature=m.temp,
                vbus=m.vbus,
                faults=tuple(sorted(m.faults)),
                controller=m.ctrl.name if running and m.ctrl is not None else None,
                cmd_position=m.cmd_p,
                cmd_velocity=m.cmd_v,
                cmd_torque=m.cmd_t,
                ui_state=ui_state,  # type: ignore[arg-type]
                power=estimate_power(m.tau, m.omega, _POWER_SPEC) if self._connected else None,
            )
        self._states = states
        if self._rec_path is not None and states:
            self._rec_rows.append((time.perf_counter() - self._rec_t0, dict(states)))
        self.state_updated.emit(states)

    def _emit_stats(self) -> None:
        now = time.perf_counter()
        dt = max(now - self._last_stats, 1e-3)
        self._last_stats = now
        tx_fps = self._counts["tx"] / dt
        rx_fps = self._counts["rx"] / dt
        self._counts["tx"] = self._counts["rx"] = 0
        active = [m for m in self._motors.values() if m.ctrl is not None and m.enabled]
        loop = 0.0
        if active:
            per = max(1, min(a.ctrl.period_ms for a in active if a.ctrl is not None))
            loop = 1000.0 / per
            tx_fps = rx_fps = loop * len(active)  # 間引き前の実効レート(表示用)
        self.stats_updated.emit(
            BusStats(
                tx_fps, rx_fps, loop, self._rng.uniform(0.08, 0.35) if active else 0.0,
                self._parse_errors, self._timeouts,
            )
        )
