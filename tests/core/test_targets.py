"""位置目標の基準の変換(core/control/targets.py)。"""

from __future__ import annotations

import math
import random

import pytest

from edulite05.core.control.targets import (
    DEFAULT_TARGET_REF,
    REF_MULTI_TURN,
    REF_WITHIN_TURN,
    nearest_target,
    resolve_target,
    wrap_to_pi,
)

TWO_PI = 2 * math.pi


def test_wrap_to_pi_range_and_half_open() -> None:
    assert wrap_to_pi(0.0) == 0.0
    assert wrap_to_pi(math.pi) == pytest.approx(-math.pi)  # [-π, π): π は -π へ
    assert wrap_to_pi(-math.pi) == pytest.approx(-math.pi)
    assert wrap_to_pi(TWO_PI + 0.5) == pytest.approx(0.5)
    assert wrap_to_pi(-TWO_PI - 0.5) == pytest.approx(-0.5)
    for x in (-1000.0, -3.2, 3.2, 1000.0):
        assert -math.pi <= wrap_to_pi(x) < math.pi


def test_default_is_within_turn() -> None:
    assert DEFAULT_TARGET_REF == REF_WITHIN_TURN


def test_nearest_target_example_from_large_position() -> None:
    p = 400.0
    t = nearest_target(1.0, p)
    assert abs(t - p) <= math.pi
    assert wrap_to_pi(t) == pytest.approx(wrap_to_pi(1.0))
    # 400 rad = 63.66 回転 → 400 - 63·2π = 4.1 近傍(+2π 側も候補)。最短の等価角
    assert t == pytest.approx(1.0 + TWO_PI * round((p - 1.0) / TWO_PI))


def test_nearest_target_near_zero_is_identity() -> None:
    assert nearest_target(0.7, 0.0) == pytest.approx(0.7)
    assert nearest_target(-0.7, 0.2) == pytest.approx(-0.7)


def test_nearest_target_chooses_shortest_direction() -> None:
    # 現在 +3.0、目標 -3.0(1 回転内)→ 順方向 +3.28 の方が近い(逆回り -6.0 ではなく)
    t = nearest_target(-3.0, 3.0)
    assert t == pytest.approx(-3.0 + TWO_PI)
    assert t - 3.0 == pytest.approx(TWO_PI - 6.0)
    # 負側の対称
    assert nearest_target(3.0, -3.0) == pytest.approx(3.0 - TWO_PI)


def test_nearest_target_properties_random() -> None:
    rng = random.Random(3)
    for _ in range(2000):
        p = rng.uniform(-1000, 1000)
        theta = rng.uniform(-math.pi, math.pi)
        t = nearest_target(theta, p)
        assert -math.pi - 1e-9 <= t - p < math.pi + 1e-9
        d = (t - theta) / TWO_PI
        assert d == pytest.approx(round(d), abs=1e-9)  # θ と整数回転だけ違う


def test_resolve_target_modes() -> None:
    assert resolve_target(2.0, 400.0, REF_MULTI_TURN) == 2.0
    assert resolve_target(2.0, 400.0, REF_WITHIN_TURN) == nearest_target(2.0, 400.0)
    with pytest.raises(ValueError):
        resolve_target(2.0, 0.0, "bogus")
