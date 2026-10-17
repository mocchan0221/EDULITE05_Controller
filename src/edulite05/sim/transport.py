"""シミュレータ用 CAN トランスポート(phase1.md §1.10)。

`SimTransport` は `CanTransport` を実装し、内部スレッドで `SimMotor` の物理を回す。
ホストが `send()` したフレームは遅延(latency_s + jitter)後に各 `SimMotor.handle()` へ渡り、
応答と主動上報は遅延後に受信キューへ入る(`recv()`)。`drop_rate` で送受信とも確率的に捨てる。
`reply_drop_rate` は **要求への応答フレームだけ**
(モータ → ホスト。主動上報・故障通知は対象外)を確率的に捨てる(実機の「主動上報中に応答が
約 5 % 欠落する」挙動の再現、hw_bench.md)。`drop_next(reply=n)` は次の n 個の応答を確実に捨てる。

遅延は **片道** の値(往復 = 2 倍)。latency_s=0 かつ jitter_s=0 のときは `send()` を呼んだ
スレッドで即座に処理する。

import 時に `registry.register("sim", ...)` する(registry は遅延 import で呼ぶ)。
"""

from __future__ import annotations

import heapq
import itertools
import logging
import random
import threading
import time
from collections import deque
from collections.abc import Iterable, Sequence
from typing import Any

from edulite05.core import clock
from edulite05.core.device.profile import MotorProfile
from edulite05.core.errors import TransportError
from edulite05.core.protocol.ids import CommType
from edulite05.core.transport import registry
from edulite05.core.transport.base import (
    CanFrame,
    CanTransport,
    Direction,
    TransportCaps,
    TransportStats,
)
from edulite05.core.transport.pacing import FramePacer
from edulite05.sim.motor_model import BENCH_MOTOR_ID, BENCH_UID, SimMotor

log = logging.getLogger(__name__)

_MAX_STEP_DT = 0.05  # 1 回の物理更新に渡す dt の上限 [s](スレッドが止まった後の暴走防止)


def default_ids(n: int) -> list[int]:
    """既定の ID: 先頭が実機と同じ 127、以降は 1, 2, ...(127 を除く)。"""
    ids = [BENCH_MOTOR_ID]
    cand = 1
    while len(ids) < n:
        if cand != BENCH_MOTOR_ID:
            ids.append(cand)
        cand += 1
    return ids[:n]


def make_motors(
    n: int = 1,
    ids: Iterable[int] | None = None,
    profile: MotorProfile | None = None,
    **motor_kwargs: Any,
) -> list[SimMotor]:
    """`n` 台(または `ids` の各 ID)の SimMotor を作る。UID は実機の末尾バイトを +k した値。"""
    prof = profile or MotorProfile.load_builtin("el05")
    id_list = list(ids) if ids is not None else default_ids(n)
    motors = []
    for k, mid in enumerate(id_list):
        last = BENCH_UID[7] + (0 if mid == BENCH_MOTOR_ID else k + 1)
        uid = bytes(BENCH_UID[:7]) + bytes([last & 0xFF])
        motors.append(SimMotor(motor_id=mid, profile=prof, uid=uid, **motor_kwargs))
    return motors


class SimTransport(CanTransport):
    """フレームレベルのシミュレータ・トランスポート。"""

    def __init__(
        self,
        motors: Sequence[SimMotor] | None = None,
        *,
        latency_s: float = 0.0005,
        jitter_s: float = 0.0,
        drop_rate: float = 0.0,
        physics_hz: float = 1000.0,
        seed: int | None = None,
        min_frame_gap_s: float = 0.0,
        reply_drop_rate: float = 0.0,
    ) -> None:
        self.motors: list[SimMotor] = list(motors) if motors is not None else make_motors(1)
        self.latency_s = latency_s
        self.jitter_s = jitter_s
        self.drop_rate = drop_rate  # 送信(ホスト→モータ)・受信(モータ→ホスト)それぞれに適用
        self.reply_drop_rate = reply_drop_rate  # 要求への応答だけを捨てる確率
        self.physics_hz = physics_hz
        self.caps = TransportCaps(
            name="sim",
            hw_timestamp=False,
            bus_state=False,
            standard_frames=True,  # MIT / CANopen の復帰フレーム(標準フレーム)を扱う
            max_fps_hint=5000,
        )
        # 送信ペーシング(AtSerialTransport と同じ。既定 0 = 無効でテストを速く保つ)。
        # 実機相当の負荷(send が 1 フレームあたり約 1 ms ブロックする)を再現するときに指定する
        self.min_frame_gap_s = float(min_frame_gap_s)
        self._pacer = FramePacer(self.min_frame_gap_s)
        self._tx_lock = threading.Lock()
        self._rng = random.Random(seed)
        self._stats = TransportStats()
        self._stats_lock = threading.Lock()
        self._cond = threading.Condition()  # _rx と _open の保護
        self._rx: deque[CanFrame] = deque()
        self._open = False
        self._ev_lock = threading.Lock()
        self._events: list[tuple[float, int, bool, CanFrame]] = []  # (due, seq, is_tx, frame)
        self._seq = itertools.count()
        self._last_due = {True: 0.0, False: 0.0}  # 方向別に到着順を保つ
        self._drop_tx = 0
        self._drop_rx = 0
        self._drop_reply = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.dropped_tx = 0
        self.dropped_rx = 0
        self.dropped_replies = 0  # reply_drop_rate / drop_next(reply=) で捨てた応答の数

    # ------------------------------------------------------------------
    # CanTransport
    # ------------------------------------------------------------------
    def open(self) -> None:
        with self._cond:
            if self._open:
                return
            self._open = True
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(self._stop,), name="sim-physics", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        with self._cond:
            was_open = self._open
            self._open = False
            self._cond.notify_all()
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._thread = None
        if was_open:
            with self._ev_lock:
                self._events.clear()
            with self._cond:
                self._rx.clear()

    @property
    def is_open(self) -> bool:
        return self._open

    def send(self, frame: CanFrame) -> None:
        if not self._open:
            raise TransportError("sim transport is closed")
        if self.min_frame_gap_s > 0:
            with self._tx_lock:  # ペーシング込みで排他(AtSerialTransport と同じ挙動)
                self._pacer.wait()
                self._pacer.mark()
        with self._stats_lock:
            self._stats.tx_frames += 1
        if self._should_drop(is_tx=True):
            return
        due = self._due(is_tx=True)
        if due is None:
            self._deliver_tx(frame)
        else:
            self._enqueue(due, True, frame)

    def recv(self, timeout: float) -> CanFrame | None:
        deadline = clock.now() + max(0.0, timeout)
        with self._cond:
            while not self._rx:
                if not self._open:
                    break
                remaining = deadline - clock.now()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            if self._rx:
                return self._rx.popleft()
        time.sleep(min(max(0.0, timeout), 0.01))  # 閉じている間にスピンさせない
        return None

    def stats(self) -> TransportStats:
        with self._stats_lock:
            return self._stats.snapshot()

    # ------------------------------------------------------------------
    # 公開 API(テスト・GUI のデモ用)
    # ------------------------------------------------------------------
    def motor(self, motor_id: int) -> SimMotor:
        for m in self.motors:
            if m.motor_id == motor_id:
                return m
        raise KeyError(f"シミュレータに ID {motor_id} のモータがありません")

    def power_cycle(self, motor_id: int) -> None:
        """`SimMotor.power_cycle` の薄いラッパ(ID 変更後は新 ID を指定する)。"""
        self.motor(motor_id).power_cycle()

    def inject_fault(self, motor_id: int, bit: int = 0, **kwargs: Any) -> None:
        """`SimMotor.inject_fault` の薄いラッパ(type 21 フレームは次の物理更新で流れる)。"""
        self.motor(motor_id).inject_fault(bit, **kwargs)

    def clear_faults(self, motor_id: int) -> None:
        self.motor(motor_id).clear_faults()

    def drop_next(self, tx: int = 0, rx: int = 0, reply: int = 0) -> None:
        """次の `tx` 個の送信フレーム / `rx` 個の受信フレーム / `reply` 個の応答フレームを
        確実に捨てる(確率とは別)。`reply` は要求への応答だけを数える(主動上報は数えない)。"""
        with self._stats_lock:
            self._drop_tx += tx
            self._drop_rx += rx
            self._drop_reply += reply

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _should_drop_reply(self) -> bool:
        """要求への応答フレームだけの欠落(`reply_drop_rate` / `drop_next(reply=)`)。"""
        with self._stats_lock:
            if self._drop_reply > 0:
                self._drop_reply -= 1
                self.dropped_replies += 1
                return True
            if self.reply_drop_rate > 0 and self._rng.random() < self.reply_drop_rate:
                self.dropped_replies += 1
                return True
        return False

    def _should_drop(self, is_tx: bool) -> bool:
        with self._stats_lock:
            if is_tx and self._drop_tx > 0:
                self._drop_tx -= 1
                self.dropped_tx += 1
                return True
            if not is_tx and self._drop_rx > 0:
                self._drop_rx -= 1
                self.dropped_rx += 1
                return True
            if self.drop_rate > 0 and self._rng.random() < self.drop_rate:
                if is_tx:
                    self.dropped_tx += 1
                else:
                    self.dropped_rx += 1
                return True
        return False

    def _due(self, is_tx: bool) -> float | None:
        """配送時刻。遅延ゼロなら None(その場で処理)。順序は方向ごとに保存する。"""
        if self.latency_s <= 0 and self.jitter_s <= 0:
            return None
        with self._stats_lock:
            jitter = self._rng.uniform(0.0, self.jitter_s) if self.jitter_s > 0 else 0.0
            due = max(clock.now() + self.latency_s + jitter, self._last_due[is_tx])
            self._last_due[is_tx] = due
        return due

    def _enqueue(self, due: float, is_tx: bool, frame: CanFrame) -> None:
        with self._ev_lock:
            heapq.heappush(self._events, (due, next(self._seq), is_tx, frame))

    def _deliver_tx(self, frame: CanFrame) -> None:
        for m in list(self.motors):
            try:
                replies = m.handle(frame)
            except Exception:
                log.exception("SimMotor.handle failed")
                with self._stats_lock:
                    self._stats.io_errors += 1
                continue
            for r in replies:
                if not self._should_drop_reply():
                    self._send_rx(r)

    def _send_rx(self, frame: CanFrame) -> None:
        if self._should_drop(is_tx=False):
            return
        due = self._due(is_tx=False)
        if due is None:
            self._push_rx(frame)
        else:
            self._enqueue(due, False, frame)

    def _push_rx(self, frame: CanFrame) -> None:
        now = clock.now()
        fr = frame.with_meta(timestamp=now, direction=Direction.RX)
        with self._cond:
            if not self._open:
                return
            self._rx.append(fr)
            self._cond.notify()
        with self._stats_lock:
            self._stats.rx_frames += 1
            self._stats.last_rx = now

    def _process_events(self, now: float) -> None:
        while True:
            with self._ev_lock:
                if not self._events or self._events[0][0] > now:
                    return
                _, _, is_tx, frame = heapq.heappop(self._events)
            if is_tx:
                self._deliver_tx(frame)
            else:
                self._push_rx(frame)

    def _next_event_due(self) -> float | None:
        with self._ev_lock:
            return self._events[0][0] if self._events else None

    def _run(self, stop: threading.Event) -> None:
        period = 1.0 / max(1.0, self.physics_hz)
        last = clock.now()
        next_tick = last + period
        while not stop.is_set():
            try:
                now = clock.now()
                self._process_events(now)
                if now >= next_tick:
                    dt = min(now - last, _MAX_STEP_DT)
                    last = now
                    for m in list(self.motors):
                        for fr in m.step(dt):
                            self._send_rx(fr)
                    next_tick += period
                    if next_tick < now:
                        next_tick = now + period
            except Exception:  # 物理スレッドは落とさない
                log.exception("sim physics loop error")
                with self._stats_lock:
                    self._stats.io_errors += 1
            wake = next_tick
            due = self._next_event_due()
            if due is not None and due < wake:
                wake = due
            delay = wake - clock.now()
            if delay > 0:
                time.sleep(min(delay, period))


# ----------------------------------------------------------------------
# registry への登録(registry が遅延 import する)
# ----------------------------------------------------------------------
def _parse_ids(ids: Any) -> list[int] | None:
    if ids is None or ids == "":
        return None
    if isinstance(ids, str):
        return [int(x, 0) for x in ids.replace(" ", "").split(",") if x]
    return [int(x) for x in ids]


def _create_sim(
    n: int = 1,
    ids: Any = None,
    latency_s: float = 0.0005,
    jitter_s: float = 0.0,
    drop_rate: float = 0.0,
    physics_hz: float = 1000.0,
    report_comm_type: int = CommType.ACTIVE_REPORT,
    seed: int | None = None,
    min_frame_gap_s: float = 0.0,
    reply_drop_rate: float = 0.0,
) -> SimTransport:
    motors = make_motors(int(n), _parse_ids(ids), report_comm_type=int(report_comm_type))
    return SimTransport(
        motors,
        latency_s=float(latency_s),
        jitter_s=float(jitter_s),
        drop_rate=float(drop_rate),
        physics_hz=float(physics_hz),
        seed=seed,
        min_frame_gap_s=float(min_frame_gap_s),
        reply_drop_rate=float(reply_drop_rate),
    )


registry.register(
    "sim",
    _create_sim,
    registry.TransportInfo(
        name="sim",
        label="シミュレータ",
        options_schema={
            "n": {"type": "int", "label": "モータ台数", "default": 1},
            "ids": {"type": "list", "label": "CAN ID(省略時 127, 1, 2, ...)"},
            "latency_s": {"type": "float", "label": "片道遅延 [s]", "default": 0.0005},
            "jitter_s": {"type": "float", "label": "遅延ジッタ [s]", "default": 0.0},
            "drop_rate": {"type": "float", "label": "フレーム欠落率", "default": 0.0},
            "physics_hz": {"type": "float", "label": "物理更新周波数 [Hz]", "default": 1000.0},
            "report_comm_type": {
                "type": "int",
                "label": "主動上報の通信タイプ(2 か 24)",
                "default": 24,
            },
            "reply_drop_rate": {
                "type": "float",
                "label": "応答の欠落率(主動上報は除く。実機の主動上報中は約 0.05)",
                "default": 0.0,
            },
            "min_frame_gap_s": {
                "type": "float",
                "label": "最小フレーム間隔 [s](実機相当は 0.001。0 で無効)",
                "default": 0.0,
            },
        },
    ),
)
