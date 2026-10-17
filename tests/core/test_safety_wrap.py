"""SafetyGuard.clamp_mit の位置の折り返し規則(phase4-fix-W。el05_protocol.md §8.0、Q-P12)。

type 2 の位置は ±4π で折り返し、多回転位置(mechPos)は折り返さない。MIT の p_set をモータが
どちらと比べるか不明("unknown")な間は、両方の解釈で最悪トルクを見積もり、±4π 付近では kp = 0。
"""

from __future__ import annotations

import dataclasses
import math
import random

import pytest

from edulite05.core.control.commands import HoldCmd, MitCmd
from edulite05.core.device.profile import MIT_FRAMES, MotorProfile
from edulite05.core.device.state import MotorState
from edulite05.core.safety import PowerTier, SafetyGuard

EPS = 1e-9
PERIOD = 8 * math.pi
BASE = MotorProfile.load_builtin("el05")
LIMIT = BASE.position.wrap_limit  # 4π - wrap_margin


def prof(frame: str) -> MotorProfile:
    return dataclasses.replace(BASE, position=dataclasses.replace(BASE.position, mit_frame=frame))


def wrap(p: float) -> float:
    return (p + PERIOD / 2) % PERIOD - PERIOD / 2


def state(multi: float, v: float = 0.0) -> MotorState:
    """多回転位置 `multi` のモータ(折り返し位置は wrap(multi))。"""
    return MotorState(
        1, online=True, position=wrap(multi), position_multi=multi, velocity=v, multi_valid=True
    )


def kp_note(notes):
    return [n for n in notes if n.field == "kp" and "position_wrap" in n.reason.split(",")]


def test_profile_has_position_section() -> None:
    assert BASE.position.mit_frame == "unknown"
    assert BASE.position.wrap_half == pytest.approx(4 * math.pi)
    assert 0.0 < BASE.position.wrap_margin < 1.0
    assert BASE.position.period == pytest.approx(PERIOD)
    assert set(MIT_FRAMES) == {"unknown", "wrapped", "multi"}


@pytest.mark.parametrize("frame", ["unknown", "multi"])
def test_kp_zero_beyond_wrap_margin(frame: str) -> None:
    g = SafetyGuard(prof(frame))
    s = state(20.0)  # 折り返し位置は約 -5.13
    out, notes = g.clamp_mit(MitCmd(position=20.0, velocity=0.0, kp=5.0, kd=0.3), s)
    assert out.kp == 0.0 and out.kd == 0.3 and out.torque == 0.0
    assert len(kp_note(notes)) == 1 and kp_note(notes)[0].requested == 5.0
    # 減衰と t_ff は(上限の範囲で)そのまま
    out, _ = g.clamp_mit(MitCmd(position=20.0, kp=5.0, kd=0.3, torque=0.2), s)
    assert (out.kp, out.kd, out.torque) == (0.0, 0.3, 0.2)
    # t_ff は段階の上限(LOW 0.5)で縮められる
    out, notes = g.clamp_mit(MitCmd(position=20.0, kp=5.0, kd=0.3, torque=3.0), s)
    assert out.kp == 0.0 and out.torque == pytest.approx(0.5)


@pytest.mark.parametrize("frame", ["unknown", "multi"])
@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_margin_boundary(frame: str, sign: float) -> None:
    g = SafetyGuard(prof(frame))
    inside = state(sign * (LIMIT - 0.01))
    out, notes = g.clamp_mit(MitCmd(position=inside.position, kp=5.0, kd=0.3), inside)
    assert out.kp == 5.0 and kp_note(notes) == []
    outside = state(sign * (LIMIT + 0.01))
    out, notes = g.clamp_mit(MitCmd(position=outside.position, kp=5.0, kd=0.3), outside)
    assert out.kp == 0.0 and len(kp_note(notes)) == 1


def test_wrapped_frame_keeps_kp_and_converts_position() -> None:
    g = SafetyGuard(prof("wrapped"))
    s = state(20.0)  # 折り返し位置 20 - 8π
    out, notes = g.clamp_mit(MitCmd(position=20.0, kp=5.0, kd=0.3), s)
    assert kp_note(notes) == [] and out.kp == 5.0
    assert out.position == pytest.approx(wrap(20.0))  # コマンド座標 = 折り返し位置(誤差 0)
    assert notes == []
    # 目標が多回転で +0.05 先 → コマンド座標でも +0.05 先
    out, _ = g.clamp_mit(MitCmd(position=20.05, kp=5.0, kd=0.3), s)
    assert out.position - s.position == pytest.approx(0.05)


def test_inside_region_behaves_like_before_in_every_frame() -> None:
    cmd = MitCmd(position=1.02, velocity=0.1, kp=5.0, kd=0.3, torque=0.01)
    s = state(1.0, 0.1)
    for frame in MIT_FRAMES:
        out, notes = SafetyGuard(prof(frame)).clamp_mit(cmd, s)
        assert out == cmd and notes == []


@pytest.mark.parametrize("frame", ["unknown", "multi"])
def test_multi_frame_target_beyond_range_is_clipped_not_wrapped(frame: str) -> None:
    g = SafetyGuard(prof(frame))
    s = state(3.0)
    out, notes = g.clamp_mit(MitCmd(position=30.0, kp=0.01), s)
    assert out.position == pytest.approx(12.57)  # p_set のレンジ(±4π)
    assert notes[0].field == "position"


def test_unknown_uses_worse_of_both_interpretations() -> None:
    # 折り返し位置と多回転位置が食い違う(ここでは人工的に 2 rad)状態: 内側の領域でも安全側
    g = SafetyGuard(prof("unknown"))
    s = MotorState(1, online=True, position=1.0, position_multi=3.0, velocity=0.0)
    # 両方から等距離(±1)の p_set = 2.0、kp = 5 → 最悪 5 N·m > 0.5。寄せても両立しない → kp = 0
    out, notes = g.clamp_mit(MitCmd(position=2.0, kp=5.0), s)
    assert out.kp == 0.0 and len(kp_note(notes)) == 1
    # 該当する方だけを見る設定: multi なら p_multi=3.0 基準、wrapped なら p_w=1.0 基準
    out, notes = SafetyGuard(prof("multi")).clamp_mit(MitCmd(position=3.05, kp=5.0), s)
    assert out.position == pytest.approx(3.05) and out.kp == 5.0
    out, notes = SafetyGuard(prof("wrapped")).clamp_mit(MitCmd(position=3.05, kp=5.0), s)
    assert out.kp == 5.0  # 変換: p_w + (3.05 - 3.0) = 1.05
    assert out.position == pytest.approx(1.05)


@pytest.mark.parametrize("frame", list(MIT_FRAMES))
@pytest.mark.parametrize("tier", [PowerTier.LOW, PowerTier.HIGH])
def test_worst_case_torque_bound_random(frame: str, tier: PowerTier) -> None:
    """どの座標系の解釈でも、送出する指令の最悪トルクは段階の上限以下。"""
    g = SafetyGuard(prof(frame), tier)
    t_max = g.limits.torque
    rng = random.Random(f"{frame}-{tier.value}")
    for _ in range(4000):
        s = state(rng.uniform(-40, 40), rng.uniform(-10, 10))
        c = MitCmd(
            position=rng.uniform(-40, 40),
            velocity=rng.uniform(-60, 60),
            kp=rng.uniform(0, 600),
            kd=rng.uniform(0, 6),
            torque=rng.uniform(-8, 8),
        )
        out, _ = g.clamp_mit(c, s)
        refs = {
            "wrapped": [s.position],
            "multi": [s.position_multi],
            "unknown": [s.position, s.position_multi],
        }[frame]
        for ref in refs:
            w = (
                abs(out.kp * (out.position - ref))
                + abs(out.kd * (out.velocity - s.velocity))
                + abs(out.torque)
            )
            assert w <= t_max * (1 + 1e-9) + 1e-9
        if frame != "wrapped" and abs(s.position_multi) > LIMIT:
            assert out.kp == 0.0
        assert -12.57 - EPS <= out.position <= 12.57 + EPS


@pytest.mark.parametrize("frame", list(MIT_FRAMES))
def test_hold_beyond_four_pi(frame: str) -> None:
    """HoldCmd(多回転座標の現在位置)を通しても、8π の引き戻しにならない。"""
    g = SafetyGuard(prof(frame))
    s = state(20.0)
    out, notes = g.clamp_mit(HoldCmd().to_mit(s.position_multi), s)
    refs = {"wrapped": [s.position], "multi": [s.position_multi], "unknown": [s.position]}[frame]
    # 多回転位置(20)を p_set にしてもコマンド座標では ±4π に収まる
    assert abs(out.position) <= 12.57 + EPS
    for ref in refs:
        assert abs(out.kp * (out.position - ref)) <= 0.5 + EPS
    if frame == "wrapped":
        assert out.kp > 0.0 and out.position == pytest.approx(s.position)
    else:
        assert out.kp == 0.0 and out.kd > 0.0 and len(kp_note(notes)) == 1


@pytest.mark.parametrize("frame", ["unknown", "multi"])
def test_unknown_frame_old_behavior_would_have_yanked(frame: str) -> None:
    """旧実装の再現: p_set = 折り返し位置のまま MIT で保持すると、多回転基準では 8π の誤差。"""
    g = SafetyGuard(prof(frame))
    s = state(20.0)
    out, _ = g.clamp_mit(MitCmd(position=s.position, kp=5.0, kd=0.3), s)  # 旧 HoldCmd.to_mit
    # kp = 0 なので、多回転位置との差が 8π あってもトルクは出ない
    assert abs(out.kp * (out.position - s.position_multi)) == 0.0
