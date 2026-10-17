"""bus / motor / session テスト用の FakeTransport とループバック応答器。

応答は el05_protocol.md §2 のとおり:
- type 0 → type 0(宛先 0xFE、UID)
- type 3 / 4 / 6 / 18 / 22 → type 2 フィードバック(type 4 の `00 C4 ..` はバージョン応答)
- type 17 → type 17(状態バイト 0 = 成功 / 1 = 失敗)
- type 24 → ON なら type 24 の上報を 1 件、OFF なら type 2 を 1 件
"""

from __future__ import annotations

import queue
import struct
import threading
import time
from collections.abc import Callable, Iterable, Sequence

from edulite05.core import clock
from edulite05.core.device.profile import MotorProfile
from edulite05.core.protocol.ids import CommType, make_id, parse_id
from edulite05.core.protocol.scaling import float_to_uint
from edulite05.core.protocol.types import MotorMode, Ranges, pack_value, unpack_value
from edulite05.core.transport.base import (
    CanFrame,
    CanTransport,
    Direction,
    TransportCaps,
    TransportStats,
)

HOST = 0xFD
_PROFILE = MotorProfile.load_builtin("el05")  # TOML 読込は 1 回だけ(共有フォルダ上で遅い)

Responder = Callable[[CanFrame], Iterable[CanFrame]]


class FakeTransport(CanTransport):
    """送信を記録し、responder が返したフレームを受信キューへ積む。"""

    caps = TransportCaps("fake", False, False, False, 1000)

    def __init__(self, responder: Responder | None = None) -> None:
        self.responder = responder
        self.sent: list[CanFrame] = []
        self._lock = threading.Lock()
        self._rx: queue.Queue[CanFrame] = queue.Queue()
        self._open = False
        self.closed_after_sent: int | None = None  # close 時点の送信数
        self.send_delay = 0.0
        self.gate: threading.Event | None = None  # セットされるまで send がブロック
        self.entered = threading.Event()  # gate で止まった最初の send
        self.fail_sends: list[Exception] = []  # 先頭から順に send で送出
        self.fail_recvs: list[Exception] = []
        self.reply_delay: Callable[[CanFrame], float] | None = None
        self._stats = TransportStats()

    # -- CanTransport -----------------------------------------------------
    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self.closed_after_sent = len(self.sent)
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def send(self, frame: CanFrame) -> None:
        if self.gate is not None:
            self.entered.set()
            self.gate.wait(5.0)
        if self.send_delay:
            time.sleep(self.send_delay)
        if self.fail_sends:
            raise self.fail_sends.pop(0)
        with self._lock:
            self.sent.append(frame)
            self._stats.tx_frames += 1
        if self.responder is not None:
            for r in self.responder(frame):
                self._deliver(r, frame)

    def recv(self, timeout: float) -> CanFrame | None:
        if self.fail_recvs:
            raise self.fail_recvs.pop(0)
        try:
            return self._rx.get(timeout=timeout)
        except queue.Empty:
            return None

    def stats(self) -> TransportStats:
        return self._stats.snapshot()

    # -- テスト用 ----------------------------------------------------------
    def inject(self, frame: CanFrame) -> None:
        self._rx.put(frame.with_meta(direction=Direction.RX))

    def _deliver(self, reply: CanFrame, request: CanFrame) -> None:
        d = self.reply_delay(request) if self.reply_delay else 0.0
        if d > 0:
            t = threading.Timer(d, self.inject, (reply,))
            t.daemon = True
            t.start()
        else:
            self.inject(reply)

    def sent_ids(self) -> list[int]:
        with self._lock:
            return [f.can_id for f in self.sent]

    def sent_types(self) -> list[int]:
        return [parse_id(i).comm_type for i in self.sent_ids()]

    def wait_sent(self, n: int, timeout: float = 2.0) -> bool:
        end = clock.now() + timeout
        while clock.now() < end:
            with self._lock:
                if len(self.sent) >= n:
                    return True
            time.sleep(0.001)
        return False


def wait_until(pred: Callable[[], object], timeout: float = 2.0) -> bool:
    end = clock.now() + timeout
    while clock.now() < end:
        if pred():
            return True
        time.sleep(0.002)
    return bool(pred())


def feedback_frame(
    motor: int,
    *,
    host: int = HOST,
    mode: int = MotorMode.RESET,
    fault: int = 0,
    position: float = 0.0,
    velocity: float = 0.0,
    torque: float = 0.0,
    temperature: float = 25.0,
    comm_type: int = CommType.FEEDBACK,
    ranges: Ranges | None = None,
) -> CanFrame:
    """モータ → ホストの type 2 / 24 フレーム(§2.3)。"""
    r = ranges or _PROFILE.ranges
    data2 = (mode << 14) | (fault << 8) | motor
    data = struct.pack(
        ">HHHh",
        float_to_uint(position, *r.position, 16),
        float_to_uint(velocity, *r.velocity, 16),
        float_to_uint(torque, *r.torque, 16),
        int(round(temperature * 10)),
    )
    return CanFrame(make_id(comm_type, data2, host), data)


def uid_of(motor: int) -> bytes:
    return bytes((0xA0, 0xB0, 0xC0, 0xD0, 0, 0, 0, motor))


def version_frame(motor: int, host: int = HOST) -> CanFrame:
    """§2.6: type 2 の ID、データ `00 C4 56 0A 05 00 02 07`(実機の応答)。"""
    return CanFrame(
        make_id(CommType.FEEDBACK, motor, host), bytes((0x00, 0xC4, 0x56, 0x0A, 5, 0, 2, 7))
    )


class FakeMotors:
    """複数モータを模す responder。状態は最小限(mode と param 値)。"""

    def __init__(
        self,
        ids: Sequence[int],
        profile: MotorProfile | None = None,
        host: int = HOST,
        *,
        version_type4: bool = True,
        save_reply: str = "uid",
    ) -> None:
        self.ids = set(ids)
        self.profile = profile or _PROFILE
        self.host = host
        self.version_type4 = version_type4
        self.save_reply = save_reply  # type 22 への応答: "uid"(実機 HW-6)/ "feedback"(他ファーム)
        self.dtypes = self.profile.param_dtypes()
        self.params: dict[int, dict[int, int | float]] = {
            m: {p.index: p.default for p in self.profile.params} for m in self.ids
        }
        self.mode: dict[int, int] = dict.fromkeys(self.ids, MotorMode.RESET)
        self.fault: dict[int, int] = dict.fromkeys(self.ids, 0)
        self.position: dict[int, float] = dict.fromkeys(self.ids, 0.0)
        self.fail_read: set[int] = set()  # type 17 を失敗で返す index

    def _fb(self, m: int, comm_type: int = CommType.FEEDBACK) -> CanFrame:
        return feedback_frame(
            m,
            host=self.host,
            mode=self.mode[m],
            fault=self.fault[m],
            position=self.position[m],
            comm_type=comm_type,
        )

    def __call__(self, frame: CanFrame) -> list[CanFrame]:
        f = parse_id(frame.can_id)
        m = f.dest
        if m not in self.ids:
            return []
        t = f.comm_type
        data = frame.data
        if t == CommType.GET_ID:
            return [CanFrame(make_id(CommType.GET_ID, m, 0xFE), uid_of(m))]
        if t == CommType.ENABLE:
            self.mode[m] = MotorMode.MOTOR
            return [self._fb(m)]
        if t == CommType.STOP:
            if data[1] == 0xC4:  # バージョン読出(§2.6)
                return [version_frame(m, self.host)] if self.version_type4 else []
            self.mode[m] = MotorMode.RESET
            if data[0] == 1:
                self.fault[m] = 0
            return [self._fb(m)]
        if t == CommType.VERSION_ALT:  # EL05 実機では通常の type 2 が返るだけ
            return [self._fb(m)]
        if t == CommType.SAVE and self.save_reply == "uid":
            return [CanFrame(make_id(CommType.GET_ID, m, 0xFE), uid_of(m))]  # 実機は type 0
        if t == CommType.SAVE and self.save_reply == "none":
            return []
        if t in (CommType.SET_ZERO, CommType.SAVE):
            return [self._fb(m)]
        if t == CommType.PARAM_WRITE:
            index = struct.unpack_from("<H", data)[0]
            dt = self.dtypes.get(index)
            if dt is not None:
                self.params[m][index] = unpack_value(dt, data[4:8])
            return [self._fb(m)]
        if t == CommType.PARAM_READ:
            index = struct.unpack_from("<H", data)[0]
            dt = self.dtypes.get(index)
            ok = index not in self.fail_read and dt is not None
            raw = pack_value(dt, self.params[m][index]) if ok and dt is not None else bytes(4)
            reply = struct.pack("<HH", index, 0) + raw
            status = 0 if ok else 1
            return [CanFrame(make_id(CommType.PARAM_READ, (status << 8) | m, self.host), reply)]
        if t == CommType.ACTIVE_REPORT:
            on = data[6] == 1
            return [self._fb(m, CommType.ACTIVE_REPORT if on else CommType.FEEDBACK)]
        return []
