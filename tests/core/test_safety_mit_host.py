"""MIT のホスト側項(負の kd・摩擦補償)と暴走ガード(phase4-fb-X。safety.py の説明)。

フレームの kd は 0–5 で負を送れないため、kd < 0 は PC 側で t_ff += kd·(v_set - v)、
friction_comp は t_ff += c·tanh(v / v_eps) として SafetyGuard が畳み込む。
"""

from __future__ import annotations

import math
import random

import pytest

from edulite05.core.control.commands import MitCmd, SetpointCmd
from edulite05.core.device.profile import MotorProfile
from edulite05.core.device.state import MotorState
from edulite05.core.safety import PowerTier, SafetyGuard

EPS = 1e-9
PROF = MotorProfile.load_builtin("el05")
MH = PROF.mit_host


def st(v: float = 0.0, p: float = 0.0, mid: int = 1) -> MotorState:
    return MotorState(mid, online=True, position=p, position_multi=p, velocity=v)


def worst(c: MitCmd, s: MotorState) -> float:
    return (
        abs(c.kp * (c.position - s.position))
        + abs(c.kd * (c.velocity - s.velocity))
        + abs(c.torque)
    )


def guard(tier: PowerTier = PowerTier.LOW) -> SafetyGuard:
    return SafetyGuard(PROF, tier)


def test_profile_has_host_side_limits() -> None:
    low, high = PROF.tier("low"), PROF.tier("high")
    assert (low.kd_negative_max, high.kd_negative_max) == (0.05, 0.2)
    assert (low.friction_comp_max, high.friction_comp_max) == (0.1, 0.3)
    assert MH.friction_v_eps == pytest.approx(0.05)
    assert 0 < MH.runaway_off < MH.runaway_on < 1 and MH.guard_kd > 0


def test_negative_kd_sends_zero_kd_and_adds_torque() -> None:
    """符号: kd < 0, v_set = 0 のとき t_ff = kd·(0 - v) = |kd|·v(動きを助ける向き)。"""
    g = guard()
    out, notes = g.clamp_mit(MitCmd(kd=-0.05), st(v=2.0))
    assert out.kd == 0.0
    assert out.torque == pytest.approx(-0.05 * (0.0 - 2.0)) and out.torque > 0
    out, _ = g.clamp_mit(MitCmd(kd=-0.05), st(v=-1.0))
    assert out.torque == pytest.approx(-0.05) and out.torque < 0
    assert not notes  # v = 2 は LOW の 0.8·5 = 4 未満。クランプなし


def test_negative_kd_tracks_v_set_difference() -> None:
    out, _ = guard().clamp_mit(MitCmd(velocity=1.0, kd=-0.04), st(v=3.0))
    assert out.torque == pytest.approx(-0.04 * (1.0 - 3.0))


def test_negative_kd_clipped_to_tier_limit() -> None:
    out, notes = guard().clamp_mit(MitCmd(kd=-1.0), st(v=1.0))
    assert out.torque == pytest.approx(0.05 * 1.0)  # -0.05 に切られる
    [n] = [n for n in notes if n.field == "kd"]
    assert (n.requested, n.applied, n.reason) == (-1.0, -0.05, "tier")
    out, _ = guard(PowerTier.HIGH).clamp_mit(MitCmd(kd=-1.0), st(v=1.0))
    assert out.torque == pytest.approx(0.2 * 1.0)


def test_friction_comp_follows_tanh_of_velocity() -> None:
    g = guard()
    for v in (-3.0, -0.02, 0.0, 0.01, 0.2, 3.0):
        out, _ = g.clamp_mit(MitCmd(friction_comp=0.03), st(v=v))
        assert out.torque == pytest.approx(0.03 * math.tanh(v / MH.friction_v_eps))
        assert out.kd == 0.0 and out.friction_comp == 0.0  # フレームには載らない
    out, _ = g.clamp_mit(MitCmd(friction_comp=0.03), st(v=2.0))
    assert out.torque > 0
    out, _ = g.clamp_mit(MitCmd(friction_comp=0.03), st(v=-2.0))
    assert out.torque < 0


def test_friction_comp_clipped_and_negative_rejected() -> None:
    out, notes = guard().clamp_mit(MitCmd(friction_comp=1.0), st(v=3.0))
    assert out.torque == pytest.approx(0.1)  # LOW の上限 0.1
    assert any(n.field == "friction_comp" and n.applied == 0.1 for n in notes)
    out, _ = guard().clamp_mit(MitCmd(friction_comp=-0.5), st(v=3.0))
    assert out.torque == 0.0


def test_host_terms_add_to_user_t_ff() -> None:
    out, _ = guard().clamp_mit(MitCmd(torque=0.1, kd=-0.02, friction_comp=0.03), st(v=1.0))
    expect = 0.1 + 0.02 * 1.0 + 0.03 * math.tanh(1.0 / MH.friction_v_eps)
    assert out.torque == pytest.approx(expect)


def test_positive_kd_unchanged_and_no_host_terms() -> None:
    out, notes = guard().clamp_mit(MitCmd(kp=2.0, kd=0.3, torque=0.05), st(v=1.0))
    assert (out.kp, out.kd, out.torque) == (2.0, 0.3, 0.05) and not notes


def test_total_torque_clipped_to_tier() -> None:
    g = guard()
    out, _ = g.clamp_mit(MitCmd(torque=0.5, friction_comp=0.1, kd=-0.05), st(v=3.0))
    assert out.torque <= 0.5 + EPS


def test_worst_case_bound_with_host_terms_random() -> None:
    """|kp·Δp| + |kd_fw·Δv| + |t_ff_total| <= tier torque(ランダム指令・状態)。"""
    rng = random.Random(7)
    for tier in (PowerTier.LOW, PowerTier.HIGH):
        g = guard(tier)
        t_max = g.limits.torque
        for _ in range(3000):
            s = st(v=rng.uniform(-60, 60), p=rng.uniform(-12.5, 12.5), mid=rng.randrange(1, 5))
            cmd = MitCmd(
                position=rng.uniform(-30, 30),
                velocity=rng.uniform(-100, 100),
                kp=rng.uniform(0, 600),
                kd=rng.uniform(-6, 6),
                torque=rng.uniform(-10, 10),
                friction_comp=rng.uniform(-1, 1),
            )
            out, _ = g.clamp_mit(cmd, s)
            assert out.kd >= 0.0 and out.friction_comp == 0.0
            assert out.kd <= g.limits.kd + EPS
            assert worst(out, s) <= t_max + 1e-6


# -- 暴走ガード --------------------------------------------------------------
def runaway_notes(notes):
    return [n for n in notes if "runaway_guard" in n.reason.split(",")]


def test_runaway_guard_engages_above_80_percent_and_drops_host_terms() -> None:
    g = guard()
    v_max = g.limits.speed
    out, notes = g.clamp_mit(MitCmd(kd=-0.05, friction_comp=0.03), st(v=0.79 * v_max))
    assert not g.runaway_active(1) and out.kd == 0.0 and out.torque > 0 and not runaway_notes(notes)
    out, notes = g.clamp_mit(MitCmd(kd=-0.05, friction_comp=0.03), st(v=0.81 * v_max))
    assert g.runaway_active(1)
    assert out.kd == pytest.approx(MH.guard_kd)  # 負の減衰の代わりに正の減衰で制動
    assert out.torque == 0.0  # ホスト側項は捨てる
    kinds = {n.field for n in runaway_notes(notes)}
    assert kinds == {"kd", "friction_comp"}
    [nk] = [n for n in notes if n.field == "kd"]
    assert (nk.requested, nk.applied) == (-0.05, pytest.approx(MH.guard_kd))


def test_runaway_guard_hysteresis() -> None:
    g = guard()
    v_max = g.limits.speed
    cmd = MitCmd(kd=-0.05, friction_comp=0.03)
    g.clamp_mit(cmd, st(v=0.9 * v_max))
    assert g.runaway_active(1)
    for frac in (0.7, 0.5, 0.41):  # 0.4 を下回るまで保つ
        out, _ = g.clamp_mit(cmd, st(v=frac * v_max))
        assert g.runaway_active(1) and out.kd == pytest.approx(MH.guard_kd) and out.torque == 0.0
    # 負の速度でも対称
    out, _ = g.clamp_mit(cmd, st(v=-0.39 * v_max))
    assert not g.runaway_active(1)
    assert out.kd == 0.0 and out.torque < 0  # ホスト側項が戻る(摩擦補償 + 負の減衰)
    # 解除後は 0.8 を超えるまで再突入しない
    g.clamp_mit(cmd, st(v=0.79 * v_max))
    assert not g.runaway_active(1)
    g.clamp_mit(cmd, st(v=-0.81 * v_max))
    assert g.runaway_active(1)


def test_runaway_guard_keeps_larger_user_kd_and_is_per_motor() -> None:
    g = guard()
    v_max = g.limits.speed
    out, _ = g.clamp_mit(MitCmd(kd=0.5, friction_comp=0.03), st(v=0.9 * v_max, mid=1))
    assert out.kd == 0.5 and out.torque == 0.0  # 既に guard_kd 以上の正の kd はそのまま
    assert g.runaway_active(1) and not g.runaway_active(2)
    g.reset_runaway(1)
    assert not g.runaway_active(1)


def test_runaway_guard_inactive_without_host_terms() -> None:
    """ホスト側項が無い通常の MIT は、高速でも kd を書き換えない。"""
    g = guard()
    out, notes = g.clamp_mit(MitCmd(kp=0.0, kd=0.0), st(v=0.95 * g.limits.speed))
    assert out.kd == 0.0 and not notes and not g.runaway_active(1)
    out, notes = g.clamp_mit(MitCmd(kd=0.05), st(v=0.95 * g.limits.speed))
    assert out.kd == 0.05 and not runaway_notes(notes)


def test_runaway_guard_uses_tier_speed() -> None:
    g = guard(PowerTier.HIGH)  # 速度上限 50 → 40 rad/s で開始
    g.clamp_mit(MitCmd(kd=-0.1), st(v=30.0))
    assert not g.runaway_active(1)
    g.clamp_mit(MitCmd(kd=-0.1), st(v=41.0))
    assert g.runaway_active(1)


def test_guard_bound_holds_when_engaged() -> None:
    g = guard()
    s = st(v=0.95 * g.limits.speed)
    out, _ = g.clamp_mit(MitCmd(kp=10.0, position=3.0, kd=-0.05, friction_comp=0.1), s)
    assert worst(out, s) <= g.limits.torque + 1e-6


# -- 電流モードの暴走ガード(ファームの limit_spd は電流モードを制限しない)----------------
def sp(value: float) -> SetpointCmd:
    return SetpointCmd("current", value)


def test_current_mode_unchanged_below_threshold_and_without_state() -> None:
    g = guard()
    out, notes = g.clamp_setpoint(sp(0.4), st(v=0.79 * g.limits.speed))
    assert out.value == 0.4 and not notes and not g.runaway_active(1)
    out, notes = g.clamp_setpoint(sp(0.4))  # state なし(従来の呼び方)は素通し
    assert out.value == 0.4 and not notes


def test_current_mode_runaway_brakes_with_hysteresis_and_note() -> None:
    g = guard()
    v_max = g.limits.speed
    out, notes = g.clamp_setpoint(sp(0.4), st(v=0.81 * v_max))
    assert g.runaway_active(1) and out.value == pytest.approx(-0.4)  # 制動方向
    [n] = notes
    assert (n.field, n.requested, n.applied) == ("value", 0.4, pytest.approx(-0.4))
    assert "runaway_guard" in n.reason.split(",")
    out, _ = g.clamp_setpoint(sp(0.4), st(v=0.5 * v_max))  # ヒステリシス: 0.4 まで保つ
    assert g.runaway_active(1) and out.value == pytest.approx(-0.4)
    out, _ = g.clamp_setpoint(sp(0.4), st(v=0.39 * v_max))
    assert not g.runaway_active(1) and out.value == 0.4
    out, _ = g.clamp_setpoint(sp(0.4), st(v=-0.9 * v_max))  # 負の速度は逆向きの制動
    assert out.value == pytest.approx(+0.4)


def test_current_mode_brake_is_bounded_by_tier_current() -> None:
    g = guard()
    out, notes = g.clamp_setpoint(sp(11.0), st(v=0.9 * g.limits.speed))
    assert abs(out.value) <= g.limits.current + EPS and out.value < 0
    assert "runaway_guard" in notes[0].reason
    h = guard(PowerTier.HIGH)
    out, _ = h.clamp_setpoint(sp(11.0), st(v=45.0))  # HIGH は速度上限 50 の 0.8 = 40 から
    assert out.value == pytest.approx(-11.0)


def test_current_mode_guard_does_not_touch_other_kinds() -> None:
    g = guard()
    out, notes = g.clamp_setpoint(SetpointCmd("velocity", 3.0), st(v=4.9))
    assert out.value == 3.0 and not notes and not g.runaway_active(1)
