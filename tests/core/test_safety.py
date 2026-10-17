"""SafetyGuard のテスト(phase2.md §1.1)。ランダム指令・状態で不変条件を検査する。"""

from __future__ import annotations

import math
import random

import pytest

from edulite05.core.control.commands import (
    DEFAULT_HOLD_KD,
    DEFAULT_HOLD_KP,
    HoldCmd,
    MitCmd,
    SetpointCmd,
)
from edulite05.core.device.profile import MotorProfile
from edulite05.core.device.state import MotorState
from edulite05.core.errors import SafetyError
from edulite05.core.safety import PowerTier, SafetyGuard, SoftLimits

EPS = 1e-9


@pytest.fixture(scope="module")
def profile() -> MotorProfile:
    return MotorProfile.load_builtin("el05")


def worst(c: MitCmd, s: MotorState) -> float:
    return (
        abs(c.kp * (c.position - s.position))
        + abs(c.kd * (c.velocity - s.velocity))
        + abs(c.torque)
    )


def ms(position: float = 0.0, velocity: float = 0.0, *, multi: float | None = None) -> MotorState:
    """多回転位置が折り返し位置と一致している(±4π の内側にいる)状態。"""
    return MotorState(
        1,
        online=True,
        position=position,
        position_multi=position if multi is None else multi,
        velocity=velocity,
    )


def rand_state(rng: random.Random) -> MotorState:
    p = rng.uniform(-12.57, 12.57)
    return ms(p, rng.uniform(-50, 50))


def rand_cmd(rng: random.Random) -> MitCmd:
    def u(lo: float, hi: float, wide: float = 3.0) -> float:
        # たまに範囲外の値も混ぜる
        if rng.random() < 0.3:
            return rng.uniform(lo * wide, hi * wide)
        return rng.uniform(lo, hi)

    return MitCmd(
        position=u(-12.57, 12.57),
        velocity=u(-50, 50),
        kp=u(0, 500),
        kd=u(0, 5),
        torque=u(-6, 6),
    )


@pytest.mark.parametrize("tier", [PowerTier.LOW, PowerTier.HIGH])
def test_mit_bound_holds_random(profile: MotorProfile, tier: PowerTier) -> None:
    g = SafetyGuard(profile, tier)
    rng = random.Random(1234)
    tq, spd = g.limits.torque, g.limits.speed
    n_changed = 0
    for _ in range(20000):
        s, c = rand_state(rng), rand_cmd(rng)
        out, notes = g.clamp_mit(c, s)
        assert worst(out, s) <= tq * (1 + EPS) + EPS
        assert 0.0 <= out.kp <= g.limits.kp
        assert 0.0 <= out.kd <= g.limits.kd
        assert abs(out.torque) <= tq * (1 + EPS)
        assert abs(out.velocity) <= max(spd, abs(s.velocity) + spd) * (1 + EPS)
        assert abs(out.position) <= 12.57 + EPS
        if out != c:
            n_changed += 1
            assert notes
        # 通知されたフィールドだけが変更されている
        changed = {
            f
            for f in ("position", "velocity", "kp", "kd", "torque")
            if getattr(out, f) != getattr(c, f)
        }
        assert changed == {n.field for n in notes}
        for n in notes:
            assert n.requested == getattr(c, n.field)
            assert n.applied == getattr(out, n.field)
    assert n_changed > 100  # 乱数が制限に当たっている


def test_mit_velocity_cap_is_tier_speed_when_state_at_rest(profile: MotorProfile) -> None:
    g = SafetyGuard(profile)
    out, notes = g.clamp_mit(MitCmd(velocity=30.0, kd=0.1), MotorState(1, online=True))
    assert out.velocity == pytest.approx(5.0)
    assert notes[0].field == "velocity" and notes[0].reason == "tier"


def test_mit_unchanged_within_limits(profile: MotorProfile) -> None:
    g = SafetyGuard(profile)
    rng = random.Random(7)
    n = 0
    for _ in range(5000):
        s = ms(rng.uniform(-3, 3), rng.uniform(-2, 2))
        c = MitCmd(
            position=s.position + rng.uniform(-0.01, 0.01),
            velocity=s.velocity + rng.uniform(-0.1, 0.1),
            kp=rng.uniform(0, 5),
            kd=rng.uniform(0, 0.5),
            torque=rng.uniform(-0.1, 0.1),
        )
        assert worst(c, s) <= 0.5
        out, notes = g.clamp_mit(c, s)
        assert out == c and notes == []
        n += 1
    assert n == 5000


def test_mit_order_ff_then_p_then_v(profile: MotorProfile) -> None:
    g = SafetyGuard(profile)  # LOW: torque 0.5
    s = MotorState(1, online=True, position=0.0, velocity=0.0)
    # t_ff だけ縮めれば足りる: kp*dp = 0.2, kd*dv = 0.1, t_ff 0.4 → 0.2 に縮む
    out, notes = g.clamp_mit(MitCmd(position=0.1, velocity=0.1, kp=2.0, kd=1.0, torque=0.4), s)
    assert out.torque == pytest.approx(0.2)
    assert out.position == 0.1 and out.velocity == 0.1
    assert [n.field for n in notes] == ["torque"] and notes[0].reason == "worst_case"
    # t_ff を 0 にしても足りない: p_set が p 側へ。v は維持
    out, notes = g.clamp_mit(MitCmd(position=1.0, velocity=0.1, kp=2.0, kd=1.0, torque=0.3), s)
    assert out.torque == 0.0
    assert out.position == pytest.approx((0.5 - 0.1) / 2.0)
    assert out.velocity == 0.1
    # kd*dv だけで超過: p_set は p(=state)へ、v_set が寄る
    out, notes = g.clamp_mit(MitCmd(position=1.0, velocity=4.0, kp=2.0, kd=1.0), s)
    assert out.torque == 0.0
    assert out.position == pytest.approx(0.0)
    assert out.velocity == pytest.approx(0.5)
    assert {n.field for n in notes} == {"position", "velocity"}
    # 符号: 負側
    out, _ = g.clamp_mit(MitCmd(position=-1.0, kp=2.0, torque=-0.3), s)
    assert out.torque == 0.0 and out.position == pytest.approx(-0.25)


def test_mit_kp_kd_tier_caps_and_notes(profile: MotorProfile) -> None:
    s = MotorState(1, online=True)
    low, _ = SafetyGuard(profile).clamp_mit(MitCmd(kp=100.0, kd=3.0), s)
    assert (low.kp, low.kd) == (20.0, 1.0)
    g = SafetyGuard(profile, PowerTier.HIGH)
    out, notes = g.clamp_mit(MitCmd(kp=100.0, kd=3.0, torque=2.0), s)
    assert (out.kp, out.kd, out.torque) == (100.0, 3.0, 2.0) and notes == []
    out, notes = g.clamp_mit(MitCmd(kp=900.0, kd=9.0, torque=9.0), s)
    assert out.kp == 500.0 and out.kd == 5.0 and out.torque <= 6.0 + EPS
    assert {n.field for n in notes} >= {"kp", "kd", "torque"}
    out, notes = g.clamp_mit(MitCmd(kp=-1.0), s)
    assert out.kp == 0.0 and notes[0].reason == "range"


def test_mit_soft_limits(profile: MotorProfile) -> None:
    g = SafetyGuard(profile, soft=SoftLimits(-1.0, 2.0))
    s = MotorState(1, online=True, position=0.0)
    out, notes = g.clamp_mit(MitCmd(position=5.0, kp=0.1), s)
    assert out.position == 2.0
    assert notes[0].field == "position" and notes[0].reason == "soft_limit"
    out, _ = g.clamp_mit(MitCmd(position=-5.0, kp=0.1), s)
    assert out.position == -1.0
    out, notes = g.clamp_mit(MitCmd(position=0.5, kp=0.1), s)
    assert out.position == 0.5 and notes == []
    # ソフトリミット + 最悪トルク: 両方の理由が連結される
    out, notes = g.clamp_mit(MitCmd(position=5.0, kp=20.0), s)
    assert out.position == pytest.approx(0.025)
    assert notes[0].requested == 5.0 and notes[0].reason == "soft_limit,worst_case"


def test_mit_soft_limits_random(profile: MotorProfile) -> None:
    soft = SoftLimits(-2.0, 3.0)
    g = SafetyGuard(profile, PowerTier.HIGH, soft)
    rng = random.Random(99)
    for _ in range(5000):
        s = ms(rng.uniform(-2, 3), rng.uniform(-5, 5))
        out, _ = g.clamp_mit(rand_cmd(rng), s)
        assert -2.0 - EPS <= out.position <= 3.0 + EPS  # state が範囲内なら寄せても範囲内
        assert worst(out, s) <= 6.0 * (1 + EPS) + EPS


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_nan_inf_rejected(profile: MotorProfile, bad: float) -> None:
    g = SafetyGuard(profile)
    s = MotorState(1, online=True)
    for field in ("position", "velocity", "kp", "kd", "torque"):
        with pytest.raises(SafetyError):
            g.clamp_mit(MitCmd(**{field: bad}), s)
    with pytest.raises(SafetyError):
        g.clamp_mit(MitCmd(), MotorState(1, online=True, position=bad))
    with pytest.raises(SafetyError):
        g.clamp_mit(MitCmd(), MotorState(1, online=True, velocity=bad))
    for kind in ("velocity", "current", "position"):
        with pytest.raises(SafetyError):
            g.clamp_setpoint(SetpointCmd(kind, bad))  # type: ignore[arg-type]
    with pytest.raises(SafetyError):
        g.clamp_setpoint(SetpointCmd("position", 0.0, speed_limit=bad))
    with pytest.raises(SafetyError):
        g.clamp_setpoint(SetpointCmd("position", 0.0, current_limit=bad))
    with pytest.raises(SafetyError):
        g.clamp_setpoint(SetpointCmd("velocity", 0.0, accel=bad))


def test_setpoint_invalid(profile: MotorProfile) -> None:
    g = SafetyGuard(profile)
    with pytest.raises(SafetyError):
        g.clamp_setpoint(SetpointCmd("torque", 0.0))  # type: ignore[arg-type]
    with pytest.raises(SafetyError):
        g.clamp_setpoint(SetpointCmd("velocity", 0.0, speed_limit=-1.0))
    with pytest.raises(SafetyError):
        g.clamp_setpoint(SetpointCmd("velocity", 0.0, accel=-1.0))
    with pytest.raises(SafetyError):
        SoftLimits(1.0, 0.0)
    with pytest.raises(SafetyError):
        SoftLimits(position_min=math.nan)


def test_setpoint_velocity_current(profile: MotorProfile) -> None:
    g = SafetyGuard(profile)
    out, notes = g.clamp_setpoint(SetpointCmd("velocity", 20.0))
    assert out.value == 5.0 and notes[0].field == "value" and notes[0].reason == "tier"
    out, _ = g.clamp_setpoint(SetpointCmd("velocity", -20.0))
    assert out.value == -5.0
    out, notes = g.clamp_setpoint(SetpointCmd("velocity", 3.0, accel=10.0))
    assert out == SetpointCmd("velocity", 3.0, accel=10.0) and notes == []
    out, notes = g.clamp_setpoint(SetpointCmd("current", -3.0, current_limit=5.0))
    assert out.value == -1.0 and out.current_limit == 1.0
    assert {n.field for n in notes} == {"value", "current_limit"}
    h = SafetyGuard(profile, PowerTier.HIGH)
    out, notes = h.clamp_setpoint(SetpointCmd("current", 20.0))
    assert out.value == 11.0
    out, notes = h.clamp_setpoint(SetpointCmd("velocity", 49.0))
    assert out.value == 49.0 and notes == []


def test_setpoint_position(profile: MotorProfile) -> None:
    g = SafetyGuard(profile, soft=SoftLimits(-1.0, 1.0))
    out, notes = g.clamp_setpoint(SetpointCmd("position", 5.0, speed_limit=9.0))
    assert out.value == 1.0 and out.speed_limit == 5.0
    assert {n.field: n.reason for n in notes} == {"value": "soft_limit", "speed_limit": "tier"}
    out, notes = g.clamp_setpoint(SetpointCmd("position", 0.5, speed_limit=2.0))
    assert out == SetpointCmd("position", 0.5, speed_limit=2.0) and notes == []
    # speed_limit 未指定は None のまま(モータ側 limit_spd が段階の値)
    out, _ = g.clamp_setpoint(SetpointCmd("position", 0.5))
    assert out.speed_limit is None
    # ソフトリミットなしなら位置は素通し(多回転 loc_ref)
    out, notes = SafetyGuard(profile).clamp_setpoint(SetpointCmd("position", 100.0))
    assert out.value == 100.0 and notes == []


def test_motor_side_limits_and_tier_switch(profile: MotorProfile) -> None:
    g = SafetyGuard(profile)
    assert g.tier is PowerTier.LOW
    assert g.limits == profile.tier("low")
    assert g.motor_side_limits() == {"limit_torque": 0.5, "limit_cur": 1.0, "limit_spd": 5.0}
    g.set_tier(PowerTier.HIGH)
    assert g.tier is PowerTier.HIGH and g.limits == profile.tier("high")
    assert g.motor_side_limits() == {"limit_torque": 6.0, "limit_cur": 11.0, "limit_spd": 50.0}
    out, _ = g.clamp_setpoint(SetpointCmd("velocity", 40.0))
    assert out.value == 40.0
    g.set_tier(PowerTier.LOW)
    out, notes = g.clamp_setpoint(SetpointCmd("velocity", 40.0))
    assert out.value == 5.0 and notes
    assert SafetyGuard(profile, PowerTier.HIGH).tier is PowerTier.HIGH


def test_hold_cmd_expansion() -> None:
    m = HoldCmd().to_mit(1.25)
    assert m == MitCmd(1.25, 0.0, DEFAULT_HOLD_KP, DEFAULT_HOLD_KD, 0.0)
    assert HoldCmd(kp=1.0, kd=0.1).to_mit(0.0).kp == 1.0
    assert HoldCmd().to_setpoint(2.0) == SetpointCmd("position", 2.0)


def test_hold_through_guard_is_untouched(profile: MotorProfile) -> None:
    g = SafetyGuard(profile)
    s = ms(1.0)
    out, notes = g.clamp_mit(HoldCmd().to_mit(s.position), s)
    assert notes == [] and out.position == 1.0
