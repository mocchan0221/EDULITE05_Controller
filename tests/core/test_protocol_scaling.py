"""スケーリング(el05_protocol.md §1.5)。"""

import math

import pytest

from edulite05.core.errors import ProtocolError
from edulite05.core.protocol.scaling import float_to_uint, uint_to_float

P = (-12.57, 12.57)
T = (-6.0, 6.0)


def test_zero_is_0x7fff_not_0x8000():
    # 32767.5 を切り捨てて 0x7FFF(§1.5 / 落とし穴)
    assert float_to_uint(0.0, *P, 16) == 0x7FFF
    assert float_to_uint(0.0, *T, 16) == 0x7FFF
    assert float_to_uint(0.0, -50.0, 50.0, 16) == 0x7FFF


def test_kp_kd_examples():
    # §2.2 例: Kp=1 → 0x0083、Kd=1 → 0x3333
    assert float_to_uint(1.0, 0.0, 500.0, 16) == 0x0083
    assert float_to_uint(1.0, 0.0, 5.0, 16) == 0x3333


def test_clamp():
    assert float_to_uint(-100.0, *T, 16) == 0
    assert float_to_uint(100.0, *T, 16) == 0xFFFF
    assert float_to_uint(-6.0, *T, 16) == 0
    assert float_to_uint(6.0, *T, 16) == 0xFFFF
    assert float_to_uint(12.57, *P, 16) == 0xFFFF
    assert float_to_uint(-math.inf, *T, 16) == 0
    assert float_to_uint(math.inf, *T, 16) == 0xFFFF


def test_nan_rejected():
    with pytest.raises(ProtocolError):
        float_to_uint(math.nan, *T, 16)


def test_12bit_denominator_is_4095():
    assert float_to_uint(6.0, *T, 12) == 4095
    assert uint_to_float(4095, *T, 12) == pytest.approx(6.0)


def test_uint_to_float_endpoints():
    assert uint_to_float(0, *P, 16) == pytest.approx(-12.57)
    assert uint_to_float(0xFFFF, *P, 16) == pytest.approx(12.57)
    assert uint_to_float(0x7FFF, *P, 16) == pytest.approx(0.0, abs=1e-3)
    # §2.3 の式: u*25.14/65535 - 12.57
    assert uint_to_float(1000, *P, 16) == pytest.approx(1000 * 25.14 / 65535 - 12.57)
    assert uint_to_float(1000, -50.0, 50.0, 16) == pytest.approx(1000 * 100 / 65535 - 50)
    assert uint_to_float(1000, *T, 16) == pytest.approx(1000 * 12 / 65535 - 6)


@pytest.mark.parametrize("x", [-12.0, -3.3, 0.0, 1.0, 5.55, 12.5])
def test_roundtrip_within_one_lsb(x):
    lsb = 25.14 / 65535
    assert abs(uint_to_float(float_to_uint(x, *P, 16), *P, 16) - x) <= lsb
