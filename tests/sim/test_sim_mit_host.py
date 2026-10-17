"""SimMotor(クーロン摩擦あり)+ SafetyGuard の閉ループ: 負の kd + 摩擦補償の暴走と暴走ガード。

実時間は使わず、制御周期 200 Hz で `clamp_mit` → MIT フレーム → `SimMotor.handle` → `step` を回す。
"""

from __future__ import annotations

from edulite05.core.control.commands import MitCmd
from edulite05.core.device.state import MotorState
from edulite05.core.protocol import private as pv
from edulite05.core.protocol.types import RunMode
from edulite05.core.safety import PowerTier, SafetyGuard

H = 0xFD
M = 0x7F
CTRL_DT = 0.005  # 200 Hz


class Closed:
    def __init__(self, bench, *, coulomb: float = 0.03) -> None:
        self.b = bench
        self.m = bench.m
        self.m.physics.coulomb = coulomb
        self.m.physics.damping = 0.014  # 実機の粘性(hw_bench.md HW-4)
        self.guard = SafetyGuard(bench.p, PowerTier.LOW)
        bench.enable()
        self.v_max = self.guard.limits.speed
        self.engaged = False
        self.peak = 0.0
        self.t = 0.0
        self.trace: list[tuple[float, float, bool, float]] = []  # (t, v, guard, 指令時の v)

    def tick(self, cmd: MitCmd) -> None:
        m = self.m
        s = MotorState(M, online=True, position=m.position, position_multi=m.position,
                       velocity=m.velocity)
        v_before = m.velocity
        c, notes = self.guard.clamp_mit(cmd, s)
        self.b.one(pv.mit_command(M, H, self.b.p.ranges, c.position, c.velocity, c.kp, c.kd,
                                  c.torque))
        if any("runaway_guard" in n.reason for n in notes):
            self.engaged = True
        for _ in range(5):  # 1 ms 刻みで 5 ms 進める
            m.step(0.001)
        self.t += CTRL_DT
        self.peak = max(self.peak, abs(m.velocity))
        self.trace.append((self.t, m.velocity, self.guard.runaway_active(M), v_before))

    def run(self, cmd: MitCmd, seconds: float, push: float = 0.0) -> None:
        self.m.ext_torque = push
        for _ in range(round(seconds / CTRL_DT)):
            self.tick(cmd)


def test_friction_is_present_without_compensation(bench):
    """前提: 補償なし(kd = 0)では、小さな押しはクーロン摩擦で止まる。"""
    c = Closed(bench)
    c.run(MitCmd(), 0.3, push=0.02)  # 0.02 < 0.03(クーロン)
    assert abs(bench.m.velocity) < 0.05


def test_compensation_alone_does_not_run_away_but_makes_it_free(bench):
    """friction_comp 0.03 + kd = 0: 摩擦を打ち消して、小さな押しで粘性の終端速度まで動く。"""
    c = Closed(bench)
    c.run(MitCmd(friction_comp=0.03), 1.0, push=0.02)
    assert 1.0 < bench.m.velocity < c.v_max * 0.8  # (0.02 - 0.03 + 0.03) / 0.014 ≈ 1.4 rad/s
    assert not c.engaged


def test_negative_kd_runs_away_guard_engages_and_decays(bench):
    c = Closed(bench)
    cmd = MitCmd(kd=-0.05, friction_comp=0.03)
    c.run(cmd, 0.15, push=0.02)  # 小さな押しで立ち上がる
    c.m.ext_torque = 0.0
    c.run(cmd, 1.5)
    assert c.engaged, "暴走ガードが働かなかった"
    first = next(i for i, (_, _, g, _) in enumerate(c.trace) if g)
    # ガード開始は 0.8·v_max の直後(1 周期ぶんの加速まで)
    v_at = abs(c.trace[first][3])
    assert 0.8 * c.v_max < v_at < 0.8 * c.v_max + 1.0
    assert c.peak < 1.2 * c.v_max, "ガードしても速度が暴走した"  # 実測 ≈ 1.001·v_max
    # ガード中に 0.4·v_max を下回るまで減速する(ヒステリシス)
    released = [i for i, (_, _, g, _) in enumerate(c.trace) if i > first and not g]
    assert released, "ガードが解除されるまで減速しなかった"
    v_rel = abs(c.trace[released[0]][3])  # 解除を判断した周期の速度
    assert v_rel < 0.4 * c.v_max + 1e-9


def test_guard_makes_speed_decay_when_command_keeps_negative_kd(bench):
    """押しをやめた後、ガードは速度を 0.4·v_max 未満へ落とす(その後は再び立ち上がりうる)。"""
    c = Closed(bench)
    cmd = MitCmd(kd=-0.05, friction_comp=0.03)
    c.run(cmd, 0.15, push=0.02)
    c.m.ext_torque = 0.0
    c.run(cmd, 0.6)
    # 限界サイクル: 速度は常に v_max 近傍で有界
    assert max(abs(v) for _, v, _, _ in c.trace) < 1.2 * c.v_max
    assert min(abs(v) for t, v, _, _ in c.trace if t > 0.3) < 0.4 * c.v_max


def test_stops_and_decays_after_user_turns_compensation_off(bench):
    c = Closed(bench)
    c.run(MitCmd(kd=-0.05, friction_comp=0.03), 0.15, push=0.02)
    c.m.ext_torque = 0.0
    c.run(MitCmd(kd=0.05), 1.0)  # ユーザが減衰ありに戻す
    assert abs(bench.m.velocity) < 0.05


def test_mode_is_operation(bench):
    c = Closed(bench)
    assert int(c.m.run_mode) == int(RunMode.OPERATION)


def test_current_mode_step_stays_near_tier_speed(bench):
    """電流モードのステップ: ファームの limit_spd は効かない(実機で LOW でも 23.9 rad/s)。
    暴走ガードが iq を制動方向へ置き換え、速度は段階の上限付近で有界になる。"""
    from edulite05.core.control.commands import SetpointCmd

    bench.set_mode(RunMode.CURRENT)
    bench.enable()
    m = bench.m
    m.physics.damping = 0.002  # 負荷なしに近い(粘性だけ)
    guard = SafetyGuard(bench.p, PowerTier.LOW)
    v_max = guard.limits.speed
    engaged = False
    peak = 0.0
    for _ in range(round(2.0 / CTRL_DT)):
        s = MotorState(M, online=True, position=m.position, position_multi=m.position,
                       velocity=m.velocity)
        c, notes = guard.clamp_setpoint(SetpointCmd("current", 0.3), s)
        engaged |= any("runaway_guard" in n.reason for n in notes)
        bench.write("iq_ref", c.value)
        for _ in range(5):
            m.step(0.001)
        peak = max(peak, abs(m.velocity))
    assert engaged and peak < 1.2 * v_max, peak


def test_current_mode_without_guard_runs_away(bench):
    """対照: ガードなしだと同じ電流で段階の上限を大きく超える(実機の 23.9 rad/s の再現)。"""
    bench.set_mode(RunMode.CURRENT)
    bench.enable()
    bench.write("iq_ref", 0.3)
    for _ in range(round(2.0 / 0.001)):
        bench.m.step(0.001)
    assert bench.m.velocity > 20.0
