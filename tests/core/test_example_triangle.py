"""サンプルプラグイン plugins/example_triangle(Qt なし): 波形・activate・SafetyGuard 越しの動作。"""

from __future__ import annotations

import math
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from edulite05.core import clock
from edulite05.core.control.commands import MitCmd, SetpointCmd
from edulite05.core.control.controllers import CONTROLLERS
from edulite05.core.control.params import PARAMS_BY_CONTROLLER
from edulite05.core.control.service import SERVICE_CLAMPED, MotorService, UiState
from edulite05.core.device.profile import MotorProfile
from edulite05.core.device.state import MotorState
from edulite05.core.protocol.types import RunMode
from edulite05.core.session import Session
from edulite05.plugin_api import PluginManager, PluginStatus
from edulite05.sim.transport import SimTransport, make_motors

ROOT = Path(__file__).resolve().parents[2]
PROFILE = MotorProfile.load_builtin("el05")
S = {"motor": MotorState(1, online=True, last_rx=1.0, position=0.5, position_multi=0.5)}


@pytest.fixture
def mgr_gui(tmp_path: Path):
    shutil.copytree(
        ROOT / "plugins" / "example_triangle", tmp_path / "plugins" / "example_triangle",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    m = PluginManager(project_root=tmp_path, builtin=[], entry_points=False, gui=True)
    m.load_all()
    yield m
    m.shutdown()


@pytest.fixture
def mgr_cli(tmp_path: Path):
    shutil.copytree(
        ROOT / "plugins" / "example_triangle", tmp_path / "plugins" / "example_triangle",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    m = PluginManager(project_root=tmp_path, builtin=[], entry_points=False, gui=False)
    m.load_all()
    yield m
    m.shutdown()


def test_activate_with_gui_registers_controller_mode_and_page(mgr_gui: PluginManager) -> None:
    rec = mgr_gui.record("example_triangle")
    assert rec is not None and rec.status is PluginStatus.ACTIVE and rec.version == "0.1.0"
    assert CONTROLLERS["triangle"].name == "triangle" and "triangle" in PARAMS_BY_CONTROLLER
    ui = mgr_gui.ui
    assert [m.id for m in ui.modes()] == ["triangle"]
    mode = ui.modes()[0]
    assert mode.group == "その他" and mode.title == "三角波"
    assert mode.controllers[0].target_fields == ("target", "amplitude", "frequency", "offset")
    pages = ui.pages()
    # スクリプト(50)の後ろ・設定(90)の前(Ctrl+5 をスクリプトから奪わない)
    assert [(p.id, p.icon, p.order) for p in pages] == [("example_triangle", "activity", 60)]


def test_activate_without_gui_registers_controller_only(mgr_cli: PluginManager) -> None:
    assert mgr_cli.record("example_triangle").active and mgr_cli.ui is None
    assert "triangle" in CONTROLLERS
    assert "triangle" not in PARAMS_BY_CONTROLLER  # フォームは UI(add_mode)が登録する


def test_shutdown_removes_registrations(mgr_gui: PluginManager) -> None:
    mgr_gui.shutdown()
    assert "triangle" not in CONTROLLERS and "triangle" not in PARAMS_BY_CONTROLLER
    assert mgr_gui.ui.modes() == [] and mgr_gui.ui.pages() == []


def test_triangle_waveform(mgr_cli: PluginManager) -> None:
    cls = CONTROLLERS["triangle"]
    p = cls.Params(target="velocity", amplitude=2.0, frequency=1.0, offset=0.5)
    v = cls.value_at
    assert v(p, 0.0) == pytest.approx(0.5)  # offset から上り始める
    assert v(p, 0.125) == pytest.approx(0.5 + 1.0)  # 傾き一定(直線): 1/8 周期で半分
    assert v(p, 0.25) == pytest.approx(0.5 + 2.0)  # 頂点
    assert v(p, 0.5) == pytest.approx(0.5)
    assert v(p, 0.75) == pytest.approx(0.5 - 2.0)  # 谷
    assert v(p, 1.0) == pytest.approx(0.5)
    # 三角波であって正弦波ではない(1/16 周期で 1/4 の高さ)
    assert v(p, 1 / 16) == pytest.approx(0.5 + 0.5)
    assert abs(math.sin(2 * math.pi / 16) * 2.0 - 0.5) > 0.1


def test_triangle_commands_and_run_mode(mgr_cli: PluginManager) -> None:
    cls = CONTROLLERS["triangle"]
    expect = {"position": RunMode.CSP, "velocity": RunMode.VELOCITY,
              "current": RunMode.CURRENT, "torque": RunMode.OPERATION}
    for target, mode in expect.items():
        assert cls(cls.Params(target=target)).run_mode is mode
    c = cls(cls.Params(target="velocity", amplitude=1.0, frequency=1.0))
    c.on_start(S)
    cmd = c.step(0.25, 0.005, S)["motor"]
    assert isinstance(cmd, SetpointCmd) and cmd.kind == "velocity"
    assert cmd.value == pytest.approx(1.0)
    c = cls(cls.Params(target="position", amplitude=1.0, frequency=0.5))
    c.on_start(S)  # 位置は開始位置からの相対
    pos = c.step(0.5, 0.005, S)["motor"]
    assert isinstance(pos, SetpointCmd) and pos.value == pytest.approx(0.5 + 1.0)
    c = cls(cls.Params(target="torque", amplitude=0.3, frequency=1.0))
    c.on_start(S)
    tq = c.step(0.25, 0.005, S)["motor"]
    assert isinstance(tq, MitCmd) and tq.kp == 0.0 and tq.kd == 0.0
    assert tq.torque == pytest.approx(0.3)
    with pytest.raises(ValueError):
        cls(cls.Params(target="nonsense"))


def wait_until(pred: Callable[[], object], timeout: float = 4.0) -> bool:
    end = clock.now() + timeout
    while clock.now() < end:
        if pred():
            return True
        time.sleep(0.005)
    return bool(pred())


def test_triangle_through_motorservice_is_clamped_by_safetyguard(mgr_cli: PluginManager) -> None:
    """振幅 8 rad/s を要求しても、MotorService → SafetyGuard が LOW の 5 rad/s 以内にする。"""
    sim = SimTransport(make_motors(1, profile=PROFILE))
    session = Session(sim, PROFILE)
    session.open()
    svc = MotorService(session)
    clamps: list = []
    session.events.subscribe(SERVICE_CLAMPED, clamps.append)
    mid = sim.motors[0].motor_id
    session.motor(mid)
    svc.open()
    try:
        assert wait_until(lambda: svc.ui_state(mid) is UiState.DISABLED)
        cls = CONTROLLERS["triangle"]
        svc.enable(mid, cls.run_mode).result(5.0)
        svc.start([mid], cls(cls.Params(target="velocity", amplitude=8.0, frequency=0.5)))
        vs: list[float] = []
        end = clock.now() + 4.2
        while clock.now() < end:
            vs.append(sim.motor(mid).velocity)
            time.sleep(0.005)
        assert max(vs) > 1.0 and min(vs) < -1.0
        assert max(abs(v) for v in vs) <= 5.3
        assert any(n.field == "value" for p in clamps for n in p["notes"])
        svc.stop_motion([mid])
        svc.disable([mid]).result(3.0)
    finally:
        svc.close()
        session.close()
