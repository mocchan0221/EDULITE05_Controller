"""MotorService を SimTransport(3 台)に対して動かす統合テスト(phase2.md §1.5 / §2)。

実時間で動くので判定は緩め。モータ ID は 127 / 1 / 2(`make_motors(3)` の既定)。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from typing import Any

import pytest

from edulite05.core import clock
from edulite05.core.control.controllers import (
    Csp,
    Current,
    Jog,
    MitDirect,
    Pp,
    SineTest,
    Velocity,
)
from edulite05.core.control.params import (
    CspParams,
    CurrentParams,
    JogParams,
    MitParams,
    PpParams,
    SineParams,
    VelocityParams,
)
from edulite05.core.control.service import (
    SERVICE_CLAMPED,
    SERVICE_COMM_LOST,
    SERVICE_CONTROL_ERROR,
    SERVICE_ESTOP,
    SERVICE_FAULT,
    SERVICE_STATE_CHANGED,
    SERVICE_TIER,
    MotorService,
    UiState,
)
from edulite05.core.device.profile import MotorProfile
from edulite05.core.errors import ProtocolError, SafetyError
from edulite05.core.events import ESTOP
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.safety import PowerTier
from edulite05.core.session import Session
from edulite05.sim.transport import SimTransport, make_motors

PROFILE = MotorProfile.load_builtin("el05")
A, B, C = 127, 1, 2  # 3 台の ID


def wait_until(pred: Callable[[], object], timeout: float = 3.0) -> bool:
    end = clock.now() + timeout
    while clock.now() < end:
        if pred():
            return True
        time.sleep(0.005)
    return bool(pred())


class Rig:
    def __init__(self, n: int = 3, **sim_kwargs: Any) -> None:
        self.sim = SimTransport(make_motors(n, profile=PROFILE), **sim_kwargs)
        self.session = Session(self.sim, PROFILE)
        self.session.open()
        self.svc = MotorService(self.session)
        self.events: list[tuple[str, Any]] = []
        for topic in (
            SERVICE_STATE_CHANGED,
            SERVICE_ESTOP,
            SERVICE_TIER,
            SERVICE_CLAMPED,
            SERVICE_FAULT,
            SERVICE_COMM_LOST,
            SERVICE_CONTROL_ERROR,
            ESTOP,
        ):
            self.session.events.subscribe(topic, lambda p, t=topic: self.events.append((t, p)))
        for mid in (A, B, C)[:n]:
            self.session.motor(mid)
        self.svc.open()

    def topics(self, topic: str) -> list[Any]:
        return [p for t, p in list(self.events) if t == topic]

    def enable(self, mid: int, mode: RunMode) -> None:
        self.svc.enable(mid, mode).result(5.0)
        assert self.svc.ui_state(mid) is UiState.ENABLED

    def close(self) -> None:
        self.svc.close()
        self.session.close()


@pytest.fixture
def rig() -> Iterator[Rig]:
    r = Rig()
    yield r
    r.close()


def sm(rig: Rig, mid: int):
    return rig.sim.motor(mid)


def all_disabled(rig: Rig) -> bool:
    return all(m.mode is MotorMode.RESET for m in rig.sim.motors)


# ---------------------------------------------------------------- 個別運転 / enable の中身
def test_initial_states_online(rig: Rig) -> None:
    # 主動上報が ON になり、全モータが DISABLED(= オンライン)になる
    assert wait_until(lambda: all(rig.svc.ui_state(i) is UiState.DISABLED for i in (A, B, C)))
    assert all(sm(rig, i).active_report for i in (A, B, C))
    assert rig.svc.tier is PowerTier.LOW and not rig.svc.estop_latched


def test_enable_sequence_writes_limits_and_can_timeout(rig: Rig) -> None:
    rig.enable(A, RunMode.VELOCITY)
    m = sm(rig, A)
    assert m.mode is MotorMode.MOTOR and m.run_mode == int(RunMode.VELOCITY)
    # LOW 段階のモータ側 limit(二重制限)と canTimeout = 0.25 s / 50 µs
    assert m.get_param("limit_torque") == pytest.approx(0.5)
    assert m.get_param("limit_cur") == pytest.approx(1.0)
    assert m.get_param("limit_spd") == pytest.approx(5.0)
    assert m.get_param("canTimeout") == 5000
    # 他の 2 台は触られない
    assert sm(rig, B).mode is MotorMode.RESET and sm(rig, B).get_param("canTimeout") == 0
    assert rig.svc.ui_state(B) is UiState.DISABLED
    assert rig.svc.run_mode_of(A) is RunMode.VELOCITY
    # 有効なだけ(コントローラなし)でも Hold(速度 0)が流れ続ける
    t0 = m.last_rx_t
    time.sleep(0.2)
    assert m.last_rx_t > t0 and abs(m.velocity) < 0.05
    rig.svc.disable([A]).result(3.0)
    assert rig.svc.ui_state(A) is UiState.DISABLED and m.mode is MotorMode.RESET
    # 無効化しても canTimeout は 0 に戻さない
    assert m.get_param("canTimeout") == 5000


def test_state_events_and_invalid_calls(rig: Rig) -> None:
    rig.enable(A, RunMode.VELOCITY)
    states = [p["state"] for p in rig.topics(SERVICE_STATE_CHANGED) if p["motor_id"] == A]
    assert UiState.ENABLED in states
    with pytest.raises(ProtocolError):  # 有効なモータは再度有効化できない
        rig.svc.enable(A, RunMode.VELOCITY)
    with pytest.raises(ProtocolError):  # 無効なモータは運転開始できない
        rig.svc.start([B], Velocity())
    with pytest.raises(ProtocolError):  # run_mode が一致しない
        rig.svc.start([A], Csp())
    with pytest.raises(ProtocolError):  # 運転中でない
        rig.svc.update([A], VelocityParams())
    with pytest.raises(ProtocolError):
        rig.svc.enable(B, 4)  # type: ignore[arg-type]  # 未定義の run_mode
    assert rig.svc.ui_state(A) is UiState.ENABLED  # どれも状態を壊さない


def test_velocity_run_update_stop_motion_keeps_enabled(rig: Rig) -> None:
    rig.enable(A, RunMode.VELOCITY)
    m = sm(rig, A)
    rig.svc.start([A], Velocity(VelocityParams(velocity=2.0, accel=50.0))).result(1.0)
    assert rig.svc.ui_state(A) is UiState.RUNNING
    assert wait_until(lambda: abs(m.velocity - 2.0) < 0.1)
    rig.svc.update([A], VelocityParams(velocity=-1.0, accel=50.0))
    assert wait_until(lambda: abs(m.velocity + 1.0) < 0.1)
    rig.svc.stop_motion([A]).result(1.0)
    assert rig.svc.ui_state(A) is UiState.ENABLED
    assert wait_until(lambda: abs(m.velocity) < 0.1)
    time.sleep(0.2)
    assert m.mode is MotorMode.MOTOR and abs(m.velocity) < 0.1  # 有効のまま保持
    rig.svc.stop_motion([A]).result(1.0)  # 運転中でなければ何もしない
    # 再度 start できる
    rig.svc.start([A], Velocity(VelocityParams(velocity=1.0, accel=50.0)))
    assert wait_until(lambda: abs(m.velocity - 1.0) < 0.1)
    rig.svc.disable([A]).result(3.0)
    assert m.mode is MotorMode.RESET and rig.svc.ui_state(A) is UiState.DISABLED
    assert rig.svc.controller_of(A) is None


@pytest.mark.parametrize("mode", ["velocity", "current", "mit", "csp", "pp", "sine", "jog"])
def test_each_mode_reaches_target_within_low_limits(rig: Rig, mode: str) -> None:
    m = sm(rig, A)
    if mode == "velocity":
        rig.enable(A, RunMode.VELOCITY)
        rig.svc.start([A], Velocity(VelocityParams(velocity=3.0, accel=50.0)))
        assert wait_until(lambda: abs(m.velocity - 3.0) < 0.15)
        assert m.get_param("limit_cur") <= 1.0 + 1e-6
    elif mode == "current":
        rig.enable(A, RunMode.CURRENT)
        rig.svc.start([A], Current(CurrentParams(iq=0.4)))
        assert wait_until(lambda: abs(m.iq - 0.4) < 0.05)
        assert m.get_param("iq_ref") == pytest.approx(0.4)
    elif mode == "mit":
        rig.enable(A, RunMode.OPERATION)
        rig.svc.start([A], MitDirect(MitParams(position=0.5, kp=5.0, kd=0.3)))
        assert wait_until(lambda: abs(m.position - 0.5) < 0.05, 5.0)
        assert abs(m.torque) <= 0.5 + 1e-3  # LOW のトルク上限
    elif mode == "csp":
        rig.enable(A, RunMode.CSP)
        rig.svc.start([A], Csp(CspParams(position=1.0, speed_limit=2.0)))
        assert wait_until(lambda: abs(m.position - 1.0) < 0.05, 5.0)
    elif mode == "pp":
        rig.enable(A, RunMode.PP)
        rig.svc.start([A], Pp(PpParams(position=1.0, velocity=2.0, accel=20.0)))
        assert wait_until(lambda: abs(m.position - 1.0) < 0.05, 5.0)
    elif mode == "sine":
        rig.enable(A, RunMode.VELOCITY)
        rig.svc.start([A], SineTest(SineParams(target="velocity", amplitude=1.0, frequency=1.0)))
        vs: list[float] = []
        end = clock.now() + 1.5
        while clock.now() < end:
            vs.append(m.velocity)
            time.sleep(0.005)
        assert max(vs) > 0.7 and min(vs) < -0.7
    else:
        rig.enable(A, RunMode.VELOCITY)
        jog = Jog(JogParams(speed=1.0, accel=50.0))
        rig.svc.start([A], jog)
        jog.set_direction(+1)
        assert wait_until(lambda: m.velocity > 0.9)
        jog.set_direction(-1)
        assert wait_until(lambda: m.velocity < -0.9)
    if mode != "current":  # 電流モードは速度を制限しない(トルクは LOW の電流上限で制限される)
        assert abs(m.velocity) <= 5.3  # 速度モード・MIT・位置系は LOW の 5 rad/s 以内
    rig.svc.stop_motion([A])
    rig.svc.disable([A]).result(3.0)
    assert m.mode is MotorMode.RESET


def test_csp_enable_seeds_loc_ref_from_mech_pos(rig: Rig) -> None:
    m = sm(rig, B)
    m.position = 1.5  # 停止中に手で回した
    m._sync_loc_ref()
    rig.enable(B, RunMode.CSP)
    assert m.get_param("loc_ref") == pytest.approx(1.5, abs=0.01)
    time.sleep(0.3)
    assert m.position == pytest.approx(1.5, abs=0.02)  # 有効化で急に動かない
    # Hold は現在位置を保つ: 外力で押しても戻ろうとする
    m.ext_torque = 0.3
    time.sleep(0.3)
    m.ext_torque = 0.0
    assert abs(m.position - 1.5) < 0.15


# ---------------------------------------------------------------- 同時運転
def test_simultaneous_three_motors_independent(rig: Rig) -> None:
    rig.enable(A, RunMode.VELOCITY)
    rig.enable(B, RunMode.CSP)
    rig.enable(C, RunMode.OPERATION)
    rig.svc.start([A], Velocity(VelocityParams(velocity=2.0, accel=50.0)))
    rig.svc.start([B], Csp(CspParams(position=1.0, speed_limit=2.0)))
    rig.svc.start([C], MitDirect(MitParams(position=-0.5, kp=5.0, kd=0.3)))
    assert wait_until(
        lambda: (
            abs(sm(rig, A).velocity - 2.0) < 0.15
            and abs(sm(rig, B).position - 1.0) < 0.05
            and abs(sm(rig, C).position + 0.5) < 0.05
        ),
        6.0,
    )
    assert all(rig.svc.ui_state(i) is UiState.RUNNING for i in (A, B, C))
    # 1 台だけ運転停止しても他は動き続ける
    rig.svc.stop_motion([A])
    assert rig.svc.ui_state(A) is UiState.ENABLED
    assert rig.svc.ui_state(B) is UiState.RUNNING and rig.svc.ui_state(C) is UiState.RUNNING
    assert wait_until(lambda: abs(sm(rig, A).velocity) < 0.1)
    # 1 台だけ無効化
    rig.svc.disable([B]).result(3.0)
    assert sm(rig, B).mode is MotorMode.RESET and rig.svc.ui_state(C) is UiState.RUNNING
    rig.svc.disable([A, C]).result(3.0)
    assert all_disabled(rig)


def test_one_controller_for_several_motors_is_cloned(rig: Rig) -> None:
    rig.enable(A, RunMode.VELOCITY)
    rig.enable(B, RunMode.VELOCITY)
    rig.svc.start([A, B], Velocity(VelocityParams(velocity=1.5, accel=50.0)))
    assert wait_until(
        lambda: abs(sm(rig, A).velocity - 1.5) < 0.1 and abs(sm(rig, B).velocity - 1.5) < 0.1
    )
    rig.svc.update([A, B], VelocityParams(velocity=-1.0, accel=50.0))  # 両方に効く
    assert wait_until(
        lambda: abs(sm(rig, A).velocity + 1.0) < 0.1 and abs(sm(rig, B).velocity + 1.0) < 0.1
    )
    rig.svc.stop_motion([A, B])
    rig.svc.disable([A, B]).result(3.0)


# ---------------------------------------------------------------- クランプ
def test_clamping_events_and_release(rig: Rig) -> None:
    rig.enable(A, RunMode.OPERATION)
    rig.svc.start([A], MitDirect(MitParams(position=0.0, kp=50.0, kd=3.0)))  # LOW: kp ≤ 20, kd ≤ 1
    assert wait_until(lambda: rig.topics(SERVICE_CLAMPED))
    notes = rig.topics(SERVICE_CLAMPED)[0]["notes"]
    fields = {n.field: n for n in notes}
    assert fields["kp"].applied == pytest.approx(20.0) and fields["kp"].reason == "tier"
    assert fields["kd"].applied == pytest.approx(1.0)
    # 範囲内へ変えるとクランプ解除(空リスト)が通知される
    rig.svc.update([A], MitParams(position=0.0, kp=5.0, kd=0.3))
    assert wait_until(lambda: rig.topics(SERVICE_CLAMPED)[-1]["notes"] == [])
    rig.svc.stop_motion([A])
    rig.svc.disable([A]).result(3.0)
    # 速度の上限(5 rad/s)
    rig.enable(B, RunMode.VELOCITY)
    rig.svc.start([B], Velocity(VelocityParams(velocity=20.0, accel=100.0)))
    assert wait_until(
        lambda: any(
            p["motor_id"] == B and any(n.field == "value" for n in p["notes"])
            for p in rig.topics(SERVICE_CLAMPED)
        )
    )
    assert wait_until(lambda: sm(rig, B).velocity > 4.5)
    time.sleep(0.3)
    assert sm(rig, B).velocity < 5.3
    rig.svc.disable([B]).result(3.0)


# ---------------------------------------------------------------- 非常停止
def test_estop_stops_everything_latches_and_rejects_calls(rig: Rig) -> None:
    rig.enable(A, RunMode.VELOCITY)
    rig.enable(B, RunMode.CSP)
    rig.enable(C, RunMode.OPERATION)
    rig.svc.start([A], Velocity(VelocityParams(velocity=2.0, accel=50.0)))
    rig.svc.start([C], MitDirect(MitParams(kp=5.0, kd=0.3)))
    assert wait_until(lambda: sm(rig, A).velocity > 1.5)

    rig.svc.estop()  # 同期: 戻った時点で停止要求は送信済み
    assert rig.svc.estop_latched and rig.svc.tier is PowerTier.LOW
    assert wait_until(lambda: all_disabled(rig), 1.0)
    assert rig.svc.loop.handles() == [] and not rig.svc.loop._keeps
    assert all(rig.svc.ui_state(i) is UiState.DISABLED for i in (A, B, C))
    assert rig.topics(ESTOP) and rig.topics(SERVICE_ESTOP)[-1]["latched"] is True
    time.sleep(0.2)
    assert all_disabled(rig)  # 制御フレームが残っていて再起動することはない

    for call in (
        lambda: rig.svc.enable(A, RunMode.VELOCITY),
        lambda: rig.svc.start([A], Velocity()),
        lambda: rig.svc.update([A], VelocityParams()),
        lambda: rig.svc.stop_motion([A]),
        lambda: rig.svc.disable([A]),
        lambda: rig.svc.clear_fault(A),
        lambda: rig.svc.set_tier(PowerTier.HIGH),
    ):
        with pytest.raises(SafetyError):
            call()

    rig.svc.release_estop()  # ラッチ解除のみ。モータは DISABLED のまま
    assert not rig.svc.estop_latched and all_disabled(rig)
    assert rig.topics(SERVICE_ESTOP)[-1]["latched"] is False
    rig.enable(A, RunMode.VELOCITY)  # 再び操作できる
    rig.svc.disable([A]).result(3.0)


def test_estop_resets_high_tier_to_low() -> None:
    r = Rig(n=1)
    try:
        assert r.svc.set_tier(PowerTier.HIGH)
        r.enable(A, RunMode.VELOCITY)  # HIGH で有効化 → limit が HIGH の値
        assert sm(r, A).get_param("limit_cur") == pytest.approx(11.0)
        r.svc.estop()
        assert r.svc.tier is PowerTier.LOW
        assert any(p["tier"] is PowerTier.LOW for p in r.topics(SERVICE_TIER))
    finally:
        r.close()


def test_estop_during_enable_sequence_aborts_it() -> None:
    r = Rig(n=1, latency_s=0.004)  # 手順に時間がかかるようにする
    try:
        assert wait_until(lambda: r.svc.ui_state(A) is UiState.DISABLED)
        fut = r.svc.enable(A, RunMode.CSP)
        time.sleep(0.03)
        r.svc.estop()
        with pytest.raises(SafetyError):
            fut.result(5.0)
        time.sleep(0.2)
        assert sm(r, A).mode is MotorMode.RESET
        assert r.svc.ui_state(A) is UiState.DISABLED and not r.svc.loop._keeps
    finally:
        r.close()


# ---------------------------------------------------------------- 出力段階
def test_tier_rules(rig: Rig) -> None:
    rig.enable(A, RunMode.VELOCITY)
    assert rig.svc.set_tier(PowerTier.HIGH) is False  # 有効なモータがある間は HIGH にできない
    assert rig.svc.tier is PowerTier.LOW
    rig.svc.disable([A]).result(3.0)
    assert rig.svc.set_tier(PowerTier.HIGH) is True
    assert rig.svc.tier is PowerTier.HIGH and rig.svc.guard.limits.speed == 50.0
    rig.enable(A, RunMode.VELOCITY)
    m = sm(rig, A)
    assert m.get_param("limit_cur") == pytest.approx(11.0)
    assert m.get_param("limit_spd") == pytest.approx(50.0)
    # HIGH で運転 → LOW へ即座に戻す: 上限が即クランプされ、モータ側 limit も書き換わる
    rig.svc.start([A], Velocity(VelocityParams(velocity=20.0, accel=100.0)))
    assert wait_until(lambda: m.velocity > 15.0)
    assert rig.svc.set_tier(PowerTier.LOW) is True
    assert wait_until(
        lambda: (
            m.get_param("limit_cur") == pytest.approx(1.0)
            and m.get_param("limit_spd") == pytest.approx(5.0)
            and m.get_param("limit_torque") == pytest.approx(0.5)
        )
    )
    assert wait_until(lambda: 3.0 < m.velocity < 5.5, 3.0)  # 5 rad/s へクランプされて減速
    rig.svc.disable([A]).result(3.0)


# ---------------------------------------------------------------- 通信監視
def test_comm_loss_disables_motor_and_publishes_event(rig: Rig) -> None:
    rig.enable(A, RunMode.VELOCITY)
    rig.enable(B, RunMode.VELOCITY)
    rig.svc.start([A], Velocity(VelocityParams(velocity=2.0, accel=50.0)))
    assert wait_until(lambda: sm(rig, A).velocity > 1.5)
    # モータ → ホストの受信を A だけ止める(送信は通る)
    orig = rig.sim._send_rx
    rig.sim._send_rx = lambda fr: None if fr.can_id & 0xFF00 == A << 8 else orig(fr)  # type: ignore[method-assign]
    t0 = clock.now()
    assert wait_until(lambda: rig.svc.ui_state(A) is UiState.OFFLINE, 2.0)
    assert clock.now() - t0 < 1.0  # comm_timeout_s = 0.25 s
    ev = [p for p in rig.topics(SERVICE_COMM_LOST) if A in p["motor_ids"]]
    assert ev and ev[0]["reason"] == "timeout" and B not in ev[0]["motor_ids"]
    assert wait_until(lambda: sm(rig, A).mode is MotorMode.RESET, 1.0)  # stop が届いた
    assert rig.svc.loop.handle_of(A) is None
    assert rig.svc.ui_state(B) is UiState.ENABLED  # 他のモータは影響なし
    # 回復: フィードバックが戻れば DISABLED(再有効化可能)
    rig.sim._send_rx = orig  # type: ignore[method-assign]
    assert wait_until(lambda: rig.svc.ui_state(A) is UiState.DISABLED, 3.0)
    rig.enable(A, RunMode.VELOCITY)
    rig.svc.disable([A, B]).result(3.0)


def test_transport_error_marks_everything_offline_and_tier_low(rig: Rig) -> None:
    assert rig.svc.set_tier(PowerTier.HIGH)
    assert rig.svc.set_tier(PowerTier.LOW)
    assert rig.svc.set_tier(PowerTier.HIGH)
    assert wait_until(lambda: all(rig.svc.ui_state(i) is UiState.DISABLED for i in (A, B, C)))
    rig.sim.close()  # アダプタ切断相当(以降の send が TransportError)
    assert wait_until(lambda: all(rig.svc.ui_state(i) is UiState.OFFLINE for i in (A, B, C)), 3.0)
    assert rig.svc.tier is PowerTier.LOW
    assert not rig.svc.estop_latched and not rig.svc.connected
    ev = rig.topics(SERVICE_COMM_LOST)
    assert ev and ev[-1]["reason"] == "transport" and set(ev[-1]["motor_ids"]) == {A, B, C}
    with pytest.raises(ProtocolError):
        rig.svc.enable(A, RunMode.VELOCITY)


def test_transport_error_while_running() -> None:
    r = Rig(n=1)
    try:
        r.enable(A, RunMode.VELOCITY)
        r.svc.start([A], Velocity(VelocityParams(velocity=1.0, accel=50.0)))
        assert wait_until(lambda: sm(r, A).velocity > 0.8)
        r.sim.close()
        assert wait_until(lambda: r.svc.ui_state(A) is UiState.OFFLINE, 3.0)
        assert r.svc.loop.handles() == []
    finally:
        r.close()


# ---------------------------------------------------------------- 故障
def test_injected_fault_goes_to_fault_and_clears(rig: Rig) -> None:
    rig.enable(A, RunMode.VELOCITY)
    rig.enable(B, RunMode.VELOCITY)
    rig.svc.start([A], Velocity(VelocityParams(velocity=2.0, accel=50.0)))
    rig.svc.start([B], Velocity(VelocityParams(velocity=1.0, accel=50.0)))
    assert wait_until(lambda: sm(rig, A).velocity > 1.5)
    rig.sim.inject_fault(A, 2)  # 低電圧
    assert wait_until(lambda: rig.svc.ui_state(A) is UiState.FAULT, 2.0)
    assert rig.svc.loop.handle_of(A) is None
    ev = rig.topics(SERVICE_FAULT)
    assert ev and ev[0]["motor_id"] == A
    assert rig.svc.fault_details(A)
    assert rig.svc.ui_state(B) is UiState.RUNNING  # 他のモータは動き続ける
    with pytest.raises(ProtocolError):  # 故障中は有効化できない
        rig.svc.enable(A, RunMode.VELOCITY)
    time.sleep(0.2)
    assert rig.svc.ui_state(A) is UiState.FAULT  # 故障が残る間は FAULT のまま
    # 故障をクリア → DISABLED → 再度有効化できる
    rig.svc.clear_fault(A).result(3.0)
    assert rig.svc.ui_state(A) is UiState.DISABLED and sm(rig, A).faults == 0
    assert rig.svc.fault_details(A) == []
    rig.enable(A, RunMode.VELOCITY)
    rig.svc.disable([A, B]).result(3.0)


def test_clear_fault_that_does_not_clear_stays_fault(rig: Rig) -> None:
    rig.sim.inject_fault(C, 2)
    assert wait_until(lambda: rig.svc.ui_state(C) is UiState.FAULT, 2.0)
    m = sm(rig, C)
    orig = m.clear_faults
    m.clear_faults = lambda: None  # type: ignore[method-assign]  # 故障が消えない
    try:
        rig.svc.disable([C], clear_fault=True).result(3.0)
        assert rig.svc.ui_state(C) is UiState.FAULT
    finally:
        m.clear_faults = orig  # type: ignore[method-assign]
    rig.svc.clear_fault(C).result(3.0)
    assert rig.svc.ui_state(C) is UiState.DISABLED


def test_controller_exception_disables_only_that_motor(rig: Rig) -> None:
    class Bad(Velocity):
        def step(self, t, dt, states):
            if t > 0.2:
                raise RuntimeError("bug")
            return super().step(t, dt, states)

    rig.enable(A, RunMode.VELOCITY)
    rig.enable(B, RunMode.VELOCITY)
    rig.svc.start([A], Velocity(VelocityParams(velocity=1.0, accel=50.0)))
    rig.svc.start([B], Bad(VelocityParams(velocity=1.0, accel=50.0)))
    assert wait_until(lambda: rig.svc.ui_state(B) is UiState.DISABLED, 3.0)
    assert wait_until(lambda: sm(rig, B).mode is MotorMode.RESET, 1.0)
    assert rig.topics(SERVICE_CONTROL_ERROR)[0]["motor_ids"] == [B]
    assert rig.svc.ui_state(A) is UiState.RUNNING
    rig.svc.disable([A]).result(3.0)


# ---------------------------------------------------------------- テレメトリ
def test_telemetry_channels_are_filled(rig: Rig) -> None:
    rig.enable(A, RunMode.VELOCITY)
    rig.svc.start([A], Velocity(VelocityParams(velocity=2.0, accel=50.0)))
    names = [
        f"m{A}.{s}"
        for s in ("pos", "pos_multi", "vel", "tq", "temp", "iqf", "vbus", "mechpos", "cmd.vel")
    ]
    assert wait_until(lambda: all(rig.svc.telemetry.latest(n) is not None for n in names), 4.0)
    hub = rig.svc.telemetry
    assert hub.latest(f"m{A}.vbus")[1] == pytest.approx(24.49, abs=0.1)
    assert hub.latest(f"m{A}.cmd.vel")[1] == pytest.approx(2.0)
    t, v = hub.window([f"m{A}.vel"], 1.0)[f"m{A}.vel"]
    # 運転中は主動上報 OFF: 制御ループの応答(約 200 サンプル/s)がフィードバック
    assert len(t) > 30 and v.max() > 1.0
    # 他のモータのチャネルも登録済みで、主動上報で埋まる
    assert wait_until(lambda: hub.latest(f"m{C}.pos") is not None)
    chans = {c.name for c in hub.channels()}
    assert {f"m{i}.{s}" for i in (A, B, C) for s in ("pos", "iqf", "vbus", "cmd.pos")} <= chans
    rig.svc.disable([A]).result(3.0)


def test_pos_multi_unwraps_across_the_wrap() -> None:
    r = Rig(n=1)
    try:
        sm(r, A).position = 12.0  # ±4π(12.57)の折返しの近く
        sm(r, A)._sync_loc_ref()
        r.enable(A, RunMode.VELOCITY)
        r.svc.start([A], Velocity(VelocityParams(velocity=3.0, accel=50.0)))
        assert wait_until(lambda: sm(r, A).position > 14.0, 5.0)
        _, v = r.svc.telemetry.window([f"m{A}.pos_multi"], 2.0)[f"m{A}.pos_multi"]
        _, raw = r.svc.telemetry.window([f"m{A}.pos"], 2.0)[f"m{A}.pos"]
        assert v.max() > 13.0 and raw.max() < 12.6 and (v[1:] - v[:-1]).min() > -0.5
        r.svc.disable([A]).result(3.0)
    finally:
        r.close()


# ---------------------------------------------------------------- 終了時
def test_session_close_leaves_every_motor_disabled() -> None:
    # latency_s=0: SimTransport.close() は配送待ち(遅延中)のフレームを捨てるので、遅延ゼロで検証する
    r = Rig(latency_s=0.0)
    r.enable(A, RunMode.VELOCITY)
    r.enable(B, RunMode.CSP)
    r.enable(C, RunMode.OPERATION)
    r.svc.start([A], Velocity(VelocityParams(velocity=2.0, accel=50.0)))
    r.svc.start([C], MitDirect(MitParams(kp=5.0, kd=0.3)))
    assert wait_until(lambda: sm(r, A).velocity > 1.5)
    sim = r.sim
    r.session.close()  # MotorService を閉じ忘れていても
    assert all(m.mode is MotorMode.RESET for m in sim.motors)
    assert wait_until(lambda: not r.svc.connected, 2.0)
    assert all(r.svc.ui_state(i) is UiState.OFFLINE for i in (A, B, C))
    r.svc.close()  # 冪等


def test_service_close_disables_all_but_keeps_session() -> None:
    r = Rig(n=2)
    try:
        r.enable(A, RunMode.VELOCITY)
        r.enable(B, RunMode.VELOCITY)
        r.svc.close()
        assert wait_until(lambda: all_disabled(r), 1.0)
        with pytest.raises(ProtocolError):
            r.svc.enable(A, RunMode.VELOCITY)
    finally:
        r.session.close()


def test_failed_enable_cleans_up(rig: Rig) -> None:
    # 存在しないモータ: 要求がタイムアウトして Future が例外になり、状態は DISABLED のまま
    fut: Future[None] = rig.svc.enable(99, RunMode.VELOCITY)
    with pytest.raises(Exception):  # noqa: B017 - RequestTimeout / TimeoutError
        fut.result(10.0)
    assert not rig.svc.is_busy(99)
    assert rig.svc.ui_state(99) is UiState.OFFLINE
    assert not rig.svc.loop._keeps
