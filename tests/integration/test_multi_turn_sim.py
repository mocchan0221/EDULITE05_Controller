"""多回転角の較正(mechPos)と ±4π 付近の MIT 保持(phase4-fix-W)。

実機の事実(el05_protocol.md §8.0): type 2 の位置は ±4π で折り返し、mechPos は多回転値。
MIT の p_set の比較対象は未確認(Q-P12)なので、シミュレータは両方の解釈(mit_frame)で試す。
実時間で動くので判定は緩め。
"""

from __future__ import annotations

import dataclasses
import math
import time
from collections.abc import Callable
from typing import Any

import pytest

from edulite05.core import clock
from edulite05.core.control.controllers import Bilateral, Csp, MitDirect
from edulite05.core.control.params import BilateralParams, CspParams, MitParams
from edulite05.core.control.service import SERVICE_CLAMPED, MotorService, UiState
from edulite05.core.device.profile import MotorProfile
from edulite05.core.errors import SafetyError
from edulite05.core.protocol.ids import CommType, parse_id
from edulite05.core.protocol.types import RunMode
from edulite05.core.session import Session
from edulite05.sim.transport import SimTransport, make_motors

PROFILE = MotorProfile.load_builtin("el05")
A, B = 127, 1
P4 = 4 * math.pi
FRAMES = ["multi", "wrapped"]  # SimMotor.mit_frame(実機の挙動は未確認)


def wait_until(pred: Callable[[], object], timeout: float = 3.0) -> bool:
    end = clock.now() + timeout
    while clock.now() < end:
        if pred():
            return True
        time.sleep(0.005)
    return bool(pred())


class Rig:
    def __init__(
        self,
        n: int = 1,
        frame: str = "multi",
        start: float | tuple = 0.0,
        guard_frame: str = "unknown",
    ) -> None:
        prof = dataclasses.replace(
            PROFILE, position=dataclasses.replace(PROFILE.position, mit_frame=guard_frame)
        )
        motors = make_motors(n, profile=prof, mit_frame=frame)
        starts = start if isinstance(start, tuple) else (start,) * n
        for m, p in zip(motors, starts, strict=False):
            m.position = p
            m._sync_loc_ref()
        self.sim = SimTransport(motors)
        self.session = Session(self.sim, prof)
        self.session.open()
        self.svc = MotorService(self.session)
        self.clamps: list[Any] = []
        self.session.events.subscribe(SERVICE_CLAMPED, self.clamps.append)
        self.ids = [A, B][:n]
        for mid in self.ids:
            self.session.motor(mid)
        self.svc.open()
        assert wait_until(lambda: all(self.svc.ui_state(i) is UiState.DISABLED for i in self.ids))

    def motor(self, mid: int = A):
        return self.session.motor(mid)

    def simm(self, mid: int = A):
        return self.sim.motor(mid)

    def enable(self, mid: int, mode: RunMode) -> None:
        self.svc.enable(mid, mode).result(5.0)
        assert self.svc.ui_state(mid) is UiState.ENABLED

    def close(self) -> None:
        self.svc.close()
        self.session.close()


@pytest.fixture
def make_rig():
    rigs: list[Rig] = []

    def make(*a: Any, **kw: Any) -> Rig:
        r = Rig(*a, **kw)
        rigs.append(r)
        return r

    yield make
    for r in rigs:
        r.close()


# ----------------------------------------------------------------- 多回転角の較正(A)
@pytest.mark.parametrize("start", [13.02, -13.02, 30.0])
def test_multi_is_seeded_from_mech_pos_on_first_contact(make_rig, start: float) -> None:
    """起動時に ±4π の外にいても、接続後に position_multi が mechPos と一致する。"""
    r = make_rig(start=start)
    assert wait_until(lambda: r.motor().state.multi_valid)
    s = r.motor().state
    assert s.position_multi == pytest.approx(start, abs=0.01)
    assert abs(s.position) <= P4 + 1e-6  # フィードバックは折り返したまま
    assert s.position == pytest.approx(start - round(start / (2 * P4)) * 2 * P4, abs=0.01)


def test_multi_seed_keeps_unwrapping_across_the_boundary(make_rig) -> None:
    r = make_rig(start=P4 - 0.05)
    assert wait_until(lambda: r.motor().state.multi_valid)
    r.simm().position = P4 + 0.1  # 折り返しをまたぐ(外力で回された想定)
    assert wait_until(lambda: r.motor().state.position < 0)
    assert r.motor().state.position_multi == pytest.approx(P4 + 0.1, abs=0.01)


def test_enable_reseeds_multi_every_time(make_rig) -> None:
    r = make_rig(start=15.0)
    assert wait_until(lambda: r.motor().state.multi_valid)
    r.motor().reset_position_unwrap()  # 較正が失われた状態(例: ゼロ点設定後)を作る
    assert not r.motor().state.multi_valid
    r.enable(A, RunMode.VELOCITY)
    s = r.motor().state
    assert s.multi_valid and s.position_multi == pytest.approx(15.0, abs=0.02)


def test_enable_fails_cleanly_when_mech_pos_times_out(make_rig) -> None:
    r = make_rig(start=15.0)
    m = r.simm()
    orig = m.handle

    def deaf_to_mech_pos(frame):  # mechPos(0x7019)の読出だけ応答しない
        if (
            parse_id(frame.can_id).comm_type == CommType.PARAM_READ
            and frame.data[0] == 0x19
            and frame.data[1] == 0x70
        ):
            return []
        return orig(frame)

    m.handle = deaf_to_mech_pos
    r.motor().reset_position_unwrap()
    with pytest.raises(SafetyError, match="mechPos"):
        r.svc.enable(A, RunMode.OPERATION).result(10.0)
    assert r.svc.ui_state(A) is UiState.DISABLED
    assert not m.enabled  # 有効化(type 3)は送られていない
    assert not r.motor().state.multi_valid
    # 応答が戻れば有効化できる
    m.handle = orig
    r.enable(A, RunMode.OPERATION)
    assert r.motor().state.multi_valid


def test_enable_fails_when_mech_pos_read_is_refused(make_rig) -> None:
    r = make_rig(start=15.0)
    from edulite05.sim import motor_model

    old = motor_model.UNREADABLE_INDICES
    motor_model.UNREADABLE_INDICES = old | {0x7019}
    try:
        with pytest.raises(SafetyError, match="mechPos"):
            r.svc.enable(A, RunMode.CSP).result(10.0)
        assert not r.simm().enabled
    finally:
        motor_model.UNREADABLE_INDICES = old


def test_csp_hold_after_enable_does_not_move_beyond_4pi(make_rig) -> None:
    """セッション相対の multi だと loc_ref が 8π ずれて 25 rad 動く。較正済みなら動かない。"""
    r = make_rig(start=13.02)
    r.enable(A, RunMode.CSP)
    m = r.simm()
    time.sleep(0.6)
    assert m.position == pytest.approx(13.02, abs=0.05)
    assert m.get_param("loc_ref") == pytest.approx(13.02, abs=0.05)
    assert abs(m.velocity) < 0.1
    # CSP 運転 → 停止(保持)も同じ座標系
    r.svc.start(A, Csp(CspParams(position=13.5, speed_limit=2.0))).result(2.0)
    assert wait_until(lambda: abs(m.position - 13.5) < 0.05, 4.0)
    r.svc.stop_motion(A).result(2.0)
    time.sleep(0.4)
    assert m.position == pytest.approx(13.5, abs=0.1)


def test_set_zero_reseeds_multi(make_rig) -> None:
    r = make_rig(start=13.02)
    assert wait_until(lambda: r.motor().state.multi_valid)
    r.svc.set_zero(A).result(5.0)
    assert wait_until(lambda: r.motor().state.multi_valid)
    assert r.motor().state.position_multi == pytest.approx(0.0, abs=0.05)


# ----------------------------------------------------------------- 4π を越えた MIT 保持(C, D)
@pytest.mark.parametrize(
    ("frame", "guard"),
    [("multi", "unknown"), ("wrapped", "unknown"), ("multi", "multi"), ("wrapped", "wrapped")],
)
def test_mit_stop_beyond_4pi_has_no_reverse_yank(make_rig, frame: str, guard: str) -> None:
    r = make_rig(frame=frame, start=P4 - 1.0, guard_frame=guard)  # 11.57 rad から 4π を越える
    r.enable(A, RunMode.OPERATION)
    m = r.simm()
    st = r.motor().state
    r.svc.start(
        A, MitDirect(MitParams(position=st.position_multi, velocity=30.0, kp=0.0, kd=0.5))
    ).result(2.0)
    assert wait_until(lambda: r.motor().state.position_multi > 16.0, 8.0)
    before = r.motor().state.position_multi
    assert r.motor().state.position < 0  # 折り返している
    r.svc.stop_motion(A).result(2.0)
    vmin, vmax = 0.0, 0.0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 1.5:
        v = r.motor().state.velocity
        vmin, vmax = min(vmin, v), max(vmax, v)
        time.sleep(0.004)
    after = r.motor().state.position_multi
    assert vmin > -1.5, f"逆走した(vmin={vmin:.2f})"  # 旧実装は -50 rad/s。kp 保持の戻りは -1 程度
    assert vmax <= 5.0 + 0.3  # LOW の速度上限
    assert abs(after - before) < 1.0  # 8π 引き戻されない
    assert abs(m.torque) <= 0.5 + 1e-6
    notes = [n for p in r.clamps for n in p["notes"]]
    if guard != "wrapped":  # 多回転側でも比較されうるので kp = 0 に落ちる(通知される)
        assert any(n.field == "kp" and "position_wrap" in n.reason for n in notes)


def test_unknown_hold_degrades_to_damping_and_notifies_once_in_log(
    make_rig, caplog: pytest.LogCaptureFixture
) -> None:
    r = make_rig(start=15.0)
    with caplog.at_level("WARNING", logger="edulite05.core.control.loop"):
        r.enable(A, RunMode.OPERATION)  # keepalive の HoldCmd(kp=5)
        time.sleep(0.6)
    assert [x for p in r.clamps for x in p["notes"] if x.field == "kp"]
    logs = [x for x in caplog.records if "±4π" in x.getMessage()]
    assert len(logs) == 1  # 毎周期出さない
    # 減衰だけで保持: 動かない
    assert r.simm().position == pytest.approx(15.0, abs=0.05)


# ----------------------------------------------------------------- バイラテラル(E)
@pytest.mark.parametrize(
    ("frame", "guard"), [("multi", "unknown"), ("wrapped", "unknown"), ("wrapped", "wrapped")]
)
def test_bilateral_crossing_4pi_has_no_jump(make_rig, frame: str, guard: str) -> None:
    r = make_rig(n=2, frame=frame, start=(P4 - 0.6, P4 - 0.6), guard_frame=guard)
    for mid in (A, B):
        r.enable(mid, RunMode.OPERATION)
    r.svc.start([A, B], Bilateral(BilateralParams())).result(2.0)
    lm, fm = r.simm(A), r.simm(B)
    vmax = 0.0
    t_end = time.perf_counter() + 4.0
    while time.perf_counter() < t_end and lm.position < P4 + 0.5:
        # leader を押して 4π を越えさせる(2 台は互いに結合しているので、速度を保つ程度に押す)
        lm.ext_torque = 0.04 if lm.velocity < 2.0 else 0.0
        vmax = max(vmax, abs(lm.velocity), abs(fm.velocity))
        time.sleep(0.005)
    lm.ext_torque = 0.0
    assert lm.position > P4 + 0.2  # leader は 4π を越えた
    t_end = time.perf_counter() + 1.0
    while time.perf_counter() < t_end:  # 越えた後の挙動
        vmax = max(vmax, abs(lm.velocity), abs(fm.velocity))
        time.sleep(0.005)
    # 8π の引き戻しなら LOW のトルク上限(0.5 N·m)で 50 rad/s まで加速する。多少の超過は許す
    # (guard=wrapped は規則 C の対象外: 折り返しの境界をまたぐ 1 周期だけ位置誤差が 8π に跳ぶため、
    #  数 rad/s の瞬間的な蹴りが出ることがある。8π の継続的な引き戻し(50 rad/s)にはならない)
    assert vmax <= (5.0 + 1.0 if guard != "wrapped" else 12.0)
    assert abs(lm.position - fm.position) < 5.0  # 8π(25 rad)の食い違いがない
    notes = [n for p in r.clamps for n in p["notes"]]
    if guard != "wrapped":  # 折り返し付近では kp = 0 に落ちたことが通知される
        assert any(n.field == "kp" and "position_wrap" in n.reason for n in notes)
    r.svc.stop_motion([A, B]).result(2.0)
    time.sleep(0.5)
    assert abs(lm.velocity) < 1.0 and abs(fm.velocity) < 1.0


# ----------------------------------------------------------------- CLI(G)
def test_cli_monitor_shows_seeded_multi_turn(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from edulite05.cli import main as cli

    motors = make_motors(1, profile=PROFILE)
    motors[0].position = 13.02
    tr = SimTransport(motors)
    monkeypatch.setattr(cli, "make_transport", lambda args: tr)
    argv = ["monitor", "127", "--sim", "--seconds", "0.4", "--rate", "20", "--timeout", "0.1"]
    code = cli.main(argv)
    out = capsys.readouterr().out
    assert code == 0
    lines = [ln for ln in out.splitlines() if "多回転" in ln]
    assert lines
    last = lines[-1]
    assert "多回転   +13.020" in last or "多回転  +13.020" in last or "+13.02" in last
    assert "位置  -12.1" in last or "位置 -12.1" in last  # 折り返した位置
    assert not tr.motor(127).enabled  # monitor は有効化しない


@pytest.mark.parametrize(
    "sub", [["mit", "127", "--kp", "5", "--kd", "0.3"], ["csp", "127", "--position", "13.02"]]
)
def test_cli_motion_beyond_4pi_does_not_jump(
    monkeypatch: pytest.MonkeyPatch, capsys, sub: list[str]
) -> None:
    """13.02 rad(±4π の外)から `mit`(位置省略 = 現在位置)/ `csp`(同じ位置)でも動かない。"""
    from edulite05.cli import main as cli

    motors = make_motors(1, profile=PROFILE)
    motors[0].position = 13.02
    motors[0]._sync_loc_ref()
    tr = SimTransport(motors)
    monkeypatch.setattr(cli, "make_transport", lambda args: tr)
    vmax = [0.0]
    stop = False

    def watch() -> None:
        while not stop:
            vmax[0] = max(vmax[0], abs(motors[0].velocity))
            time.sleep(0.003)

    import threading

    th = threading.Thread(target=watch, daemon=True)
    th.start()
    try:
        code = cli.main([*sub, "--seconds", "0.8", "--yes", "--sim", "--timeout", "0.1"])
    finally:
        stop = True
        th.join(1.0)
    capsys.readouterr()
    assert code == 0
    assert motors[0].position == pytest.approx(13.02, abs=0.1)
    assert vmax[0] < 1.0
