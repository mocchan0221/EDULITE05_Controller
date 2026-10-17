"""公式 RobStride USB-CAN(CH340、"AT" シリアルフレーム)トランスポート。

フレーム仕様は docs/research/can_transport.md §2.2 / §2.4、設計は §5.4。
    'A' 'T' | ID field 32bit BE | DLC | data[0..DLC-1] | 0D 0A    (全長 9 + DLC)
    ID field = (can_id29 << 3) | 0x04(bit2 = IDE: 拡張フレーム)
"""

from __future__ import annotations

import struct
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from edulite05.core import clock
from edulite05.core.errors import TransportError

from .base import CanFrame, CanTransport, Direction, TransportCaps, TransportStats
from .pacing import FramePacer

_HEADER = b"AT"
_TERMINATOR = b"\r\n"
_IDE_BIT = 0x04  # can_transport.md §2.2(推論: bxCAN の IDE)
_EXT_MASK = 0x1FFFFFFF
_STD_MASK = 0x7FF


def encode_at(frame: CanFrame) -> bytes:
    """CanFrame を AT フレームのバイト列にする(can_transport.md §2.2)。

    拡張フレームは ``(can_id << 3) | 0x04``。標準フレームは ``(can_id << 21)``(IDE=0)で
    組み立てるが、これは bxCAN のレジスタ配置からの推定で **未検証(Q-T1)**。
    """
    data = bytes(frame.data)
    if len(data) > 8:
        raise ValueError(f"DLC > 8: {len(data)}")
    if frame.extended:
        if not 0 <= frame.can_id <= _EXT_MASK:
            raise ValueError(f"extended CAN ID out of range: {frame.can_id:#x}")
        field = (frame.can_id << 3) | _IDE_BIT
    else:
        if not 0 <= frame.can_id <= _STD_MASK:
            raise ValueError(f"standard CAN ID out of range: {frame.can_id:#x}")
        field = frame.can_id << 21  # 未検証(Q-T1)
    return _HEADER + struct.pack(">I", field) + bytes([len(data)]) + data + _TERMINATOR


class AtFrameParser:
    """AT フレームのストリームパーサ(can_transport.md §2.2 注意 / §5.4)。

    ``AT`` 探索 → DLC ≤ 8 確認 → 全長が揃うまで待つ → 末尾 ``0D 0A`` 検証 → bit2 で拡張/標準判定。
    失敗したら 1 バイト進めて再同期する。データ中の ``41 54`` / ``0D 0A`` では誤動作しない。

    カウンタ: ``parse_errors`` = 候補フレームの検証失敗(DLC 超過・終端不一致)の回数、
    ``resyncs`` = 再同期(1 バイト送り、または AT 以前のゴミ読み捨て)の回数。
    """

    __slots__ = ("_buf", "parse_errors", "resyncs")

    def __init__(self) -> None:
        self._buf = bytearray()
        self.parse_errors = 0
        self.resyncs = 0

    def reset(self) -> None:
        self._buf.clear()

    @property
    def pending(self) -> int:
        """未処理のバッファ長(バイト)。"""
        return len(self._buf)

    def feed(self, chunk: bytes, timestamp: float = 0.0) -> list[CanFrame]:
        buf = self._buf
        buf += chunk
        frames: list[CanFrame] = []
        pos = 0
        n = len(buf)
        while True:
            i = buf.find(_HEADER, pos)
            if i < 0:
                # 末尾が 'A' のときは次の chunk の 'T' と合わさる可能性があるので残す
                keep = n - 1 if n > pos and buf[n - 1] == 0x41 else n
                if keep > pos:
                    self.resyncs += 1
                pos = keep
                break
            if i > pos:
                self.resyncs += 1  # AT 以前のゴミを読み捨て
                pos = i
            if n - pos < 7:
                break  # ヘッダ(AT + ID 4B + DLC)が揃っていない
            dlc = buf[pos + 6]
            if dlc > 8:
                self.parse_errors += 1
                self.resyncs += 1
                pos += 1
                continue
            total = 9 + dlc
            if n - pos < total:
                break  # 全長が揃うまで待つ
            if buf[pos + total - 2 : pos + total] != _TERMINATOR:
                self.parse_errors += 1
                self.resyncs += 1
                pos += 1
                continue
            (field,) = struct.unpack_from(">I", buf, pos + 2)
            if field & _IDE_BIT:
                can_id, extended = (field >> 3) & _EXT_MASK, True
            else:
                can_id, extended = (field >> 21) & _STD_MASK, False  # 未検証(Q-T1)
            frames.append(
                CanFrame(
                    can_id=can_id,
                    data=bytes(buf[pos + 7 : pos + 7 + dlc]),
                    extended=extended,
                    timestamp=timestamp,
                    direction=Direction.RX,
                )
            )
            pos += total
        del buf[:pos]
        return frames


@dataclass(frozen=True, slots=True)
class PortInfo:
    device: str
    description: str
    vid: int | None
    pid: int | None
    preferred: bool  # 1A86:7523(CH340)


_CH340_VID = 0x1A86
_CH340_PID = 0x7523


def list_ports() -> list[PortInfo]:
    """シリアルポート一覧。CH340(1A86:7523)を preferred として先頭に置く。"""
    from serial.tools import list_ports as _lp

    ports = [
        PortInfo(
            device=p.device,
            description=p.description or "",
            vid=p.vid,
            pid=p.pid,
            preferred=(p.vid == _CH340_VID and p.pid == _CH340_PID),
        )
        for p in _lp.comports()
    ]
    ports.sort(key=lambda p: (not p.preferred, p.device))
    return ports


SerialFactory = Callable[..., Any]

# CH340 が大電流時に一時的に出す "returned no data" 等を許容する連続回数(can_transport.md §2.5)
_MAX_CONSECUTIVE_IO_ERRORS = 50


class AtSerialTransport(CanTransport):
    """公式 USB-CAN(AT フレーム)。AT 設定コマンドは一切送らない(§2.5)。

    受信は専用の読取りスレッドが行い、各フレームに **シリアルから読み出した時刻**
    (`core.clock.now()`)を付けてキューへ積む。`recv()` はそのキューを待つだけなので、
    受信側の処理(デコード・購読者・ログ)が遅れても、タイムスタンプは受信時刻のまま
    (実機初試験では、読取りが処理に引きずられて 148 フレームが同じ時刻になっていた)。

    送信は **フレーム間に最小 `min_frame_gap_s`(既定 1 ms)を空ける**(ペーシング)。
    実測(hw_bench.md「送信間隔と取りこぼし」)で、間隔 0.5 ms 未満のフレームはアダプタ/モータ
    のどちらかで取りこぼし、1 ms 以上なら取りこぼさなかった。このため `send_many` も
    連結 write をせず、1 フレームずつペーシングして書く(連結してもアダプタ側では
    ゼロ間隔の連続フレームになり、実測で 6/16 しか応答がなかった)。
    N フレームの `send_many` は約 N ms かかる。`min_frame_gap_s=0` で無効(テスト用)。
    """

    caps = TransportCaps(
        name="robstride_at",
        hw_timestamp=False,
        bus_state=False,
        standard_frames=True,  # 送受信は実装するが未検証(Q-T1)
        max_fps_hint=5400,
    )

    def __init__(
        self,
        port: str,
        baudrate: int = 921600,
        read_chunk: int = 4096,
        *,
        serial_factory: SerialFactory | None = None,
        min_frame_gap_s: float = 0.001,
    ) -> None:
        self._pacer = FramePacer(min_frame_gap_s)  # 負値は ValueError
        self.min_frame_gap_s = self._pacer.gap_s
        base = type(self).caps
        gap = self.min_frame_gap_s
        # 送信上限の目安 = 1 / 最小間隔(ペーシング無効ならフレーム時間で決まる既定値)
        self.caps = base
        if gap > 0:
            self.caps = replace(base, max_fps_hint=min(base.max_fps_hint, int(1.0 / gap)))
        self.port = port
        self.baudrate = baudrate
        self.read_chunk = read_chunk
        self._factory = serial_factory
        self._ser: Any = None
        self._parser = AtFrameParser()
        self._rx: deque[CanFrame] = deque()
        self._rx_cv = threading.Condition()  # _rx と _fatal の保護
        self._fatal: TransportError | None = None
        self._reader: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._stats = TransportStats()
        self._stats_lock = threading.Lock()
        self._tx_lock = threading.Lock()
        self._io_err_run = 0

    # -- open / close ------------------------------------------------------
    def open(self) -> None:
        if self._ser is not None:
            return
        factory = self._factory
        if factory is None:
            import serial

            factory = serial.Serial
        try:
            ser = factory(
                self.port,
                self.baudrate,
                timeout=0.005,
                write_timeout=0.2,
                exclusive=True,
            )
            ser.reset_input_buffer()
        except Exception as e:  # serial.SerialException / ValueError / OSError
            raise TransportError(f"ポート {self.port} を開けません: {e}") from e
        self._ser = ser
        self._pacer.reset()
        self._parser.reset()
        with self._rx_cv:
            self._rx.clear()
            self._fatal = None
        self._io_err_run = 0
        stop = self._reader_stop = threading.Event()
        self._reader = threading.Thread(
            target=self._read_loop, args=(ser, stop), name="edulite05-at-read", daemon=True
        )
        self._reader.start()

    def close(self) -> None:
        self._reader_stop.set()
        reader, self._reader = self._reader, None
        if reader is not None and reader is not threading.current_thread():
            reader.join(1.0)
        ser, self._ser = self._ser, None
        with self._rx_cv:
            self._rx_cv.notify_all()
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    @property
    def is_open(self) -> bool:
        ser = self._ser
        return ser is not None and bool(getattr(ser, "is_open", True))

    # -- TX ----------------------------------------------------------------
    def send(self, frame: CanFrame) -> None:
        self._write_frames((frame,))

    def send_many(self, frames: Sequence[CanFrame]) -> None:
        """複数フレームを順に送る。連結 write はせず、1 フレームずつペーシングする。"""
        if frames:
            self._write_frames(frames)

    def _write_frames(self, frames: Sequence[CanFrame]) -> None:
        ser = self._ser
        if ser is None:
            raise TransportError("トランスポートが開いていません")
        payloads = [encode_at(f) for f in frames]  # エンコード失敗は何も送らずに ValueError
        sent = 0
        try:
            with self._tx_lock:  # ペーシング込みで排他(バッチの順序も保つ)
                pacer = self._pacer
                for payload in payloads:
                    pacer.wait()
                    try:
                        ser.write(payload)
                    except Exception as e:
                        with self._stats_lock:
                            self._stats.io_errors += 1
                        raise TransportError(f"送信に失敗しました: {e}") from e
                    finally:
                        pacer.mark()
                    sent += 1
        finally:
            if sent:
                with self._stats_lock:
                    self._stats.tx_frames += sent

    # -- RX ----------------------------------------------------------------
    def recv(self, timeout: float) -> CanFrame | None:
        deadline = clock.now() + max(0.0, timeout)
        with self._rx_cv:
            while True:
                if self._rx:
                    return self._rx.popleft()
                if self._fatal is not None:
                    raise self._fatal
                if self._ser is None:
                    raise TransportError("トランスポートが開いていません")
                rem = deadline - clock.now()
                if rem <= 0:
                    return None
                self._rx_cv.wait(rem)

    def _read_loop(self, ser: Any, stop: threading.Event) -> None:
        """読取りスレッド: 読めたらすぐ時刻を付けてキューへ(処理は recv 側)。"""
        while not stop.is_set():
            try:
                self._pump(ser)
            except TransportError as e:
                with self._rx_cv:
                    self._fatal = e
                    self._rx_cv.notify_all()
                return
            except Exception as e:  # 想定外でも読取りは止めない(致命的なら連続して溜まる)
                with self._stats_lock:
                    self._stats.io_errors += 1
                self._io_err_run += 1
                if self._io_err_run >= _MAX_CONSECUTIVE_IO_ERRORS:
                    with self._rx_cv:
                        self._fatal = TransportError(f"受信に失敗しました: {e}")
                        self._rx_cv.notify_all()
                    return
                time.sleep(0.002)

    def _pump(self, ser: Any) -> None:
        try:
            n = min(getattr(ser, "in_waiting", 0) or 1, self.read_chunk)
            data = ser.read(n)
        except Exception as e:
            # CH340 は大電流時に一時的に例外を出すことがある → 切断扱いにせず再試行
            with self._stats_lock:
                self._stats.io_errors += 1
            self._io_err_run += 1
            if self._io_err_run >= _MAX_CONSECUTIVE_IO_ERRORS:
                raise TransportError(f"受信に失敗しました: {e}") from e
            time.sleep(0.002)
            return
        self._io_err_run = 0
        if not data:
            return
        now = clock.now()  # このチャンクを読み出した時刻 = 受信時刻
        p = self._parser
        pe0, rs0 = p.parse_errors, p.resyncs
        frames = p.feed(data, now)
        with self._stats_lock:
            st = self._stats
            st.parse_errors += p.parse_errors - pe0
            st.resyncs += p.resyncs - rs0
            if frames:
                st.rx_frames += len(frames)
                st.last_rx = now
        if frames:
            with self._rx_cv:
                self._rx.extend(frames)
                self._rx_cv.notify()

    def stats(self) -> TransportStats:
        with self._stats_lock:
            return self._stats.snapshot()
