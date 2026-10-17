"""Bilateral を MotorService + SimTransport(2 台)で動かす統合テスト(phase4_5.md §O)。

実時間で動くので判定は緩め。leader = ID 127、follower = ID 1。
外力は SimMotor.ext_torque をパルスで加える(2 台は結合しているので連続で加えると加速し続ける)。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from edulite05.core import clock
from edulite05.core.control.controllers import Bilateral
from edulite05.core.control.params import BilateralParams
from edulite05.core.control.service import SERVICE_CLAMPED, MotorService, UiState
from edulite05.core.device.profile import MotorProfile
from edulite05.core.errors import ProtocolError
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.session import Session
from edulite05.sim.transport import SimTransport, make_motors

PROFILE = MotorProfile.load_builtin("el05")
L, F = 127, 1  # leader / follower の ID


def wait_until(pred: Callable[[], object], timeout: float = 3.0) -> bool:
    end = clock.now() + timeout
    while clock.now() < end:
        if pred():
            return True
        time.sleep(0.005)
    return bool(pred())


class Rig:
    def __init__(self) -> None:
        self.sim = SimTransport(make_motors(2, profile=PROFILE))
        self.session = Session(self.sim, PROFILE)
        self.session.open()
        self.svc = MotorService(self.session)
        self.events: list[Any] = []
        self.session.events.subscribe(SERVICE_CLAMPED, self.events.append)
        for mid in (L, F):
            self.session.motor(mid)
        self.svc.open()
        assert wait_until(lambda: all(self.svc.ui_state(i) is UiState.DISABLED for i in (L, F)))
        self.lm, self.fm = self.sim.motor(L), self.sim.motor(F)

    def start(self, params: BilateralParams, lp: float = 0.0, fp: float = 0.0) -> Bilateral:
        self.lm.position, self.fm.position = lp, fp
        assert wait_until(lambda: self._fb(lp, fp))
        for mid in (L, F):
            self.svc.enable(mid, RunMode.OPERATION).result(5.0)
        ctrl = Bilateral(params)
        self.svc.start([L, F], ctrl).result(1.0)
        assert self.svc.ui_state(L) is UiState.RUNNING and self.svc.ui_state(F) is UiState.RUNNING
        return ctrl

    def _fb(self, lp: float, fp: float) -> bool:
        ls, fs = self.session.motor(L).state, self.session.motor(F).state
        return abs(ls.position - lp) < 0.01 and abs(fs.position - fp) < 0.01

    def pulse(self, motor: Any, torque: float, secs: float = 0.1) -> None:
        motor.ext_torque = torque
        time.sleep(secs)
        motor.ext_torque = 0.0

    def close(self) -> None:
        self.svc.close()
        self.session.close()


@pytest.fixture
def rig() -> Iterator[Rig]:
    r = Rig()
    yield r
    r.close()


def test_start_requires_two_motors_and_run_mode(rig: Rig) -> None:
    rig.svc.enable(L, RunMode.OPERATION).result(5.0)
    rig.svc.enable(F, RunMode.VELOCITY).result(5.0)
    with pytest.raises(ProtocolError):  # run_mode 不一致(F は速度モード)
        rig.svc.start([L, F], Bilateral())
    with pytest.raises(ProtocolError):  # 1 台ではスロットを埋められない
        rig.svc.start([L], Bilateral())


def test_symmetric_follower_tracks_and_offset_is_preserved(rig: Rig) -> None:
    rig.start(BilateralParams(), lp=0.0, fp=0.3)  # d0 = 0.3
    time.sleep(0.3)
    # 開始直後は引き寄せない
    assert rig.lm.position == pytest.approx(0.0, abs=0.05)
    assert rig.fm.position == pytest.approx(0.3, abs=0.05)
    # leader に外力 → follower が追従(差 0.3 を保ったまま動く)
    rig.pulse(rig.lm, 0.2, 0.3)
    time.sleep(0.3)
    assert rig.lm.position > 0.4 and rig.fm.position > 0.7
    assert rig.fm.position - rig.lm.position == pytest.approx(0.3, abs=0.06)
    # 逆向き: follower に外力 → leader が追従する(対称型)
    p0 = rig.lm.position
    rig.pulse(rig.fm, -0.2, 0.3)
    time.sleep(0.3)
    assert rig.lm.position < p0 - 0.3
    assert rig.fm.position - rig.lm.position == pytest.approx(0.3, abs=0.08)
    # LOW のトルク上限
    assert abs(rig.lm.torque) <= 0.5 + 1e-3 and abs(rig.fm.torque) <= 0.5 + 1e-3
    rig.svc.stop_motion([L, F]).result(1.0)
    assert rig.svc.ui_state(L) is UiState.ENABLED and rig.svc.ui_state(F) is UiState.ENABLED


def test_zero_offset_mode_converges(rig: Rig) -> None:
    rig.start(BilateralParams(offset_mode="zero", converge_speed=0.5), lp=0.0, fp=0.6)
    d = lambda: rig.fm.position - rig.lm.position  # noqa: E731
    assert d() > 0.3  # 直後はまだ差が残る(速度制限付き)
    assert wait_until(lambda: abs(d()) < 0.05, 5.0)
    assert rig.lm.position > 0.05 and rig.fm.position < 0.55  # お互いが歩み寄る


def test_master_slave_leader_is_free_follower_tracks(rig: Rig) -> None:
    ctrl = rig.start(BilateralParams(mode="master_slave"), lp=0.0, fp=0.0)
    assert ctrl.params.mode == "master_slave"
    # follower に外力 → leader は引かれない(leader は kp = 0)
    rig.pulse(rig.fm, 0.1, 0.1)
    time.sleep(0.2)
    assert abs(rig.lm.position) < 0.02
    assert wait_until(lambda: abs(rig.fm.position - rig.lm.position) < 0.1, 3.0)  # follower は戻る
    # leader を手で動かす → follower が追従
    p_f = rig.fm.position
    p_l = rig.lm.position
    rig.pulse(rig.lm, 0.2, 0.3)
    time.sleep(0.3)
    assert rig.lm.position - p_l > 0.5
    assert rig.fm.position - p_f > 0.4
    assert abs(rig.fm.position - rig.lm.position) < 0.15
    # 静止後、leader の出力トルクは 0(kp = 0・t_ff = 0)
    assert abs(rig.lm.torque) < 0.02


def test_low_tier_clamp_notes_when_k_times_error_exceeds_bound(rig: Rig) -> None:
    # 位置差の目標を急に 1 rad ずらす(収束速度制限を緩める): K·誤差 = 20 > 0.5 N·m
    rig.start(BilateralParams(stiffness=20.0, converge_speed=50.0))
    rig.svc.update([L, F], BilateralParams(stiffness=20.0, converge_speed=50.0, offset=1.0))
    assert wait_until(
        lambda: any(
            n.field == "position" and n.reason == "worst_case"
            for p in rig.events
            for n in p["notes"]
        ),
        3.0,
    )
    time.sleep(0.3)
    assert abs(rig.lm.torque) <= 0.5 + 1e-3 and abs(rig.fm.torque) <= 0.5 + 1e-3
    rig.svc.disable([L, F]).result(3.0)
    assert rig.lm.mode is MotorMode.RESET and rig.fm.mode is MotorMode.RESET
