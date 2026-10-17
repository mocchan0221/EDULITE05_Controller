"""ControlLoop のテスト(phase2.md §1.4)。

決定的な検証は `loop._tick(now)` を手動で呼ぶ(スレッドなし)。周期の精度・停止・統計は
実スレッドで測る(判定は緩め)。FakeTransport に積まれたフレームをデコードして確認する。
"""

from __future__ import annotations

import math
import struct
import sys
import threading
import time
from collections.abc import Iterator

import pytest
from fakes import HOST, FakeMotors, FakeTransport, feedback_frame, wait_until

from edulite05.core import clock
from edulite05.core.bus import BusManager, Priority
from edulite05.core.control.commands import HoldCmd, SetpointCmd
from edulite05.core.control.controller import Controller
from edulite05.core.control.controllers import (
    Csp,
    Current,
    Jog,
    MitDirect,
    Pp,
    SineTest,
    Velocity,
)
from edulite05.core.control.loop import (
    CONTROL_CLAMPED,
    CONTROL_ERROR,
    ControlLoop,
    timer_resolution,
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
from edulite05.core.device.motor import Motor
from edulite05.core.device.profile import MotorProfile
from edulite05.core.errors import ProtocolError
from edulite05.core.events import EventBus
from edulite05.core.protocol.ids import CommType, parse_id
from edulite05.core.protocol.private import DecodeContext
from edulite05.core.protocol.scaling import uint_to_float
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.safety import PowerTier, SafetyGuard
from edulite05.core.telemetry import TelemetryHub

PROFILE = MotorProfile.load_builtin("el05")
CTX = DecodeContext(HOST, PROFILE.ranges, PROFILE.param_dtypes())
R = PROFILE.ranges
INDEX_KEY = {p.index: p.key for p in PROFILE.params}


# ---------------------------------------------------------------------------
# フレームのデコード
# ---------------------------------------------------------------------------
def types(frames) -> list[int]:
    return [parse_id(f.can_id).comm_type for f in frames]


def writes(frames, motor: int | None = None) -> list[tuple[int, str, float]]:
    """type 18 のフレーム → (モータ ID, パラメータ key, 値)。"""
    out = []
    for f in frames:
        i = parse_id(f.can_id)
        if i.comm_type != CommType.PARAM_WRITE or (motor is not None and i.dest != motor):
            continue
        idx = struct.unpack_from("<H", f.data)[0]
        key = INDEX_KEY[idx]
        raw = f.data[4:8]
        val = struct.unpack("<B", raw[:1])[0] if key == "run_mode" else struct.unpack("<f", raw)[0]
        out.append((i.dest, key, val))
    return out


def mit_values(f) -> tuple[float, float, float, float, float]:
    """type 1 → (p, v, kp, kd, t_ff)。"""
    i = parse_id(f.can_id)
    assert i.comm_type == CommType.MIT_CTRL
    p, v, kp, kd = struct.unpack(">HHHH", f.data)
    return (
        uint_to_float(p, *R.position, 16),
        uint_to_float(v, *R.velocity, 16),
        uint_to_float(kp, *R.kp, 16),
        uint_to_float(kd, *R.kd, 16),
        uint_to_float(i.data2, *R.torque, 16),
    )


# ---------------------------------------------------------------------------
# リグ
# ---------------------------------------------------------------------------
class Rig:
    def __init__(self, ids=(1, 2), tier: PowerTier = PowerTier.LOW, rate_hz: float = 200.0) -> None:
        self.fake = FakeMotors(ids, PROFILE)
        self.tr = FakeTransport(self.fake)
        self.tr.open()
        self.events = EventBus()
        self.bus = BusManager(self.tr, CTX, self.events)
        self.bus.start()
        self.guard = SafetyGuard(PROFILE, tier)
        self.hub = TelemetryHub(capacity_s=10.0, max_rate_hz=1000.0)
        self.loop = ControlLoop(self.bus, self.guard, rate_hz, telemetry=self.hub)
        self.motors = {i: Motor(self.bus, i, PROFILE, HOST) for i in ids}
        self.log: list[tuple[str, object]] = []
        for t in (CONTROL_CLAMPED, CONTROL_ERROR):
            self.events.subscribe(t, lambda p, t=t: self.log.append((t, p)))

    def feed(self, motor_id: int, **kw) -> None:
        """フィードバックを注入し、Motor.state に反映されるまで待つ。"""
        kw.setdefault("mode", MotorMode.MOTOR)
        m = self.motors[motor_id]
        before = m.state.last_rx
        if "position" in kw:
            self.fake.position[motor_id] = kw["position"]  # 応答器のフィードバックも同じ位置にする
        self.tr.inject(feedback_frame(motor_id, **kw))
        assert wait_until(lambda: m.state.last_rx is not None and m.state.last_rx != before)

    def set_run_mode(self, motor_id: int, mode: RunMode) -> None:
        self.motors[motor_id].write_param("run_mode", int(mode)).result(2.0)

    def drain(self) -> list:
        """ここまでに積んだフレームが全部送られるのを待って、送信済みフレームを返し、クリアする。"""
        assert wait_until(lambda: self.bus.queue_len() == 0)
        time.sleep(0.02)
        with self.tr._lock:
            out, self.tr.sent = self.tr.sent, []
        return out

    def tick(self) -> list:
        self.loop._tick(clock.now())
        return self.drain()

    def close(self) -> None:
        self.loop.stop()
        self.bus.stop()


@pytest.fixture
def rig() -> Iterator[Rig]:
    r = Rig()
    yield r
    r.close()


def feedback_ready(r: Rig, mode: RunMode, ids=(1, 2), **kw) -> None:
    for i in ids:
        r.set_run_mode(i, mode)  # 停止中(mode 不明/Reset)にだけ書ける
        r.feed(i, **kw)
    r.drain()


# ---------------------------------------------------------------------------
# BusManager.send_many
# ---------------------------------------------------------------------------
def test_bus_send_many_order_and_priority(rig: Rig) -> None:
    from edulite05.core.protocol import private

    rig.tr.gate = threading.Event()
    rig.bus.send(private.get_uid(1, HOST), Priority.BACKGROUND)  # TX スレッドを gate で止める
    assert rig.tr.entered.wait(2.0)
    frames = [private.stop(i, HOST) for i in (3, 4, 5)]
    rig.bus.send(private.get_uid(9, HOST), Priority.REQUEST)
    rig.bus.send_many(frames, Priority.CONTROL)
    rig.bus.send_many([], Priority.CONTROL)  # 空は何もしない
    assert rig.bus.queue_len() == 4
    rig.tr.gate.set()
    assert wait_until(lambda: len(rig.tr.sent) == 5)
    # CONTROL が REQUEST より先、CONTROL 内は積んだ順
    assert [parse_id(f.can_id).dest for f in rig.tr.sent] == [1, 3, 4, 5, 9]


# ---------------------------------------------------------------------------
# 周期ごとの出力
# ---------------------------------------------------------------------------
def test_velocity_frames_per_tick_and_limit_writes_only_on_change(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY)
    c = Velocity(VelocityParams(velocity=2.0, current_limit=0.8, accel=15.0))
    rig.loop.attach(c, {"motor": rig.motors[1]})

    first = rig.tick()
    # 初回: limit_cur, acc_rad を書いてから spd_ref
    assert [(k, round(v, 3)) for _, k, v in writes(first)] == [
        ("limit_cur", 0.8),
        ("acc_rad", 15.0),
        ("spd_ref", 2.0),
    ]
    assert all(t == CommType.PARAM_WRITE for t in types(first))
    for _ in range(3):  # 以後は spd_ref だけ
        assert [k for _, k, _ in writes(rig.tick())] == ["spd_ref"]
    # 変更したぶんだけ書く
    c.update_params(VelocityParams(velocity=-1.0, current_limit=0.8, accel=25.0))
    got = writes(rig.tick())
    assert [(k, round(v, 3)) for _, k, v in got] == [("acc_rad", 25.0), ("spd_ref", -1.0)]
    assert [k for _, k, _ in writes(rig.tick())] == ["spd_ref"]
    # invalidate すると書き直す
    rig.loop.invalidate(1)
    assert [k for _, k, _ in writes(rig.tick())] == ["limit_cur", "acc_rad", "spd_ref"]


def test_two_controllers_one_send_per_tick(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY)
    rig.loop.attach(Velocity(VelocityParams(velocity=1.0)), {"motor": rig.motors[1]})
    rig.loop.attach(Velocity(VelocityParams(velocity=-1.0)), {"motor": rig.motors[2]})
    rig.tick()  # 制限書込
    sent_calls: list[int] = []
    orig = rig.bus.send_many

    def spy(fr, pr=Priority.CONTROL):
        sent_calls.append(len(fr))
        orig(fr, pr)

    rig.bus.send_many = spy
    out = rig.tick()
    assert sent_calls == [2]  # 2 台分が 1 回の send_many
    assert sorted(writes(out)) == [(1, "spd_ref", 1.0), (2, "spd_ref", -1.0)]


def test_mit_encoding_and_cmd_telemetry(rig: Rig) -> None:
    feedback_ready(rig, RunMode.OPERATION, position=0.0)
    rig.loop.attach(
        MitDirect(MitParams(position=0.01, velocity=0.0, kp=5.0, kd=0.3, torque_ff=0.05)),
        {"motor": rig.motors[1]},
    )
    out = rig.tick()
    assert types(out) == [CommType.MIT_CTRL]
    p, v, kp, kd, tq = mit_values(out[0])
    assert (p, v, kp, kd, tq) == pytest.approx((0.01, 0.0, 5.0, 0.3, 0.05), abs=1e-2)
    # コマンドチャネル(クランプ後の値)
    for name, val in (("cmd.pos", 0.01), ("cmd.vel", 0.0), ("cmd.tq", 0.05)):
        t, got = rig.hub.latest(f"m1.{name}")
        assert got == pytest.approx(val, abs=1e-9) and t <= clock.now()


def test_setpoint_telemetry_channels(rig: Rig) -> None:
    feedback_ready(rig, RunMode.CURRENT, ids=(1,))
    rig.loop.attach(Current(CurrentParams(iq=0.3)), {"motor": rig.motors[1]})
    rig.tick()
    assert rig.hub.latest("m1.cmd.cur")[1] == pytest.approx(0.3)
    assert rig.hub.latest("m1.cmd.vel") is None


# ---------------------------------------------------------------------------
# 安全: クランプ(LOW)
# ---------------------------------------------------------------------------
def test_low_tier_clamps_setpoints_and_notifies(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    rig.loop.attach(
        Velocity(VelocityParams(velocity=30.0, current_limit=11.0, accel=20.0)),
        {"motor": rig.motors[1]},
    )
    got = {k: v for _, k, v in writes(rig.tick())}
    assert got["spd_ref"] == pytest.approx(5.0)  # LOW: 5 rad/s
    assert got["limit_cur"] == pytest.approx(1.0)  # LOW: 1 A
    assert rig.hub.latest("m1.cmd.vel")[1] == pytest.approx(5.0)  # 記録はクランプ後
    clamps = [p for t, p in rig.log if t == CONTROL_CLAMPED]
    assert len(clamps) == 1 and clamps[0]["motor_id"] == 1
    assert {n.field for n in clamps[0]["notes"]} == {"value", "current_limit"}
    for _ in range(5):  # 継続中は再通知しない(1 s 間隔)
        rig.tick()
    assert len([1 for t, _ in rig.log if t == CONTROL_CLAMPED]) == 1
    # 解除されると空リストで通知
    rig.loop.handles()[0].controller.update_params(VelocityParams(velocity=1.0, current_limit=1.0))
    rig.tick()
    clears = [p for t, p in rig.log if t == CONTROL_CLAMPED]
    assert len(clears) == 2 and clears[1]["notes"] == []


def test_low_tier_clamps_mit_gains_and_torque(rig: Rig) -> None:
    feedback_ready(rig, RunMode.OPERATION, ids=(1,), position=0.0)
    rig.loop.attach(
        MitDirect(MitParams(position=0.0, kp=400.0, kd=4.0, torque_ff=5.0)),
        {"motor": rig.motors[1]},
    )
    out = rig.tick()
    _, _, kp, kd, tq = mit_values(out[0])
    assert kp <= 20.0 + 0.02 and kd <= 1.0 + 0.001
    assert abs(tq) <= 0.5 + 0.001
    notes = [p for t, p in rig.log if t == CONTROL_CLAMPED][0]["notes"]
    assert {"kp", "kd", "torque"} <= {n.field for n in notes}


def test_mit_host_side_terms_become_frame_t_ff_with_kd_zero(rig: Rig) -> None:
    """負の kd と摩擦補償: フレームの kd は 0、t_ff に kd·(v_set - v) と c·tanh(v/v_eps) が載る。"""
    feedback_ready(rig, RunMode.OPERATION, ids=(1,), position=0.0, velocity=2.0)
    rig.loop.attach(
        MitDirect(MitParams(position=0.0, kp=0.0, kd=-0.04, torque_ff=0.0, friction_comp=0.03)),
        {"motor": rig.motors[1]},
    )
    out = rig.tick()
    _, v_set, kp, kd, tq = mit_values(out[0])
    assert (kp, kd, v_set) == pytest.approx((0.0, 0.0, 0.0), abs=1e-2)
    expect = 0.04 * 2.0 + 0.03 * math.tanh(2.0 / PROFILE.mit_host.friction_v_eps)
    assert tq == pytest.approx(expect, abs=2e-3)
    assert rig.hub.latest("m1.cmd.tq")[1] == pytest.approx(expect, abs=1e-3)  # 記録は合算後
    assert not [p for t, p in rig.log if t == CONTROL_CLAMPED and p["notes"]]


def test_mit_runaway_guard_notifies_and_resets_on_detach(rig: Rig) -> None:
    """|v| > 0.8·v_max で ClampNote "runaway_guard" と kd ≥ guard_kd。外すとラッチも解除される。"""
    feedback_ready(rig, RunMode.OPERATION, ids=(1,), position=0.0, velocity=4.5)
    h = rig.loop.attach(
        MitDirect(MitParams(kd=-0.05, friction_comp=0.03)), {"motor": rig.motors[1]}
    )
    _, _, _, kd, tq = mit_values(rig.tick()[0])
    assert kd >= PROFILE.mit_host.guard_kd - 1e-3 and abs(tq) < 1e-3
    notes = [p for t, p in rig.log if t == CONTROL_CLAMPED][0]["notes"]
    assert {n.field for n in notes} == {"kd", "friction_comp"}
    assert all("runaway_guard" in n.reason for n in notes)
    assert rig.guard.runaway_active(1)
    rig.loop.detach(h)
    assert not rig.guard.runaway_active(1)


def test_current_mode_runaway_guard_brakes_iq_ref(rig: Rig) -> None:
    """電流モード: |v| > 0.8·速度上限 のとき、iq_ref は制動方向になり、cmd.cur にも記録される。"""
    feedback_ready(rig, RunMode.CURRENT, ids=(1,), velocity=4.5)
    rig.loop.attach(Current(CurrentParams(iq=0.3)), {"motor": rig.motors[1]})
    got = {k: v for _, k, v in writes(rig.tick())}
    assert got["iq_ref"] == pytest.approx(-0.3)
    assert rig.hub.latest("m1.cmd.cur")[1] == pytest.approx(-0.3)
    notes = [p for t, p in rig.log if t == CONTROL_CLAMPED][0]["notes"]
    assert "runaway_guard" in notes[0].reason
    rig.feed(1, velocity=0.5)  # 0.4·5 = 2 を下回ると通常の指令へ戻る
    got = {k: v for _, k, v in writes(rig.tick())}
    assert got["iq_ref"] == pytest.approx(0.3)


def test_high_tier_passes_larger_values() -> None:
    r = Rig(ids=(1,), tier=PowerTier.HIGH)
    try:
        feedback_ready(r, RunMode.VELOCITY, ids=(1,))
        r.loop.attach(Velocity(VelocityParams(velocity=30.0)), {"motor": r.motors[1]})
        got = {k: v for _, k, v in writes(r.tick())}
        assert got["spd_ref"] == pytest.approx(30.0)
    finally:
        r.close()


def test_tier_change_applies_on_next_tick(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    rig.loop.attach(Velocity(VelocityParams(velocity=3.0)), {"motor": rig.motors[1]})
    assert dict((k, v) for _, k, v in writes(rig.tick()))["spd_ref"] == pytest.approx(3.0)
    rig.guard.set_tier(PowerTier.LOW)
    rig.loop.handles()[0].controller.update_params(VelocityParams(velocity=9.0))
    assert dict((k, v) for _, k, v in writes(rig.tick()))["spd_ref"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# attach / detach
# ---------------------------------------------------------------------------
def test_one_motor_one_controller_and_validation(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY)
    h = rig.loop.attach(Velocity(), {"motor": rig.motors[1]})
    assert rig.loop.handle_of(1) is h and rig.loop.handle_of(2) is None
    with pytest.raises(ProtocolError, match="割り当て済み"):
        rig.loop.attach(Jog(), {"motor": rig.motors[1]})
    with pytest.raises(ProtocolError, match="slots"):
        rig.loop.attach(Velocity(), {"leader": rig.motors[2]})
    with pytest.raises(ProtocolError, match="run_mode"):  # モータは速度モード
        rig.loop.attach(Csp(), {"motor": rig.motors[2]})
    rig.loop.detach(h)
    rig.loop.attach(Jog(), {"motor": rig.motors[1]})  # 外せば再割り当て可


def test_detach_sends_on_stop_once_and_is_idempotent(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    h = rig.loop.attach(Velocity(VelocityParams(velocity=3.0)), {"motor": rig.motors[1]})
    rig.tick()
    rig.tick()
    rig.loop.detach(h)
    out = rig.drain()
    assert [(k, v) for _, k, v in writes(out)] == [("spd_ref", 0.0)]  # on_stop = 速度 0
    rig.loop.detach(h)  # 冪等
    assert rig.drain() == []
    assert rig.tick() == []  # もう何も送らない
    assert rig.loop.handles() == []


def test_detach_without_on_stop(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    h = rig.loop.attach(Velocity(VelocityParams(velocity=3.0)), {"motor": rig.motors[1]})
    rig.tick()
    rig.loop.detach(h, send_on_stop=False)
    assert rig.drain() == []


def test_detach_on_stop_for_position_controller_holds_position(rig: Rig) -> None:
    feedback_ready(rig, RunMode.CSP, ids=(1,), position=1.0)
    h = rig.loop.attach(Csp(CspParams(position=3.0, speed_limit=1.0)), {"motor": rig.motors[1]})
    got = writes(rig.tick())
    assert [(k, round(v, 3)) for _, k, v in got] == [("limit_spd", 1.0), ("loc_ref", 3.0)]
    rig.loop.detach(h)
    (_, key, val), = writes(rig.drain())
    assert key == "loc_ref" and val == pytest.approx(1.0, abs=1e-2)  # 現在位置保持


def test_on_stop_failure_disables_motor(rig: Rig) -> None:
    class Bad(Velocity):
        def on_stop(self, states):
            raise RuntimeError("boom")

    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    h = rig.loop.attach(Bad(), {"motor": rig.motors[1]})
    rig.loop.detach(h)
    out = rig.drain()
    assert CommType.STOP in types(out)
    assert any(t == CONTROL_ERROR for t, _ in rig.log)


def test_attach_on_start_failure_registers_nothing(rig: Rig) -> None:
    class Bad(Velocity):
        def on_start(self, states):
            raise RuntimeError("no")

    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    with pytest.raises(RuntimeError):
        rig.loop.attach(Bad(), {"motor": rig.motors[1]})
    assert rig.loop.handles() == []
    rig.loop.attach(Velocity(), {"motor": rig.motors[1]})  # 割り当ては残っていない


# ---------------------------------------------------------------------------
# コントローラの例外
# ---------------------------------------------------------------------------
class Exploding(Velocity):
    def __init__(self, params=None, after: int = 1) -> None:
        super().__init__(params)
        self.n = 0
        self.after = after

    def step(self, t, dt, states):
        self.n += 1
        if self.n > self.after:
            raise RuntimeError("controller bug")
        return super().step(t, dt, states)


def test_controller_exception_detaches_and_disables_only_that_motor(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY)
    bad = Exploding(VelocityParams(velocity=1.0), after=1)
    rig.loop.attach(bad, {"motor": rig.motors[1]})
    rig.loop.attach(Velocity(VelocityParams(velocity=1.0)), {"motor": rig.motors[2]})
    rig.tick()  # 1 周期目は正常
    out = rig.tick()  # 2 周期目に例外
    stops = [parse_id(f.can_id).dest for f in out if parse_id(f.can_id).comm_type == CommType.STOP]
    assert stops == [1]  # モータ 1 だけ無効化(type 4)
    assert [m for m, k, _ in writes(out) if k == "spd_ref"] == [2]  # 2 は継続
    assert rig.loop.handle_of(1) is None and rig.loop.handle_of(2) is not None
    errs = [p for t, p in rig.log if t == CONTROL_ERROR]
    assert len(errs) == 1 and errs[0]["motor_ids"] == [1]
    assert isinstance(errs[0]["error"], RuntimeError)
    assert rig.fake.mode[1] == MotorMode.RESET  # FakeMotors が type 4 を受けた
    assert writes(rig.tick(), 1) == []  # 外れたモータには以後送らない


def test_nan_command_is_a_controller_failure(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    rig.loop.attach(Velocity(VelocityParams(velocity=math.nan)), {"motor": rig.motors[1]})
    out = rig.tick()
    assert types(out) == [CommType.STOP]  # NaN は SafetyError → 無効化
    assert rig.loop.handles() == []
    assert rig.log[-1][0] == CONTROL_ERROR


def test_wrong_command_kind_for_run_mode_fails(rig: Rig) -> None:
    class Wrong(Velocity):
        def step(self, t, dt, states):
            return {"motor": SetpointCmd("current", 0.1)}

    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    rig.loop.attach(Wrong(), {"motor": rig.motors[1]})
    out = rig.tick()
    assert types(out) == [CommType.STOP]


# ---------------------------------------------------------------------------
# keepalive / Hold
# ---------------------------------------------------------------------------
def test_keepalive_hold_csp_latches_position(rig: Rig) -> None:
    feedback_ready(rig, RunMode.CSP, ids=(1,), position=1.0)
    rig.loop.keepalive(rig.motors[1], HoldCmd())
    (_, key, val), = writes(rig.tick())
    assert key == "loc_ref" and val == pytest.approx(1.0, abs=1e-2)
    rig.feed(1, position=1.4)  # 外力で動かされても保持位置は変わらない
    (_, key, val), = writes(rig.tick())
    assert val == pytest.approx(1.0, abs=1e-2)
    # 置き換え(再 keepalive)で再ラッチ
    rig.loop.keepalive(rig.motors[1], HoldCmd())
    (_, _, val), = writes(rig.tick())
    assert val == pytest.approx(1.4, abs=1e-2)
    rig.loop.clear_keepalive(1)
    assert rig.tick() == []


def test_keepalive_hold_by_run_mode(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    feedback_ready(rig, RunMode.OPERATION, ids=(2,), position=0.5)
    rig.loop.keepalive(rig.motors[1], HoldCmd())
    rig.loop.keepalive(rig.motors[2], HoldCmd())
    out = rig.tick()
    w = writes(out)
    assert [(m, k, v) for m, k, v in w] == [(1, "spd_ref", 0.0)]  # 速度モードは 0 rad/s
    mit = [f for f in out if parse_id(f.can_id).comm_type == CommType.MIT_CTRL]
    assert len(mit) == 1 and parse_id(mit[0].can_id).dest == 2
    p, v, kp, kd, tq = mit_values(mit[0])
    assert (p, v, tq) == pytest.approx((0.5, 0.0, 0.0), abs=1e-2)
    assert kp == pytest.approx(5.0, abs=0.02) and kd == pytest.approx(0.3, abs=0.001)


def test_keepalive_skips_without_feedback_or_run_mode(rig: Rig) -> None:
    rig.loop.keepalive(rig.motors[1], HoldCmd(), run_mode=RunMode.CSP)
    assert rig.tick() == []  # フィードバック未受信: 位置不明なので送らない
    rig.feed(1, position=0.2)
    assert len(writes(rig.tick())) == 1  # 受信後は送る(run_mode は引数)
    rig.loop.keepalive(rig.motors[2], HoldCmd())  # run_mode 不明
    rig.feed(2)
    assert writes(rig.tick(), 2) == []


def test_keepalive_rejected_for_attached_and_dropped_on_attach(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    rig.loop.keepalive(rig.motors[1], HoldCmd())
    h = rig.loop.attach(Velocity(VelocityParams(velocity=2.0)), {"motor": rig.motors[1]})
    got = [(k, v) for _, k, v in writes(rig.tick())]
    assert ("spd_ref", 2.0) in got and len(got) == 3  # keepalive は破棄され二重送信なし
    with pytest.raises(ProtocolError):
        rig.loop.keepalive(rig.motors[1], HoldCmd())
    rig.loop.detach(h)
    rig.drain()
    rig.loop.keepalive(rig.motors[1], HoldCmd())  # detach 後は可
    assert [(k, v) for _, k, v in writes(rig.tick())] == [("spd_ref", 0.0)]


def test_keepalive_failure_disables_motor(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    rig.loop.keepalive(rig.motors[1], SetpointCmd("velocity", math.inf))
    out = rig.tick()
    assert types(out) == [CommType.STOP]
    assert rig.tick() == []  # keepalive は外れた


# ---------------------------------------------------------------------------
# Jog / Pp / Sine をループ越しに
# ---------------------------------------------------------------------------
def test_jog_through_loop(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    j = Jog(JogParams(speed=1.0, accel=20.0, current_limit=0.5))
    rig.loop.attach(j, {"motor": rig.motors[1]})
    assert dict((k, v) for _, k, v in writes(rig.tick()))["spd_ref"] == 0.0
    j.set_direction(+1)
    assert [(k, v) for _, k, v in writes(rig.tick())] == [("spd_ref", 1.0)]
    j.set_direction(-1)
    assert [(k, v) for _, k, v in writes(rig.tick())] == [("spd_ref", -1.0)]
    j.set_direction(0)
    assert [(k, v) for _, k, v in writes(rig.tick())] == [("spd_ref", 0.0)]


def test_pp_writes_vel_max_and_acc_set(rig: Rig) -> None:
    feedback_ready(rig, RunMode.PP, ids=(1,), position=0.0)
    rig.loop.attach(Pp(PpParams(position=1.0, velocity=3.0, accel=8.0)), {"motor": rig.motors[1]})
    assert [(k, round(v, 3)) for _, k, v in writes(rig.tick())] == [
        ("vel_max", 3.0),
        ("acc_set", 8.0),
        ("loc_ref", 1.0),
    ]


def test_sine_torque_uses_mit_and_clamps(rig: Rig) -> None:
    feedback_ready(rig, RunMode.OPERATION, ids=(1,), position=0.0)
    s = SineTest(SineParams(target="torque", amplitude=3.0, frequency=1.0))
    rig.loop.attach(s, {"motor": rig.motors[1]})
    rig.tick()  # t = 0
    time.sleep(0.25)  # 1/4 周期 → ほぼ最大(3 N·m は LOW で 0.5 に制限)
    out = rig.tick()
    _, _, kp, kd, tq = mit_values(out[0])
    assert (kp, kd) == (0.0, 0.0) and 0.4 < tq <= 0.5 + 0.001


# ---------------------------------------------------------------------------
# スレッド: 周期・統計・停止
# ---------------------------------------------------------------------------
def _run_loop_window() -> tuple[object, int, float]:
    """200 Hz のループを 1 秒回して (統計, 送った spd_ref の数, 経過秒) を返す。"""
    r = Rig(ids=(1,))
    try:
        feedback_ready(r, RunMode.VELOCITY, ids=(1,))
        r.loop.attach(Velocity(VelocityParams(velocity=1.0)), {"motor": r.motors[1]})
        r.drain()
        t0 = clock.now()
        r.loop.start()
        time.sleep(1.0)
        s = r.loop.stats
        r.loop.stop()
        elapsed = clock.now() - t0
        total_spd = sum(1 for _, k, _ in writes(r.drain()) if k == "spd_ref")
        return s, total_spd, elapsed
    finally:
        r.close()


def test_loop_rate_accuracy_and_stats() -> None:
    """周期の精度は「1 秒の窓を最大 5 回測り、1 回でも設計値を満たせばよい」とする。

    ControlLoop は通常スレッド(ms 周期)なので、OS のスケジューラに CPU を取られる(他のテスト・
    他のプロセスが重いとき)と、1 回の窓の jitter / 周期は数 ms 単位で悪化する(空いた機械で
    jitter 約 1 ms・負荷が高いと 5〜25 ms)。窓ごとの悪化は独立なので、実装の欠陥(周期が合わない・
    取りこぼす)ならどの窓でも外れ、負荷による悪化なら空いた窓で設計値が出る。以前は 1 回だけ
    測って固定の許容で判定していたため、他の処理が走っていると不定期に失敗した。
    フレーム数の整合(1 周期 1 フレーム)は負荷によらず成り立つので、全部の窓で厳密に検査する。
    """
    problems: list[str] = []
    for attempt in range(5):
        s, total_spd, elapsed = _run_loop_window()
        print(
            f"\n[loop] #{attempt} rate={s.rate_hz:.1f} Hz jitter_p99={s.jitter_ms_p99:.3f} ms "
            f"overruns={s.overruns} ticks={s.ticks} frames={total_spd} elapsed={elapsed:.3f}"
        )
        # --- 負荷によらず成り立つ検査(どの窓でも) ---
        # 1 周期 1 フレーム(spd_ref)+ 初回だけ制限書込 2 本(limit_cur / acc_rad)
        assert s.frames_sent == s.ticks + 2
        assert total_spd == s.ticks
        assert s.ticks <= 210  # 1 秒で 200 周期を超えない(位相は絶対時刻で刻む)
        assert s.send_errors == 0 and s.skipped == 0
        # --- 周期の精度(設計値) ---
        problems = []
        if s.rate_hz != pytest.approx(200.0, rel=0.05):
            problems.append(f"rate {s.rate_hz:.1f}")
        if abs(s.last_dt - 0.005) >= 0.003:
            problems.append(f"last_dt {s.last_dt * 1000:.2f} ms")
        if s.jitter_ms_p99 >= 3.0:  # 空いた Windows で 1 ms 前後
            problems.append(f"jitter_p99 {s.jitter_ms_p99:.2f} ms")
        if s.overruns > 3:
            problems.append(f"overruns {s.overruns}")
        if not 190 <= s.ticks <= 210:
            problems.append(f"ticks {s.ticks}")
        if not problems:
            return
    pytest.fail(f"5 回の窓のどれも設計値を満たさなかった(最後の窓): {problems}")


def test_loop_start_stop_and_set_rate() -> None:
    r = Rig(ids=(1,), rate_hz=100.0)
    try:
        assert r.loop.rate_hz == pytest.approx(100.0)
        r.loop.start()
        assert r.loop.running
        with pytest.raises(RuntimeError):
            r.loop.start()
        r.loop.set_rate(400.0)
        assert r.loop.rate_hz == pytest.approx(400.0)
        r.loop.stop()
        assert not r.loop.running
        r.loop.start()  # 再開できる
        time.sleep(0.05)
        r.loop.stop()
        with pytest.raises(ValueError):
            r.loop.set_rate(0)
        with pytest.raises(ValueError):
            ControlLoop(r.bus, r.guard, rate_hz=-1)
    finally:
        r.close()


def test_loop_thread_survives_tick_exception() -> None:
    r = Rig(ids=(1,))
    try:
        calls = {"n": 0}

        def boom(now):
            calls["n"] += 1
            raise RuntimeError("tick failed")

        r.loop._tick = boom
        r.loop.start()
        deadline = time.monotonic() + 2.0  # 負荷の高い環境でも固定待ちに頼らない
        while calls["n"] < 5 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert r.loop.running and calls["n"] >= 5
        r.loop.stop()
    finally:
        r.close()


def test_overrun_is_counted_and_schedule_realigned() -> None:
    r = Rig(ids=(1,), rate_hz=100.0)
    try:
        slow = {"first": True}
        real = r.loop._tick

        def tick(now):
            if slow["first"]:
                slow["first"] = False
                time.sleep(0.035)  # 3 周期ぶん遅れる
            real(now)

        r.loop._tick = tick
        r.loop.start()
        time.sleep(0.3)
        s = r.loop.stats
        r.loop.stop()
        assert 1 <= s.overruns <= 3  # 負荷の高い環境では追加の遅れが入りうる
        assert s.rate_hz < 100.0 * 1.1  # 遅れを取り戻そうとして連打しない
    finally:
        r.close()


def test_backlog_skips_tick() -> None:
    r = Rig(ids=(1,))
    try:
        feedback_ready(r, RunMode.VELOCITY, ids=(1,))
        r.loop.attach(Velocity(VelocityParams(velocity=1.0)), {"motor": r.motors[1]})
        r.tr.gate = threading.Event()  # 送信を止めてキューを溜める
        from edulite05.core.protocol import private

        r.bus.send(private.get_uid(1, HOST), Priority.BACKGROUND)
        assert r.tr.entered.wait(2.0)
        r.bus.send_many([private.get_uid(1, HOST)] * 70, Priority.REQUEST)
        r.loop._tick(clock.now())
        assert r.loop.stats.skipped == 1 and r.loop.stats.frames_sent == 0
        r.tr.gate.set()
    finally:
        r.close()


def test_send_failure_is_counted_not_fatal(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    rig.loop.attach(Velocity(VelocityParams(velocity=1.0)), {"motor": rig.motors[1]})
    rig.bus.stop()  # send_many が TransportError
    rig.loop._tick(clock.now())
    assert rig.loop.stats.send_errors == 1 and len(rig.loop.handles()) == 1


def test_timer_resolution_context_is_harmless() -> None:
    with timer_resolution(1):
        pass
    if sys.platform == "win32":
        with timer_resolution(1), timer_resolution(1):  # 入れ子でも対で解除される
            pass


def test_controller_abc_is_abstract() -> None:
    with pytest.raises(TypeError):
        Controller()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# 停止の安全(phase2-fix): 古い指令の破棄・on_stop の最優先送信・指令抑止
# ---------------------------------------------------------------------------
def _stall_tx(r: Rig) -> None:
    """TX スレッドを 1 フレーム目で止める(以後に積んだフレームはキューに溜まる)。"""
    from edulite05.core.protocol import private

    r.tr.gate = threading.Event()
    r.bus.send(private.get_uid(9, HOST), Priority.BACKGROUND)
    assert r.tr.entered.wait(2.0)


def test_bus_purge_control_only_drops_target_control_frames(rig: Rig) -> None:
    from edulite05.core.protocol import private

    _stall_tx(rig)
    rig.bus.send_many([private.stop(1, HOST), private.stop(2, HOST)], Priority.CONTROL)
    rig.bus.send(private.get_uid(1, HOST), Priority.REQUEST)  # 同じモータでも CONTROL 以外は残す
    rig.bus.send(private.get_uid(1, HOST), Priority.ESTOP)
    assert rig.bus.queue_len() == 4
    assert rig.bus.purge_control([1]) == 1
    assert rig.bus.purge_control([]) == 0
    assert rig.bus.queue_len() == 3
    rig.tr.gate.set()
    assert wait_until(lambda: rig.bus.queue_len() == 0)
    time.sleep(0.02)
    dests = [(parse_id(f.can_id).comm_type, parse_id(f.can_id).dest) for f in rig.tr.sent]
    assert (CommType.STOP, 1) not in dests and (CommType.STOP, 2) in dests
    assert dests.count((CommType.GET_ID, 1)) == 2


def test_detach_on_stop_goes_ahead_of_stale_setpoints(rig: Rig) -> None:
    """停止時、キューに溜まった古い速度指令の後ろに on_stop(速度 0)が並ばない。"""
    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    h = rig.loop.attach(Velocity(VelocityParams(velocity=2.0)), {"motor": rig.motors[1]})
    rig.drain()
    _stall_tx(rig)
    for _ in range(6):
        rig.loop._tick(clock.now())  # 古い spd_ref=2 が溜まる
    assert rig.bus.queue_len() >= 6
    rig.loop.detach(h, send_on_stop=True)
    rig.tr.gate.set()
    assert wait_until(lambda: rig.bus.queue_len() == 0)
    time.sleep(0.02)
    vals = [v for _, k, v in writes(list(rig.tr.sent), 1) if k == "spd_ref"]
    assert 0.0 in vals
    first_zero = vals.index(0.0)
    assert all(v == 0.0 for v in vals[first_zero:])  # 0 の後に運転中の指令は出ない
    assert len(vals[:first_zero]) <= 1  # 送信中だった 1 本だけ


def test_inhibit_blocks_all_output_until_allowed(rig: Rig) -> None:
    feedback_ready(rig, RunMode.VELOCITY, ids=(1, 2))
    rig.loop.attach(Velocity(VelocityParams(velocity=1.0)), {"motor": rig.motors[1]})
    rig.loop.keepalive(rig.motors[2], HoldCmd())
    assert rig.tick()
    _stall_tx(rig)
    rig.loop._tick(clock.now())
    queued = rig.bus.queue_len()
    assert queued > 0
    handles = rig.loop.inhibit([1, 2])
    assert len(handles) == 1 and rig.loop.handle_of(1) is None
    assert rig.bus.queue_len() < queued  # キュー内の指令も捨てられた
    rig.tr.gate.set()
    rig.drain()
    assert rig.tick() == []  # 以後は何も送らない
    assert rig.loop.is_inhibited(1)
    rig.loop.keepalive(rig.motors[2], HoldCmd())  # 抑止中は無視される
    assert rig.tick() == []
    with pytest.raises(ProtocolError):
        rig.loop.attach(Velocity(VelocityParams(velocity=1.0)), {"motor": rig.motors[1]})
    rig.loop.allow([1, 2])
    rig.loop.keepalive(rig.motors[2], HoldCmd())
    assert writes(rig.tick(), 2)


def test_clear_keepalive_is_synchronous_with_loop_thread(rig: Rig) -> None:
    """実スレッドのループが動いていても、clear_keepalive が戻った後は何も送られない。"""
    feedback_ready(rig, RunMode.VELOCITY, ids=(1,))
    rig.loop.keepalive(rig.motors[1], HoldCmd())
    rig.loop.start()
    try:
        assert wait_until(lambda: len(rig.tr.sent) > 10)
        rig.loop.clear_keepalive(1)
        rig.drain()
        n = len(rig.tr.sent)
        time.sleep(0.05)  # 10 周期分
        assert len(rig.tr.sent) == n
    finally:
        rig.loop.stop()
