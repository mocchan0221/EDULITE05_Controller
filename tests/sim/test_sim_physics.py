"""SimMotor.step の 1 自由度物理・制御則の健全性。"""

import math

import pytest

from edulite05.core.protocol import private as pv
from edulite05.core.protocol.types import RunMode
from edulite05.sim.motor_model import SimMotor

H = 0xFD
M = 0x7F
DT = 0.001


def run(motor: SimMotor, seconds: float, dt: float = DT) -> None:
    for _ in range(round(seconds / dt)):
        motor.step(dt)


def mit(bench, pos=0.0, vel=0.0, kp=0.0, kd=0.0, tff=0.0):
    bench.one(pv.mit_command(M, H, bench.p.ranges, pos, vel, kp, kd, tff))


# -- 速度モード ---------------------------------------------------------------
def test_velocity_mode_converges_to_spd_ref(bench):
    m = bench.m
    bench.set_mode(RunMode.VELOCITY)
    bench.enable()
    bench.write("spd_ref", 10.0)
    run(m, 2.0)  # acc_rad 10 rad/s² → 1 s で到達
    assert m.velocity == pytest.approx(10.0, abs=0.05)
    assert m.position > 0


def test_velocity_mode_negative_and_acc_rad_ramp(bench):
    m = bench.m
    bench.set_mode(RunMode.VELOCITY)
    bench.enable()
    bench.write("acc_rad", 20.0)
    bench.write("spd_ref", -20.0)
    run(m, 0.5)
    assert m.velocity == pytest.approx(-10.0, abs=0.3)  # 20 rad/s² × 0.5 s
    run(m, 1.5)
    assert m.velocity == pytest.approx(-20.0, abs=0.05)


def test_velocity_mode_respects_limit_cur(bench):
    m = bench.m
    kt = m.physics.kt
    bench.set_mode(RunMode.VELOCITY)
    bench.write("limit_cur", 0.5)
    bench.write("acc_rad", 1000.0)
    bench.enable()
    bench.write("spd_ref", 30.0)
    peak = 0.0
    for _ in range(20):
        m.step(DT)
        peak = max(peak, abs(m.torque), abs(m.iq) * kt)
    assert peak <= kt * 0.5 + 1e-9
    assert peak > 0.99 * kt * 0.5  # 飽和している
    # 飽和中の運動: v = (τ/b)(1 - exp(-b t / J))
    tau, b, j = kt * 0.5, m.physics.damping, m.physics.inertia
    assert m.velocity == pytest.approx(tau / b * (1 - math.exp(-b / j * 0.02)), rel=0.02)
    assert m.velocity < 30.0
    run(m, 3.0)
    assert m.velocity == pytest.approx(30.0, abs=0.1)  # 時間をかければ到達する


def test_velocity_mode_respects_limit_torque(bench):
    m = bench.m
    bench.set_mode(RunMode.VELOCITY)
    bench.write("limit_torque", 0.2)
    bench.write("acc_rad", 1000.0)
    bench.enable()
    bench.write("spd_ref", 30.0)
    run(m, 0.05)
    assert abs(m.torque) <= 0.2 + 1e-6  # 0.2 は float32 に丸められて書かれる


# -- 電流モード ---------------------------------------------------------------
def test_current_mode_torque_is_kt_times_iq(bench):
    m = bench.m
    bench.set_mode(RunMode.CURRENT)
    bench.enable()
    bench.write("iq_ref", 2.0)
    run(m, 0.01)
    assert m.torque == pytest.approx(0.55 * 2.0)
    assert m.iq == pytest.approx(2.0)
    bench.write("iq_ref", -1.0)
    run(m, 0.01)
    assert m.torque == pytest.approx(-0.55)


def test_current_mode_accelerates_at_torque_over_inertia(bench):
    m = bench.m
    bench.set_mode(RunMode.CURRENT)
    bench.enable()
    bench.write("iq_ref", 1.0)
    run(m, 0.05)
    a = 0.55 / m.physics.inertia
    b_over_j = m.physics.damping / m.physics.inertia
    expected = a / b_over_j * (1 - math.exp(-b_over_j * 0.05))
    assert m.velocity == pytest.approx(expected, rel=0.01)


# -- 運控(MIT) --------------------------------------------------------------
def test_mit_with_kp_holds_position(bench):
    m = bench.m
    bench.enable()
    mit(bench, pos=1.0, kp=20.0, kd=1.0)
    run(m, 2.0)
    assert m.position == pytest.approx(1.0, abs=0.005)
    assert abs(m.velocity) < 1e-3


def _mit_far(profile, frame: str) -> SimMotor:
    m = SimMotor(motor_id=M, profile=profile, mit_frame=frame, initial_position=20.0)
    return m


def test_sim_mit_frame_multi_compares_with_multi_turn_position(profile, ctx):
    """mit_frame="multi"(既定・悲観側): 折り返し位置を p_set にすると 8π の誤差でトルクが出る。"""
    from conftest import Bench

    m = _mit_far(profile, "multi")
    assert SimMotor(motor_id=M, profile=profile).mit_frame == "multi"
    b = Bench(m, profile, ctx)
    b.enable()
    wrapped = m._wrap_position()
    assert wrapped == pytest.approx(20.0 - 8 * math.pi, abs=1e-6)
    mit(b, pos=wrapped, kp=1.0, kd=0.0)
    m.step(0.001)
    assert m.torque == pytest.approx(-6.0)  # 誤差 -8π × kp 1 → limit_torque 6 N·m で飽和


def test_sim_mit_frame_wrapped_compares_with_wrapped_position(profile, ctx):
    from conftest import Bench

    m = _mit_far(profile, "wrapped")
    b = Bench(m, profile, ctx)
    b.enable()
    mit(b, pos=m._wrap_position(), kp=20.0, kd=1.0)
    run(m, 1.0)
    assert abs(m.torque) < 0.01 and m.position == pytest.approx(20.0, abs=0.01)


def test_sim_feedback_wraps_and_mech_pos_is_multi_turn(profile, ctx):
    """実機(§8.0): type 2 の位置は ±4π で折り返し、mechPos は多回転値(13.02 → -12.11)。"""
    from conftest import Bench
    from edulite05.core.protocol import private as pvv

    m = SimMotor(motor_id=M, profile=profile, initial_position=13.02)
    b = Bench(m, profile, ctx)
    fb = pvv.decode(m.feedback_frame(), ctx)
    assert fb.position == pytest.approx(13.02 - 8 * math.pi, abs=1e-3)
    assert b.read("mechPos").value == pytest.approx(13.02, abs=1e-4)
    m.position = -13.02
    assert pvv.decode(m.feedback_frame(), ctx).position == pytest.approx(
        -13.02 + 8 * math.pi, abs=1e-3
    )
    with pytest.raises(ValueError):
        SimMotor(motor_id=M, profile=profile, mit_frame="bogus")


def test_mit_position_error_under_external_load(bench):
    m = bench.m
    bench.enable()
    mit(bench, pos=0.0, kp=20.0, kd=1.0)
    m.ext_torque = 0.5
    run(m, 2.0)
    assert m.position == pytest.approx(0.5 / 20.0, abs=0.002)  # τ/Kp


def test_mit_damping_tracks_velocity(bench):
    m = bench.m
    bench.enable()
    mit(bench, vel=1.0, kd=1.0)
    run(m, 1.0)
    assert m.velocity == pytest.approx(1.0, abs=0.01)


def test_mit_feedforward_torque(bench):
    m = bench.m
    bench.enable()
    mit(bench, tff=1.0)
    run(m, 0.01)
    assert m.torque == pytest.approx(1.0, abs=1e-3)


def test_mit_max_gains_are_stable_and_torque_saturates(bench):
    m = bench.m
    bench.enable()
    mit(bench, pos=1.0, kp=500.0, kd=5.0)
    peak = 0.0
    for _ in range(2000):
        m.step(DT)
        peak = max(peak, abs(m.torque))
        assert math.isfinite(m.position) and abs(m.velocity) <= 50.0
    assert peak == pytest.approx(6.0, abs=1e-6)  # limit_torque / 物理上限で飽和
    assert m.position == pytest.approx(1.0, abs=0.01)


def test_mit_ignored_unless_enabled_and_in_operation_mode(bench):
    m = bench.m
    mit(bench, pos=1.0, kp=20.0, kd=1.0)  # 無効のまま
    run(m, 0.1)
    assert m.position == 0.0 and m.torque == 0.0
    bench.set_mode(RunMode.CURRENT)
    bench.enable()
    run(m, 0.1)
    assert m.torque == 0.0  # 電流モードでは type 1 は効かない(iq_ref=0)


# -- CSP / PP ----------------------------------------------------------------
def test_csp_tracks_loc_ref_with_limit_spd(bench):
    m = bench.m
    bench.set_mode(RunMode.CSP)
    bench.write("limit_spd", 2.0)
    bench.enable()
    bench.write("loc_ref", 3.0)
    vmax = 0.0
    for _ in range(3000):
        m.step(DT)
        vmax = max(vmax, m.velocity)
    assert vmax <= 2.0 + 0.15
    assert vmax > 1.8
    assert m.position == pytest.approx(3.0, abs=0.005)


def test_csp_enable_holds_current_position(bench):
    m = bench.m
    m.position = 2.038
    m.step(DT)  # 停止中は loc_ref が現在位置に追従する
    bench.set_mode(RunMode.CSP)
    bench.enable()
    run(m, 0.5)
    assert m.position == pytest.approx(2.038, abs=0.002)


def test_pp_trapezoid_profile(bench):
    m = bench.m
    bench.set_mode(RunMode.PP)
    bench.write("vel_max", 2.0)
    bench.write("acc_set", 4.0)
    bench.enable()
    bench.write("loc_ref", 5.0)
    vmax, over = 0.0, 0.0
    for _ in range(6000):
        m.step(DT)
        vmax = max(vmax, m.velocity)
        over = max(over, m.position - 5.0)
    assert vmax == pytest.approx(2.0, abs=0.1)
    assert over < 0.02  # オーバーシュートが小さい
    assert m.position == pytest.approx(5.0, abs=0.005)
    assert abs(m.velocity) < 0.01
    # 台形: 加速 0.5 s + 巡航 + 減速 0.5 s、移動距離 5 rad → 約 3 s かかる
    m2 = SimMotor(motor_id=M, profile=m.profile)
    m2.set_param("run_mode", 1)
    m2.set_param("vel_max", 2.0)
    m2.set_param("acc_set", 4.0)
    m2.handle(pv.enable(M, H))
    m2.set_param("loc_ref", 5.0)
    run(m2, 2.0)
    assert m2.position < 4.9  # 2 s ではまだ終わらない(約 3 s)
    run(m2, 2.0)
    assert m2.position == pytest.approx(5.0, abs=0.005)


def test_pp_vel_max_zero_stops(bench):
    m = bench.m
    bench.set_mode(RunMode.PP)
    bench.write("vel_max", 2.0)
    bench.write("acc_set", 4.0)
    bench.enable()
    bench.write("loc_ref", 50.0)
    run(m, 1.5)
    assert m.velocity > 1.5
    bench.write("vel_max", 0.0)  # 緊急停止(§3.3)
    run(m, 1.5)
    assert abs(m.velocity) < 0.01
    stopped_at = m.position
    run(m, 0.5)
    assert m.position == pytest.approx(stopped_at, abs=0.005)
    assert stopped_at < 50.0


# -- 無効化・惰行 -------------------------------------------------------------
def test_disable_coasts_with_viscous_decay(bench):
    m = bench.m
    bench.set_mode(RunMode.VELOCITY)
    bench.write("acc_rad", 1000.0)
    bench.enable()
    bench.write("spd_ref", 10.0)
    run(m, 0.5)
    assert m.velocity == pytest.approx(10.0, abs=0.05)
    bench.stop()
    assert m.torque == 0.0
    v0 = m.velocity
    prev = v0
    for _ in range(100):
        m.step(DT)
        assert m.velocity <= prev + 1e-12  # 単調に減速
        prev = m.velocity
    tau = m.physics.inertia / m.physics.damping
    assert m.velocity == pytest.approx(v0 * math.exp(-0.1 / tau), rel=0.01)
    assert m.velocity > 0.5 * v0  # すぐには止まらない(惰行)
    assert m.torque == 0.0


def test_external_torque_spins_disabled_motor(bench):
    m = bench.m
    m.ext_torque = 0.1  # 手で回す
    run(m, 0.2)
    assert m.velocity > 1.0 and m.position > 0.0
    assert m.params[0x7016] == pytest.approx(m.position)  # loc_ref が追従


def test_speed_is_physically_clamped(bench):
    m = bench.m
    bench.set_mode(RunMode.CURRENT)
    bench.enable()
    bench.write("iq_ref", 11.0)
    run(m, 2.0)
    assert m.velocity <= 50.0 + 1e-9


# -- 温度 --------------------------------------------------------------------
def test_temperature_rises_with_current_and_cools_down(bench):
    m = bench.m
    t0 = m.temperature
    assert t0 == pytest.approx(29.0)
    run(m, 5.0)
    assert m.temperature == pytest.approx(t0, abs=1e-6)  # 電流ゼロなら変化なし
    bench.set_mode(RunMode.CURRENT)
    bench.enable()
    m.ext_torque = -0.55 * 5.0  # 回転しないよう反力で釣り合わせる(5 A を流し続ける)
    bench.write("iq_ref", 5.0)
    run(m, 20.0, dt=0.01)
    hot = m.temperature
    assert hot > t0 + 1.0
    assert hot < t0 + 30  # ゆっくり上がる
    bench.stop()
    run(m, 20.0, dt=0.01)
    assert m.temperature < hot  # 無通電で冷める
    assert m.temperature > t0 - 1e-6


def test_overtemp_trips_motor_in_run(bench):
    m = bench.m
    bench.set_mode(RunMode.CURRENT)
    bench.enable()
    m.temperature = 134.99
    m.ext_torque = -0.55 * 11.0
    bench.write("iq_ref", 11.0)
    for _ in range(200):
        m.step(0.05)
    assert m.faults & 1 and not m.enabled
