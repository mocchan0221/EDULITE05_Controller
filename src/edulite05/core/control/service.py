"""MotorService: UI と CLI が使う唯一の運転 API(phase2.md §1.5, ui_design.md §3 / §12)。

構成: `Session`(バス・Motor 群)の上に `SafetyGuard` / `ControlLoop` / `TelemetryHub` を載せ、
モータごとの UI 状態(`UiState`)を管理する。モータへの運転系指令はすべてここを経由する。

停止の 3 種(混同しない):
  * 運転停止 `stop_motion`  : コントローラを外し(on_stop を 1 回)、位置保持へ。有効のまま。
  * 無効化   `disable`      : type 4。
  * 非常停止 `estop`        : 全モータへ type 4 を `send_now` → 全 detach → ラッチ → 出力段階 LOW。

スレッド:
  * GUI スレッドから呼んでよい。I/O を待つ複数手順(enable / disable)は小さなワーカーで実行し、
    `Future` を返す。`start` / `stop_motion` / `update` / `estop` / `set_tier` は待たずに戻る
    (ControlLoop のロックを待つだけ)。
  * 監視スレッド 1 本(約 50 Hz)が、通信監視・故障処理・低速チャネルの取得(10 Hz)を行う。
    RX スレッドのコールバックでは重い処理をせず、キューに積んで監視スレッドへ渡す。
  * ロック順序: `MotorService._lock` → `ControlLoop._tick_lock`。ControlLoop のスレッドから
    呼ばれるイベント購読(control.clamped / control.error)は `_lock` を取らない。

E-STOP との競合: 手順(enable)の途中で E-STOP が入ったら、各段階の前後で世代番号(`_Rec.gen`)を
確認して中断し、type 3 を送ってしまった場合は即 stop を送り直す。

主動上報の方針: ON(10 ms)は **無効(DISABLED / FAULT)の監視中だけ**。実機では主動上報中に要求への
応答が約 5 % 欠落する(hw_bench.md)ため、`enable()` の最初(設定の書込みより前)に OFF にし、
有効・運転中は OFF のまま(制御ループの type 1 / 18 への type 2 応答が周期どおりフィード
バックになる。keepalive の Hold を常に送るのでフィードバックは途切れない。通信監視もこれを使う)。
無効化の完了後・故障・通信断・E-STOP の後は ON へ戻す。
`report_while_enabled=True` で運転中も ON のままにできる。

停止時の減速: `stop_and_disable` は運転停止 → 速度が `speed_eps` 未満になる(または
`settle_timeout_s`)まで待つ → 無効化。無効化の直前まで速度指令 0 が効いていたモータが惰走しない。
E-STOP は待たない。

保守操作(`set_zero` / `set_can_id` / `set_report_period` / `set_can_timeout` / `set_baud` /
`switch_protocol` / `restore_protocol` / `write_param` / `read_all_params` / `save_params`):
すべて `Future` を返し、対象が **DISABLED**・E-STOP ラッチなし・接続中が前提(違反は呼び出しの時点で
ProtocolError / SafetyError)。`restore_protocol` だけは接続と「有効・運転中のモータなし」で足りる。
実行中は `rec.busy = "maintenance"`(有効化は拒否)。主動上報は操作の前に OFF、後に ON へ戻す
(応答の欠落を減らす)。冪等でない操作(set_zero / save / set_can_id / baud / protocol)は再送しない。
運転系パラメータ(run_mode / iq_ref / spd_ref / loc_ref / limit_*)は `write_param` では書けない
(運転経路 = enable / コントローラだけが書く)。

canTimeout(0x7028)は有効化時に `round(comm_timeout_s / 50e-6)` を書く。無効化時は 0 に戻さない
(安全側)。ホスト側の通信監視(`comm_timeout_s`)は同じ時間でモータを無効化する。
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from functools import partial
from typing import Any

from edulite05.core import clock
from edulite05.core.bus import Priority
from edulite05.core.control.commands import HoldCmd
from edulite05.core.control.controller import Controller
from edulite05.core.control.loop import (
    CONTROL_CLAMPED,
    CONTROL_ERROR,
    CONTROL_SEND_ERROR,
    ControlLoop,
    Handle,
)
from edulite05.core.device.motor import Motor
from edulite05.core.device.profile import ParamDef
from edulite05.core.errors import ProtocolError, RequestTimeout, SafetyError, TransportError
from edulite05.core.events import (
    BUS_ERROR,
    CONNECTION_CHANGED,
    ESTOP,
    MOTOR_DISCOVERED,
    Unsubscribe,
)
from edulite05.core.protocol import private
from edulite05.core.protocol.faults import (
    FaultInfo,
    describe_fault21,
    describe_feedback_faults,
)
from edulite05.core.protocol.private import (
    FaultReport,
    Feedback,
    Message,
    ParamResponse,
    UidResponse,
)
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.safety import PowerTier, SafetyGuard
from edulite05.core.session import Session
from edulite05.core.telemetry import TelemetryHub, motor_channel, register_motor_channels
from edulite05.core.telemetry.power import estimate_power
from edulite05.core.transport.base import CanFrame

log = logging.getLogger(__name__)

# --- イベント(`session.events` へ発行。ペイロードは dict)---------------------
# {"motor_id", "state": UiState, "previous": UiState | None, "reason": str}
SERVICE_STATE_CHANGED = "service.state_changed"
SERVICE_ESTOP = "service.estop"  # {"latched": bool, "motor_ids": list[int]}
SERVICE_TIER = "service.tier"  # {"tier": PowerTier, "previous": PowerTier}
SERVICE_CLAMPED = "service.clamped"  # control.clamped の転送 {"motor_id", "notes"}(空 = 解除)
SERVICE_FAULT = "service.fault"  # {"motor_id", "fault_bits", "fault", "warning", "source"}
SERVICE_COMM_LOST = "service.comm_lost"  # {"motor_ids": list[int], "reason": "timeout"|"transport"}
SERVICE_CONTROL_ERROR = "service.control_error"  # {"motor_ids", "controller", "error"}

# 保守操作(set_can_id で ID が変わったときの付け替えを含む)
# {"motor_ids": 付け替え後の全 ID(昇順), "renamed": {旧 ID: 新 ID}}
SERVICE_MOTORS_CHANGED = "service.motors_changed"
# 保守操作の完了(成否とも)。{"op", "motor_id", "ok": bool, "message": str, "error": 例外 | None}
SERVICE_MAINTENANCE = "service.maintenance"

DEFAULT_COMM_TIMEOUT_S = 0.25
CAN_TIMEOUT_UNIT_S = 50e-6  # canTimeout の 1 カウント(el05_protocol.md §5.2)
REPORT_PERIOD_MS = 10  # 主動上報の周期(EPScan_time N=0 で書かれる。周期 = 10 + 5·N ms)
MONITOR_PERIOD_S = 0.02
POLL_PERIOD_S = 0.1  # 低速チャネル: 1 モータにつき 10 Hz で 1 件(iqf → VBUS → mechPos の順)
POLL_TIMEOUT_S = 0.2
SETTLE_POLL_S = 0.005  # stop_and_disable: 速度の確認周期
WAIT_S = 3.0  # 手順の各ステップを待つ上限(Motor の要求タイムアウトより十分長い)
DISABLE_TRIES = 4  # 無効化(type 4)の送信回数の上限(Reset のフィードバックを確認できるまで)
SEED_RETRY_S = 1.0  # 停止中の多回転角の較正(sync_multi)を再試行する間隔
STOPPED_TICKS = 3  # 有効なはずのモータが Reset のまま続いたと見なす監視周期数

_REPORT_PERIOD_KEY = "EPScan_time"
_CAN_TIMEOUT_KEY = "canTimeout"

# 運転経路(enable / コントローラ)だけが書くパラメータ。サービスの保守 API では書かせない。
# limit_* は出力段階(SafetyGuard)の二重制限そのもの
MOTION_PARAM_KEYS = frozenset(
    {"run_mode", "iq_ref", "spd_ref", "loc_ref", "limit_torque", "limit_cur", "limit_spd"}
)
ZERO_RUN_MODES = (RunMode.OPERATION, RunMode.CSP)  # type 6 が効く run_mode(el05_protocol.md §2.7)
ZERO_TOLERANCE_RAD = 0.1  # set_zero 後に mechPos がこの範囲なら「0 付近」
MAX_REPORT_PERIOD_MS = 500  # 上報周期の上限(DISABLED 監視のオンライン判定 1 s に収まる範囲)
BAUD_LABELS = {1: "1 Mbps", 2: "500 kbps", 3: "250 kbps", 4: "125 kbps"}
PROTOCOL_LABELS = {0: "独自(私有)", 1: "CANopen", 2: "MIT"}
_PROTOCOL_ALIASES = {
    "private": 0,
    "own": 0,
    "canopen": 1,
    "mit": 2,
}
_OP_LABELS = {
    "set_zero": "ゼロ点設定",
    "set_can_id": "CAN ID 変更",
    "set_report_period": "上報周期の設定",
    "set_can_timeout": "CAN タイムアウトの設定",
    "set_baud": "ボーレート変更",
    "switch_protocol": "プロトコル切替",
    "write_param": "パラメータ書込",
    "read_all_params": "パラメータ全読出",
    "save_params": "パラメータ保存",
    "recover": "復帰確認",
}
_RESTORE_SOURCES = ("mit", "canopen")
RESTORE_RETRIES = 2  # restore_protocol の再送回数(冪等: 同じ F_CMD を送るだけ)
RESTORE_REPLY_TIMEOUT_S = 0.3

# (パラメータキー, チャネルのサフィックス)
_SLOW_CHANNELS = (("iqf", "iqf"), ("VBUS", "vbus"), ("mechPos", "mechpos"))
_MECHPOS_CHANNEL = ("mechpos", "rad", "param", "mechPos (poll)")


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    """保守操作の結果。`message` は UI にそのまま出せる日本語。

    needs_power_cycle: 電源の再投入が必要な操作(baud / protocol / restore)。
    verified: 結果を読み戻し・応答で確認できたか(確認できない操作は False)。
    """

    op: str
    message: str
    motor_id: int | None = None
    value: Any = None
    needs_power_cycle: bool = False
    verified: bool = True


class IdChangeResult(int):
    """`set_can_id` の結果: **新しい ID**(int)に `message` などが付く。"""

    message: str
    old_id: int
    needs_power_cycle: bool = False

    def __new__(cls, new_id: int, old_id: int, message: str) -> IdChangeResult:
        obj = super().__new__(cls, new_id)
        obj.old_id = old_id
        obj.message = message
        return obj


class UiState(Enum):
    """ui_design.md §3 の UI 状態。"""

    OFFLINE = "offline"
    DISABLED = "disabled"
    ENABLED = "enabled"
    RUNNING = "running"
    FAULT = "fault"


@dataclass(slots=True)
class _Rec:
    """モータ 1 台の運転状態。`state` は内部状態(OFFLINE は持たず、`MotorService._ui` で導出)。"""

    motor: Motor
    state: UiState = UiState.DISABLED
    run_mode: RunMode | None = None
    busy: str | None = None  # "enable" / "disable"
    gen: int = 0  # 中断用の世代番号(disable / estop / 故障 / 通信断で進める)
    handle: Handle | None = None
    controllers: list[Controller] = field(default_factory=list)
    comm_lost: bool = False
    lost_t: float = 0.0
    fault_bits: int = 0
    fault21: int = 0
    warning21: int = 0
    fault_clear_t: float = 0.0
    enabled_t: float = 0.0
    stopped_ticks: int = 0
    poll_fut: Future[Any] | None = None
    poll_i: int = 0
    last_ui: UiState | None = None
    report_ms: int = REPORT_PERIOD_MS  # 主動上報の周期(set_report_period で変更)
    seed_fut: Future[Any] | None = None  # 多回転角の較正(sync_multi)の実行中の要求
    seed_t: float = -1e9  # 最後に較正を試みた時刻(clock.now())
    fb_off: Callable[[], None] | None = None  # Motor のフィードバック購読の解除


def _done(value: Any = None) -> Future[Any]:
    f: Future[Any] = Future()
    f.set_result(value)
    return f


class MotorService:
    def __init__(
        self,
        session: Session,
        rate_hz: float = 200.0,
        tier: PowerTier = PowerTier.LOW,
        *,
        comm_timeout_s: float = DEFAULT_COMM_TIMEOUT_S,
        telemetry: TelemetryHub | None = None,
        report_while_enabled: bool = False,
    ) -> None:
        if comm_timeout_s <= 0:
            raise ValueError("comm_timeout_s は正である必要があります")
        self.session = session
        self.comm_timeout_s = comm_timeout_s
        self.report_while_enabled = report_while_enabled  # True: 有効・運転中も主動上報 ON のまま
        self.guard = SafetyGuard(session.profile, tier)
        self.telemetry = telemetry or TelemetryHub(capacity_s=120.0, max_rate_hz=500.0)
        self.loop = ControlLoop(session.bus, self.guard, rate_hz, telemetry=self.telemetry)
        self.events = session.events

        self._lock = threading.RLock()
        self._recs: dict[int, _Rec] = {}
        self._latched = False
        self._transport_lost = False
        self._tier_gen = 0
        self._started = False
        self._closed = False

        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="edulite05-svc")
        self._wake = threading.Event()
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._q: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        self._next_poll = 0.0
        self._unsubs: list[Unsubscribe] = []
        self._unsubs.append(session.bus.subscribe(self._on_message))
        ev = session.events
        self._unsubs.append(ev.subscribe(BUS_ERROR, self._on_bus_error))
        self._unsubs.append(ev.subscribe(CONTROL_SEND_ERROR, self._on_send_error))
        self._unsubs.append(ev.subscribe(CONTROL_CLAMPED, self._on_clamped))
        self._unsubs.append(ev.subscribe(CONTROL_ERROR, self._on_control_error))
        self._unsubs.append(ev.subscribe(MOTOR_DISCOVERED, self._on_discovered))
        self._unsubs.append(ev.subscribe(CONNECTION_CHANGED, self._on_connection))

    # ==================================================================
    # ライフサイクル
    # ==================================================================
    def open(self) -> None:
        """ループと監視スレッドを開始し、既知のモータを取り込む(冪等。運転 API が遅延で呼ぶ)。"""
        with self._lock:
            if self._started or self._closed:
                return
            self._started = True
        self.loop.start()
        self._thread = threading.Thread(
            target=self._monitor_run, name="edulite05-service", daemon=True
        )
        self._thread.start()
        for mid in sorted(self.session.motors):
            self._adopt(mid)

    def close(self) -> None:
        """全モータへ stop を送り、ループ・監視を止める(Session は閉じない)。冪等。"""
        self._shutdown(send_stop=True)

    def __enter__(self) -> MotorService:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _shutdown(self, *, send_stop: bool) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._latched_gen_bump()
            self.loop.inhibit(self._all_ids())
            for rec in self._recs.values():
                rec.handle = None
                rec.controllers = []
                rec.busy = None
                rec.state = UiState.DISABLED
                rec.comm_lost = True
        if send_stop:
            self._send_stop(self._all_motors())
        self._stop_evt.set()
        self._wake.set()
        self.loop.stop()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(2.0)
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        for rec in list(self._recs.values()):
            if rec.fb_off is not None:
                rec.fb_off()
                rec.fb_off = None
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ==================================================================
    # 参照
    # ==================================================================
    @property
    def tier(self) -> PowerTier:
        return self.guard.tier

    @property
    def estop_latched(self) -> bool:
        return self._latched

    @property
    def connected(self) -> bool:
        return not self._transport_lost and not self._closed

    def motor_ids(self) -> list[int]:
        return sorted(self._recs)

    def ui_state(self, motor_id: int) -> UiState:
        rec = self._recs.get(motor_id)
        if rec is None:
            return UiState.OFFLINE
        return self._ui(rec)

    def states(self) -> dict[int, UiState]:
        return {i: self._ui(r) for i, r in list(self._recs.items())}

    def is_busy(self, motor_id: int) -> bool:
        rec = self._recs.get(motor_id)
        return rec is not None and rec.busy is not None

    def run_mode_of(self, motor_id: int) -> RunMode | None:
        """有効化済みのモータの run_mode(無効なら None)。"""
        rec = self._recs.get(motor_id)
        if rec is None or rec.state not in (UiState.ENABLED, UiState.RUNNING):
            return None
        return rec.run_mode

    def controller_of(self, motor_id: int) -> Controller | None:
        rec = self._recs.get(motor_id)
        return rec.controllers[0] if rec is not None and rec.controllers else None

    def fault_details(self, motor_id: int) -> list[FaultInfo]:
        rec = self._recs.get(motor_id)
        if rec is None:
            return []
        return describe_feedback_faults(rec.fault_bits) + describe_fault21(
            rec.fault21, rec.warning21
        )

    def motor(self, motor_id: int) -> Motor:
        return self._rec(motor_id).motor

    def _ui(self, rec: _Rec) -> UiState:
        if self._transport_lost or self._closed:
            return UiState.OFFLINE
        if rec.state is UiState.DISABLED and (rec.comm_lost or not rec.motor.state.online):
            return UiState.OFFLINE
        return rec.state

    # ==================================================================
    # 運転 API
    # ==================================================================
    def enable(self, motor_id: int, run_mode: RunMode) -> Future[None]:
        """停止 → run_mode → モータ側 limit → canTimeout →(CSP/PP: loc_ref = mechPos)→ type 3
        → keepalive(Hold)。`DISABLED` のモータのみ。"""
        try:
            mode = RunMode(run_mode)
        except ValueError:
            raise ProtocolError(f"未定義の run_mode です: {run_mode!r}") from None
        with self._lock:
            self._check_operable()
            rec = self._rec(motor_id)
            if rec.busy is not None:
                raise ProtocolError(f"ID {motor_id}: {rec.busy} の処理中です")
            if rec.state is not UiState.DISABLED:
                raise ProtocolError(
                    f"ID {motor_id}: 有効化できるのは停止中のモータだけ(現在: {rec.state.value})"
                )
            rec.busy = "enable"
            gen = rec.gen
            self.loop.allow(motor_id)  # disable / E-STOP で立てた指令抑止を解除(keepalive が可能に)
        return self._executor.submit(self._enable_job, rec, mode, gen)

    def start(self, motor_ids: Iterable[int] | int, controller: Controller) -> Future[None]:
        """コントローラを開始する(`ENABLED` で run_mode が一致するモータのみ)。

        単一スロットのコントローラを複数台へ渡したときは、2 台目以降へ同じ Params の複製
        (`type(controller)(controller.params)`)を割り当てる(`controller` 自体は 1 台目)。
        複数スロットのコントローラは `motor_ids` の順にスロットへ割り当てる。
        """
        ids = self._ids(motor_ids)
        slots = tuple(controller.slots)
        with self._lock:
            self._check_operable()
            recs = [self._rec(i) for i in ids]
            if len(slots) == 1:
                ctrls = [controller] + [type(controller)(controller.params) for _ in ids[1:]]
                assigns = [{slots[0]: r.motor} for r in recs]
            elif len(slots) == len(ids):
                ctrls = [controller]
                assigns = [{s: r.motor for s, r in zip(slots, recs, strict=True)}]
            else:
                raise ProtocolError(
                    f"{controller.name}: スロット {slots} に対してモータ {ids} は割り当てられません"
                )
            for r in recs:
                mid = r.motor.motor_id
                if r.busy is not None:
                    raise ProtocolError(f"ID {mid}: {r.busy} の処理中です")
                if r.state is not UiState.ENABLED:
                    raise ProtocolError(
                        f"ID {mid}: 運転開始できるのは有効なモータだけです(現在: {r.state.value})"
                    )
                if r.run_mode != controller.run_mode:
                    raise ProtocolError(
                        f"ID {mid}: run_mode {_mode_name(r.run_mode)} は {controller.name} の "
                        f"{_mode_name(controller.run_mode)} と一致しません"
                    )
            attached: list[tuple[Controller, Handle, list[Motor]]] = []
            try:
                for c, a in zip(ctrls, assigns, strict=True):
                    attached.append((c, self.loop.attach(c, a), list(a.values())))
            except BaseException:
                for _, h, motors in attached:
                    self.loop.detach(h)
                    for m in motors:
                        self._to_hold(self._recs[m.motor_id])
                for r in recs:
                    if self.loop.handle_of(r.motor.motor_id) is None:
                        self._to_hold(r)
                raise
            for c, h, motors in attached:
                for m in motors:
                    r = self._recs[m.motor_id]
                    r.handle = h
                    r.controllers = [c]
                    r.state = UiState.RUNNING
                    r.stopped_ticks = 0
                    self._emit_ui(r, "start")
        return _done()

    def update(self, motor_ids: Iterable[int] | int, params: Any) -> None:
        """運転中のコントローラの Params を差し替える(目標変更)。"""
        ids = self._ids(motor_ids)
        with self._lock:
            self._check_operable()
            seen: set[int] = set()
            ctrls: list[Controller] = []
            for i in ids:
                r = self._rec(i)
                if r.state is not UiState.RUNNING or not r.controllers:
                    raise ProtocolError(f"ID {i}: 運転中ではありません(現在: {r.state.value})")
                for c in r.controllers:
                    if id(c) not in seen:
                        seen.add(id(c))
                        ctrls.append(c)
            for c in ctrls:
                c.update_params(params)

    def stop_motion(self, motor_ids: Iterable[int] | int) -> Future[None]:
        """運転停止: コントローラを外し(on_stop 指令を 1 回)、現在位置保持へ。`ENABLED` に戻る。

        冪等で、どの状態でも安全: 運転中(RUNNING)でなくても、コントローラ / keepalive が
        残っていれば外す。有効(ENABLED / RUNNING)なら外した後に現在位置保持の keepalive へ
        戻す。無効・故障のモータにはコントローラも keepalive も残さず、保持も送らない。
        外した後はキュー内の古い制御指令を捨て、on_stop の指令(速度 0 / 位置保持)を
        最優先で送る。有効化・無効化の処理中(`busy`)のモータには触らない。"""
        ids = self._ids(motor_ids)
        with self._lock:
            self._check_latched()
            for i in ids:
                r = self._rec(i)
                if r.busy is not None:
                    continue
                affected: list[_Rec] = []
                if r.state in (UiState.RUNNING, UiState.ENABLED):
                    affected = self._drop_control(r, send_on_stop=True)
                    r.state = UiState.ENABLED
                    self._to_hold(r)
                else:  # DISABLED / FAULT: 残っていれば外すだけ(on_stop も保持も送らない)
                    affected = self._drop_control(r, send_on_stop=False)
                self._emit_ui(r, "stop_motion")
                for o in affected:
                    self._emit_ui(o, "stop_motion")
        return _done()

    def disable(self, motor_ids: Iterable[int] | int, clear_fault: bool = False) -> Future[None]:
        """無効化(type 4)。コントローラ・keepalive は先に外す。`clear_fault` で故障クリア(B0=1)。

        実行中の有効化手順は中断される。Reset への遷移を確認し(最大 3 回送り直す)、故障が残って
        いれば `FAULT`、なければ `DISABLED` になる。"""
        ids = self._ids(motor_ids)
        with self._lock:
            self._check_latched()
            self._ensure_open()
            recs = [self._rec(i) for i in ids]
            for r in recs:
                r.gen += 1
                self._drop_control(r, send_on_stop=False)
                r.busy = "disable"
                if r.state in (UiState.ENABLED, UiState.RUNNING):
                    r.state = UiState.DISABLED
                self._emit_ui(r, "disable")
        futs: list[Future[Any]] = []
        try:
            futs = [r.motor.disable(clear_fault) for r in recs]
        except BaseException:
            with self._lock:
                for r in recs:
                    if r.busy == "disable":
                        r.busy = None
            raise
        return self._executor.submit(self._finish_disable, recs, futs, clear_fault)

    def stop_and_disable(
        self,
        motor_ids: Iterable[int] | int,
        settle_timeout_s: float = 0.5,
        speed_eps: float = 0.05,
        clear_fault: bool = False,
    ) -> Future[None]:
        """運転停止 → 減速を待つ → 無効化。通常の終了時用(E-STOP は `estop()` で待たない)。

        停止(速度 0 指令 / 位置保持)の後、有効なモータすべての |速度| が `speed_eps` [rad/s]
        未満になる(停止後に受信したフィードバックで判定)か `settle_timeout_s` 経過するまで
        待ってから `disable` する。完了は無効化の完了(Future)。"""
        ids = self._ids(motor_ids)
        self.stop_motion(ids)
        return self.settle_and_disable(ids, settle_timeout_s, speed_eps, clear_fault)

    def settle_and_disable(
        self,
        motor_ids: Iterable[int] | int,
        settle_timeout_s: float = 0.5,
        speed_eps: float = 0.05,
        clear_fault: bool = False,
    ) -> Future[None]:
        """`stop_and_disable` の後半(停止指令は呼び出し側が済ませている前提)。
        有効でないモータ・待つ必要がない場合は即 `disable`。"""
        ids = self._ids(motor_ids)
        with self._lock:
            self._check_latched()
            self._ensure_open()
            recs = [self._rec(i) for i in ids]
            watch = [(r, r.gen) for r in recs if r.state in (UiState.ENABLED, UiState.RUNNING)]
            t0 = clock.now()
        if not watch or settle_timeout_s <= 0:
            return self.disable(ids, clear_fault)
        out: Future[None] = Future()

        def job() -> None:
            try:
                self._settle(watch, t0, settle_timeout_s, speed_eps)
                inner = self.disable(ids, clear_fault)
            except BaseException as e:
                out.set_exception(e)
                return
            inner.add_done_callback(lambda f: _copy_result(f, out))

        try:
            self._executor.submit(job)
        except RuntimeError as e:  # 終了処理中
            out.set_exception(ProtocolError(f"MotorService は閉じられています: {e}"))
        return out

    def _settle(
        self, watch: list[tuple[_Rec, int]], t0: float, timeout_s: float, eps: float
    ) -> None:
        """速度が下がるまで待つ。E-STOP・状態変化(故障・通信断・無効化)・終了なら即戻る。
        速度は `t0`(停止指令の時刻)以降に受信したフィードバックだけで判定する。"""
        deadline = t0 + timeout_s
        while clock.now() < deadline:
            if self._latched or self._closed or self._transport_lost:
                return
            done = True
            for rec, gen in watch:
                if rec.gen != gen or rec.state not in (UiState.ENABLED, UiState.RUNNING):
                    continue  # 既に無効化・故障など: 待つ意味がない
                s = rec.motor.state
                if s.fb_t is None or s.fb_t < t0 or abs(s.velocity) >= eps:
                    done = False
                    break
            if done:
                return
            time.sleep(SETTLE_POLL_S)

    def clear_fault(self, motor_id: int) -> Future[None]:
        """故障クリア(= `disable(clear_fault=True)`)。故障が消えたら `DISABLED`。"""
        return self.disable([motor_id], clear_fault=True)

    def estop(self) -> None:
        """非常停止(同期・即時): 全モータへ type 4 を `send_now` → 全 detach → ラッチ → tier=LOW。

        ラッチは最初に立てる(実行中の手順が次の段階で中断する)。stop は detach の前後で 2 回送る。
        """
        self._latched = True
        motors = self._all_motors()
        ids_all = [m.motor_id for m in motors]
        self._send_stop(motors)
        with self._lock:
            self._latched_gen_bump()
            self.loop.inhibit(ids_all)  # 古い指令をキューごと捨てる(以後 enable まで指令なし)
            for rec in self._recs.values():
                rec.handle = None
                rec.controllers = []
                if rec.state in (UiState.ENABLED, UiState.RUNNING):
                    rec.state = UiState.DISABLED
            prev = self.guard.tier
            self.guard.set_tier(PowerTier.LOW)
            self._tier_gen += 1
            recs = list(self._recs.values())
        self._send_stop(motors)
        ids = sorted(m.motor_id for m in motors)
        self.events.publish(ESTOP, {"motor_ids": ids})
        self.events.publish(SERVICE_ESTOP, {"latched": True, "motor_ids": ids})
        if prev is not PowerTier.LOW:
            self.events.publish(SERVICE_TIER, {"tier": PowerTier.LOW, "previous": prev})
        for r in recs:
            self._emit_ui(r, "estop")
        self._reports_on(recs)  # 停止後は監視用に ON へ(通常のキュー。E-STOP の送信は済んでいる)

    def release_estop(self) -> None:
        """ラッチ解除のみ(モータは DISABLED のまま・自動再開しない)。"""
        with self._lock:
            if not self._latched:
                return
            self._latched = False
            ids = sorted(self._recs)
        self.events.publish(SERVICE_ESTOP, {"latched": False, "motor_ids": ids})

    def set_tier(self, tier: PowerTier) -> bool:
        """出力段階の切替。HIGH は全モータが無効(処理中なし)・ラッチなし・接続中のときだけ。
        LOW へは常に切り替え、有効なモータのモータ側 limit を即書き換える。"""
        tier = PowerTier(tier)
        targets: list[_Rec] = []
        with self._lock:
            self._check_latched()
            prev = self.guard.tier
            if tier is prev:
                return True
            if tier is PowerTier.HIGH:
                if self._transport_lost or self._closed:
                    return False
                if any(
                    r.busy is not None or r.state in (UiState.ENABLED, UiState.RUNNING)
                    for r in self._recs.values()
                ):
                    return False
                self.guard.set_tier(PowerTier.HIGH)
            else:
                self.guard.set_tier(PowerTier.LOW)
                targets = [
                    r for r in self._recs.values() if r.state in (UiState.ENABLED, UiState.RUNNING)
                ]
                for r in targets:
                    self.loop.invalidate(r.motor)
            self._tier_gen += 1
        for r in targets:
            self._write_limits_async(r.motor)
        self.events.publish(SERVICE_TIER, {"tier": tier, "previous": prev})
        return True

    # ==================================================================
    # 保守操作(ui_design.md の保守ページ / phase3.md §1)
    # ==================================================================
    def set_zero(self, motor_id: int) -> Future[MaintenanceResult]:
        """機械ゼロ点設定(type 6、§2.7)。再送しない。

        type 6 は run_mode が運控(0)/ CSP(5)のときだけ効くので、それ以外なら先に run_mode を
        0 へ変更する(停止中なのでモータは動かない)。実行後に mechPos が 0 付近(±0.1 rad)に
        なったことを読み戻して確認する(応答が欠落しても位置が 0 なら成功とする)。
        ゼロ点が flash に自動保存されるかは未確認(§8.3 6)。`save_params` は 0x70xx を保存しない。
        """
        return self._maint("set_zero", motor_id, self._do_set_zero)

    def set_can_id(self, motor_id: int, new_id: int) -> Future[int]:
        """CAN ID 変更(type 7、§2.8)。再送しない。結果は **新 ID**(`IdChangeResult` は int)。

        新 ID から type 0 の応答が来ることを確認し(応答が欠落したときは新 ID へ UID を問い合わせて
        確認する)、Session / MotorService / TelemetryHub の登録を新 ID に付け替えて
        `SERVICE_MOTORS_CHANGED` を発行する。以後は新 ID で運転できる。
        flash へ自動保存されるかは未確認(§8.3 6)。
        """
        private.set_can_id(motor_id, self.session.host_id, new_id)  # 範囲検証(同期)
        if new_id == motor_id:
            raise ProtocolError(f"新しい ID が現在の ID と同じです: {new_id}")

        def pre(rec: _Rec) -> None:
            if new_id in self._recs or new_id in self.session.motors:
                raise ProtocolError(f"ID {new_id} は既に使われています")

        return self._maint(
            "set_can_id", motor_id, lambda rec: self._do_set_can_id(rec, motor_id, new_id), pre=pre
        )

    def set_report_period(self, motor_id: int, period_ms: int) -> Future[MaintenanceResult]:
        """主動上報の周期を設定する(EPScan_time N = (period_ms - 10) / 5。実機の式 10 + 5N ms、
        §8.0)。10 ≤ period_ms ≤ 500 の 5 ms 刻み。以後の主動上報 ON でもこの周期を使う。
        EPScan_time は flash に保存されない(電源再投入で 10 ms に戻る)。"""
        if isinstance(period_ms, bool) or not isinstance(period_ms, int):
            raise ProtocolError(f"周期は整数 [ms] で指定してください: {period_ms!r}")
        if not REPORT_PERIOD_MS <= period_ms <= MAX_REPORT_PERIOD_MS:
            raise ProtocolError(
                f"主動上報の周期は {REPORT_PERIOD_MS}〜{MAX_REPORT_PERIOD_MS} ms です: {period_ms}"
            )
        if (period_ms - REPORT_PERIOD_MS) % 5:
            raise ProtocolError(f"主動上報の周期は 5 ms 刻みです(10 + 5N ms): {period_ms}")
        return self._maint(
            "set_report_period",
            motor_id,
            lambda rec: self._do_set_report_period(rec, period_ms),
            report_off=False,
        )

    def set_can_timeout(self, motor_id: int, seconds: float) -> Future[MaintenanceResult]:
        """canTimeout(0x7028)= round(seconds / 50 µs)。0 で無効。書き込んで読み戻して確認する。
        有効化(`enable`)は毎回 `comm_timeout_s` で書き直すので、運転前には上書きされる。
        0x70xx なので電源を切るまで有効(type 22 でも保存されない。実測 HW-6)。"""
        if isinstance(seconds, bool) or not isinstance(seconds, int | float):
            raise ProtocolError(f"秒数を数値で指定してください: {seconds!r}")
        if not math.isfinite(seconds) or seconds < 0:
            raise ProtocolError(f"CAN タイムアウトは 0 以上の有限の秒数です: {seconds!r}")
        count = round(seconds / CAN_TIMEOUT_UNIT_S)
        if count > 0xFFFFFFFF:
            raise ProtocolError(f"CAN タイムアウトが大きすぎます: {seconds} s")
        return self._maint(
            "set_can_timeout", motor_id, lambda rec: self._do_set_can_timeout(rec, count)
        )

    def set_baud(self, motor_id: int, code: int) -> Future[MaintenanceResult]:
        """ボーレート変更(type 23、§2.13)。1=1M / 2=500k / 3=250k / 4=125k。**電源の再投入が
        必要**(それまでは変わらない)。ホスト(アダプタ)側のビットレートは変更しない。再送しない。"""
        if isinstance(code, bool) or code not in BAUD_LABELS:
            raise ProtocolError(f"ボーレートコードは 1〜4 です: {code!r}")
        return self._maint("set_baud", motor_id, lambda rec: self._do_set_baud(rec, int(code)))

    def switch_protocol(self, motor_id: int, protocol: int | str) -> Future[MaintenanceResult]:
        """プロトコル切替(type 25、§2.15)。0 独自 / 1 CANopen / 2 MIT(または "private" /
        "canopen" / "mit")。**電源の再投入が必要**。再投入後は私有プロトコルで応答しなくなる
        (戻すには `restore_protocol`)。再送しない。"""
        if isinstance(protocol, str):
            key = protocol.strip().lower()
            if key not in _PROTOCOL_ALIASES:
                raise ProtocolError(f"未知のプロトコルです: {protocol!r}")
            code = _PROTOCOL_ALIASES[key]
        else:
            code = protocol
        if isinstance(code, bool) or code not in PROTOCOL_LABELS:
            raise ProtocolError(f"プロトコル番号は 0〜2 です: {protocol!r}")
        return self._maint(
            "switch_protocol", motor_id, lambda rec: self._do_switch_protocol(rec, int(code))
        )

    def restore_protocol(self, target_id: int, source: str) -> Future[MaintenanceResult]:
        """MIT / CANopen で動作中のモータを私有プロトコルへ戻す。**モータ未選択でも可**
        (私有プロトコルのスキャンには出てこない)。接続中・E-STOP ラッチなし・有効 / 運転中の
        モータなしが前提。

        - `"mit"`: **11bit 標準フレーム** ID = `target_id`(0x000 | id)、データ
          `FF FF FF FF FF FF 00 FD`(§4.11)。標準フレームを送れないアダプタ(`caps.standard_frames`
          が偽)は拒否する。
        - `"canopen"`: 拡張フレーム ID 0xFFF、データ `01 02 03 04 05 06 00 00`(§6.4)。
          フレームにモータ ID はなく、応答の照合にだけ `target_id` を使う。

        応答(標準フレーム、ID = target_id)を確認する。同じ指令の再送は無害なので、応答が
        なければ最大 2 回再送する。確認できなくても例外にせず `verified=False` の結果にする。
        **公式 USB-CAN(AT)での標準フレームの表現は未検証(Q-T1)**: 結果メッセージに
        「未検証」を含める。成功しても **電源の再投入が必要**。
        """
        if source not in _RESTORE_SOURCES:
            raise ProtocolError(f"source は 'mit' か 'canopen' です: {source!r}")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            raise ProtocolError(f"モータ ID は整数です: {target_id!r}")
        if not 0 <= target_id <= 127:
            raise ProtocolError(f"モータ ID が範囲外です: {target_id}")
        caps = self.session.transport.caps
        if source == "mit" and not caps.standard_frames:
            raise ProtocolError(
                "このアダプタは標準(11bit)フレームを送れないため、MIT からは戻せません"
            )
        frame = (
            private.restore_from_mit(target_id)
            if source == "mit"
            else private.restore_from_canopen(0)
        )
        with self._lock:
            self._check_operable()
            active = [
                i
                for i, r in self._recs.items()
                if r.busy is not None or r.state in (UiState.ENABLED, UiState.RUNNING)
            ]
            if active:
                raise ProtocolError(
                    f"有効・運転中(または処理中)のモータがあります: ID {sorted(active)}"
                )
        out: Future[MaintenanceResult] = Future()

        def job() -> None:
            try:
                res = self._do_restore(frame, target_id, source, unverified=caps.name != "sim")
            except BaseException as e:
                self._publish_maint("restore_protocol", target_id, None, e)
                out.set_exception(e)
                return
            self._publish_maint("restore_protocol", target_id, res, None)
            out.set_result(res)

        try:
            self._executor.submit(job)
        except RuntimeError as e:
            raise ProtocolError(f"MotorService は閉じられています: {e}") from None
        return out

    def write_param(
        self, motor_id: int, key: str | int, value: int | float
    ) -> Future[MaintenanceResult]:
        """パラメータ 1 件の書込(type 18)。dtype・範囲・書込可否を検証し、読み戻して確認する
        (読み戻せないパラメータは `verified=False`)。0x70xx は運転時の値で、電源を切ると
        工場値へ戻る(type 22 でも保存されない。実測 HW-6)。

        運転系(run_mode / iq_ref / spd_ref / loc_ref / limit_torque / limit_cur / limit_spd)は
        **拒否**(ProtocolError。運転 API = enable / コントローラ経由でだけ書く)。"""
        pd = self._param_def(key)
        if pd.key in MOTION_PARAM_KEYS:
            raise ProtocolError(
                f"{pd.key} は運転系パラメータのため、保守操作では書き込めません"
                "(運転 API 経由でのみ変更されます)"
            )

        def pre(rec: _Rec) -> None:
            rec.motor.check_write(pd.index, value)  # dtype・範囲・書込可否(違反は ProtocolError)

        return self._maint(
            "write_param", motor_id, lambda rec: self._do_write_param(rec, pd, value), pre=pre
        )

    def read_all_params(self, motor_id: int) -> Future[dict[int, ParamResponse]]:
        """プロファイルの全パラメータを読む(`Motor.read_all_params`)。読出失敗(ok=False)も含む。"""
        return self._maint(
            "read_all_params", motor_id, lambda rec: self._wait_long(rec.motor.read_all_params())
        )

    def save_params(self, motor_id: int) -> Future[MaintenanceResult]:
        """パラメータの保存(type 22、§2.12)。再送しない。

        実機は type 0(UID)で応答する。保存されるのは 0x20xx と 0x702A / 0x702B だけで、
        0x70xx の運転パラメータは電源を切ると工場値へ戻る(実測 HW-6)。"""
        return self._maint("save_params", motor_id, self._do_save_params)

    def recover(self, motor_id: int) -> Future[bool]:
        """電源の再投入後などに、モータが応答するか確認して監視(主動上報 ON)を再開する。

        書込済みパラメータのキャッシュを捨て、UID を問い合わせ、応答があれば主動上報を ON に戻す。
        結果は応答の有無(False = 応答なし)。有効 / 運転中のモータには行えない。"""
        return self._maint("recover", motor_id, self._do_recover, report_off=False)

    # ------------------------------------------------------------------
    def _param_def(self, key: str | int) -> ParamDef:
        try:
            return self.session.profile.param(key)
        except KeyError as e:
            raise ProtocolError(str(e.args[0])) from None

    def _maint(
        self,
        op: str,
        motor_id: int,
        body: Callable[[_Rec], Any],
        *,
        pre: Callable[[_Rec], None] | None = None,
        report_off: bool = True,
    ) -> Future[Any]:
        """保守操作の共通処理: 前提の確認 → busy → ワーカーで(主動上報 OFF → body → ON)。

        前提(DISABLED・ラッチなし・接続中・処理中でない)を満たさなければ、ここで ProtocolError /
        SafetyError を送出する(Future ではなく呼び出しの時点で)。`pre` も同様(検証用)。"""
        with self._lock:
            self._check_operable()
            if motor_id not in self._recs and motor_id not in self.session.motors:
                raise ProtocolError(f"ID {motor_id} は登録されていません(スキャンが必要です)")
            rec = self._rec(motor_id)
            if rec.busy is not None:
                raise ProtocolError(f"ID {motor_id}: {rec.busy} の処理中です")
            if rec.state is not UiState.DISABLED:
                raise ProtocolError(
                    f"ID {motor_id}: {_OP_LABELS[op]}は停止中(DISABLED)のモータだけに行えます"
                    f"(現在: {rec.state.value})"
                )
            if pre is not None:
                pre(rec)
            rec.busy = "maintenance"
        out: Future[Any] = Future()

        def job() -> None:
            result: Any = None
            err: BaseException | None = None
            try:
                if report_off:
                    self._report_off(rec.motor)
                self._maint_alive()
                result = body(rec)
            except BaseException as e:
                err = e
            with self._lock:
                if rec.busy == "maintenance":
                    rec.busy = None
                alive = not (self._closed or self._transport_lost)
            if alive and op != "recover":
                self._report_on(rec.motor)  # 以後の監視(失敗しても致命ではない)
            self._publish_maint(op, rec.motor.motor_id, result, err)
            if err is not None:
                out.set_exception(err)
            else:
                out.set_result(result)

        try:
            self._executor.submit(job)
        except RuntimeError as e:  # 終了処理中
            with self._lock:
                if rec.busy == "maintenance":
                    rec.busy = None
            raise ProtocolError(f"MotorService は閉じられています: {e}") from None
        return out

    def _maint_alive(self) -> None:
        if self._latched:
            raise SafetyError("非常停止中です(解除するまで操作できません)")
        if self._transport_lost or self._closed:
            raise ProtocolError("アダプタとの通信が途絶えています")

    def _publish_maint(
        self, op: str, motor_id: int | None, result: Any, err: BaseException | None
    ) -> None:
        if err is not None:
            msg = f"{_OP_LABELS.get(op, op)}に失敗しました: {err}"
        elif isinstance(result, MaintenanceResult | IdChangeResult):
            msg = result.message
        else:
            msg = f"{_OP_LABELS.get(op, op)}が完了しました"
        self.events.publish(
            SERVICE_MAINTENANCE,
            {"op": op, "motor_id": motor_id, "ok": err is None, "message": msg, "error": err},
        )

    @staticmethod
    def _wait_long(fut: Future[Any]) -> Any:
        return fut.result(30.0)  # read_all_params など複数要求の手順用

    # --- 各操作の本体(ワーカースレッド) ----------------------------------
    def _do_set_zero(self, rec: _Rec) -> MaintenanceResult:
        m = rec.motor
        mid = m.motor_id
        r = self._wait(m.read_param("run_mode"))
        if not r.ok or r.value is None:
            raise ProtocolError(f"ID {mid}: run_mode を読み出せません")
        note = ""
        if int(r.value) not in ZERO_RUN_MODES:
            # type 6 は運控(0)/ CSP(5)でのみ有効(§2.7)。停止中なので 0 へ変えても動かない
            self._wait(m.set_run_mode(RunMode.OPERATION))
            r = self._wait(m.read_param("run_mode"))
            if not r.ok or r.value is None or int(r.value) != int(RunMode.OPERATION):
                raise ProtocolError(f"ID {mid}: ゼロ設定のための run_mode 変更に失敗しました")
            note = "(run_mode を 0(運控)に変更しました)"
        self._maint_alive()
        # 位置の履歴は旧い座標系。0 への跳びをグラフが縦線で結ばないよう、NaN で線を切る。
        # type 6 の応答(type 2)は新しい位置だが、多回転の展開オフセットは旧いまま(下の
        # reset_position_unwrap まで)なので、前後 2 か所に挟んで応答のサンプルを孤立させる
        self._break_position_history(mid)
        lost = False
        try:
            self._wait(m.set_zero())  # 再送しない
        except RequestTimeout:
            lost = True
        pos = self._wait(m.read_param("mechPos"))
        if not pos.ok or pos.value is None:
            raise ProtocolError(f"ID {mid}: ゼロ設定後の位置を読み出せません")
        value = float(pos.value)
        if abs(value) > ZERO_TOLERANCE_RAD:
            if lost:
                raise RequestTimeout(
                    f"ID {mid}: ゼロ設定の応答がなく、位置も 0 になっていません"
                    f"(mechPos={value:.3f})"
                )
            raise ProtocolError(
                f"ID {mid}: ゼロ設定後も位置が 0 付近になりません(mechPos={value:.3f} rad)。"
                "run_mode が運控(0)か CSP(5)であることを確認してください"
            )
        with self._lock:  # 位置が跳ぶ: 多回転展開をやり直す(次の FB で mechPos に合わせる)
            m.reset_position_unwrap(value)
        self._break_position_history(mid)
        tail = "(応答は欠落しましたが位置が 0 になったことで確認しました)" if lost else ""
        return MaintenanceResult(
            "set_zero",
            f"ID {mid}: 機械ゼロ点を設定しました(位置 {value:+.4f} rad){note}{tail}。"
            "永続化されるかは未確認です(0x70xx は type 22 でも保存されません)",
            mid,
            value,
        )

    def _break_position_history(self, motor_id: int) -> None:
        """位置系のチャネルへ NaN を 1 点追記する(グラフの線を切る。座標系が変わる前後に使う)。"""
        now = clock.now()
        for suffix in ("pos", "pos_multi", "cmd.pos"):
            ch = motor_channel(motor_id, suffix)
            if self.telemetry.has(ch):
                self.telemetry.append(ch, now, math.nan)

    def _probe_uid(self, motor_id: int) -> UidResponse | None:
        """type 0 を最大 3 回送って UID を得る(冪等)。応答なしなら None。"""
        bus = self.session.bus
        frame = private.get_uid(motor_id, self.session.host_id)

        def match(msg: Message) -> bool:
            return isinstance(msg, UidResponse) and msg.motor_id == motor_id

        for _ in range(3):
            try:
                res = bus.request(frame, match, 0.2, Priority.REQUEST).result(WAIT_S)
            except RequestTimeout:
                continue
            if isinstance(res, UidResponse):
                return res
        return None

    def _do_set_can_id(self, rec: _Rec, old: int, new: int) -> IdChangeResult:
        m = rec.motor
        reply: UidResponse | None = None
        try:
            r = self._wait(m.set_can_id(new))  # 新 ID からの type 0。再送しない
            reply = r if isinstance(r, UidResponse) else None
        except RequestTimeout:
            reply = self._probe_uid(new)  # 応答が欠落したかもしれない: 新 ID へ問い合わせて確認
        if reply is None:
            raise RequestTimeout(
                f"ID {old} → {new}: 新しい ID から応答がありません。"
                "ID は変更されていない可能性があります(元の ID で確認してください)"
            )
        if m.uid is not None and reply.uid != m.uid:
            raise ProtocolError(
                f"ID {new}: 応答した UID が元のモータと異なります"
                "(同じ ID の別のモータがバスにいる可能性があります)"
            )
        with self._lock:
            self._rekey(rec, old, new)
            m.uid = reply.uid
        self.events.publish(
            SERVICE_MOTORS_CHANGED,
            {"motor_ids": self.motor_ids(), "renamed": {old: new}},
        )
        return IdChangeResult(
            new,
            old,
            f"CAN ID を {old} から {new} に変更しました。永続化されるかは未確認です"
            "(0x70xx は type 22 でも保存されません)",
        )

    def _rekey(self, rec: _Rec, old: int, new: int) -> None:
        """Session / `_recs` / ControlLoop / TelemetryHub の登録を新 ID へ(`_lock` 保持下)。"""
        if new in self._recs:
            raise ProtocolError(f"ID {new} は既に使われています")
        self.session.rekey_motor(old, new)
        del self._recs[old]
        self._recs[new] = rec
        self.loop.rekey(old, new)
        self.telemetry.rename_motor(old, new)
        rec.poll_fut = None
        rec.last_ui = None  # 新 ID の状態イベントを改めて出す
        self._emit_ui(rec, "rekey")

    def _do_set_report_period(self, rec: _Rec, period_ms: int) -> MaintenanceResult:
        m = rec.motor
        mid = m.motor_id
        self._wait(m.set_active_report(True, period_ms))  # EPScan_time を書いて ON
        rec.report_ms = period_ms
        n = (period_ms - REPORT_PERIOD_MS) // 5
        pd = self._param_def(_REPORT_PERIOD_KEY)
        r = self._wait(m.read_param(pd.index))
        if r.ok and r.value is not None and int(r.value) != n:
            raise ProtocolError(f"ID {mid}: 上報周期を確認できません(EPScan_time={r.value})")
        return MaintenanceResult(
            "set_report_period",
            f"ID {mid}: 主動上報の周期を {period_ms} ms に設定しました"
            "(電源を切るまで有効です。電源を切ると 10 ms に戻り、type 22 の保存でも残りません)",
            mid,
            period_ms,
        )

    def _write_verified(self, m: Motor, pd: ParamDef, value: int | float) -> tuple[bool, Any]:
        """書込 → 読み戻し。(確認できたか, 読み戻した値)。読み戻せなければ (False, None)、
        読み戻した値が違えば ProtocolError。"""
        self._wait(m.write_param(pd.index, value))
        r = self._wait(m.read_param(pd.index))
        if not r.ok or r.value is None:
            return False, None
        if not math.isclose(float(r.value), float(value), rel_tol=1e-6, abs_tol=1e-9):
            raise ProtocolError(
                f"ID {m.motor_id}: {pd.key} の書込を確認できません(書込 {value} / 読出 {r.value})"
            )
        return True, r.value

    def _do_set_can_timeout(self, rec: _Rec, count: int) -> MaintenanceResult:
        m = rec.motor
        pd = self._param_def(_CAN_TIMEOUT_KEY)
        verified, _ = self._write_verified(m, pd, count)
        what = "無効" if count == 0 else f"{count * CAN_TIMEOUT_UNIT_S:g} s"
        return MaintenanceResult(
            "set_can_timeout",
            f"ID {m.motor_id}: CAN タイムアウトを {what}(canTimeout={count})に設定しました。"
            "電源を切るまで有効です(保存しても残りません)。なお本アプリは有効化のたびに"
            "canTimeout と各制限値を運転用の値で書き直します",
            m.motor_id,
            count,
            verified=verified,
        )

    def _do_set_baud(self, rec: _Rec, code: int) -> MaintenanceResult:
        m = rec.motor
        self._wait(m.set_baud(code))  # type 0 の応答。再送しない
        return MaintenanceResult(
            "set_baud",
            f"ID {m.motor_id}: ボーレートを {BAUD_LABELS[code]} に設定しました。"
            "電源の再投入が必要です(再投入後は、アダプタ側のビットレートも合わせてください。"
            "ホスト側は変更していません)",
            m.motor_id,
            code,
            needs_power_cycle=True,
        )

    def _do_switch_protocol(self, rec: _Rec, code: int) -> MaintenanceResult:
        m = rec.motor
        self._wait(m.switch_protocol(code))  # type 0 の応答。再送しない
        extra = ""
        if code != 0:
            extra = "再投入後は私有プロトコルでは応答しなくなります(戻すには「プロトコル復帰」)。"
        return MaintenanceResult(
            "switch_protocol",
            f"ID {m.motor_id}: プロトコルを {PROTOCOL_LABELS[code]} に切り替えました。"
            f"電源の再投入が必要です。{extra}",
            m.motor_id,
            code,
            needs_power_cycle=True,
        )

    def _do_write_param(self, rec: _Rec, pd: ParamDef, value: int | float) -> MaintenanceResult:
        m = rec.motor
        verified, got = self._write_verified(m, pd, value)
        tail = "読み戻して確認しました" if verified else "読み戻せないため確認できていません"
        return MaintenanceResult(
            "write_param",
            f"ID {m.motor_id}: {pd.key} = {value} を書き込みました({tail})。"
            "0x70xx は電源を切ると工場値に戻ります(type 22 の保存でも残りません)。",
            m.motor_id,
            got if verified else value,
            verified=verified,
        )

    def _do_save_params(self, rec: _Rec) -> MaintenanceResult:
        m = rec.motor
        self._wait(m.save_params())  # type 22。再送しない
        return MaintenanceResult(
            "save_params",
            f"ID {m.motor_id}: モータへ保存しました(type 22)。この EL05 では 0x70xx の運転"
            "パラメータは電源を切ると工場値に戻ります(実測。保存の対象は 0x20xx / 0x702A / 0x702B)",
            m.motor_id,
        )

    def _do_recover(self, rec: _Rec) -> bool:
        m = rec.motor
        m.params.clear()  # 電源再投入で未保存の書込は失われている
        self.loop.invalidate(m)
        try:
            self._wait(m.get_uid())
        except RequestTimeout:
            return False
        with self._lock:
            rec.comm_lost = False
        self._report_on(m)
        return True

    def _do_restore(
        self, frame: CanFrame, target_id: int, source: str, *, unverified: bool
    ) -> MaintenanceResult:
        bus = self.session.bus

        def reply(f: CanFrame) -> bool:  # 標準フレーム、ID = モータ ID、データ = UID(§4.3, §6.4)
            return (not f.extended) and f.can_id == target_id and len(f.data) == 8

        confirmed = False
        for attempt in range(1 + RESTORE_RETRIES):
            self._maint_alive()
            try:
                bus.request(
                    frame, lambda _m: False, RESTORE_REPLY_TIMEOUT_S, Priority.REQUEST, reply
                ).result(WAIT_S)
                confirmed = True
                break
            except RequestTimeout:
                if attempt < RESTORE_RETRIES:
                    bus.note_retry()  # 冪等(同じ F_CMD を送るだけ)なので再送する
        src = {"mit": "MIT", "canopen": "CANopen"}[source]
        warn = "(この復帰フレームの AT アダプタ上の表現は未検証です)" if unverified else ""
        if confirmed:
            msg = (
                f"ID {target_id}: {src} から独自プロトコルへの復帰指令を送信し、応答を確認しました"
                f"{warn}。電源の再投入が必要です"
            )
        else:
            msg = (
                f"ID {target_id}: {src} から独自プロトコルへの復帰指令を送信しましたが、応答を"
                f"確認できませんでした{warn}。電源を再投入し、スキャンで見つかるか確認して"
                "ください。電源の再投入が必要です"
            )
        return MaintenanceResult(
            "restore_protocol",
            msg,
            target_id,
            source,
            needs_power_cycle=True,
            verified=confirmed and not unverified,
        )

    # ==================================================================
    # 手順(ワーカースレッド)
    # ==================================================================
    @staticmethod
    def _wait(fut: Future[Any]) -> Any:
        return fut.result(WAIT_S)

    def _check_alive(self, rec: _Rec, gen: int) -> None:
        if self._latched or self._transport_lost or self._closed or rec.gen != gen:
            raise SafetyError(f"ID {rec.motor.motor_id}: 有効化を中断しました")

    def _enable_job(self, rec: _Rec, mode: RunMode, gen: int) -> None:
        m = rec.motor
        try:
            self._report_off(m)  # 0. 主動上報 OFF(以後の書込みの応答が欠落しにくい)
            self._check_alive(rec, gen)
            self._wait(m.disable())  # 1. stop
            self._check_alive(rec, gen)
            self._seed_multi(m)  # 1b. 多回転角を mechPos に合わせる(合わせられなければ有効化しない)
            self._check_alive(rec, gen)
            self._wait(m.set_run_mode(mode))  # 2. run_mode(読み戻して確認)
            r = self._wait(m.read_param("run_mode"))
            if not r.ok or r.value is None or int(r.value) != int(mode):
                raise ProtocolError(f"ID {m.motor_id}: run_mode を {int(mode)} にできませんでした")
            self._check_alive(rec, gen)
            tier_gen = self._tier_gen  # 3. モータ側 limit(二重制限)+ canTimeout
            writes = [m.write_param(k, v) for k, v in self.guard.motor_side_limits().items()]
            can_timeout = round(self.comm_timeout_s / CAN_TIMEOUT_UNIT_S)
            writes.append(m.write_param("canTimeout", can_timeout))
            for f in writes:
                self._wait(f)
            if mode in (RunMode.CSP, RunMode.PP):  # 4. 現在位置を目標へ(急に動かない)
                r = self._wait(m.read_param("mechPos"))
                if not r.ok or r.value is None:
                    raise ProtocolError(f"ID {m.motor_id}: mechPos を読み出せませんでした")
                self._wait(m.write_param("loc_ref", float(r.value)))
            self._check_alive(rec, gen)
            self._wait(m.enable())  # 5. type 3
            if m.state.mode != MotorMode.MOTOR:
                bits = m.state.fault_bits
                raise ProtocolError(
                    f"ID {m.motor_id}: 有効化できませんでした"
                    f"(mode={m.state.mode}, fault=0x{bits:02X})"
                )
            with self._lock:
                self._check_alive(rec, gen)
                self.loop.invalidate(m)
                self.loop.keepalive(m, HoldCmd(), mode)
                rec.state = UiState.ENABLED
                rec.run_mode = mode
                rec.busy = None
                rec.enabled_t = clock.now()
                rec.stopped_ticks = 0
                self._emit_ui(rec, "enable")
                tier_changed = self._tier_gen != tier_gen
            if tier_changed:  # 手順の途中で出力段階が変わった: 新しい段階の limit を書き直す
                for k, v in self.guard.motor_side_limits().items():
                    self._wait(m.write_param(k, v))
        except BaseException:
            with self._lock:
                if rec.gen == gen:
                    self.loop.clear_keepalive(m)
                if rec.busy == "enable":
                    rec.busy = None
                if rec.state is UiState.FAULT or (
                    rec.gen == gen and rec.state in (UiState.ENABLED, UiState.RUNNING)
                ):
                    pass
                else:
                    rec.state = UiState.DISABLED
                self._emit_ui(rec, "enable_failed")
            self._send_stop([m])  # type 3 が飛んでしまった可能性があるので、念のため
            with self._lock:
                idle = rec.state not in (UiState.ENABLED, UiState.RUNNING)
            if idle:
                self._report_on(m)
            raise

    def _seed_multi(self, m: Motor) -> None:
        """mechPos(0x7019)で多回転角を較正する。読めなければ SafetyError(未較正では動かさない)。

        MIT の保持・CSP/PP の loc_ref は多回転座標で出すため、セッション開始時に ±4π の外に
        いた場合に k·8π ずれた値で「保持」すると 25 rad 動く(el05_protocol.md §8.0)。"""
        try:
            self._wait(m.sync_multi())
        except Exception as e:
            raise SafetyError(
                f"ID {m.motor_id}: 多回転位置(mechPos)を読み出せないため有効化できません"
                f"(未較正の位置では動かしません): {e}"
            ) from e

    def _finish_disable(self, recs: list[_Rec], futs: list[Future[Any]], clear: bool) -> None:
        err: BaseException | None = None
        for rec, f in zip(recs, futs, strict=True):
            m = rec.motor
            try:
                self._confirm_reset(m, f, clear)
                lost = False
            except BaseException as e:
                err = err or e
                lost = True
                self._send_stop([m])
            with self._lock:
                bits = m.state.fault_bits
                now = clock.now()
                if lost:
                    rec.comm_lost = True
                    rec.lost_t = now
                elif clear and bits == 0:
                    rec.fault_bits = rec.fault21 = rec.warning21 = 0
                    rec.fault_clear_t = now
                if rec.busy == "disable":
                    rec.busy = None
                if rec.state in (UiState.ENABLED, UiState.RUNNING):
                    pass  # 無効化中に別の手順で有効化された(通常は起きない)
                elif bits != 0 or rec.fault_bits != 0 or rec.fault21 != 0:
                    rec.state = UiState.FAULT
                else:
                    rec.state = UiState.DISABLED
                self._emit_ui(rec, "disable")
                idle = rec.state not in (UiState.ENABLED, UiState.RUNNING)
            if idle:  # 通信断でも ON を送る(戻ったときにフィードバックで復帰を検知する)
                self._report_on(m)
        if err is not None:
            raise err

    def _confirm_reset(self, m: Motor, first: Future[Any], clear: bool) -> None:
        """type 4 の送信後に Reset のフィードバックが届くまで確認する(最大 `DISABLE_TRIES` 回送る)。

        type 4 への応答は type 2 だが、運転中の指令への古い応答や、受信処理の遅れで遅れて
        処理されるフィードバックと区別がつかない。そこで「**type 4 を実際に送信した時刻以降に
        受信した** Reset のフィードバック」だけを確認とし(`Motor.is_reset_since`)、
        届かなければ type 4 を送り直す。"""
        per_try = max(2 * m.request_timeout, 0.2)
        fut = first
        last: BaseException | None = None
        for attempt in range(DISABLE_TRIES):
            if attempt:
                fut = m.disable(clear)
            try:
                fut.result(WAIT_S)
            except RequestTimeout as e:  # 応答なし: 送り直す
                last = e
            t_tx = m.last_stop_tx
            if t_tx is None:
                last = last or ProtocolError(f"ID {m.motor_id}: stop を送信できませんでした")
                continue
            deadline = clock.now() + per_try
            while True:
                if m.is_reset_since(t_tx):
                    return
                if clock.now() >= deadline:
                    break
                time.sleep(0.002)
        raise ProtocolError(f"ID {m.motor_id}: 無効化を確認できませんでした") from last

    def _write_limits_async(self, motor: Motor) -> None:
        for k, v in self.guard.motor_side_limits().items():
            try:
                f = motor.write_param(k, v)
            except Exception:
                log.exception("モータ側 limit の書込に失敗しました: ID %d %s", motor.motor_id, k)
                continue
            f.add_done_callback(partial(_log_failure, f"ID {motor.motor_id} {k} の書込"))

    # ==================================================================
    # 内部ヘルパ
    # ==================================================================
    @staticmethod
    def _ids(motor_ids: Iterable[int] | int) -> list[int]:
        ids = [motor_ids] if isinstance(motor_ids, int) else list(motor_ids)
        if not ids:
            raise ProtocolError("モータ ID が指定されていません")
        out: list[int] = []
        for i in ids:
            if i not in out:
                out.append(i)
        return out

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProtocolError("MotorService は閉じられています")
        if not self._started:
            self.open()

    def _check_latched(self) -> None:
        if self._latched:
            raise SafetyError("非常停止中です(解除するまで操作できません)")

    def _check_operable(self) -> None:
        self._ensure_open()
        self._check_latched()
        if self._transport_lost or self._closed:
            raise ProtocolError("アダプタとの通信が途絶えています")

    def _latched_gen_bump(self) -> None:
        for r in self._recs.values():
            r.gen += 1

    def _rec(self, motor_id: int) -> _Rec:
        rec = self._recs.get(motor_id)
        return rec if rec is not None else self._adopt(motor_id)

    def _adopt(self, motor_id: int) -> _Rec:
        """モータを取り込む: チャネル登録・主動上報 ON。"""
        with self._lock:
            rec = self._recs.get(motor_id)
            if rec is not None:
                return rec
            motor = self.session.motor(motor_id)
            register_motor_channels(self.telemetry, motor_id)
            suffix, unit, kind, label = _MECHPOS_CHANNEL
            self.telemetry.register(
                motor_channel(motor_id, suffix), unit, motor_id, kind, f"m{motor_id} {label}"
            )
            rec = _Rec(motor)
            hub = self.telemetry
            pspec = self.session.profile.power_estimate

            def on_fb(fb: Any, multi: float, m: Motor = motor) -> None:
                t = fb.frame.timestamp or clock.now()
                hub.append(motor_channel(m.motor_id, "pos_multi"), t, multi)
                # 消費電力の概算(未較正。バス電流は測っていない。core/telemetry/power.py)
                est = estimate_power(fb.torque, fb.velocity, pspec)
                hub.append(motor_channel(m.motor_id, "power_est"), t, est.p_in)

            rec.fb_off = motor.add_feedback_listener(on_fb)
            self._recs[motor_id] = rec
            started = self._started and not self._closed
        if started:
            self._report_on(motor)
        return rec

    def _report_on(self, motor: Motor) -> None:
        """主動上報 ON(10 ms)。失敗してもよい(運転中は制御フレームへの type 2 応答も来る)。

        EPScan_time(周期)は、既に書込み済み(N=0 を確認済み)なら書き直さない(無効化直後の
        余計な type 18 を避ける)。"""
        rec = self._recs.get(motor.motor_id)
        want_ms = rec.report_ms if rec is not None else REPORT_PERIOD_MS
        period: int | None = want_ms
        try:
            idx = self.session.profile.param(_REPORT_PERIOD_KEY).index
            if motor.params.get(idx) == (want_ms - REPORT_PERIOD_MS) // 5:
                period = None
            f = motor.set_active_report(True, period)
        except Exception:
            log.exception("主動上報を開始できません: ID %d", motor.motor_id)
            return
        f.add_done_callback(partial(self._report_on_done, motor.motor_id))

    def _report_on_done(self, motor_id: int, fut: Future[Any]) -> None:
        if self._closed and not fut.cancelled() and isinstance(fut.exception(), TransportError):
            return  # 終了処理中(バス停止)の失敗は正常
        _log_failure(f"ID {motor_id} の主動上報 ON", fut)

    def _report_off(self, motor: Motor) -> None:
        """主動上報 OFF(有効化の最初)。応答がなくても(再送の後)続行する: 後続の要求が失敗を示す。
        `report_while_enabled` なら何もしない。ワーカースレッドから呼ぶ(待つ)。"""
        if self.report_while_enabled:
            return
        try:
            self._wait(motor.set_active_report(False))
        except RequestTimeout:
            log.warning("主動上報を OFF にできませんでした: ID %d", motor.motor_id)

    def _reports_on(self, recs: Iterable[_Rec]) -> None:
        for r in recs:
            self._report_on(r.motor)

    def _all_ids(self) -> list[int]:
        return [m.motor_id for m in self._all_motors()]

    def _all_motors(self) -> list[Motor]:
        byid = {m.motor_id: m for m in list(self.session.motors.values())}
        for i, r in list(self._recs.items()):
            byid.setdefault(i, r.motor)
        return [byid[i] for i in sorted(byid)]

    def _send_stop(self, motors: Sequence[Motor]) -> None:
        """type 4 を `send_now`(失敗しても例外は出さない)。"""
        if not motors:
            return
        try:
            self.session.bus.send_now([private.stop(m.motor_id, m.host_id) for m in motors])
        except Exception as e:
            log.warning("stop の送信に失敗しました: %r", e)

    def _to_hold(self, rec: _Rec) -> None:
        self.loop.invalidate(rec.motor)
        self.loop.keepalive(rec.motor, HoldCmd(), rec.run_mode)

    def _drop_control(self, rec: _Rec, *, send_on_stop: bool) -> list[_Rec]:
        """コントローラ(ハンドル)と keepalive を外す。同じハンドルの他モータ(複数スロット)は
        有効のまま保持へ戻し、そのレコードを返す。

        `send_on_stop=False`(無効化)ではループの指令抑止(`inhibit`)を立てる: 戻った時点で
        このモータ宛ての指令は積まれず、キューに残った古い指令も捨てられている。
        `send_on_stop=True`(運転停止)では、先に keepalive を外してキューを掃除し、
        detach が on_stop の指令を最優先で積む(その後ろに古い指令は残らない)。"""
        affected: list[_Rec] = []
        mid0 = rec.motor.motor_id
        handles: list[Handle] = []
        if send_on_stop:
            self.loop.clear_keepalive(rec.motor)
            h = self.loop.handle_of(mid0) or rec.handle
            if h is not None:
                handles.append(h)
                self.loop.detach(h, send_on_stop=True)
        else:
            handles = list(self.loop.inhibit([mid0]))
            if rec.handle is not None and rec.handle not in handles:
                handles.append(rec.handle)
        for h in handles:
            for mid in h.motor_ids:
                o = self._recs.get(mid)
                if o is None or o is rec:
                    continue
                o.handle = None
                o.controllers = []
                if o.state is UiState.RUNNING:
                    o.state = UiState.ENABLED
                    self._to_hold(o)
                affected.append(o)
        rec.handle = None
        rec.controllers = []
        return affected

    def _emit_ui(self, rec: _Rec, reason: str) -> None:
        ui = self._ui(rec)
        prev = rec.last_ui
        if ui is prev:
            return
        rec.last_ui = ui
        self.events.publish(
            SERVICE_STATE_CHANGED,
            {"motor_id": rec.motor.motor_id, "state": ui, "previous": prev, "reason": reason},
        )

    # ==================================================================
    # 受信・イベントの購読(RX / ControlLoop スレッドから。重い処理はしない)
    # ==================================================================
    def _on_message(self, msg: Message) -> None:
        if isinstance(msg, Feedback):
            rec = self._recs.get(msg.motor_id)
            if rec is None:
                return
            t = msg.frame.timestamp or clock.now()
            mid = msg.motor_id
            hub = self.telemetry
            hub.append(motor_channel(mid, "pos"), t, msg.position)
            hub.append(motor_channel(mid, "vel"), t, msg.velocity)
            hub.append(motor_channel(mid, "tq"), t, msg.torque)
            hub.append(motor_channel(mid, "temp"), t, msg.temperature)
            if msg.fault_bits and rec.state is not UiState.FAULT:
                self._q.put(("fault", (mid, msg.fault_bits, 0, 0, "feedback", t)))
                self._wake.set()
        elif isinstance(msg, FaultReport):
            rec = self._recs.get(msg.motor_id)
            if rec is not None and msg.fault and rec.state is not UiState.FAULT:
                t = msg.frame.timestamp or clock.now()
                self._q.put(("fault", (msg.motor_id, 0, msg.fault, msg.warning, "type21", t)))
                self._wake.set()

    def _on_bus_error(self, payload: Any) -> None:
        err = payload.get("error")
        if payload.get("where") in ("tx", "recv") and isinstance(err, TransportError):
            self._q.put(("transport", repr(err)))
            self._wake.set()

    def _on_send_error(self, payload: Any) -> None:
        err = payload.get("error")
        if isinstance(err, TransportError):
            self._q.put(("transport", repr(err)))
            self._wake.set()

    def _on_clamped(self, payload: Any) -> None:
        self.events.publish(SERVICE_CLAMPED, payload)

    def _on_control_error(self, payload: Any) -> None:
        try:
            self._executor.submit(self._handle_control_error, payload)
        except RuntimeError:  # 終了処理中
            pass

    def _on_discovered(self, payload: Any) -> None:
        if not self._closed:
            self._adopt(int(payload["motor_id"]))

    def _on_connection(self, payload: Any) -> None:
        if not payload.get("connected", True):
            self._shutdown(send_stop=False)

    # ==================================================================
    # 異常処理(監視スレッド / ワーカー)
    # ==================================================================
    def _handle_control_error(self, payload: Any) -> None:
        """ControlLoop がコントローラ(または keepalive)を外してモータを無効化した後始末。"""
        ids = list(payload["motor_ids"])
        with self._lock:
            for i in ids:
                rec = self._recs.get(i)
                if rec is None:
                    continue
                rec.gen += 1
                rec.handle = None
                rec.controllers = []
                self.loop.clear_keepalive(rec.motor)
                if rec.state in (UiState.ENABLED, UiState.RUNNING):
                    rec.state = UiState.DISABLED
                self._emit_ui(rec, "controller_error")
        for i in ids:
            r = self._recs.get(i)
            if r is not None:
                self._report_on(r.motor)
        self.events.publish(
            SERVICE_CONTROL_ERROR,
            {
                "motor_ids": ids,
                "controller": payload.get("controller"),
                "error": payload.get("error"),
            },
        )

    def _handle_fault(self, data: tuple[int, int, int, int, str, float]) -> None:
        mid, bits, fault21, warning21, source, t = data
        with self._lock:
            rec = self._recs.get(mid)
            if rec is None or t <= rec.fault_clear_t or self._closed:
                return
            if bits:
                rec.fault_bits = bits
            if fault21:
                rec.fault21 = fault21
                rec.warning21 = warning21
            if rec.state is UiState.FAULT:
                return
            rec.gen += 1  # 実行中の有効化手順を中断
            affected = self._drop_control(rec, send_on_stop=False)
            rec.state = UiState.FAULT
            fault_bits, f21, w21 = rec.fault_bits, rec.fault21, rec.warning21
            self._emit_ui(rec, "fault")
            for o in affected:
                self._emit_ui(o, "fault")
        self._send_stop([rec.motor])
        self._report_on(rec.motor)
        self.events.publish(
            SERVICE_FAULT,
            {
                "motor_id": mid,
                "fault_bits": fault_bits,
                "fault": f21,
                "warning": w21,
                "source": source,
            },
        )

    def _comm_lost(self, recs: list[_Rec]) -> None:
        """有効なモータのフィードバックが `comm_timeout_s` 途絶えた: 無効化して通知。"""
        self._send_stop([r.motor for r in recs])
        now = clock.now()
        with self._lock:
            for rec in recs:
                rec.gen += 1
                self._drop_control(rec, send_on_stop=False)
                rec.state = UiState.DISABLED
                rec.comm_lost = True
                rec.lost_t = now
                self._emit_ui(rec, "comm_lost")
        self._reports_on(recs)
        self.events.publish(
            SERVICE_COMM_LOST,
            {"motor_ids": [r.motor.motor_id for r in recs], "reason": "timeout"},
        )

    def _motor_stopped(self, rec: _Rec) -> None:
        """有効なはずのモータが自分で Reset になった(canTimeout・保護など)。"""
        with self._lock:
            if rec.state not in (UiState.ENABLED, UiState.RUNNING) or rec.busy is not None:
                return
            rec.gen += 1
            self._drop_control(rec, send_on_stop=False)
            rec.state = UiState.DISABLED
            self._emit_ui(rec, "motor_stopped")
        self._report_on(rec.motor)

    def _transport_lost_handler(self, reason: str) -> None:
        """アダプタ切断: 全モータ offline、ラッチなし、tier=LOW。"""
        with self._lock:
            if self._transport_lost:
                return
            self._transport_lost = True
            self._latched_gen_bump()
            self.loop.detach_all(send_on_stop=False)
            now = clock.now()
            recs = list(self._recs.values())
            for rec in recs:
                self.loop.clear_keepalive(rec.motor)
                rec.handle = None
                rec.controllers = []
                rec.comm_lost = True
                rec.lost_t = now
                if rec.state in (UiState.ENABLED, UiState.RUNNING):
                    rec.state = UiState.DISABLED
            prev = self.guard.tier
            self.guard.set_tier(PowerTier.LOW)
            self._tier_gen += 1
        self._send_stop(self._all_motors())  # 通る見込みは薄いが念のため
        log.warning("アダプタとの通信が途絶えました: %s", reason)
        self.events.publish(
            SERVICE_COMM_LOST,
            {"motor_ids": [r.motor.motor_id for r in recs], "reason": "transport"},
        )
        if prev is not PowerTier.LOW:
            self.events.publish(SERVICE_TIER, {"tier": PowerTier.LOW, "previous": prev})
        for r in recs:
            self._emit_ui(r, "transport_lost")

    # ==================================================================
    # 監視スレッド
    # ==================================================================
    def _monitor_run(self) -> None:
        while not self._stop_evt.is_set():
            self._wake.wait(MONITOR_PERIOD_S)
            self._wake.clear()
            if self._stop_evt.is_set():
                break
            try:
                self._monitor_tick()
            except Exception:  # スレッドは死なせない
                log.exception("MotorService の監視周期で例外")

    def _monitor_tick(self) -> None:
        now = clock.now()
        while True:
            try:
                kind, data = self._q.get_nowait()
            except queue.Empty:
                break
            if kind == "transport":
                self._transport_lost_handler(data)
            elif kind == "fault":
                self._handle_fault(data)
        if self._transport_lost or self._closed:
            return
        recs = list(self._recs.values())
        lost: list[_Rec] = []
        stopped: list[_Rec] = []
        for rec in recs:
            if rec.busy is None and rec.state in (UiState.ENABLED, UiState.RUNNING):
                s = rec.motor.state
                last = s.last_rx
                if last is None or now - last > self.comm_timeout_s:
                    lost.append(rec)
                elif s.mode == MotorMode.RESET and now - rec.enabled_t > 0.1:
                    rec.stopped_ticks += 1
                    if rec.stopped_ticks >= STOPPED_TICKS:
                        stopped.append(rec)
                else:
                    rec.stopped_ticks = 0
            elif rec.comm_lost and rec.busy is None and rec.state is not UiState.RUNNING:
                last = rec.motor.state.last_rx
                if last is not None and last > rec.lost_t:
                    rec.comm_lost = False  # フィードバックが戻った
        if lost:
            self._comm_lost(lost)
        for rec in stopped:
            self._motor_stopped(rec)
        for rec in recs:
            self._emit_ui(rec, "link")
            self._seed_idle(rec, now)
        if now >= self._next_poll:
            self._next_poll = now + POLL_PERIOD_S
            self._poll(recs)

    def _seed_idle(self, rec: _Rec, now: float) -> None:
        """初回接触(取り込み直後・ゼロ設定後)に、停止中のモータの多回転角を mechPos で較正する。

        失敗してもよい(表示用。1 秒ごとに再試行)。運転の前提は `enable` が必ず較正する。"""
        m = rec.motor
        if (
            rec.state is not UiState.DISABLED
            or rec.busy is not None
            or rec.comm_lost
            or (rec.seed_fut is not None and not rec.seed_fut.done())
            or now - rec.seed_t < SEED_RETRY_S
        ):
            return
        s = m.state
        if not s.online or s.multi_valid or s.last_rx is None:
            return
        rec.seed_t = now
        try:
            rec.seed_fut = m.sync_multi()
        except Exception:
            log.debug("ID %d: 多回転角の較正を開始できません", m.motor_id, exc_info=True)

    def _poll(self, recs: list[_Rec]) -> None:
        """低速チャネル(iqf / VBUS / mechPos)を BACKGROUND 優先で 1 モータ 1 件ずつ取得する。"""
        bus = self.session.bus
        for rec in recs:
            if rec.poll_fut is not None and not rec.poll_fut.done():
                continue
            m = rec.motor
            if not m.state.online:
                continue
            key, suffix = _SLOW_CHANNELS[rec.poll_i % len(_SLOW_CHANNELS)]
            rec.poll_i += 1
            index = self.session.profile.param(key).index
            frame = private.read_param(m.motor_id, m.host_id, index)

            def match(msg: Message, mid: int = m.motor_id, idx: int = index) -> bool:
                return isinstance(msg, ParamResponse) and msg.motor_id == mid and msg.index == idx

            try:
                fut = bus.request(frame, match, POLL_TIMEOUT_S, Priority.BACKGROUND)
            except Exception:
                return  # バス停止中など
            rec.poll_fut = fut
            fut.add_done_callback(partial(self._on_poll, m.motor_id, suffix))

    def _on_poll(self, motor_id: int, suffix: str, fut: Future[Any]) -> None:
        if fut.cancelled() or fut.exception() is not None:
            return
        msg = fut.result()
        if isinstance(msg, ParamResponse) and msg.ok and msg.value is not None:
            t = msg.frame.timestamp or clock.now()
            ch = motor_channel(motor_id, suffix)
            if self.telemetry.has(ch):  # ID 変更(付け替え)の直後は旧チャネルが無い
                self.telemetry.append(ch, t, float(msg.value))


def _mode_name(mode: RunMode | None) -> str:
    return "不明" if mode is None else f"{int(mode)}({mode.name})"


def _copy_result(src: Future[Any], dst: Future[Any]) -> None:
    if src.cancelled():
        dst.cancel()
    elif (exc := src.exception()) is not None:
        dst.set_exception(exc)
    else:
        dst.set_result(None)


def _log_failure(what: str, fut: Future[Any]) -> None:
    if fut.cancelled():
        return
    exc = fut.exception()
    if exc is not None:
        log.warning("%s に失敗しました: %r", what, exc)
