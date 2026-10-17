"""値のパック/アンパック・列挙(el05_protocol.md §2.9/§2.10, §3.1)。"""

import math
import struct

import pytest

from edulite05.core.errors import ProtocolError
from edulite05.core.protocol.types import (
    DType,
    MotorMode,
    Ranges,
    RunMode,
    pack_value,
    unpack_value,
)


def test_pack_uint8_goes_to_b4():
    assert pack_value(DType.UINT8, 5) == bytes.fromhex("05000000")
    assert pack_value(DType.UINT8, 255) == bytes.fromhex("FF000000")


def test_pack_uint16_le():
    assert pack_value(DType.UINT16, 0x1234) == bytes.fromhex("34120000")


def test_pack_uint32_le():
    assert pack_value(DType.UINT32, 20000) == bytes.fromhex("204E0000")
    assert pack_value(DType.UINT32, 0xFFFFFFFF) == bytes.fromhex("FFFFFFFF")


def test_pack_int16_le():
    assert pack_value(DType.INT16, -1) == bytes.fromhex("FFFF0000")
    assert pack_value(DType.INT16, 337) == bytes.fromhex("51010000")


def test_pack_float_le():
    # §2.10 の例
    assert pack_value(DType.FLOAT, 1.0) == bytes.fromhex("0000803F")
    assert pack_value(DType.FLOAT, 5.0) == bytes.fromhex("0000A040")
    assert pack_value(DType.FLOAT, 30.0) == bytes.fromhex("0000F041")  # §2.9 の例


@pytest.mark.parametrize(
    ("dtype", "bad"),
    [
        (DType.UINT8, 256), (DType.UINT8, -1), (DType.UINT16, 65536), (DType.UINT32, 2**32),
        (DType.INT16, 32768), (DType.INT16, -32769), (DType.UINT8, 1.5),
    ],
)
def test_pack_out_of_range(dtype, bad):
    with pytest.raises(ProtocolError):
        pack_value(dtype, bad)


def test_pack_integer_valued_float_accepted_for_int_dtype():
    assert pack_value(DType.UINT8, 5.0) == bytes.fromhex("05000000")


@pytest.mark.parametrize(
    ("dtype", "value"),
    [
        (DType.UINT8, 0), (DType.UINT8, 255), (DType.UINT16, 12345), (DType.UINT32, 4000000000),
        (DType.INT16, -32768), (DType.INT16, 32767),
    ],
)
def test_int_roundtrip(dtype, value):
    assert unpack_value(dtype, pack_value(dtype, value)) == value


def test_float_roundtrip():
    for v in (0.0, -11.0, 0.17, 1e-3, 24.195):
        out = unpack_value(DType.FLOAT, pack_value(DType.FLOAT, v))
        assert out == pytest.approx(v, rel=1e-6, abs=1e-9)
    assert math.isinf(unpack_value(DType.FLOAT, struct.pack("<f", math.inf)))


def test_unpack_reads_only_dtype_width():
    # uint8 は B4 のみを読む(B5..B7 のゴミは無視)
    assert unpack_value(DType.UINT8, bytes.fromhex("02AABBCC")) == 2
    assert unpack_value(DType.UINT16, bytes.fromhex("0300AABB")) == 3


def test_unpack_short_buffer():
    with pytest.raises(ProtocolError):
        unpack_value(DType.UINT32, b"\x00\x00")


def test_dtype_names_and_size():
    assert DType.from_name("uint8") is DType.UINT8
    assert DType.from_name("FLOAT") is DType.FLOAT
    order = (DType.UINT8, DType.UINT16, DType.UINT32, DType.INT16, DType.FLOAT)
    assert [d.size for d in order] == [1, 2, 4, 2, 4]
    with pytest.raises(ProtocolError):
        DType.from_name("double")


def test_enums():
    assert [int(m) for m in RunMode] == [0, 1, 2, 3, 5]
    assert RunMode.CSP == 5 and RunMode.PP == 1 and RunMode.VELOCITY == 2
    assert [int(m) for m in MotorMode] == [0, 1, 2]


def test_ranges_is_frozen():
    r = Ranges((-1, 1), (-1, 1), (-1, 1), (0, 1), (0, 1))
    with pytest.raises(AttributeError):
        r.position = (0, 0)  # type: ignore[misc]
