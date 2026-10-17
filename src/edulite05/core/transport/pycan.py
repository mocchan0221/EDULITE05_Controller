"""python-can 経由の汎用 CAN トランスポート(can_transport.md §5.5)。

python-can は **任意依存**(``uv sync --extra pycan``)。このモジュールは python-can を
モジュール先頭で import せず、`open()` で初めて import する(未導入でもアプリが動く)。
未導入・オープン失敗は `TransportError`(日本語メッセージ)。

- 受信時刻は python-can の ``msg.timestamp`` ではなく、``recv`` が返す瞬間の
  ``core.clock.now()``(コア共通の時計に揃える)。
- 標準フレーム(11 bit)と拡張フレーム(29 bit)に対応。リモート/エラーフレームは捨てて
  ``parse_errors`` に数える。
- 送信ペーシング(``min_frame_gap_s``)は既定 0(これらのアダプタは公式 USB-CAN と違い、
  連続送信で取りこぼす実測がない)。必要なら指定する。
- 実機の公式 USB-CAN(COM7)には使わない(``robstride_at`` を使う)。
"""

from __future__ import annotations

import importlib
import importlib.util
import threading
from collections.abc import Sequence
from dataclasses import replace
from types import ModuleType
from typing import Any

from edulite05.core import clock
from edulite05.core.errors import TransportError

from .base import CanFrame, CanTransport, Direction, TransportCaps, TransportStats
from .pacing import FramePacer

#: GUI の選択肢(registry の options_schema から参照)。python-can の interface 名
KNOWN_INTERFACES = ("slcan", "gs_usb", "candle", "pcan", "socketcan", "virtual")

_EXT_MASK = 0x1FFFFFFF
_STD_MASK = 0x7FF

INSTALL_HINT = "python-can が未導入です(`uv sync --extra pycan` で導入してください)"


def is_available() -> bool:
    """python-can が import できる状態か(import はしない)。"""
    try:
        return importlib.util.find_spec("can") is not None
    except (ImportError, ValueError):
        return False


def _import_can() -> ModuleType:
    """python-can を import する(テストで差し替え可能)。"""
    try:
        return importlib.import_module("can")
    except ImportError as e:
        raise TransportError(f"{INSTALL_HINT}: {e}") from e


def to_message(can_mod: Any, frame: CanFrame) -> Any:
    """CanFrame → can.Message。範囲外は ValueError。"""
    data = bytes(frame.data)
    if len(data) > 8:
        raise ValueError(f"DLC > 8: {len(data)}")
    mask = _EXT_MASK if frame.extended else _STD_MASK
    if not 0 <= frame.can_id <= mask:
        kind = "extended" if frame.extended else "standard"
        raise ValueError(f"{kind} CAN ID out of range: {frame.can_id:#x}")
    return can_mod.Message(
        arbitration_id=frame.can_id,
        is_extended_id=frame.extended,
        data=data,
        is_rx=False,
    )


def from_message(msg: Any, timestamp: float) -> CanFrame | None:
    """can.Message → CanFrame。リモート/エラーフレームは None。"""
    if getattr(msg, "is_error_frame", False) or getattr(msg, "is_remote_frame", False):
        return None
    return CanFrame(
        can_id=int(msg.arbitration_id),
        data=bytes(msg.data)[: int(msg.dlc)] if msg.dlc is not None else bytes(msg.data),
        extended=bool(msg.is_extended_id),
        timestamp=timestamp,
        direction=Direction.RX,
    )


class PythonCanTransport(CanTransport):
    """``can.Bus(interface, channel, bitrate, **kw)`` を包むトランスポート。

    例: ``PythonCanTransport("slcan", "COM5")``(CANable)、
    ``PythonCanTransport("gs_usb", "", index=0)``(candleLight)、
    ``PythonCanTransport("pcan", "PCAN_USBBUS1")``、``PythonCanTransport("virtual", "ch0")``。
    ``recv`` は RX スレッド専用(python-can の ``Bus.recv`` を直接待つ)。
    """

    caps = TransportCaps(
        name="pycan",
        hw_timestamp=False,  # 受信時刻はソフト時刻(clock.now())。HW 時刻は使わない
        bus_state=False,
        standard_frames=True,
        max_fps_hint=5000,  # 1 Mbps・DLC 8 の理論値(約 8000, can_transport.md §4.1)に余裕を見た値
    )

    def __init__(
        self,
        interface: str,
        channel: str | int | None = None,
        bitrate: int = 1_000_000,
        *,
        min_frame_gap_s: float = 0.0,
        **bus_kwargs: Any,
    ) -> None:
        self._pacer = FramePacer(min_frame_gap_s)  # 負値は ValueError
        self.min_frame_gap_s = self._pacer.gap_s
        self.interface = interface
        self.channel = channel
        self.bitrate = int(bitrate)
        self.bus_kwargs = dict(bus_kwargs)
        base = type(self).caps
        gap = self.min_frame_gap_s
        self.caps = replace(base, name=f"pycan:{interface}")
        if gap > 0:
            self.caps = replace(self.caps, max_fps_hint=min(base.max_fps_hint, int(1.0 / gap)))
        self._bus: Any = None
        self._can: Any = None
        self._stats = TransportStats()
        self._stats_lock = threading.Lock()
        self._tx_lock = threading.Lock()

    # -- open / close ------------------------------------------------------
    def open(self) -> None:
        if self._bus is not None:
            return
        can_mod = _import_can()
        kw: dict[str, Any] = dict(self.bus_kwargs)
        kw["interface"] = self.interface
        kw["bitrate"] = self.bitrate
        if self.channel not in (None, ""):
            kw["channel"] = self.channel
        try:
            bus = can_mod.Bus(**kw)
        except Exception as e:  # CanInitializationError / 未実装 interface / OSError など
            raise TransportError(
                f"python-can の interface={self.interface!r} channel={self.channel!r} を"
                f"開けません: {e}"
            ) from e
        self._can = can_mod
        self._bus = bus
        self._pacer.reset()

    def close(self) -> None:
        bus, self._bus = self._bus, None
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:
                pass

    @property
    def is_open(self) -> bool:
        return self._bus is not None

    # -- TX ----------------------------------------------------------------
    def send(self, frame: CanFrame) -> None:
        self._write_frames((frame,))

    def send_many(self, frames: Sequence[CanFrame]) -> None:
        if frames:
            self._write_frames(frames)

    def _write_frames(self, frames: Sequence[CanFrame]) -> None:
        bus, can_mod = self._bus, self._can
        if bus is None:
            raise TransportError("トランスポートが開いていません")
        msgs = [to_message(can_mod, f) for f in frames]  # 失敗時は何も送らない
        sent = 0
        try:
            with self._tx_lock:  # ペーシング込みで排他(バッチの順序も保つ)
                pacer = self._pacer
                for msg in msgs:
                    pacer.wait()
                    try:
                        bus.send(msg)
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
        while True:
            bus = self._bus
            if bus is None:
                raise TransportError("トランスポートが開いていません")
            rem = max(0.0, deadline - clock.now())
            try:
                msg = bus.recv(rem)
            except Exception as e:
                with self._stats_lock:
                    self._stats.io_errors += 1
                if self._bus is None:  # 待機中に close された
                    raise TransportError("トランスポートが開いていません") from e
                raise TransportError(f"受信に失敗しました: {e}") from e
            if msg is None:
                return None
            now = clock.now()
            frame = from_message(msg, now)
            with self._stats_lock:
                if frame is None:
                    self._stats.parse_errors += 1
                else:
                    self._stats.rx_frames += 1
                    self._stats.last_rx = now
            if frame is not None:
                return frame
            if clock.now() >= deadline:
                return None

    def stats(self) -> TransportStats:
        with self._stats_lock:
            return self._stats.snapshot()
