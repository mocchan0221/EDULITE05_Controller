"""DemoBridge の挙動(Bridge 契約の範囲)。状態モデルは ui_design.md §3・§11。"""

from __future__ import annotations

import pytest

from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.viewmodels import ConnectionInfo, FrameEntry, LogEntry, ParamValue
from edulite05.core.control.params import (
    CspParams,
    JogParams,
    MitParams,
    VelocityParams,
)


@pytest.fixture
def bridge(qtbot):
    b = DemoBridge(kind="mock", demo_motion=False, seed=0)
    b.start()
    yield b
    b.shutdown()


def ui_states(b: DemoBridge) -> dict[int, str]:
    return {i: s.ui_state for i, s in b.states().items()}


def test_initial_state(bridge) -> None:
    assert bridge.backend_kind == "mock"
    assert bridge.connection_info().connected
    assert [m.id for m in bridge.motors()] == [1, 2]
    assert bridge.power_tier() == "low"
    assert not bridge.estop_latched()
    lim = bridge.power_limits()
    assert (lim.torque, lim.current, lim.speed, lim.kp, lim.kd) == (0.5, 1.0, 5.0, 20.0, 1.0)
    names = {c.name for c in bridge.available_channels()}
    assert {"m1.pos", "m1.cmd_vel", "m2.torque", "m2.temp"} <= names
    assert ui_states(bridge) == {1: "disabled", 2: "disabled"}


def test_state_model_enable_start_stop_disable(bridge, qtbot) -> None:
    """disabled →(有効化)enabled →(運転開始)running →(運転停止)enabled →(無効化)disabled。"""
    bridge.enable([1, 2], 2)
    assert ui_states(bridge) == {1: "enabled", 2: "enabled"}
    assert bridge.states()[1].run_mode == 2 and bridge.states()[1].mode == "motor"
    bridge.start_controller("velocity", {"motor": 1}, VelocityParams(velocity=2.0))
    qtbot.wait(300)
    s = bridge.states()[1]
    assert s.ui_state == "running" and s.controller == "velocity"
    assert bridge.states()[2].ui_state == "enabled"  # 他のモータは有効のまま
    bridge.stop_controller("velocity", {"motor": 1})
    s = bridge.states()[1]
    assert s.ui_state == "enabled" and s.controller is None and s.mode == "motor"
    bridge.disable([1])
    assert ui_states(bridge) == {1: "disabled", 2: "enabled"}
    assert bridge.states()[1].mode == "reset"


def test_start_controller_requires_enabled(bridge, qtbot) -> None:
    logs: list[LogEntry] = []
    bridge.log_message.connect(logs.append)
    bridge.start_controller("mit", {"motor": 1}, MitParams())
    assert ui_states(bridge)[1] == "disabled"  # 無効のモータでは運転開始できない
    assert any(e.level == "warn" for e in logs)


def test_stop_controller_holds_instead_of_coasting(bridge, qtbot) -> None:
    """運転停止: 速度は 0 へ、位置は現在位置保持へ(有効のまま)。"""
    bridge.enable([1], 2)
    bridge.start_controller("velocity", {"motor": 1}, VelocityParams(velocity=3.0))
    qtbot.wait(900)
    assert bridge.states()[1].velocity > 1.5
    bridge.stop_controller("velocity", {"motor": 1})
    qtbot.wait(1200)
    s = bridge.states()[1]
    assert s.ui_state == "enabled" and abs(s.velocity) < 0.3
    # 位置モード: 運転停止後は現在位置に留まる(目標へ向かい続けない)
    bridge.disable([1])
    bridge.enable([1], 5)
    bridge.start_controller("csp", {"motor": 1}, CspParams(position=4.0, speed_limit=2.0))
    qtbot.wait(600)
    pos_at_stop = bridge.states()[1].position_multi
    bridge.stop_controller("csp", {"motor": 1})
    qtbot.wait(1000)
    assert abs(bridge.states()[1].position_multi - pos_at_stop) < 0.5
    assert bridge.states()[1].ui_state == "enabled"


def test_estop_latch_and_release(bridge, qtbot) -> None:
    changes: list[bool] = []
    bridge.estop_changed.connect(changes.append)
    bridge.enable([1, 2], 2)
    bridge.start_controller("velocity", {"motor": 1}, VelocityParams(velocity=2.0))
    qtbot.wait(100)
    bridge.estop()
    assert bridge.estop_latched() and changes == [True]
    assert ui_states(bridge) == {1: "disabled", 2: "disabled"}
    # ラッチ中は有効化も HIGH も受け付けない
    bridge.enable([1], 2)
    assert ui_states(bridge)[1] == "disabled"
    assert bridge.set_power_tier("high") is False
    bridge.estop()  # 二重押ししても通知は 1 回(ラッチは冪等)
    assert changes == [True]
    bridge.release_estop()
    assert not bridge.estop_latched() and changes == [True, False]
    assert ui_states(bridge) == {1: "disabled", 2: "disabled"}  # 解除しても自動再開しない
    bridge.enable([1], 2)
    assert ui_states(bridge)[1] == "enabled"


def test_estop_returns_to_low_tier(bridge) -> None:
    tiers: list[str] = []
    bridge.power_tier_changed.connect(tiers.append)
    assert bridge.set_power_tier("high") is True
    bridge.estop()
    assert bridge.power_tier() == "low" and tiers == ["high", "low"]
    assert bridge.estop_latched()


def test_tier_rules(bridge, qtbot) -> None:
    changes: list[str] = []
    bridge.power_tier_changed.connect(changes.append)
    bridge.enable([1], 2)
    assert bridge.set_power_tier("high") is False  # 有効のモータがあると HIGH 不可
    bridge.disable([1])
    assert bridge.set_power_tier("high") is True
    assert bridge.power_limits().tier == "high"
    bridge.enable([2], 2)
    assert bridge.set_power_tier("low") is True  # 低出力へはいつでも
    assert changes == ["high", "low"]


def test_low_tier_clamps_mit_torque(bridge, qtbot) -> None:
    # 低出力の上限(0.5 N·m)を超える指令は実際には出力されない
    bridge.enable([1], 0)
    bridge.start_controller("mit", {"motor": 1}, MitParams(position=10.0, kp=500.0, kd=0.0,
                                                           torque_ff=6.0))
    qtbot.wait(800)
    _, tq = bridge.scope_data(["m1.cmd_torque"], 0.5)["m1.cmd_torque"]
    assert max(abs(tq)) <= 0.5 + 1e-6


def test_jog_runs_while_held_and_holds_after_release(bridge, qtbot) -> None:
    bridge.enable([1], 2)
    bridge.start_controller("jog", {"motor": 1}, JogParams(speed=-2.0))
    qtbot.wait(1200)
    assert bridge.states()[1].velocity < -1.0
    bridge.stop_controller("jog", {"motor": 1})
    qtbot.wait(800)
    s = bridge.states()[1]
    assert s.ui_state == "enabled" and s.controller is None and abs(s.velocity) < 0.3


def test_fault_state_details_and_clear(bridge, qtbot) -> None:
    bridge.inject_fault(1, "overtemp")
    s = bridge.states()[1]
    assert s.ui_state == "fault" and "overtemp" in s.faults
    bridge.enable([1], 2)  # 故障中は有効化できない
    assert bridge.states()[1].ui_state == "fault"
    bridge.inject_fault(1, "overtemp")
    bridge.inject_fault(1, "encoder")
    details = {d.key: d for d in bridge.fault_details(1)}
    assert set(details) == {"overtemp", "encoder"}
    d = details["overtemp"]
    assert d.count == 2 and d.measured == pytest.approx(138.2) and d.threshold == 135.0
    assert d.unit == "℃" and d.causes and d.remedy and d.description and d.first_t <= d.last_t
    assert bridge.fault_details(2) == []
    bridge.clear_fault([1])
    assert bridge.fault_details(1) == [] and bridge.states()[1].ui_state == "disabled"
    bridge.enable([1], 2)
    qtbot.wait(50)
    assert bridge.states()[1].ui_state == "enabled"


def test_comm_loss_and_reconnect(bridge, qtbot) -> None:
    infos: list[ConnectionInfo] = []
    bridge.connection_changed.connect(infos.append)
    bridge.enable([1], 2)
    bridge.inject_comm_loss()
    info = bridge.connection_info()
    assert not info.connected and info.lost
    assert ui_states(bridge) == {1: "offline", 2: "offline"}
    assert bridge.power_tier() == "low"
    bridge.reconnect()
    qtbot.wait(1500)
    info = bridge.connection_info()
    assert info.connected and not info.lost
    assert ui_states(bridge) == {1: "disabled", 2: "disabled"}  # 再接続後も自動再開しない
    assert infos[0].lost and infos[-1].connected


def test_start_offline_connect_scans(qtbot) -> None:
    b = DemoBridge(kind="mock", demo_motion=False, seed=0, start_offline=True)
    b.start()
    try:
        assert not b.connection_info().connected and b.motors() == [] and b.states() == {}
        b.connect_adapter({"adapter": "simulator"})
        qtbot.wait(1500)  # 接続 → 自動スキャン
        assert b.connection_info().connected
        assert [m.id for m in b.motors()] == [1, 2]
        assert ui_states(b) == {1: "disabled", 2: "disabled"}
    finally:
        b.shutdown()


def test_params_roundtrip_and_dirty(bridge, qtbot) -> None:
    got: list[tuple[int, list[ParamValue]]] = []
    bridge.params_updated.connect(lambda mid, vals: got.append((mid, vals)))
    bridge.write_param(1, 0x7017, 3.0)
    mid, vals = got[-1]
    v = {p.index: p for p in vals}
    assert mid == 1 and v[0x7017].value == 3.0 and v[0x7017].dirty
    bridge.save_params(1)
    v = {p.index: p for p in got[-1][1]}
    assert v[0x7017].dirty  # 保存しても 0x70xx は電源断で戻る(変更済みのまま)
    assert len(bridge.param_definitions()) == len(vals) >= 25


def test_logs_and_frames_flow(bridge, qtbot) -> None:
    logs: list[LogEntry] = []
    frames: list[FrameEntry] = []
    bridge.log_message.connect(logs.append)
    bridge.frame_logged.connect(frames.append)
    bridge.enable([1], 0)
    bridge.start_controller("mit", {"motor": 1}, MitParams())
    qtbot.wait(600)
    assert any(e.level == "info" for e in logs)
    assert {f.direction for f in frames} == {"TX", "RX"}
    type1 = [f for f in frames if (f.can_id >> 24) & 0x1F == 1]
    assert type1 and len(type1[0].data) == 8 and type1[0].decoded_text


def test_disconnect_reconnect(bridge, qtbot) -> None:
    bridge.disconnect_adapter()
    assert not bridge.connection_info().connected and bridge.motors() == []
    bridge.connect_adapter({"adapter": "simulator"})
    qtbot.wait(1500)
    assert bridge.connection_info().connected
    assert len(bridge.motors()) == 2
