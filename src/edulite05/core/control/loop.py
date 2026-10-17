"""ControlLoop: 固定周期の制御スレッド(phase2.md §1.4, architecture.md §4.5)。

- スレッド 1 本。`core.clock.now()`(perf_counter)の絶対時刻で周期を刻む(遅れても位相は
  ずらさず、取りこぼした周期はスキップして `overruns` に数える)。Windows では開始時に
  `timeBeginPeriod(1)`、終了時に `timeEndPeriod(1)`(他 OS では何もしない)。各周期の
  残り 1 ms 未満はスピンして待つ。
- 1 周期で「attach 済みコントローラ全部の step」+「keepalive のモータ」の指令を
  SafetyGuard でクランプ → エンコード → `bus.send_many(…, Priority.CONTROL)` で 1 回に積む。
    * MitCmd → type 1(`private.mit_command`、レンジは `motor.profile.ranges`)
    * SetpointCmd → type 18(`spd_ref` / `iq_ref` / `loc_ref`)。速度・電流制限と加速度
      (CSP: `limit_spd`、PP: `vel_max` / `acc_set`、速度: `acc_rad`、共通: `limit_cur`)は
      **値が変わったときだけ**、指令値より前に書く(モータごとのキャッシュ。attach /
      keepalive / `invalidate()` で破棄)。
    * HoldCmd → run_mode で展開: 運控 = MIT で位置保持(多回転座標。±4π 付近では SafetyGuard が
      kp = 0 にして減衰のみになり、`control.clamped` に "position_wrap" が載る)/
      PP・CSP = loc_ref で位置保持 /
      速度 = spd_ref 0 / 電流 = iq_ref 0。保持位置は最初の展開時にラッチする(外乱で
      ずるずる動かない)。フィードバック未受信(`last_rx is None`)のモータは位置不明なので
      MIT・Hold は送らない(次の周期で再試行)。
- 指令値(クランプ後)を `TelemetryHub`(任意)の `m{id}.cmd.*` へ記録し、クランプは
  イベント `control.clamped` で通知する(ノートの種類が変わったとき・継続中は 1 s ごと・
  解除時は空リスト)。
- コントローラの例外(step / on_start 以外のエンコード含む)はそのコントローラを外し、
  対象モータへ type 4 を `send_now` して `control.error` を発行する。
- 1 モータは 1 コントローラにのみ割り当てられる。keepalive 中のモータへ attach すると
  keepalive は破棄される。

スレッド安全: `attach` / `detach` / `keepalive` / `clear_keepalive` / `inhibit` は任意の
スレッドから呼べる。1 周期の処理(step → エンコード → キューへ積む)とこれらは `_tick_lock` で
直列化する(これらが戻った後に、そのモータへ古い指令が積まれることはない)。

停止の安全(phase2-fix。実機初試験で、停止後も古い spd_ref が流れ続けた):
- `detach(send_on_stop=True)`: そのモータの TX キュー内の CONTROL 指令を捨ててから on_stop の
  指令を **ESTOP 優先度** で積む(= 古い指令の後ろに並ばず、続く stop(type 4)より前に出る)。
- `detach(send_on_stop=False)` / `clear_keepalive` / `inhibit`: 外した後、キュー内の古い CONTROL
  指令を捨てる。`inhibit` は更に「抑止」フラグを立て、`allow` されるまでそのモータへは
  コントローラ・keepalive の指令を一切送らない(無効化の開始から完了まで)。
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import time
from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from edulite05.core import clock
from edulite05.core.bus import BusManager, Priority
from edulite05.core.control.commands import Command, HoldCmd, MitCmd, SetpointCmd
from edulite05.core.control.controller import Controller
from edulite05.core.device.motor import Motor
from edulite05.core.device.state import MotorState
from edulite05.core.errors import ProtocolError
from edulite05.core.protocol import private
from edulite05.core.protocol.types import RunMode
from edulite05.core.safety import ClampNote, SafetyGuard
from edulite05.core.telemetry import TelemetryHub, motor_channel, register_motor_channels
from edulite05.core.transport.base import CanFrame

log = logging.getLogger(__name__)

# --- イベント(`bus.events` へ発行。ペイロードは dict)-----------------------
CONTROL_CLAMPED = "control.clamped"  # {"motor_id": int, "notes": list[ClampNote]}(空 = 解除)
CONTROL_ERROR = "control.error"  # {"motor_ids": list[int], "controller": str, "error": Exception}
CONTROL_SEND_ERROR = "control.send_error"  # {"error": Exception}(送信失敗。1 s に 1 回まで)

SPIN_S = 0.001  # 周期の残りがこれ未満になったらスピン
MAX_BACKLOG = 16  # TX キューの未送信フレームがこれを超えたら、その周期の指令を送らない
# (滞留した古い指令が停止指令より後にモータへ届くのを防ぐため小さく保つ。実機初試験の事故)
_STATS_WINDOW = 400  # レート算出に使う直近の周期数
_JITTER_WINDOW = 1000  # p99 算出に使う直近の周期数
_NOTES_REPUBLISH_S = 1.0
_ERROR_REPUBLISH_S = 1.0

# SetpointCmd の対象 → 値を書くパラメータ / 必要な run_mode
_REF_KEY = {"velocity": "spd_ref", "current": "iq_ref", "position": "loc_ref"}
_KIND_MODES: dict[str, tuple[RunMode, ...]] = {
    "velocity": (RunMode.VELOCITY,),
    "current": (RunMode.CURRENT,),
    "position": (RunMode.PP, RunMode.CSP),
}
# run_mode ごとの制限パラメータ(el05_protocol.md §3.3)
_SPEED_LIMIT_KEY = {RunMode.CSP: "limit_spd", RunMode.PP: "vel_max"}
_ACCEL_KEY = {RunMode.VELOCITY: "acc_rad", RunMode.PP: "acc_set"}
_CURRENT_LIMIT_KEY = "limit_cur"


@dataclass(frozen=True, slots=True)
class LoopStats:
    rate_hz: float  # 直近の実周期レート
    jitter_ms_p99: float  # 周期開始の予定時刻とのずれの p99 [ms]
    overruns: int  # 処理が次の周期にずれ込んだ回数
    last_dt: float  # 直近の周期間隔 [s]
    ticks: int = 0
    frames_sent: int = 0
    skipped: int = 0  # TX キュー滞留で見送った周期数
    send_errors: int = 0


class Handle:
    """attach の戻り値。`ControlLoop.detach` に渡す。"""

    __slots__ = ("controller", "assign", "t0", "last_t", "active")

    def __init__(self, controller: Controller, assign: dict[str, Motor]) -> None:
        self.controller = controller
        self.assign = assign
        self.t0: float | None = None  # 最初の step の時刻(t = 0)
        self.last_t: float | None = None
        self.active = True

    @property
    def motor_ids(self) -> list[int]:
        return [m.motor_id for m in self.assign.values()]


@dataclass(slots=True)
class _MotorCtx:
    """モータごとのループ内状態。"""

    limits: dict[str, float] = field(default_factory=dict)  # 最後に書いた制限値
    hold_src: HoldCmd | None = None  # ラッチ済みの HoldCmd(値が等しい間は同じ位置を保持)
    hold_pos: tuple[float, float] = (0.0, 0.0)  # (position, position_multi)
    notes_key: frozenset[tuple[str, str]] = frozenset()
    notes_t: float = 0.0


@dataclass(slots=True)
class _Keep:
    motor: Motor
    cmd: Command
    run_mode: RunMode | None


@dataclass(slots=True)
class _Enc:
    """1 モータ・1 周期分のエンコード結果。"""

    motor: Motor
    ctx: _MotorCtx
    frames: list[CanFrame]
    limit_updates: dict[str, float]
    record: dict[str, float]  # テレメトリのサフィックス → 値
    notes: list[ClampNote]


@dataclass(slots=True)
class _Group:
    """1 コントローラ(または 1 keepalive)分。失敗時は丸ごと破棄する。"""

    handle: Handle | None
    keep: _Keep | None
    encs: list[_Enc]


@contextlib.contextmanager
def timer_resolution(ms: int = 1) -> Iterator[None]:
    """Windows のタイマ分解能を上げる(timeBeginPeriod / timeEndPeriod)。他 OS では何もしない。"""
    winmm: Any = None
    if sys.platform == "win32":
        try:
            import ctypes

            winmm = ctypes.WinDLL("winmm")
            if winmm.timeBeginPeriod(ms) != 0:
                winmm = None
        except Exception:
            log.warning("timeBeginPeriod に失敗しました", exc_info=True)
            winmm = None
    try:
        yield
    finally:
        if winmm is not None:
            try:
                winmm.timeEndPeriod(ms)
            except Exception:
                log.warning("timeEndPeriod に失敗しました", exc_info=True)


class ControlLoop:
    def __init__(
        self,
        bus: BusManager,
        guard: SafetyGuard,
        rate_hz: float = 200.0,
        telemetry: TelemetryHub | None = None,
    ) -> None:
        if rate_hz <= 0:
            raise ValueError("rate_hz は正である必要があります")
        self.bus = bus
        self.guard = guard
        self.telemetry = telemetry
        self._period = 1.0 / rate_hz

        self._lock = threading.RLock()  # _handles / _owner / _keeps / _ctx
        self._tick_lock = threading.RLock()  # 1 周期 / attach / detach の直列化
        self._handles: list[Handle] = []
        self._owner: dict[int, Handle] = {}
        self._keeps: dict[int, _Keep] = {}
        self._ctx: dict[int, _MotorCtx] = {}
        self._inhibited: set[int] = set()  # 指令を送ってはいけないモータ(無効化中など)
        self._wrap_logged: set[int] = set()  # position_wrap(kp = 0)をログ済みのモータ
        self._runaway_logged: set[int] = set()  # runaway_guard をログ済みのモータ

        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()

        # 統計(ループスレッドが書き、stats が読む)
        self._tick_times: deque[float] = deque(maxlen=_STATS_WINDOW)
        self._jitters: deque[float] = deque(maxlen=_JITTER_WINDOW)
        self._ticks = 0
        self._overruns = 0
        self._last_dt = 0.0
        self._frames_sent = 0
        self._skipped = 0
        self._send_errors = 0
        self._last_send_err = -1e9

    # ------------------------------------------------------------------
    # ライフサイクル
    # ------------------------------------------------------------------
    @property
    def rate_hz(self) -> float:
        return 1.0 / self._period

    def set_rate(self, rate_hz: float) -> None:
        """周期を変更する(次の周期から有効)。"""
        if rate_hz <= 0:
            raise ValueError("rate_hz は正である必要があります")
        self._period = 1.0 / rate_hz

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def start(self) -> None:
        if self.running:
            raise RuntimeError("ControlLoop は既に開始しています")
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, name="edulite05-control", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """スレッドを止める。attach 済みコントローラへの on_stop 送信などはしない
        (必要なら先に `detach_all()`)。"""
        self._stop_evt.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)
        self._thread = None

    @property
    def stats(self) -> LoopStats:
        times = list(self._tick_times)
        rate = 0.0
        if len(times) >= 2 and times[-1] > times[0]:
            rate = (len(times) - 1) / (times[-1] - times[0])
        jit = sorted(self._jitters)
        p99 = jit[int(0.99 * (len(jit) - 1))] * 1000.0 if jit else 0.0
        return LoopStats(
            rate_hz=rate,
            jitter_ms_p99=p99,
            overruns=self._overruns,
            last_dt=self._last_dt,
            ticks=self._ticks,
            frames_sent=self._frames_sent,
            skipped=self._skipped,
            send_errors=self._send_errors,
        )

    # ------------------------------------------------------------------
    # attach / detach / keepalive
    # ------------------------------------------------------------------
    def attach(self, controller: Controller, assign: Mapping[str, Motor]) -> Handle:
        """コントローラをモータへ割り当てる。`assign` のキーは `controller.slots` と一致すること。

        1 モータは 1 コントローラにのみ(違反は ProtocolError)。モータの run_mode が既知で
        コントローラの run_mode と異なる場合も ProtocolError。`on_start` の例外はそのまま送出
        (何も登録されない)。
        """
        assign = dict(assign)
        if set(assign) != set(controller.slots):
            raise ProtocolError(
                f"slots {tuple(controller.slots)} に対して {tuple(assign)} が割り当てられました"
            )
        ids = [m.motor_id for m in assign.values()]
        if len(set(ids)) != len(ids):
            raise ProtocolError("同じモータを複数のスロットへ割り当てられません")
        for m in assign.values():
            rm = m.state.run_mode
            if rm is not None and rm != controller.run_mode:
                raise ProtocolError(
                    f"ID {m.motor_id}: run_mode {int(rm)} は {controller.name} の"
                    f" run_mode {int(controller.run_mode)} と一致しません"
                )
        with self._tick_lock:
            with self._lock:
                busy = [i for i in ids if i in self._owner]
                if busy:
                    raise ProtocolError(f"ID {busy} は別のコントローラに割り当て済みです")
                held = [i for i in ids if i in self._inhibited]
                if held:
                    raise ProtocolError(f"ID {held} は指令を抑止中です(無効化中)")
            controller.on_start({slot: m.state for slot, m in assign.items()})
            for i in ids:
                self.guard.reset_runaway(i)  # 暴走ガードのラッチは運転ごとに初期化
            h = Handle(controller, assign)
            with self._lock:
                self._handles.append(h)
                for m in assign.values():
                    self._owner[m.motor_id] = h
                    self._keeps.pop(m.motor_id, None)
                    self._ctx[m.motor_id] = _MotorCtx()
            for i in ids:
                self._register_channels(i)
        return h

    def detach(self, handle: Handle, *, send_on_stop: bool = True) -> None:
        """コントローラを外す。`send_on_stop` なら on_stop の指令を 1 回送ってから外す
        (E-STOP のようにモータを止める経路では False)。冪等。

        on_stop が例外を出した場合は対象モータへ type 4 を送って `control.error` を発行する。
        """
        with self._tick_lock:
            with self._lock:
                if not handle.active:
                    return
                handle.active = False
                self._handles.remove(handle)
                for m in handle.assign.values():
                    self._owner.pop(m.motor_id, None)
            # 古い指令が on_stop / 停止より後ろから送られないよう、キューの CONTROL 指令を捨てる
            self.bus.purge_control(handle.motor_ids)
            for i in handle.motor_ids:
                self.guard.reset_runaway(i)
            if not send_on_stop:
                return
            now = clock.now()
            try:
                states = {slot: m.state for slot, m in handle.assign.items()}
                cmds = handle.controller.on_stop(states)
                group = self._encode_group(handle, None, cmds, now)
            except Exception as e:
                log.exception("on_stop が失敗しました: %s", handle.controller.name)
                self._disable_motors(list(handle.assign.values()))
                self._publish_error(handle.motor_ids, handle.controller.name, e)
                return
            self._commit([group], now, Priority.ESTOP)  # 速度 0 / 保持を最優先で送る

    def detach_all(self, *, send_on_stop: bool = True) -> None:
        for h in self.handles():
            self.detach(h, send_on_stop=send_on_stop)

    def handles(self) -> list[Handle]:
        with self._lock:
            return list(self._handles)

    def handle_of(self, motor_id: int) -> Handle | None:
        with self._lock:
            return self._owner.get(motor_id)

    def keepalive(self, motor: Motor, cmd: Command, run_mode: RunMode | None = None) -> None:
        """コントローラの無い(有効な)モータへ、毎周期 `cmd` を送る(通常は `HoldCmd()`)。

        `run_mode` を省略するとモータの `state.run_mode` を使う(不明なら送らない)。
        既に keepalive 中なら置き換える。コントローラが attach 済みのモータは ProtocolError。
        """
        with self._tick_lock:
            with self._lock:
                if motor.motor_id in self._owner:
                    raise ProtocolError(f"ID {motor.motor_id} にはコントローラが割り当て済みです")
                if motor.motor_id in self._inhibited:
                    log.debug("ID %d は指令抑止中のため keepalive しません", motor.motor_id)
                    return
                self._keeps[motor.motor_id] = _Keep(motor, cmd, run_mode)
                self._ctx[motor.motor_id] = _MotorCtx()
        self._register_channels(motor.motor_id)

    def clear_keepalive(self, motor: Motor | int, *, purge: bool = True) -> None:
        """keepalive を外す。戻った時点で、このモータへ新しい keepalive 指令は積まれない
        (`purge` なら、キューに残った古い CONTROL 指令も捨てる)。"""
        mid = motor if isinstance(motor, int) else motor.motor_id
        with self._tick_lock:
            with self._lock:
                self._keeps.pop(mid, None)
            if purge:
                self.bus.purge_control([mid])

    def inhibit(self, motor_ids: Iterable[int]) -> list[Handle]:
        """無効化の開始: そのモータの指令を即座に・同期的に止める。

        コントローラ(on_stop は送らない)と keepalive を外し、キュー内の古い CONTROL 指令を
        捨て、`allow` されるまで指令を一切送らない。戻った時点でループスレッドはこのモータ
        宛てに何も積まない。外したコントローラの Handle(複数スロットなら他のモータも含む)を返す。
        """
        ids = {int(i) for i in motor_ids}
        with self._tick_lock:
            with self._lock:
                self._inhibited |= ids
                for i in ids:
                    self._keeps.pop(i, None)
                handles = [h for h in self._handles if ids.intersection(h.motor_ids)]
                touched = set(ids)
                for h in handles:
                    h.active = False
                    self._handles.remove(h)
                    for m in h.assign.values():
                        self._owner.pop(m.motor_id, None)
                        touched.add(m.motor_id)
            self.bus.purge_control(touched)
            for i in touched:
                self.guard.reset_runaway(i)
        return handles

    def allow(self, motor_ids: Iterable[int] | int) -> None:
        """`inhibit` を解除する(有効化の開始時)。"""
        ids = [motor_ids] if isinstance(motor_ids, int) else list(motor_ids)
        with self._lock:
            self._inhibited.difference_update(int(i) for i in ids)

    def rekey(self, old_id: int, new_id: int) -> None:
        """CAN ID 変更(type 7)後の付け替え(指令抑止・制限値キャッシュを新 ID へ)。
        コントローラ / keepalive が残っているモータは ProtocolError(無効化してから)。"""
        with self._tick_lock:
            with self._lock:
                if old_id in self._owner or old_id in self._keeps:
                    raise ProtocolError(f"ID {old_id}: 運転中は ID を付け替えられません")
                if old_id in self._inhibited:
                    self._inhibited.discard(old_id)
                    self._inhibited.add(new_id)
                ctx = self._ctx.pop(old_id, None)
                if ctx is not None:
                    self._ctx[new_id] = ctx

    def is_inhibited(self, motor_id: int) -> bool:
        with self._lock:
            return motor_id in self._inhibited

    def invalidate(self, motor: Motor | int | None = None) -> None:
        """書込み済み制限値のキャッシュを捨てる(モータ側の制限を別経路で書き換えた後に呼ぶ。
        次の周期で制限値を書き直す)。"""
        with self._lock:
            if motor is None:
                for c in self._ctx.values():
                    c.limits.clear()
            else:
                mid = motor if isinstance(motor, int) else motor.motor_id
                if mid in self._ctx:
                    self._ctx[mid].limits.clear()

    # ------------------------------------------------------------------
    # スレッド本体
    # ------------------------------------------------------------------
    def _register_channels(self, motor_id: int) -> None:
        if self.telemetry is not None:
            register_motor_channels(self.telemetry, motor_id)

    def _sleep_until(self, target: float) -> None:
        while not self._stop_evt.is_set():
            rem = target - clock.now()
            if rem <= 0:
                return
            if rem > SPIN_S:
                time.sleep(min(rem - SPIN_S, 0.02))
            # 残り SPIN_S 未満はスピン

    def _run(self) -> None:
        with timer_resolution(1):
            next_t = clock.now()
            prev_t0: float | None = None
            self._tick_errors = 0
            self._last_error_log = float("-inf")
            while not self._stop_evt.is_set():
                self._sleep_until(next_t)
                if self._stop_evt.is_set():
                    break
                t0 = clock.now()
                try:
                    self._tick(t0)
                except Exception:  # スレッドは死なせない
                    self._tick_errors += 1
                    # 毎周期失敗するとログが溢れるので、トレースバックは 1 秒に 1 回まで
                    if t0 - self._last_error_log >= 1.0:
                        self._last_error_log = t0
                        log.exception(
                            "ControlLoop の周期処理で例外 (累計 %d 回)", self._tick_errors
                        )
                t1 = clock.now()
                self._ticks += 1
                self._jitters.append(abs(t0 - next_t))
                self._tick_times.append(t0)
                if prev_t0 is not None:
                    self._last_dt = t0 - prev_t0
                prev_t0 = t0
                period = self._period
                next_t += period
                if next_t <= t1:  # 処理が次の周期にずれ込んだ: 取りこぼした周期を飛ばす
                    self._overruns += 1
                    next_t += (int((t1 - next_t) / period) + 1) * period

    # ------------------------------------------------------------------
    # 1 周期
    # ------------------------------------------------------------------
    def _tick(self, now: float) -> None:
        with self._tick_lock:
            with self._lock:  # 抑止中のモータは(通常は外れているが念のため)エンコード前に除く
                inh = self._inhibited
                handles = [h for h in self._handles if not inh.intersection(h.motor_ids)]
                keeps = [k for k in self._keeps.values() if k.motor.motor_id not in inh]
            if not handles and not keeps:
                return
            if self.bus.queue_len() > MAX_BACKLOG:
                self._skipped += 1
                return
            groups: list[_Group] = []
            failed: list[tuple[_Group, Exception]] = []
            for h in handles:
                group = _Group(h, None, [])
                try:
                    dt = self._period if h.last_t is None else now - h.last_t
                    if h.t0 is None:
                        h.t0 = now
                    h.last_t = now
                    states = {slot: m.state for slot, m in h.assign.items()}
                    cmds = h.controller.step(now - h.t0, dt, states)
                    group = self._encode_group(h, None, cmds, now, states)
                    groups.append(group)
                except Exception as e:
                    failed.append((group, e))
            for k in keeps:
                group = _Group(None, k, [])
                try:
                    group = self._encode_group(None, k, {"motor": k.cmd}, now)
                    groups.append(group)
                except Exception as e:
                    failed.append((group, e))
            self._commit(groups, now)
            for group, exc in failed:
                self._fail(group, exc)

    def _encode_group(
        self,
        handle: Handle | None,
        keep: _Keep | None,
        cmds: Mapping[str, Command],
        now: float,
        states: dict[str, MotorState] | None = None,
    ) -> _Group:
        group = _Group(handle, keep, [])
        if handle is not None:
            for slot, cmd in cmds.items():
                motor = handle.assign[slot]  # 未知のスロットは KeyError → コントローラ失敗
                state = states[slot] if states is not None else motor.state
                enc = self._encode(motor, cmd, handle.controller.run_mode, state)
                if enc is not None:
                    group.encs.append(enc)
        elif keep is not None:
            rm = keep.run_mode if keep.run_mode is not None else keep.motor.state.run_mode
            if rm is not None:
                enc = self._encode(keep.motor, keep.cmd, rm, keep.motor.state)
                if enc is not None:
                    group.encs.append(enc)
        return group

    def _ctx_of(self, motor_id: int) -> _MotorCtx:
        with self._lock:
            c = self._ctx.get(motor_id)
            if c is None:
                c = self._ctx[motor_id] = _MotorCtx()
            return c

    def _expand_hold(
        self, cmd: HoldCmd, run_mode: RunMode, state: MotorState, ctx: _MotorCtx
    ) -> MitCmd | SetpointCmd:
        if ctx.hold_src != cmd:  # 新しい保持: 現在位置をラッチ
            ctx.hold_src = cmd
            ctx.hold_pos = (state.position, state.position_multi)
        if run_mode == RunMode.OPERATION:
            # MitCmd.position は多回転座標。コマンド座標への変換は SafetyGuard(clamp_mit)が行う
            return cmd.to_mit(ctx.hold_pos[1])
        if run_mode in (RunMode.PP, RunMode.CSP):
            return cmd.to_setpoint(ctx.hold_pos[1])
        if run_mode == RunMode.VELOCITY:
            return SetpointCmd("velocity", 0.0)
        return SetpointCmd("current", 0.0)

    def _encode(
        self, motor: Motor, cmd: Command, run_mode: RunMode, state: MotorState
    ) -> _Enc | None:
        """1 指令をクランプしてフレームにする。位置が必要なのにフィードバック未受信なら None。"""
        ctx = self._ctx_of(motor.motor_id)
        if isinstance(cmd, HoldCmd):
            if state.last_rx is None:
                return None
            cmd = self._expand_hold(cmd, run_mode, state, ctx)
        else:
            ctx.hold_src = None

        if isinstance(cmd, MitCmd):
            if run_mode != RunMode.OPERATION:
                raise ProtocolError(f"MitCmd は run_mode {int(run_mode)} では送れません")
            if state.last_rx is None:
                return None
            c, notes = self.guard.clamp_mit(cmd, state)
            frame = private.mit_command(
                motor.motor_id,
                motor.host_id,
                motor.profile.ranges,
                c.position,
                c.velocity,
                c.kp,
                c.kd,
                c.torque,
            )
            record = {"cmd.pos": c.position, "cmd.vel": c.velocity, "cmd.tq": c.torque}
            return _Enc(motor, ctx, [frame], {}, record, notes)

        if isinstance(cmd, SetpointCmd):
            if run_mode not in _KIND_MODES.get(cmd.kind, ()):
                raise ProtocolError(f"{cmd.kind} 指令は run_mode {int(run_mode)} では送れません")
            sc, notes = self.guard.clamp_setpoint(cmd, state)
            writes: list[tuple[str, float]] = []
            if sc.current_limit is not None:
                writes.append((_CURRENT_LIMIT_KEY, sc.current_limit))
            if sc.speed_limit is not None and run_mode in _SPEED_LIMIT_KEY:
                writes.append((_SPEED_LIMIT_KEY[run_mode], sc.speed_limit))
            if sc.accel is not None and run_mode in _ACCEL_KEY:
                writes.append((_ACCEL_KEY[run_mode], sc.accel))
            frames: list[CanFrame] = []
            updates: dict[str, float] = {}
            for key, value in writes:
                if ctx.limits.get(key) != value:
                    frames.append(self._write(motor, key, value))
                    updates[key] = value
            frames.append(self._write(motor, _REF_KEY[sc.kind], sc.value))
            suffix = {"velocity": "cmd.vel", "current": "cmd.cur", "position": "cmd.pos"}[sc.kind]
            return _Enc(motor, ctx, frames, updates, {suffix: sc.value}, notes)

        raise ProtocolError(f"未知の指令型です: {type(cmd).__name__}")

    @staticmethod
    def _write(motor: Motor, key: str, value: float) -> CanFrame:
        pd = motor.profile.param(key)
        return private.write_param(motor.motor_id, motor.host_id, pd.index, pd.dtype, value)

    # ------------------------------------------------------------------
    # 送信・記録・失敗処理
    # ------------------------------------------------------------------
    def _commit(
        self, groups: list[_Group], now: float, priority: Priority = Priority.CONTROL
    ) -> None:
        encs = [e for g in groups for e in g.encs]
        frames = [f for e in encs for f in e.frames]
        ok = True
        if frames:
            try:
                self.bus.send_many(frames, priority)
                self._frames_sent += len(frames)
            except Exception as e:
                ok = False
                self._send_errors += 1
                self._publish_send_error(e, now)
        for e in encs:
            if ok:
                e.ctx.limits.update(e.limit_updates)
                if self.telemetry is not None:
                    for suffix, value in e.record.items():
                        self.telemetry.append(motor_channel(e.motor.motor_id, suffix), now, value)
            self._publish_notes(e.motor.motor_id, e.ctx, e.notes, now)

    def _publish_notes(
        self, motor_id: int, ctx: _MotorCtx, notes: list[ClampNote], now: float
    ) -> None:
        key = frozenset((n.field, n.reason) for n in notes)
        runaway = any("runaway_guard" in n.reason.split(",") for n in notes)
        if runaway and motor_id not in self._runaway_logged:
            self._runaway_logged.add(motor_id)
            log.warning(
                "ID %d: 速度が上限に近づいたため暴走ガードで制動します"
                "(MIT の負の kd・摩擦補償 / 電流モード)",
                motor_id,
            )
        elif not runaway:
            self._runaway_logged.discard(motor_id)
        wrap = any("position_wrap" in n.reason.split(",") for n in notes)
        if not wrap:
            self._wrap_logged.discard(motor_id)
        elif motor_id not in self._wrap_logged:  # 区間に入ったときだけ 1 回(毎周期は出さない)
            self._wrap_logged.add(motor_id)
            log.warning(
                "ID %d: ±4π 付近のため位置保持/位置目標(kp)を 0 にし、減衰のみで保持します"
                "(運転ページの「原点に設定」で解消)",
                motor_id,
            )
        if key != ctx.notes_key or (notes and now - ctx.notes_t >= _NOTES_REPUBLISH_S):
            ctx.notes_key = key
            ctx.notes_t = now
            self.bus.events.publish(CONTROL_CLAMPED, {"motor_id": motor_id, "notes": list(notes)})

    def _publish_send_error(self, exc: Exception, now: float) -> None:
        if now - self._last_send_err >= _ERROR_REPUBLISH_S:
            self._last_send_err = now
            log.warning("制御指令の送信に失敗: %r", exc)
            self.bus.events.publish(CONTROL_SEND_ERROR, {"error": exc})

    def _publish_error(self, motor_ids: list[int], controller: str, exc: Exception) -> None:
        self.bus.events.publish(
            CONTROL_ERROR, {"motor_ids": motor_ids, "controller": controller, "error": exc}
        )

    def _disable_motors(self, motors: list[Motor]) -> None:
        """type 4 を send_now(失敗しても例外は出さない)。"""
        if not motors:
            return
        try:
            self.bus.purge_control([m.motor_id for m in motors])
            self.bus.send_now([private.stop(m.motor_id, m.host_id) for m in motors])
        except Exception:
            log.exception("コントローラ失敗後の stop 送信に失敗しました")

    def _fail(self, group: _Group, exc: Exception) -> None:
        """コントローラ(または keepalive)の例外: 外し、対象モータを無効化し、イベントを出す。"""
        if group.handle is not None:
            h = group.handle
            name = h.controller.name
            motors = list(h.assign.values())
            with self._lock:
                if h.active:
                    h.active = False
                    self._handles.remove(h)
                for m in motors:
                    self._owner.pop(m.motor_id, None)
        else:
            assert group.keep is not None
            name = "keepalive"
            motors = [group.keep.motor]
            with self._lock:
                self._keeps.pop(group.keep.motor.motor_id, None)
        log.error("制御が失敗: %s: %r", name, exc, exc_info=exc)
        self._disable_motors(motors)
        self._publish_error([m.motor_id for m in motors], name, exc)
