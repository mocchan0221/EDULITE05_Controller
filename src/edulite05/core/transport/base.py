"""CAN トランスポート抽象(phase1.md §1.1、can_transport.md §5.2-5.3)。"""

from __future__ import annotations

import dataclasses
import enum
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass


class Direction(enum.Enum):
    RX = "rx"
    TX = "tx"


@dataclass(frozen=True, slots=True)
class CanFrame:
    can_id: int  # 29bit(extended)/ 11bit(standard)
    data: bytes  # 0..8 バイト
    extended: bool = True
    timestamp: float = 0.0  # core.clock.now()
    channel: int = 0
    direction: Direction = Direction.RX

    def with_meta(
        self, *, timestamp: float | None = None, direction: Direction | None = None
    ) -> CanFrame:
        changes: dict[str, object] = {}
        if timestamp is not None:
            changes["timestamp"] = timestamp
        if direction is not None:
            changes["direction"] = direction
        return dataclasses.replace(self, **changes)


@dataclass(frozen=True, slots=True)
class TransportCaps:
    name: str
    hw_timestamp: bool
    bus_state: bool
    standard_frames: bool
    max_fps_hint: int


@dataclass(slots=True)
class TransportStats:
    """実装側が更新する。読む側は stats() が返すコピーを使う。"""

    tx_frames: int = 0
    rx_frames: int = 0
    parse_errors: int = 0
    resyncs: int = 0
    io_errors: int = 0
    last_rx: float | None = None

    def snapshot(self) -> TransportStats:
        return dataclasses.replace(self)


class CanTransport(ABC):
    caps: TransportCaps

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @abstractmethod
    def send(self, frame: CanFrame) -> None: ...

    def send_many(self, frames: Sequence[CanFrame]) -> None:
        """既定は send の繰り返し(AT 実装もペーシングのため 1 フレームずつ書く)。"""
        for f in frames:
            self.send(f)

    @abstractmethod
    def recv(self, timeout: float) -> CanFrame | None:
        """RX スレッド専用。timeout 秒で受信なしなら None。"""

    @abstractmethod
    def stats(self) -> TransportStats:
        """統計のコピーを返す。"""
