"""ControlLoop を SimTransport(フレームレベルシミュレータ)へ繋ぐ結合テスト。

実時間で動くので判定は緩め。Session / Motor で有効化の手順(停止 → run_mode → enable)を踏み、
ControlLoop が作るフレームでシミュレータのモータが実際に動くことと、制御フレームへ type 2 の
応答が返ることを確認する。
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fakes import wait_until

from edulite05.core.control.commands import HoldCmd
from edulite05.core.control.controllers import Csp, Jog, MitDirect, SineTest, Velocity
from edulite05.core.control.loop import ControlLoop
from edulite05.core.control.params import (
    CspParams,
    JogParams,
    MitParams,
    SineParams,
    VelocityParams,
)
from edulite05.core.device.profile import MotorProfile
from edulite05.core.protocol.private import Feedback
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.safety import PowerTier, SafetyGuard
from edulite05.core.session import Session
from edulite05.core.telemetry import TelemetryHub
from edulite05.sim.transport import SimTransport, make_motors

PROFILE = MotorProfile.load_builtin("el05")


class Rig:
    def __init__(self, n: int = 2, **sim_kwargs: float) -> None:
        self.sim = SimTransport(make_motors(n, profile=PROFILE), **sim_kwargs)
        self.session = Session(self.sim, PROFILE)
        self.session.open()
        self.guard = SafetyGuard(PROFILE, PowerTier.LOW)
        self.hub = TelemetryHub(capacity_s=10.0)
        self.loop = ControlLoop(self.session.bus, self.guard, 200.0, telemetry=self.hub)
        self.replies: list[Feedback] = []
        self.session.bus.subscribe(self._on_msg)

    def _on_msg(self, msg) -> None:
        if isinstance(msg, Feedback) and msg.comm_type == 2:
            self.replies.append(msg)

    def enable(self, motor_id: int, mode: RunMode):
        m = self.session.motor(motor_id)
        m.disable().result(2.0)
        m.set_run_mode(mode).result(2.0)
        m.enable().result(2.0)
        assert self.sim.motor(motor_id).mode == MotorMode.MOTOR
        return m

    def close(self) -> None:
        self.loop.stop()
        self.session.close()


@pytest.fixture
def rig() -> Iterator[Rig]:
    r = Rig()
    yield r
    r.close()


def test_velocity_reaches_setpoint_and_gets_type2_replies(rig: Rig) -> None:
    m = rig.enable(127, RunMode.VELOCITY)
    sm = rig.sim.motor(127)
    h = rig.loop.attach(Velocity(VelocityParams(velocity=2.0, accel=50.0)), {"motor": m})
    rig.loop.start()
    t0 = time.perf_counter()
    n_before = len(rig.replies)
    assert wait_until(lambda: abs(sm.velocity - 2.0) < 0.1, 3.0)
    # モータ側の制限も LOW の値(limit_cur ≤ 1 A)で書かれている
    assert sm.params[0x7018] <= 1.0 + 1e-6
    time.sleep(max(0.0, 1.0 - (time.perf_counter() - t0)))
    n_replies = len(rig.replies) - n_before
    s = rig.loop.stats
    print(
        f"\n[sim] ticks={s.ticks} replies={n_replies} rate={s.rate_hz:.1f} Hz "
        f"jitter_p99={s.jitter_ms_p99:.3f} ms overruns={s.overruns}"
    )
    # 制御フレーム(type 18)ごとに type 2 の応答が返る(約 200/s)
    assert n_replies >= 150
    assert m.state.velocity == pytest.approx(2.0, abs=0.2)
    # 指令チャネルとフィードバックの両方が記録される
    t, v = rig.hub.latest("m127.cmd.vel")
    assert v == pytest.approx(2.0)
    # detach で速度 0 → keepalive(Hold = 速度 0)で止まったまま
    rig.loop.detach(h)
    rig.loop.keepalive(m, HoldCmd())
    assert wait_until(lambda: abs(sm.velocity) < 0.1, 3.0)
    time.sleep(0.2)
    assert abs(sm.velocity) < 0.1
    rig.loop.clear_keepalive(m)
    m.disable().result(2.0)


def test_low_tier_limits_velocity_in_sim(rig: Rig) -> None:
    m = rig.enable(127, RunMode.VELOCITY)
    sm = rig.sim.motor(127)
    rig.loop.attach(Velocity(VelocityParams(velocity=20.0, accel=100.0)), {"motor": m})
    rig.loop.start()
    assert wait_until(lambda: sm.velocity > 4.5, 3.0)
    time.sleep(0.3)
    assert sm.velocity < 5.3  # LOW の 5 rad/s を超えない


def test_jog_through_sim(rig: Rig) -> None:
    m = rig.enable(1, RunMode.VELOCITY)
    sm = rig.sim.motor(1)
    jog = Jog(JogParams(speed=1.0, accel=50.0))
    h = rig.loop.attach(jog, {"motor": m})
    rig.loop.start()
    jog.set_direction(+1)
    assert wait_until(lambda: sm.velocity > 0.9, 3.0)
    jog.set_direction(-1)
    assert wait_until(lambda: sm.velocity < -0.9, 3.0)
    jog.set_direction(0)
    assert wait_until(lambda: abs(sm.velocity) < 0.1, 3.0)
    rig.loop.detach(h)
    m.disable().result(2.0)


def test_csp_moves_and_holds_after_detach(rig: Rig) -> None:
    m = rig.enable(127, RunMode.CSP)
    sm = rig.sim.motor(127)
    h = rig.loop.attach(Csp(CspParams(position=1.0, speed_limit=2.0)), {"motor": m})
    rig.loop.start()
    assert wait_until(lambda: abs(sm.position - 1.0) < 0.05, 5.0)
    rig.loop.detach(h)
    rig.loop.keepalive(m, HoldCmd())
    held = sm.position  # 保持位置はこの付近でラッチされる
    sm.ext_torque = 0.3  # 外力で押しても loc_ref は変わらないので戻ろうとする
    time.sleep(0.3)
    assert abs(sm.position - held) < 0.1
    sm.ext_torque = 0.0
    rig.loop.clear_keepalive(m)
    m.disable().result(2.0)


def test_mit_position_hold_in_sim(rig: Rig) -> None:
    m = rig.enable(127, RunMode.OPERATION)
    sm = rig.sim.motor(127)
    rig.loop.attach(
        MitDirect(MitParams(position=0.5, kp=5.0, kd=0.3)), {"motor": m}
    )
    rig.loop.start()
    assert wait_until(lambda: abs(sm.position - 0.5) < 0.05, 5.0)
    assert m.state.mode == MotorMode.MOTOR


def test_sine_velocity_in_sim(rig: Rig) -> None:
    m = rig.enable(127, RunMode.VELOCITY)
    sm = rig.sim.motor(127)
    rig.loop.attach(
        SineTest(SineParams(target="velocity", amplitude=1.0, frequency=1.0)), {"motor": m}
    )
    rig.loop.start()
    vs: list[float] = []
    end = time.perf_counter() + 1.5
    while time.perf_counter() < end:
        vs.append(sm.velocity)
        time.sleep(0.005)
    assert max(vs) > 0.7 and min(vs) < -0.7


def test_two_motors_simultaneously_and_controller_exception(rig: Rig) -> None:
    a = rig.enable(127, RunMode.VELOCITY)
    b = rig.enable(1, RunMode.VELOCITY)

    class Bad(Velocity):
        def step(self, t, dt, states):
            if t > 0.3:
                raise RuntimeError("bug")
            return super().step(t, dt, states)

    rig.loop.attach(Velocity(VelocityParams(velocity=1.0, accel=50.0)), {"motor": a})
    rig.loop.attach(Bad(VelocityParams(velocity=-1.0, accel=50.0)), {"motor": b})
    rig.loop.start()
    assert wait_until(lambda: rig.sim.motor(127).velocity > 0.9, 3.0)
    # 例外を出した側(ID 1)だけ無効化される。ID 127 は動き続ける
    assert wait_until(lambda: rig.sim.motor(1).mode == MotorMode.RESET, 3.0)
    assert rig.sim.motor(127).mode == MotorMode.MOTOR
    assert rig.sim.motor(127).velocity > 0.8
    a.disable().result(2.0)


def test_three_motors_200hz_with_1ms_frame_pacing_no_skipped_ticks() -> None:
    """実機相当の送信ペーシング(send が 1 フレーム約 1 ms ブロック)でも周期が破綻しない。

    3 台 × 200 Hz = 600 フレーム/s(= TX スレッドの稼働率 約 60%)。TX キューは各周期の
    3 フレームだけで MAX_BACKLOG に届かず、skipped = 0 のまま全周期が送られる。
    """
    r = Rig(3, min_frame_gap_s=0.001)
    try:
        motors = [r.enable(mid, RunMode.VELOCITY) for mid in (127, 1, 2)]
        for m in motors:
            r.loop.attach(Velocity(VelocityParams(velocity=1.0, accel=50.0)), {"motor": m})
        sent0 = r.sim.stats().tx_frames
        r.loop.start()
        time.sleep(1.0)
        s = r.loop.stats
        queued = r.session.bus.queue_len()
        r.loop.stop()
        sent = r.sim.stats().tx_frames - sent0
        print(
            f"\n[sim paced] ticks={s.ticks} skipped={s.skipped} overruns={s.overruns} "
            f"frames_sent={s.frames_sent} tx={sent} queue={queued}"
        )
        assert s.skipped == 0
        assert s.ticks >= 180  # 約 200 周期(タイマ分解能の余裕)
        assert s.frames_sent >= 3 * 180
        assert r.session.bus.queue_len() <= 3
        for mid in (127, 1, 2):  # 全モータが指令に追従
            assert r.sim.motor(mid).velocity == pytest.approx(1.0, abs=0.2)
    finally:
        r.close()
