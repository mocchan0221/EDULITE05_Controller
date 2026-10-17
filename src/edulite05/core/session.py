"""Session: transport / BusManager / Motor 群 / EventBus の生成と破棄(phase1.md §1.9)。

`close()` は **接続中の全モータへ stop を `send_now` で送ってから** バスを止め、transport を
閉じる(architecture.md §4.7 の終了時フック)。`atexit` にも登録するので、`close()` し忘れても
インタプリタ終了時に停止が送られる。Session は 1 回限り(close 後の再 open は不可)。
"""

from __future__ import annotations

import atexit
import logging
import threading
from collections.abc import Iterable
from concurrent.futures import Future
from types import TracebackType

from edulite05.core.bus import BusManager
from edulite05.core.device.motor import Motor
from edulite05.core.device.profile import MotorProfile
from edulite05.core.errors import EduliteError, ProtocolError
from edulite05.core.events import CONNECTION_CHANGED, MOTOR_DISCOVERED, EventBus
from edulite05.core.protocol import private
from edulite05.core.protocol.private import DecodeContext, UidResponse
from edulite05.core.transport.base import CanTransport
from edulite05.core.transport.framelog import FrameLogWriter

log = logging.getLogger(__name__)


class Session:
    def __init__(
        self,
        transport: CanTransport,
        profile: MotorProfile,
        host_id: int = 0xFD,
        framelog_path: str | None = None,
    ) -> None:
        self.transport = transport
        self.profile = profile
        self.host_id = host_id
        self.events = EventBus()
        self.framelog = FrameLogWriter(framelog_path) if framelog_path else None
        ctx = DecodeContext(host_id, profile.ranges, profile.param_dtypes())
        self.bus = BusManager(transport, ctx, self.events, self.framelog)
        self.motors: dict[int, Motor] = {}
        self._motors_lock = threading.Lock()
        self._lock = threading.Lock()  # open/close の排他
        self._opened = False
        self._closed = False

    # ------------------------------------------------------------------
    def open(self) -> None:
        with self._lock:
            if self._closed:
                raise EduliteError("Session は閉じられています")
            if self._opened:
                return
            try:
                if not self.transport.is_open:
                    self.transport.open()
                self.bus.start()
            except BaseException:
                self.bus.stop()
                try:
                    self.transport.close()
                finally:
                    if self.framelog is not None:
                        self.framelog.close()
                    self._closed = True
                raise
            self._opened = True
            atexit.register(self.close)
        self.events.publish(CONNECTION_CHANGED, {"connected": True})

    def close(self) -> None:
        """全モータへ stop(send_now)→ バス停止 → transport 閉鎖。冪等。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            was_open = self._opened
            self._opened = False
        atexit.unregister(self.close)
        if was_open:
            self._stop_all()
        try:
            self.bus.stop()
        finally:
            try:
                self.transport.close()
            finally:
                with self._motors_lock:
                    motors = list(self.motors.values())
                for m in motors:
                    m.close()
                if self.framelog is not None:
                    self.framelog.close()
        if was_open:
            self.events.publish(CONNECTION_CHANGED, {"connected": False})

    def _stop_all(self) -> None:
        with self._motors_lock:
            ids = sorted(self.motors)
        if not ids or not self.bus.running:
            return
        frames = [private.stop(i, self.host_id) for i in ids]
        try:
            self.bus.send_now(frames)
        except Exception:
            log.exception("終了時の stop 送信に失敗しました")

    def __enter__(self) -> Session:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    def motor(self, motor_id: int) -> Motor:
        """Motor を取得する。未登録なら生成して `motors` に登録する(スキャン不要)。"""
        with self._motors_lock:
            m = self.motors.get(motor_id)
            if m is None:
                m = Motor(self.bus, motor_id, self.profile, self.host_id)
                self.motors[motor_id] = m
            return m

    def rekey_motor(self, old_id: int, new_id: int) -> Motor:
        """CAN ID 変更(type 7)後の付け替え: `motors` の鍵と `Motor.motor_id` を新 ID にする。"""
        with self._motors_lock:
            m = self.motors.get(old_id)
            if m is None:
                raise KeyError(f"ID {old_id} のモータは登録されていません")
            if new_id in self.motors:
                raise ProtocolError(f"ID {new_id} は既に登録されています")
            del self.motors[old_id]
            m.rekey(new_id)
            self.motors[new_id] = m
        return m

    def scan(
        self,
        ids: Iterable[int] = range(1, 128),
        gap_s: float = 0.002,
        settle_s: float = 0.3,
    ) -> Future[list[Motor]]:
        """ID スキャン。応答した ID の Motor を生成・登録し、ID 昇順で返す。"""
        out: Future[list[Motor]] = Future()
        bus_fut = self.bus.scan(ids, gap_s=gap_s, settle_s=settle_s)

        def done(f: Future[list[UidResponse]]) -> None:
            try:
                found: list[Motor] = []
                for r in f.result():
                    with self._motors_lock:
                        is_new = r.motor_id not in self.motors
                    m = self.motor(r.motor_id)
                    m.uid = r.uid
                    found.append(m)
                    if is_new:
                        self.events.publish(
                            MOTOR_DISCOVERED, {"motor_id": r.motor_id, "uid": r.uid}
                        )
                out.set_result(found)
            except BaseException as e:
                out.set_exception(e)

        bus_fut.add_done_callback(done)
        return out
