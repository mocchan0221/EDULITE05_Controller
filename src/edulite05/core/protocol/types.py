"""値の型・モード・レンジ(el05_protocol.md §2.9-2.10, §3.1, §2.3)。"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum, IntEnum

from edulite05.core.errors import ProtocolError


class DType(Enum):
    """type 17/18 のパラメータ値の型。値は TOML での表記。"""

    UINT8 = "uint8"
    UINT16 = "uint16"
    UINT32 = "uint32"
    INT16 = "int16"
    FLOAT = "float"

    @property
    def size(self) -> int:
        return _SIZES[self]

    @property
    def is_integer(self) -> bool:
        return self is not DType.FLOAT

    @classmethod
    def from_name(cls, name: str) -> DType:
        try:
            return cls(name.lower())
        except ValueError:
            raise ProtocolError(f"未知のデータ型: {name!r}") from None


_SIZES = {DType.UINT8: 1, DType.UINT16: 2, DType.UINT32: 4, DType.INT16: 2, DType.FLOAT: 4}
_INT_LIMITS = {
    DType.UINT8: (0, 0xFF),
    DType.UINT16: (0, 0xFFFF),
    DType.UINT32: (0, 0xFFFFFFFF),
    DType.INT16: (-0x8000, 0x7FFF),
}
_INT_FMT = {DType.UINT8: "<B", DType.UINT16: "<H", DType.UINT32: "<I", DType.INT16: "<h"}


def pack_value(dtype: DType, value: int | float) -> bytes:
    """B4–B7 の 4 バイトにする。リトルエンディアン、B4 から詰める(uint8 は B4 のみ)。

    el05_protocol.md §2.10: uint8 は B4 に整数値(float で書かない)。
    """
    if dtype is DType.FLOAT:
        try:
            return struct.pack("<f", float(value))
        except OverflowError as e:
            raise ProtocolError(f"float に変換できません: {value!r}") from e
    iv = int(value)
    if iv != value:
        raise ProtocolError(f"{dtype.value} に小数は書けません: {value!r}")
    lo, hi = _INT_LIMITS[dtype]
    if not lo <= iv <= hi:
        raise ProtocolError(f"{dtype.value} の範囲外: {value!r}")
    return struct.pack(_INT_FMT[dtype], iv).ljust(4, b"\x00")


def unpack_value(dtype: DType, b4: bytes) -> int | float:
    """B4–B7 の 4 バイトから値を取り出す(型に応じた幅だけ読む)。§2.9"""
    if len(b4) < dtype.size:
        raise ProtocolError(f"{dtype.value} には {dtype.size} バイト必要です")
    if dtype is DType.FLOAT:
        return float(struct.unpack_from("<f", b4)[0])
    return int(struct.unpack_from(_INT_FMT[dtype], b4)[0])


class RunMode(IntEnum):
    """run_mode(0x7005)。el05_protocol.md §3.1(4 は未定義)。"""

    OPERATION = 0
    PP = 1
    VELOCITY = 2
    CURRENT = 3
    CSP = 5


class MotorMode(IntEnum):
    """type 2 の ID bit23..22(§2.3)。"""

    RESET = 0
    CALI = 1
    MOTOR = 2


@dataclass(frozen=True, slots=True)
class Ranges:
    """type 1/2 のスケーリング範囲(各 (lo, hi))。値はプロファイルから(§1.5)。"""

    position: tuple[float, float]
    velocity: tuple[float, float]
    torque: tuple[float, float]
    kp: tuple[float, float]
    kd: tuple[float, float]
