"""Session / Motor を SimTransport(担当 C のフレームレベルシミュレータ)に繋ぐ結合テスト。"""

from __future__ import annotations

import pytest
from fakes import wait_until

from edulite05.core.device.profile import MotorProfile
from edulite05.core.events import MOTOR_FAULT
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.session import Session
from edulite05.sim.motor_model import BENCH_UID
from edulite05.sim.transport import SimTransport, make_motors

PROFILE = MotorProfile.load_builtin("el05")


def make(n: int = 3) -> tuple[Session, SimTransport]:
    sim = SimTransport(make_motors(n, profile=PROFILE))
    return Session(sim, PROFILE), sim


def test_scan_finds_all_sim_motors_including_127() -> None:
    s, sim = make(3)  # ID 127, 1, 2
    with s:
        motors = s.scan(gap_s=0.0, settle_s=0.2).result(10.0)
        assert [m.motor_id for m in motors] == [1, 2, 127]
        assert s.motors[127].uid == BENCH_UID


def test_info_version_and_all_params() -> None:
    s, _ = make(1)
    with s:
        m = s.scan(range(120, 128), gap_s=0.0, settle_s=0.2).result(10.0)[0]
        assert m.get_uid().result(2.0).uid == BENCH_UID
        info = m.read_version().result(2.0)
        assert info.version == "10.5.0.2"
        assert m.state.mode is None  # バージョン応答で状態は作られない
        res = m.read_all_params().result(20.0)
        assert set(res) == {p.index for p in PROFILE.params}
        assert res[0x7005].ok and res[0x7005].value == 0


def test_enable_disable_run_mode_flow() -> None:
    s, sim = make(1)
    with s:
        m = s.motor(127)
        m.disable().result(2.0)
        assert m.state.mode == MotorMode.RESET
        m.set_run_mode(RunMode.CSP).result(2.0)
        assert m.read_param("run_mode").result(2.0).value == 5
        assert m.state.run_mode == RunMode.CSP
        assert m.enable().result(2.0).mode == MotorMode.MOTOR
        with pytest.raises(Exception, match="Reset"):
            m.set_run_mode(RunMode.VELOCITY)
        m.disable().result(2.0)


def test_active_report_updates_state_and_stops() -> None:
    s, _ = make(1)
    with s:
        m = s.motor(127)
        m.set_active_report(True).result(2.0)
        assert wait_until(lambda: m.state.rx_rate >= 20, 3.0)  # 約 100 Hz
        m.set_active_report(False).result(2.0)
        assert wait_until(lambda: m.state.rx_rate < 5, 3.0)


def test_fault_injection_reaches_events_and_state() -> None:
    s, sim = make(1)
    events: list[object] = []
    s.events.subscribe(MOTOR_FAULT, events.append)
    with s:
        m = s.motor(127)
        m.set_active_report(True).result(2.0)
        sim.inject_fault(127, 0)  # 過温
        assert wait_until(lambda: m.state.fault_bits != 0, 3.0)
        assert any(e.get("source") == "type21" for e in events)  # type: ignore[attr-defined]
        assert any(e.get("source") == "feedback" for e in events)  # type: ignore[attr-defined]


def test_close_stops_enabled_motor() -> None:
    s, sim = make(2)
    s.open()
    m = s.scan(gap_s=0.0, settle_s=0.2).result(10.0)[0]
    m.enable().result(2.0)
    assert sim.motor(m.motor_id).mode == MotorMode.MOTOR
    s.close()
    assert all(sm.mode == MotorMode.RESET for sm in sim.motors)
