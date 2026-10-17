"""float と符号なし整数のスケーリング(el05_protocol.md §1.5)。"""

from __future__ import annotations

import math

from edulite05.core.errors import ProtocolError


def float_to_uint(x: float, lo: float, hi: float, bits: int) -> int:
    """`u = (int)((clamp(x) - lo) * (2^bits - 1) / (hi - lo))`(切り捨て、範囲外はクランプ)。

    切り捨てが仕様どおり: 0 は 16bit で 0x7FFF(32767.5 → 32767)になる。
    """
    if math.isnan(x):
        raise ProtocolError("NaN はスケーリングできません")
    umax = (1 << bits) - 1
    if x <= lo:
        return 0
    if x >= hi:
        return umax
    return min(umax, int((x - lo) * umax / (hi - lo)))


def uint_to_float(u: int, lo: float, hi: float, bits: int) -> float:
    """`x = u * (hi - lo) / (2^bits - 1) + lo`。"""
    umax = (1 << bits) - 1
    return (u & umax) * (hi - lo) / umax + lo
