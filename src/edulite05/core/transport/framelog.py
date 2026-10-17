"""candump -L 互換のフレームログ(phase1.md §1.3)。

1 行 1 フレーム: ``(1696000000.123456) at0 1200FD01#0570000001000000``。TX は行末に `` T``。
拡張フレームは ID 8 桁、標準は 3 桁。タイムスタンプはファイル上は壁時計(epoch 秒)。
``CanFrame.timestamp`` は core.clock.now() 基準なので、Writer が起動時のオフセット
(time.time() - clock.now())で壁時計に換算する。
Reader は **ファイル上の値(epoch 秒)** をそのまま ``timestamp`` に入れる。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Iterator
from os import PathLike
from typing import IO

from edulite05.core import clock

from .base import CanFrame, Direction


def format_line(frame: CanFrame, wall_time: float) -> str:
    idw = f"{frame.can_id:08X}" if frame.extended else f"{frame.can_id:03X}"
    tail = " T" if frame.direction is Direction.TX else ""
    return f"({wall_time:.6f}) at{frame.channel} {idw}#{bytes(frame.data).hex().upper()}{tail}"


class FrameLogWriter:
    """非同期のフレームログ書込み。

    `write()` は 1 行を整形してキューへ積むだけで、ファイル I/O は専用スレッドが行う。
    共有フォルダ上のログは 1 行の書込みが約 3 ms かかり、TX / RX スレッド(`BusManager`)から
    同期で書くと TX キューの滞留と RX の遅れを起こした(実機初試験の事故)。
    キューが `max_pending` 行を超えたら新しい行を捨てる(`dropped` に数える)。
    """

    _BATCH_FLUSH_S = 0.05  # 書き溜めの最大待ち

    def __init__(
        self,
        path: str | PathLike[str],
        *,
        wall_offset: float | None = None,
        append: bool = False,
        max_pending: int = 500_000,
    ) -> None:
        """``wall_offset`` = wall - clock.now()。None なら生成時に計測する。"""
        self._offset = (time.time() - clock.now()) if wall_offset is None else wall_offset
        self._fh: IO[str] | None = open(  # noqa: SIM115
            path, "a" if append else "w", encoding="ascii", newline="\n"
        )
        self._cv = threading.Condition()
        self._q: deque[str] = deque()
        self._max_pending = max_pending
        self._busy = False  # 書込み中(flush の待ち合わせ用)
        self._closed = False
        self.dropped = 0
        self._thread = threading.Thread(target=self._run, name="edulite05-framelog", daemon=True)
        self._thread.start()

    def write(self, frame: CanFrame) -> None:
        # timestamp 未設定(0.0)のフレームは現在時刻にする
        wall = frame.timestamp + self._offset if frame.timestamp else time.time()
        line = format_line(frame, wall)
        with self._cv:
            if self._closed:
                return
            if len(self._q) >= self._max_pending:
                self.dropped += 1
                return
            self._q.append(line)
            self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._q and not self._closed:
                    self._cv.wait(self._BATCH_FLUSH_S)
                if not self._q and self._closed:
                    return
                lines = list(self._q)
                self._q.clear()
                self._busy = True
            try:
                fh = self._fh
                if fh is not None:
                    fh.write("\n".join(lines) + "\n")
                    fh.flush()
            except Exception:  # ログの失敗で本体を止めない
                pass
            finally:
                with self._cv:
                    self._busy = False
                    self._cv.notify_all()

    def flush(self, timeout: float = 5.0) -> None:
        """それまでに `write` した行がファイルへ出るまで待つ。"""
        end = clock.now() + timeout
        with self._cv:
            self._cv.notify()
            while (self._q or self._busy) and clock.now() < end:
                self._cv.wait(0.01)

    def close(self) -> None:
        with self._cv:
            if self._closed:
                return
            self._closed = True
            self._cv.notify_all()
        t = self._thread
        if t is not threading.current_thread():
            t.join(10.0)
        fh, self._fh = self._fh, None
        if fh is not None:
            fh.close()

    def __enter__(self) -> FrameLogWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def parse_line(line: str) -> CanFrame:
    """1 行を CanFrame にする。不正なら ValueError。"""
    parts = line.split()
    if len(parts) not in (3, 4) or (len(parts) == 4 and parts[3] != "T"):
        raise ValueError(f"bad framelog line: {line!r}")
    ts, iface, body = parts[0], parts[1], parts[2]
    if not (ts.startswith("(") and ts.endswith(")")) or "#" not in body:
        raise ValueError(f"bad framelog line: {line!r}")
    id_s, data_s = body.split("#", 1)
    digits = iface.lstrip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
    channel = int(digits) if digits.isdigit() else 0
    data = bytes.fromhex(data_s)
    if len(data) > 8:
        raise ValueError(f"DLC > 8: {line!r}")
    return CanFrame(
        can_id=int(id_s, 16),
        data=data,
        extended=len(id_s) > 3,
        timestamp=float(ts[1:-1]),
        channel=channel,
        direction=Direction.TX if len(parts) == 4 else Direction.RX,
    )


def read_framelog(path: str | PathLike[str]) -> Iterator[CanFrame]:
    with open(path, encoding="ascii") as fh:
        for no, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                yield parse_line(line)
            except ValueError as e:
                raise ValueError(f"{path}:{no}: {e}") from e
