"""主動上報の方針(運転中は OFF)・減速してから無効化(stop_and_disable)・応答欠落への耐性。

Sim(3 台、ID 127 / 1 / 2)に対して MotorService を実時間で動かす。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from test_service_sim import PROFILE, A, B, C, Rig, sm, wait_until

from edulite05.core.control.controllers import Velocity
from edulite05.core.control.params import VelocityParams
from edulite05.core.control.service import MotorService, UiState
from edulite05.core.errors import SafetyError
from edulite05.core.protocol.ids import CommType, parse_id
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.session import Session
from edulite05.sim.transport import SimTransport, make_motors


def record_frames(rig: Rig, mid: int) -> list[tuple[int, int, bool, float, float]]:
    """モータ宛てフレームを (comm_type, index, その時の active_report, 速度, 時刻) で記録する。"""
    m = sm(rig, mid)
    log: list[tuple[int, int, bool, float, float]] = []
    real = m.handle

    def handle(frame: Any):  # type: ignore[no-untyped-def]
        t = parse_id(frame.can_id).comm_type
        idx = int.from_bytes(frame.data[:2], "little") if t in (17, 18) else -1
        log.append((t, idx, m.active_report, m.velocity, time.monotonic()))
        return real(frame)

    m.handle = handle  # type: ignore[method-assign]
    return log


@pytest.fixture
def rig() -> Iterator[Rig]:
    r = Rig()
    yield r
    r.close()


# ---------------------------------------------------------------- 主動上報の方針
def test_report_on_when_idle_off_during_enable_and_back_on_after_disable(rig: Rig) -> None:
    assert wait_until(lambda: all(sm(rig, i).active_report for i in (A, B, C)))
    log = record_frames(rig, A)
    rig.enable(A, RunMode.VELOCITY)
    # 有効化後は OFF。他のモータ(無効)は ON のまま
    assert not sm(rig, A).active_report
    assert sm(rig, B).active_report and sm(rig, C).active_report
    # 設定の書込み(limit_* / canTimeout / run_mode)と type 3 の時点で、既に OFF だった
    cfg = {PROFILE.param(k).index for k in ("limit_torque", "limit_cur", "limit_spd", "canTimeout")}
    cfg.add(PROFILE.param("run_mode").index)
    writes = [e for e in log if e[0] == CommType.PARAM_WRITE and e[1] in cfg]
    assert writes and all(not e[2] for e in writes), writes
    assert all(not e[2] for e in log if e[0] == CommType.ENABLE)
    # 運転中も OFF のまま、フィードバック(type 2 応答)は流れ続ける
    rig.svc.start([A], Velocity(VelocityParams(velocity=1.0, accel=20.0)))
    t0 = rig.svc.motor(A).state.last_rx
    time.sleep(0.3)
    assert not sm(rig, A).active_report
    assert rig.svc.motor(A).state.last_rx > t0 and rig.svc.ui_state(A) is UiState.RUNNING
    assert rig.svc.motor(A).state.rx_rate > 50  # 制御ループの応答(約 200 Hz)
    # 無効化が完了したら ON に戻る
    rig.svc.stop_motion([A])
    rig.svc.disable([A]).result(3.0)
    assert wait_until(lambda: sm(rig, A).active_report)
    assert rig.svc.ui_state(A) is UiState.DISABLED


def test_report_stays_on_when_report_while_enabled() -> None:
    sim = SimTransport(make_motors(1, profile=PROFILE))
    session = Session(sim, PROFILE)
    session.open()
    svc = MotorService(session, report_while_enabled=True)
    try:
        session.motor(A)
        svc.open()
        assert wait_until(lambda: sm_of(sim, A).active_report)
        svc.enable(A, RunMode.VELOCITY).result(5.0)
        time.sleep(0.2)
        assert sm_of(sim, A).active_report
        svc.disable([A]).result(3.0)
    finally:
        svc.close()
        session.close()


def sm_of(sim: SimTransport, mid: int):  # type: ignore[no-untyped-def]
    return sim.motor(mid)


def test_comm_watchdog_uses_loop_feedback_while_enabled(rig: Rig) -> None:
    """運転中は主動上報 OFF でも、Hold の応答がフィードバックになり通信断と誤判定しない。
    応答が止まれば(上報も無いので)通信断として無効化する。"""
    rig.enable(A, RunMode.VELOCITY)
    assert not sm(rig, A).active_report
    time.sleep(3 * rig.svc.comm_timeout_s)  # 監視の期限(0.25 s)を十分に超えて有効のまま
    assert rig.svc.ui_state(A) is UiState.ENABLED
    rig.sim.drop_rate = 1.0  # 応答が全く来なくなる
    assert wait_until(lambda: rig.svc.ui_state(A) is not UiState.ENABLED, 2.0)
    rig.sim.drop_rate = 0.0
    rig.svc.disable([A]).result(5.0)


def test_enable_survives_lost_replies(rig: Rig) -> None:
    """JOG が開始前に失敗した事象: limit_torque 書込みの応答が欠落しても有効化が成功する。"""
    rig.enable(B, RunMode.VELOCITY)  # まず正常
    rig.svc.disable([B]).result(3.0)
    wait_until(lambda: sm(rig, B).active_report)
    rig.sim.drop_next(reply=2)  # 次の 2 件の応答を落とす(最初の要求が 2 回再送される)
    rig.svc.enable(B, RunMode.VELOCITY).result(5.0)
    assert rig.svc.ui_state(B) is UiState.ENABLED
    assert rig.sim.dropped_replies == 2
    assert rig.session.bus.stats().retries >= 1  # 応答欠落ごとに再送(ポーリングの分も数える)
    rig.svc.disable([B]).result(3.0)


# ---------------------------------------------------------------- 減速してから無効化
def run_velocity(rig: Rig, mid: int, v: float = 2.0) -> None:
    rig.enable(mid, RunMode.VELOCITY)
    rig.svc.start([mid], Velocity(VelocityParams(velocity=v, accel=10.0)))
    assert wait_until(lambda: sm(rig, mid).velocity > 0.9 * v, 3.0)


def test_stop_and_disable_waits_for_low_speed(rig: Rig) -> None:
    log = record_frames(rig, A)
    run_velocity(rig, A, 2.0)
    t0 = time.monotonic()
    rig.svc.stop_and_disable([A]).result(5.0)
    stops = [e for e in log if e[0] == CommType.STOP and e[4] >= t0]
    assert stops and sm(rig, A).mode is MotorMode.RESET
    first = stops[0]
    assert abs(first[3]) < 0.1  # 無効化の時点で十分減速している(2 rad/s から)
    assert first[4] - t0 > 0.1  # 減速(加速度 10 rad/s² → 約 0.2 s)を待った
    assert rig.svc.ui_state(A) is UiState.DISABLED


def test_stop_and_disable_is_bounded_by_the_timeout(rig: Rig) -> None:
    log = record_frames(rig, A)
    run_velocity(rig, A, 2.0)
    t0 = time.monotonic()
    rig.svc.stop_and_disable([A], settle_timeout_s=0.03).result(5.0)
    first = next(e for e in log if e[0] == CommType.STOP and e[4] >= t0)
    assert first[3] > 0.5  # 減速しきる前に打ち切った
    assert first[4] - t0 < 0.3
    assert sm(rig, A).mode is MotorMode.RESET


def test_stop_and_disable_on_idle_or_disabled_motor_is_quick(rig: Rig) -> None:
    t0 = time.monotonic()
    rig.svc.stop_and_disable([B]).result(3.0)  # DISABLED: 待たない
    assert time.monotonic() - t0 < 0.4
    rig.enable(C, RunMode.VELOCITY)
    rig.svc.stop_and_disable([C]).result(3.0)  # ENABLED・停止中: すぐ速度が低い
    assert sm(rig, C).mode is MotorMode.RESET and rig.svc.ui_state(C) is UiState.DISABLED


def test_estop_is_immediate_and_not_delayed_by_settling(rig: Rig) -> None:
    log = record_frames(rig, A)
    run_velocity(rig, A, 2.0)
    t0 = time.monotonic()
    rig.svc.estop()
    assert wait_until(lambda: sm(rig, A).mode is MotorMode.RESET, 0.5)  # 同期送信・待たない
    first = next(e for e in log if e[0] == CommType.STOP and e[4] >= t0)
    assert first[4] - t0 < 0.05 and first[3] > 1.0  # 減速を待たず速いうちに type 4
    with pytest.raises(SafetyError):
        rig.svc.stop_and_disable([A])
    rig.svc.release_estop()


def test_estop_during_settle_aborts_the_wait(rig: Rig) -> None:
    run_velocity(rig, A, 2.0)
    f = rig.svc.stop_and_disable([A], settle_timeout_s=2.0)
    rig.svc.estop()
    with pytest.raises(SafetyError):
        f.result(3.0)  # E-STOP のラッチで中断(待ち続けない)
    assert wait_until(lambda: sm(rig, A).mode is MotorMode.RESET, 0.5)
