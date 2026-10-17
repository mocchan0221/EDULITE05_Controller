"""消費電力の概算(core/telemetry/power.py。係数は [power_estimate]・未較正)。"""

from __future__ import annotations

import math

import pytest

from edulite05.core.device.profile import MotorProfile, PowerEstimateSpec
from edulite05.core.telemetry import STANDARD_CHANNELS
from edulite05.core.telemetry.power import (
    SMOOTH_TAU_S,
    LowPass,
    PowerEstimate,
    estimate_power,
    smooth_estimate,
)

SPEC = PowerEstimateSpec(kt=0.69, r_phase=1.36, p_base=1.0)


def test_profile_section_and_defaults() -> None:
    p = MotorProfile.load_builtin("el05").power_estimate
    assert (p.kt, p.r_phase, p.p_base) == (0.65, 1.36, 1.0)
    assert PowerEstimateSpec() == p  # DemoBridge 等が使う既定と一致


def test_idle_is_base_only() -> None:
    e = estimate_power(0.0, 0.0, SPEC)
    assert (e.p_mech, e.p_cu, e.p_base, e.p_in) == (0.0, 0.0, 1.0, 1.0)
    assert not e.regenerating


def test_components_and_sum() -> None:
    e = estimate_power(0.5, 4.0, SPEC)
    assert e.p_mech == pytest.approx(2.0)
    assert e.p_cu == pytest.approx(1.5 * 1.36 * (0.5 / 0.69) ** 2)
    assert e.p_in == pytest.approx(e.p_mech + e.p_cu + e.p_base)


def test_copper_loss_is_even_in_torque_and_mech_is_signed() -> None:
    a, b = estimate_power(0.3, 2.0, SPEC), estimate_power(-0.3, 2.0, SPEC)
    assert a.p_cu == pytest.approx(b.p_cu)
    assert a.p_mech == pytest.approx(0.6) and b.p_mech == pytest.approx(-0.6)
    c = estimate_power(-0.3, -2.0, SPEC)  # 逆回転で逆トルクも正の機械出力
    assert c.p_mech == pytest.approx(0.6)


def test_regeneration_is_negative_total() -> None:
    e = estimate_power(-2.0, 10.0, SPEC)  # ブレーキ: τ·ω = -20 W
    assert e.p_in < 0 and e.regenerating
    assert e.p_in == pytest.approx(-20.0 + e.p_cu + 1.0)


def test_non_finite_inputs_are_zero() -> None:
    e = estimate_power(float("nan"), float("inf"), SPEC)
    assert e.p_in == pytest.approx(1.0) and math.isfinite(e.p_cu)


def test_lowpass_first_value_and_time_constant() -> None:
    f = LowPass(0.3)
    assert f.update(0.0, 10.0) == 10.0  # 最初の値はそのまま
    y = f.update(0.3, 0.0)  # 1 時定数でステップの 63.2 % 近づく
    assert y == pytest.approx(10.0 * math.exp(-1.0), rel=1e-6)
    f.reset()
    assert f.value is None
    with pytest.raises(ValueError):
        LowPass(0.0)
    assert SMOOTH_TAU_S == pytest.approx(0.3)


def test_lowpass_damps_200hz_ripple() -> None:
    f = LowPass()
    out = [f.update(i * 0.005, 5.0 + (2.0 if i % 2 else -2.0)) for i in range(400)]
    assert max(out[300:]) - min(out[300:]) < 0.05  # ±2 W の揺れがほぼ消える(収束後)
    assert abs(out[-1] - 5.0) < 0.1


def test_smooth_estimate_total_is_sum_of_smoothed_parts() -> None:
    fs = (LowPass(), LowPass(), LowPass())
    smooth_estimate(fs, 0.0, estimate_power(0.0, 0.0, SPEC))
    s = smooth_estimate(fs, 0.05, estimate_power(0.5, 4.0, SPEC))
    assert isinstance(s, PowerEstimate)
    assert s.p_in == pytest.approx(s.p_mech + s.p_cu + s.p_base)
    assert 1.0 < s.p_in < estimate_power(0.5, 4.0, SPEC).p_in


def test_power_est_is_a_standard_derived_channel() -> None:
    row = next(r for r in STANDARD_CHANNELS if r[0] == "power_est")
    assert row[1] == "W" and row[2] == "derived"
