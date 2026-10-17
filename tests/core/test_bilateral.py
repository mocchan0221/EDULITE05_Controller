"""Bilateral の step / on_start / on_stop の出力(ControlLoop なし。phase4_5.md §O)。"""

from __future__ import annotations

import pytest

from edulite05.core.control.commands import HoldCmd, MitCmd
from edulite05.core.control.controllers import BUILTIN_CONTROLLERS, CONTROLLERS, Bilateral
from edulite05.core.control.params import BilateralParams
from edulite05.core.device.state import MotorState
from edulite05.core.protocol.types import RunMode


def st(
    mid: int,
    pos: float,
    vel: float = 0.0,
    tq: float = 0.0,
    *,
    multi: float | None = None,
    fed: bool = True,
) -> MotorState:
    return MotorState(
        mid,
        online=fed,
        last_rx=1.0 if fed else None,
        position=pos,
        position_multi=pos if multi is None else multi,
        velocity=vel,
        torque=tq,
    )


def states(lp: float, fp: float, **kw: float) -> dict[str, MotorState]:
    return {
        "leader": st(1, lp, kw.get("lv", 0.0), kw.get("lt", 0.0)),
        "follower": st(2, fp, kw.get("fv", 0.0), kw.get("ft", 0.0)),
    }


def run(c: Bilateral, s: dict[str, MotorState], t: float = 0.0, dt: float = 0.005):
    out = c.step(t, dt, s)
    assert set(out) == {"leader", "follower"}
    return out["leader"], out["follower"]


def test_registered_and_metadata() -> None:
    assert CONTROLLERS["bilateral"] is Bilateral and Bilateral in BUILTIN_CONTROLLERS
    assert Bilateral.slots == ("leader", "follower")
    assert Bilateral.run_mode is RunMode.OPERATION
    assert Bilateral.label == "controller.bilateral" and Bilateral.Params is BilateralParams
    p = BilateralParams()
    assert (p.stiffness, p.damping, p.mode, p.offset_mode) == (5.0, 0.2, "symmetric", "keep")


def test_symmetric_targets_are_the_others_position_and_velocity() -> None:
    c = Bilateral()
    s = states(0.0, 0.0, lv=1.0, fv=-2.0)
    c.on_start(s)
    ld, fl = run(c, s)
    s2 = states(0.4, -0.1, lv=1.0, fv=-2.0)
    ld, fl = run(c, s2, 0.005)
    # follower → leader の位置・速度 / leader → follower
    assert fl.position == pytest.approx(0.4) and ld.position == pytest.approx(-0.1)
    assert (fl.velocity, fl.kp, fl.kd, fl.torque) == (1.0, 5.0, 0.2, 0.0)
    assert (ld.velocity, ld.kp, ld.kd, ld.torque) == (-2.0, 5.0, 0.2, 0.0)


def test_keep_offset_preserves_initial_difference() -> None:
    c = Bilateral()
    s = states(0.2, 0.7)  # d0 = 0.5
    c.on_start(s)
    ld, fl = run(c, s)
    # 開始直後は引き寄せない: 目標 = 現在位置(誤差 0)
    assert fl.position == pytest.approx(0.7) and ld.position == pytest.approx(0.2)
    # leader が動くと follower の目標は leader + d0、leader の目標は follower - d0
    ld, fl = run(c, states(1.0, 0.7), 0.005)
    assert fl.position == pytest.approx(1.5)
    assert ld.position == pytest.approx(0.7 - 0.5)
    assert c.offset_now == pytest.approx(0.5)


def test_offset_param_added_in_keep_mode_with_speed_limit() -> None:
    c = Bilateral(BilateralParams(offset=0.3, converge_speed=1.0))
    s = states(0.0, 0.0)
    c.on_start(s)
    _, fl = run(c, s, 0.0, 0.1)  # 1 rad/s × 0.1 s = 0.1 rad まで
    assert fl.position == pytest.approx(0.1)
    for k in range(10):
        _, fl = run(c, s, 0.1 * (k + 1), 0.1)
    assert c.offset_now == pytest.approx(0.3)  # 超えない
    assert fl.position == pytest.approx(0.3)


def test_zero_offset_mode_converges_with_speed_limit() -> None:
    c = Bilateral(BilateralParams(offset_mode="zero", converge_speed=0.5))
    s = states(0.0, 1.0)  # d0 = 1
    c.on_start(s)
    ld, fl = run(c, s, 0.0, 0.1)
    assert c.offset_now == pytest.approx(0.95)
    assert fl.position == pytest.approx(0.95)  # leader(0) + d
    assert ld.position == pytest.approx(1.0 - 0.95)
    for k in range(40):
        run(c, s, 0.1 * (k + 1), 0.1)
    assert c.offset_now == 0.0  # 0 へ収束し、行き過ぎない
    run(c, s, 5.0, 0.1)
    assert c.offset_now == 0.0


def test_zero_converge_speed_freezes_offset() -> None:
    c = Bilateral(BilateralParams(offset_mode="zero", converge_speed=0.0))
    s = states(0.0, 1.0)
    c.on_start(s)
    run(c, s, 0.0, 0.1)
    assert c.offset_now == pytest.approx(1.0)


def test_master_slave_leader_is_free() -> None:
    c = Bilateral(BilateralParams(mode="master_slave", leader_damping=0.07, force_ff=1.0))
    s = states(0.3, 0.3, lv=2.0, ft=0.4, lt=0.2)
    c.on_start(s)
    ld, fl = run(c, s)
    assert ld == MitCmd(0.3, 0.0, 0.0, 0.07, 0.0)  # kp=0, t_ff=0(力順送も掛けない)
    assert fl.kp == 5.0 and fl.kd == 0.2 and fl.velocity == 2.0
    assert fl.torque == pytest.approx(0.2)  # follower へ leader のトルク × 係数


def test_force_feedforward_in_symmetric() -> None:
    c = Bilateral(BilateralParams(force_ff=0.5))
    s = states(0.0, 0.0, lt=0.4, ft=-0.2)
    c.on_start(s)
    ld, fl = run(c, s)
    assert fl.torque == pytest.approx(0.2) and ld.torque == pytest.approx(-0.1)
    ld, fl = run(Bilateral(), s)  # 既定は無効
    assert ld.torque == 0.0 and fl.torque == 0.0


def test_position_wrap_boundary_uses_multi_turn_error() -> None:
    # follower が +4π 近傍で周期ループした直後でも、誤差は多回転角で評価する
    c = Bilateral()
    s0 = {
        "leader": st(1, 12.5, multi=12.5),
        "follower": st(2, 12.5, multi=12.5),
    }
    c.on_start(s0)
    s1 = {
        "leader": st(1, -12.5, multi=12.6),  # 周期ループ(-4π 側へ)
        "follower": st(2, 12.5, multi=12.5),
    }
    ld, fl = run(c, s1, 0.005)
    # p_set は多回転座標(折り返し位置 ±12.5 ではなく 12.5〜12.6)。8π の跳びが出ない
    assert fl.position == pytest.approx(12.6)  # leader(12.6) + d(0)
    assert ld.position == pytest.approx(12.5)  # follower(12.5) - d(0)
    assert abs(fl.position - s1["follower"].position_multi) == pytest.approx(0.1)
    assert abs(ld.position - s1["leader"].position_multi) == pytest.approx(0.1)


def test_no_feedback_sends_nothing_then_latches_offset_later() -> None:
    c = Bilateral()
    cold = {"leader": st(1, 0.0, fed=False), "follower": st(2, 0.5)}
    c.on_start(cold)
    assert c.step(0.0, 0.005, cold) == {}
    ld, fl = run(c, states(0.0, 0.5), 0.005)  # 最初の有効な step で差(0.5)を記録
    assert c.offset_now == pytest.approx(0.5)
    assert fl.position == pytest.approx(0.5) and ld.position == pytest.approx(0.0)


def test_update_params_live_and_validation() -> None:
    c = Bilateral()
    s = states(0.0, 0.0)
    c.on_start(s)
    c.update_params(BilateralParams(stiffness=9.0, damping=0.4))
    ld, fl = run(c, s)
    assert (fl.kp, fl.kd) == (9.0, 0.4)
    with pytest.raises(ValueError):
        c.update_params(BilateralParams(mode="bogus"))
    with pytest.raises(ValueError):
        Bilateral(BilateralParams(offset_mode="bogus"))
    with pytest.raises(TypeError):
        c.update_params(object())  # type: ignore[arg-type]


def test_on_stop_holds_both_and_restart_relatches() -> None:
    c = Bilateral()
    c.on_start(states(0.0, 0.5))
    out = c.on_stop(states(0.0, 0.5))
    assert out == {"leader": HoldCmd(), "follower": HoldCmd()}
    c.on_start(states(1.0, 1.2))
    run(c, states(1.0, 1.2))
    assert c.offset_now == pytest.approx(0.2)
