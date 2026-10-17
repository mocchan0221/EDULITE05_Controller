"""フレームログ(candump -L 互換、framelog.py)の再生トランスポート(can_transport.md §5.7)。

ログ中の **RX フレームだけ** を元の時刻間隔(÷ ``speed``)で `recv()` から返す。TX は
`send()` で受け取って捨てる(読み出し専用: ``caps.read_only``)。返すフレームの
``timestamp`` は再生時点の ``core.clock.now()``(ログ上の壁時計ではない)。

終端: ``loop=False`` ならログの最後のフレームの後は `recv()` が(timeout 待って)None を返し、
``stats().finished`` が True になる。``loop=True`` なら先頭へ戻って繰り返す
(つなぎ目の間隔はログの平均フレーム間隔)。進捗は ``stats()`` の ``position / total``。
再生の起点は最初の `recv()` 呼び出し(open からの待ちで先頭がまとめて出ないように)。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from os import PathLike

from edulite05.core import clock
from edulite05.core.errors import TransportError
from edulite05.core.protocol.ids import CommType, parse_id

from .base import CanFrame, CanTransport, Direction, TransportCaps, TransportStats
from .framelog import read_framelog

# 待ち時間のうち、この長さは sleep せずスピンで待つ(pacing.py と同じ Windows 対策)
_SPIN_S = 0.0015
_LONG_WAIT_S = 0.05


# モータ → ホストの通信タイプ(el05_protocol.md §2):
# フィードバック / 主動上報 / パラメータ応答 / 故障 / UID 応答
_MOTOR_TO_HOST = frozenset(
    {CommType.GET_ID, CommType.FEEDBACK, CommType.PARAM_READ, CommType.FAULT,
     CommType.ACTIVE_REPORT}
)


def motor_ids_in_log(path: str | PathLike[str]) -> list[int]:
    """ログの RX フレームに現れるモータ ID(昇順、1〜127)。読めなければ空。

    再生ではスキャン(TX)の応答が返らないので、接続側がこの一覧でモータを登録する。
    モータ ID は ID の bit15..8(el05_protocol.md §1.2)。"""
    ids: set[int] = set()
    try:
        for f in read_framelog(path):
            if f.direction is not Direction.RX or not f.extended:
                continue
            fields = parse_id(f.can_id)
            if fields.comm_type in _MOTOR_TO_HOST:
                mid = fields.data2 & 0xFF
                if 1 <= mid <= 127:
                    ids.add(mid)
    except (OSError, ValueError):
        return []  # 読めない理由は open() が TransportError で伝える
    return sorted(ids)


@dataclass(frozen=True, slots=True)
class ReplayCaps(TransportCaps):
    read_only: bool = True  # モータへ指令を送れない(TX は捨てる)


@dataclass(slots=True)
class ReplayStats(TransportStats):
    """再生の進捗つき統計。``tx_frames`` は捨てた TX の数。"""

    total: int = 0  # ログ中の RX フレーム数
    position: int = 0  # 今回の周回で返したフレーム数
    loops: int = 0  # 完了した周回数
    finished: bool = False  # 終端に達した(loop=False)

    @property
    def progress(self) -> float:
        """今回の周回の進捗 0.0〜1.0(終端で 1.0)。"""
        if self.finished:
            return 1.0
        return self.position / self.total if self.total else 0.0


class ReplayTransport(CanTransport):
    caps = ReplayCaps(
        name="replay",
        hw_timestamp=False,
        bus_state=False,
        standard_frames=True,
        max_fps_hint=100_000,
        read_only=True,
    )

    def __init__(self, path: str | PathLike[str], speed: float = 1.0, loop: bool = False) -> None:
        speed = float(speed)
        if not speed > 0:  # NaN も弾く
            raise ValueError(f"再生速度は正の数です: {speed}")
        self.path = path
        self.speed = speed
        self.loop = bool(loop)
        self._frames: list[CanFrame] = []
        self._opened = False
        self._stop = threading.Event()
        self._stats = ReplayStats()
        self._stats_lock = threading.Lock()
        self._idx = 0
        self._t0: float | None = None  # frames[0] を返す clock 時刻(今周回)
        self._span = 0.0  # ログの長さ(秒)
        self._loop_gap = 0.0

    # -- open / close ------------------------------------------------------
    def open(self) -> None:
        if self._opened:
            return
        try:
            frames = [f for f in read_framelog(self.path) if f.direction is Direction.RX]
        except (OSError, ValueError) as e:
            raise TransportError(f"ログを読めません: {self.path}: {e}") from e
        if not frames:
            raise TransportError(f"再生できる RX フレームがログにありません: {self.path}")
        self._frames = frames
        self._span = max(0.0, frames[-1].timestamp - frames[0].timestamp)
        self._loop_gap = self._span / (len(frames) - 1) if len(frames) > 1 else 0.001
        self._idx = 0
        self._t0 = None
        self._stop = threading.Event()
        with self._stats_lock:
            self._stats = ReplayStats(total=len(frames))
        self._opened = True

    def close(self) -> None:
        self._opened = False
        self._stop.set()

    @property
    def is_open(self) -> bool:
        return self._opened

    # -- TX(捨てる)-------------------------------------------------------
    def send(self, frame: CanFrame) -> None:
        if not self._opened:
            raise TransportError("トランスポートが開いていません")
        with self._stats_lock:
            self._stats.tx_frames += 1

    def send_many(self, frames: Sequence[CanFrame]) -> None:
        if not self._opened:
            raise TransportError("トランスポートが開いていません")
        with self._stats_lock:
            self._stats.tx_frames += len(frames)

    # -- RX ----------------------------------------------------------------
    def _wait_until(self, target: float) -> bool:
        """clock 時刻 target まで待つ。close されたら False。"""
        stop = self._stop
        while True:
            if stop.is_set():
                return False
            rem = target - clock.now()
            if rem <= 0:
                return True
            if rem > _LONG_WAIT_S:
                stop.wait(rem - _LONG_WAIT_S)  # 長い待ちは close で起こせる(分解能は粗い)
            elif rem > _SPIN_S:
                time.sleep(rem - _SPIN_S)  # 3.11+ の Windows は高分解能タイマ(Event.wait は粗い)
            else:
                time.sleep(0)  # GIL を手放しながらスピン

    def recv(self, timeout: float) -> CanFrame | None:
        if not self._opened:
            raise TransportError("トランスポートが開いていません")
        deadline = clock.now() + max(0.0, timeout)
        frames = self._frames
        n = len(frames)
        if self._t0 is None:
            self._t0 = clock.now()
        if self._idx >= n:
            if not self.loop:
                with self._stats_lock:
                    self._stats.finished = True
                self._idle(deadline)
                return None
            # 次の周回へ(つなぎ目は平均フレーム間隔)
            self._t0 += (self._span + self._loop_gap) / self.speed
            self._idx = 0
            with self._stats_lock:
                self._stats.loops += 1
                self._stats.position = 0
        f = frames[self._idx]
        due = self._t0 + (f.timestamp - frames[0].timestamp) / self.speed
        if due > deadline:
            self._idle(deadline)
            return None
        if not self._wait_until(due):
            raise TransportError("トランスポートが開いていません")
        self._idx += 1
        now = clock.now()
        out = f.with_meta(timestamp=now)
        with self._stats_lock:
            s = self._stats
            s.rx_frames += 1
            s.last_rx = now
            s.position = self._idx
            if self._idx >= n and not self.loop:
                s.finished = True
        return out

    def _idle(self, deadline: float) -> None:
        """何も返さない待ち(timeout 満了か close まで)。"""
        rem = deadline - clock.now()
        if rem > 0:
            self._stop.wait(rem)
        if self._stop.is_set() and not self._opened:
            raise TransportError("トランスポートが開いていません")

    def stats(self) -> ReplayStats:
        with self._stats_lock:
            return self._stats.snapshot()  # type: ignore[return-value]
