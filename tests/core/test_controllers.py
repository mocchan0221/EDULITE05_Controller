"""組込みコントローラの step / on_stop の出力(ControlLoop なし。phase2.md §1.3)。"""

from __future__ import annotations

import math

import pytest

from edulite05.core.control.commands import HoldCmd, MitCmd, SetpointCmd
from edulite05.core.control.controller import Controller
from edulite05.core.control.controllers import BUILTIN_CONTROLLERS, CONTROLLERS
from edulite05.core.control.controllers.current import Current
from edulite05.core.control.controllers.jog import Jog
from edulite05.core.control.controllers.mit import MitDirect
from edulite05.core.control.controllers.position import Csp, Pp
from edulite05.core.control.controllers.tests import SineTest, StepTest
from edulite05.core.control.controllers.velocity import Velocity
from edulite05.core.control.controllers.zero_return import ZeroReturn
from edulite05.core.control.params import (
    PARAMS_BY_CONTROLLER,
    CspParams,
    CurrentParams,
    JogParams,
    MitParams,
    PpParams,
    SineParams,
    StepParams,
    VelocityParams,
    ZeroReturnParams,
)
from edulite05.core.device.state import MotorState
from edulite05.core.protocol.types import RunMode

S = {"motor": MotorState(1, online=True, last_rx=1.0, position=0.5, position_multi=0.5)}


def first(c: Controller, t: float = 0.0, states=S):
    c.on_start(states)
    return c.step(t, 0.005, states)["motor"]


# -- 登録・メタデータ ----------------------------------------------------------
def test_registry_and_metadata() -> None:
    assert set(CONTROLLERS) == {
        "mit",
        "jog",
        "velocity",
        "current",
        "csp",
        "pp",
        "sine",
        "step",
        "zero_return",
        "bilateral",
    }
    assert len(BUILTIN_CONTROLLERS) == 10
    for name, cls in CONTROLLERS.items():
        assert cls.name == name and cls.label == f"controller.{name}"
        assert cls.slots == (("leader", "follower") if name == "bilateral" else ("motor",))
        if name in PARAMS_BY_CONTROLLER:
            assert cls.Params is PARAMS_BY_CONTROLLER[name]
    assert MitDirect.run_mode == RunMode.OPERATION
    assert Jog.run_mode == RunMode.VELOCITY == Velocity.run_mode
    assert Current.run_mode == RunMode.CURRENT
    assert Csp.run_mode == RunMode.CSP == ZeroReturn.run_mode
    assert Pp.run_mode == RunMode.PP


def test_default_params_and_type_check() -> None:
    assert isinstance(Velocity().params, VelocityParams)
    with pytest.raises(TypeError):
        Velocity(CspParams())
    with pytest.raises(TypeError):
        Velocity().update_params(CspParams())


def test_update_params_copies() -> None:
    p = VelocityParams(velocity=1.0)
    c = Velocity(p)
    p.velocity = 99.0  # 渡した後に呼び出し側が書き換えても影響しない
    assert first(c).value == 1.0
    q = VelocityParams(velocity=2.0)
    c.update_params(q)
    q.velocity = 99.0
    assert c.step(0.1, 0.005, S)["motor"].value == 2.0


# -- 個別 ----------------------------------------------------------------------
def test_mit_direct() -> None:
    c = MitDirect(MitParams(position=1.0, velocity=2.0, kp=3.0, kd=0.4, torque_ff=0.2))
    assert first(c) == MitCmd(1.0, 2.0, 3.0, 0.4, 0.2)
    assert c.on_stop(S) == {"motor": HoldCmd()}
    c.update_params(MitParams(position=-1.0))
    assert c.step(0.0, 0.005, S)["motor"] == MitCmd(-1.0, 0.0, 0.0, 0.0, 0.0)


def test_velocity_and_current() -> None:
    c = Velocity(VelocityParams(velocity=3.0, current_limit=0.8, accel=15.0))
    assert first(c) == SetpointCmd("velocity", 3.0, current_limit=0.8, accel=15.0)
    stop = c.on_stop(S)["motor"]
    assert stop == SetpointCmd("velocity", 0.0, current_limit=0.8, accel=15.0)

    c2 = Current(CurrentParams(iq=0.4))
    assert first(c2) == SetpointCmd("current", 0.4)
    assert c2.on_stop(S)["motor"] == SetpointCmd("current", 0.0)


def test_csp_pp_zero_return() -> None:
    c = Csp(CspParams(position=1.5, speed_limit=2.5))
    assert first(c) == SetpointCmd("position", 1.5, speed_limit=2.5)
    assert c.on_stop(S) == {"motor": HoldCmd()}

    c = Pp(PpParams(position=-1.0, velocity=3.0, accel=7.0))
    assert first(c) == SetpointCmd("position", -1.0, speed_limit=3.0, accel=7.0)
    assert c.on_stop(S) == {"motor": HoldCmd()}

    c = ZeroReturn(ZeroReturnParams(speed_limit=0.7))
    assert first(c) == SetpointCmd("position", 0.0, speed_limit=0.7)
    assert c.on_stop(S) == {"motor": HoldCmd()}


# -- Jog -----------------------------------------------------------------------
def test_jog_direction_changes() -> None:
    c = Jog(JogParams(speed=1.5, accel=30.0, current_limit=0.6))
    c.on_start(S)

    def cmd() -> SetpointCmd:
        return c.step(0.0, 0.005, S)["motor"]

    assert cmd() == SetpointCmd("velocity", 0.0, current_limit=0.6, accel=30.0)
    c.set_direction(+1)
    assert cmd().value == 1.5
    c.set_direction(-1)
    assert cmd().value == -1.5
    c.set_direction(0)
    assert cmd().value == 0.0
    c.set_direction(7)  # 符号に丸める
    assert c.direction == 1 and cmd().value == 1.5
    c.update_params(JogParams(speed=0.5))
    assert cmd().value == 0.5
    c.set_direction(-1)
    assert c.on_stop(S)["motor"].value == 0.0 and c.direction == 0


# -- 試験信号 --------------------------------------------------------------------
def test_sine_velocity_waveform() -> None:
    c = SineTest(SineParams(target="velocity", amplitude=2.0, frequency=0.5, offset=0.5))
    assert c.run_mode == RunMode.VELOCITY
    c.on_start(S)
    for t in (0.0, 0.25, 0.5, 1.0, 1.75):
        v = c.step(t, 0.005, S)["motor"]
        assert v.kind == "velocity"
        assert v.value == pytest.approx(0.5 + 2.0 * math.sin(2 * math.pi * 0.5 * t))
    assert c.step(1.0, 0.005, S)["motor"].value == pytest.approx(0.5, abs=1e-9)  # 零交差
    assert c.step(0.5, 0.005, S)["motor"].value != c.step(0.75, 0.005, S)["motor"].value
    assert c.on_stop(S)["motor"] == SetpointCmd("velocity", 0.0)


def test_sine_targets_choose_run_mode_and_command() -> None:
    expect = {
        "position": (RunMode.CSP, SetpointCmd),
        "velocity": (RunMode.VELOCITY, SetpointCmd),
        "current": (RunMode.CURRENT, SetpointCmd),
        "torque": (RunMode.OPERATION, MitCmd),
    }
    for target, (mode, typ) in expect.items():
        c = SineTest(SineParams(target=target, amplitude=1.0, frequency=1.0))
        assert c.run_mode == mode
        assert isinstance(first(c, 0.25), typ)
    cur = first(SineTest(SineParams(target="current", amplitude=1.0, frequency=1.0)), 0.25)
    assert cur.kind == "current"
    # torque は kp = kd = 0 の t_ff
    tq = first(SineTest(SineParams(target="torque", amplitude=0.3, frequency=1.0)), 0.25)
    assert (tq.kp, tq.kd) == (0.0, 0.0) and tq.torque == pytest.approx(0.3)
    with pytest.raises(ValueError):
        SineTest(SineParams(target="nonsense"))


def test_sine_position_is_relative_to_start_and_limits_speed() -> None:
    c = SineTest(SineParams(target="position", amplitude=1.0, frequency=0.5, offset=0.1))
    c.on_start(S)  # 開始位置 0.5
    cmd = c.step(1.0, 0.005, S)["motor"]  # sin(π) = 0
    assert cmd.kind == "position"
    assert cmd.value == pytest.approx(0.5 + 0.1, abs=1e-9)
    assert cmd.speed_limit == pytest.approx(1.5 * 2 * math.pi * 0.5 * 1.0)
    assert c.on_stop(S) == {"motor": HoldCmd()}


def test_sine_target_change_only_before_start() -> None:
    c = SineTest(SineParams(target="velocity"))
    c.update_params(SineParams(target="current"))  # 開始前は可: run_mode も追従
    assert c.run_mode == RunMode.CURRENT
    c.on_start(S)
    c.update_params(SineParams(target="current", amplitude=2.0))  # 同じ target の値変更は可
    with pytest.raises(ValueError, match="target"):
        c.update_params(SineParams(target="velocity"))
    c.on_stop(S)
    c.update_params(SineParams(target="velocity"))  # 停止後はまた変更できる
    assert c.run_mode == RunMode.VELOCITY


def test_step_waveform() -> None:
    c = StepTest(StepParams(target="velocity", low=0.0, high=1.5, period=2.0))
    c.on_start(S)

    def val(t: float) -> float:
        return c.step(t, 0.005, S)["motor"].value

    assert [val(t) for t in (0.0, 0.5, 0.99)] == [0.0, 0.0, 0.0]
    assert [val(t) for t in (1.0, 1.5, 1.99)] == [1.5, 1.5, 1.5]
    assert [val(t) for t in (2.0, 2.9, 3.0, 4.2)] == [0.0, 0.0, 1.5, 0.0]
    c.update_params(StepParams(target="velocity", low=-1.0, high=1.0, period=0.5))
    assert val(0.1) == -1.0 and val(0.3) == 1.0


def test_step_targets() -> None:
    c = StepTest(StepParams(target="position", low=0.0, high=1.0, period=2.0))
    assert c.run_mode == RunMode.CSP
    c.on_start(S)
    lo = c.step(0.1, 0.005, S)["motor"]
    hi = c.step(1.1, 0.005, S)["motor"]
    assert (lo.value, hi.value) == (pytest.approx(0.5), pytest.approx(1.5))  # 開始位置 0.5 + 値
    assert hi.speed_limit >= 1.0
    cur = StepTest(StepParams(target="current", low=0.0, high=0.3, period=1.0))
    assert cur.run_mode == RunMode.CURRENT
    assert first(cur, 0.6) == SetpointCmd("current", 0.3)
    assert cur.on_stop(S)["motor"] == SetpointCmd("current", 0.0)
    assert StepTest(StepParams(target="torque")).run_mode == RunMode.OPERATION
