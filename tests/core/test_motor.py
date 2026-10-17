"""Motor / MotorState のテスト(phase1.md §1.8)。FakeMotors(プロトコル準拠の応答器)を使う。"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator

import pytest
from fakes import (
    HOST,
    FakeMotors,
    FakeTransport,
    feedback_frame,
    uid_of,
    version_frame,
    wait_until,
)

from edulite05.core import clock
from edulite05.core.bus import BusManager
from edulite05.core.device.motor import ONLINE_TIMEOUT_S, Motor
from edulite05.core.device.profile import MotorProfile
from edulite05.core.device.state import PositionUnwrapper
from edulite05.core.errors import ProtocolError, RequestTimeout
from edulite05.core.events import (
    MOTOR_FAULT,
    MOTOR_MODE_CHANGED,
    PARAM_UPDATED,
    EventBus,
)
from edulite05.core.protocol.ids import CommType, make_id, parse_id
from edulite05.core.protocol.private import DecodeContext, Feedback, ParamResponse, UidResponse
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.transport.base import CanFrame

PROFILE = MotorProfile.load_builtin("el05")
CTX = DecodeContext(HOST, PROFILE.ranges, PROFILE.param_dtypes())


class Rig:
    def __init__(self, ids=(1, 127), **fake_kw) -> None:
        self.fake = FakeMotors(ids, PROFILE, **fake_kw)
        self.tr = FakeTransport(self.fake)
        self.tr.open()
        self.events = EventBus()
        self.bus = BusManager(self.tr, CTX, self.events)
        self.bus.start()
        self.log: list[tuple[str, object]] = []
        for t in (MOTOR_MODE_CHANGED, MOTOR_FAULT, PARAM_UPDATED):
            self.events.subscribe(t, lambda p, t=t: self.log.append((t, p)))

    def motor(self, mid: int = 1, **kw) -> Motor:
        return Motor(self.bus, mid, PROFILE, HOST, **kw)

    def types_sent(self) -> list[int]:
        return self.tr.sent_types()


@pytest.fixture
def rig() -> Iterator[Rig]:
    r = Rig()
    yield r
    r.bus.stop()


# ---------------------------------------------------------------------------
# 正常系
# ---------------------------------------------------------------------------
def test_get_uid(rig: Rig) -> None:
    m = rig.motor(127)
    r = m.get_uid().result(2.0)
    assert isinstance(r, UidResponse) and r.uid == uid_of(127)
    assert m.uid == uid_of(127)
    assert m.state.online


def test_read_version_type4(rig: Rig) -> None:
    m = rig.motor()
    info = m.read_version().result(2.0)
    assert info.version == "10.5.0.2" and m.version == info.version
    assert rig.types_sent() == [CommType.STOP]  # type 4 だけ(26 は試していない)
    # バージョン応答は状態(位置など)を壊さない: Feedback として扱われていない
    assert m.state.mode is None and m.state.position == 0.0 and m.state.last_rx is None


def test_read_version_falls_back_to_type26_then_times_out() -> None:
    r = Rig(version_type4=False)  # EL05 実機: type 26 は通常の type 2 を返すだけ
    try:
        m = r.motor(request_timeout=0.1)
        with pytest.raises(RequestTimeout):
            m.read_version().result(2.0)
        assert r.types_sent() == [CommType.STOP, CommType.VERSION_ALT]
        assert m.version is None
        # type 26 への通常フィードバックは受理されず、状態にだけ反映される
        assert m.state.mode == MotorMode.RESET
    finally:
        r.bus.stop()


def test_read_version_fallback_succeeds_when_type26_replies_version() -> None:
    r = Rig()
    # type 4 には応答せず、type 26 にバージョン応答を返す機種
    orig = r.fake.__call__

    def responder(frame):
        t = parse_id(frame.can_id).comm_type
        if t == CommType.STOP and frame.data[1] == 0xC4:
            return []
        if t == CommType.VERSION_ALT:
            return [version_frame(parse_id(frame.can_id).dest)]
        return orig(frame)

    r.tr.responder = responder
    try:
        m = r.motor(request_timeout=0.1)
        assert m.read_version().result(2.0).version == "10.5.0.2"
        assert r.types_sent() == [CommType.STOP, CommType.VERSION_ALT]
    finally:
        r.bus.stop()


def test_read_version_while_running_uses_type26_only(rig: Rig) -> None:
    m = rig.motor()
    m.enable().result(2.0)
    assert m.state.mode == MotorMode.MOTOR
    m.request_timeout = 0.05
    with pytest.raises(RequestTimeout):  # type 26 は通常のフィードバックを返すだけ
        m.read_version().result(2.0)
    assert CommType.STOP not in rig.types_sent()  # 運転中に type 4 を送っていない


def test_read_param_float_and_uint8(rig: Rig) -> None:
    m = rig.motor()
    rig.fake.params[1][PROFILE.param("loc_kp").index] = 30.0
    r = m.read_param("loc_kp").result(2.0)
    assert isinstance(r, ParamResponse) and r.ok and r.value == pytest.approx(30.0)
    r2 = m.read_param(0x7005).result(2.0)  # index 指定
    assert r2.ok and r2.value == 0
    assert m.params[0x7005] == 0
    assert m.state.run_mode == RunMode.OPERATION
    assert any(t == PARAM_UPDATED and p["key"] == "run_mode" for t, p in rig.log)


def test_read_param_failure_resolves_with_ok_false(rig: Rig) -> None:
    rig.fake.fail_read.add(0x702C)
    r = rig.motor().read_param(0x702C).result(2.0)
    assert r.ok is False and r.value is None


def test_read_all_params_includes_failures(rig: Rig) -> None:
    rig.fake.fail_read.update({0x702C, 0x702D})
    res = rig.motor().read_all_params().result(10.0)
    assert set(res) == {p.index for p in PROFILE.params}
    assert res[0x702C].ok is False and res[0x7005].ok is True
    # 要求は 1 件ずつ(並行しない)で、順序はプロファイル順
    idx = [int.from_bytes(f.data[:2], "little") for f in rig.tr.sent]
    assert idx == [p.index for p in PROFILE.params]


def test_read_all_params_aborts_after_consecutive_timeouts() -> None:
    r = Rig(ids=(2,))  # モータ 1 は存在しない
    try:
        m = r.motor(1, request_timeout=0.05, retries=0)
        with pytest.raises(RequestTimeout):
            m.read_all_params().result(5.0)
        assert len(r.tr.sent) == 3
    finally:
        r.bus.stop()


def test_enable_disable_and_state(rig: Rig) -> None:
    m = rig.motor(127)
    fb = m.enable().result(2.0)
    assert isinstance(fb, Feedback) and fb.mode == MotorMode.MOTOR
    assert m.state.mode == MotorMode.MOTOR and m.state.online
    fb = m.disable().result(2.0)
    assert fb.mode == MotorMode.RESET and m.state.mode == MotorMode.RESET
    modes = [(p["mode"], p["previous"]) for t, p in rig.log if t == MOTOR_MODE_CHANGED]
    assert modes == [(MotorMode.MOTOR, None), (MotorMode.RESET, MotorMode.MOTOR)]


def test_disable_clear_fault_sends_b0_1(rig: Rig) -> None:
    rig.fake.fault[1] = 0b000100
    m = rig.motor()
    m.disable(clear_fault=True).result(2.0)
    assert rig.tr.sent[0].data[0] == 1
    assert m.state.fault_bits == 0


def test_fault_bits_event(rig: Rig) -> None:
    m = rig.motor()
    rig.tr.inject(feedback_frame(1, fault=0b001000))
    assert wait_until(lambda: m.state.fault_bits == 0b001000)
    assert (
        MOTOR_FAULT,
        {"motor_id": 1, "fault_bits": 8, "previous": 0, "source": "feedback"},
    ) in rig.log


def test_set_run_mode_requires_reset(rig: Rig) -> None:
    m = rig.motor()
    with pytest.raises(ProtocolError):  # モード不明
        m.set_run_mode(RunMode.CSP)
    m.disable().result(2.0)
    fb = m.set_run_mode(RunMode.CSP).result(2.0)
    assert isinstance(fb, Feedback)
    assert rig.fake.params[1][0x7005] == 5
    assert m.state.run_mode == RunMode.CSP
    m.enable().result(2.0)
    with pytest.raises(ProtocolError):
        m.set_run_mode(RunMode.VELOCITY)
    with pytest.raises(ProtocolError):  # write_param 経由でも運転中は不可
        m.write_param("run_mode", 2)
    assert rig.fake.params[1][0x7005] == 5


def test_set_run_mode_rejects_undefined_value(rig: Rig) -> None:
    m = rig.motor()
    m.disable().result(2.0)
    with pytest.raises(ProtocolError):
        m.set_run_mode(4)  # type: ignore[arg-type]


def test_write_param_roundtrip_dtypes(rig: Rig) -> None:
    m = rig.motor()
    m.write_param("limit_spd", 5.0).result(2.0)  # float
    m.write_param(0x7005, 3).result(2.0)  # uint8(B4 のみ)
    assert rig.fake.params[1][PROFILE.param("limit_spd").index] == pytest.approx(5.0)
    assert rig.fake.params[1][0x7005] == 3
    frame = rig.tr.sent[1]
    assert frame.data == bytes((0x05, 0x70, 0, 0, 3, 0, 0, 0))
    assert m.params[0x7005] == 3 and m.state.run_mode == RunMode.CURRENT


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("limit_spd", 1e6),  # 上限超
        ("limit_spd", -1.0),  # 下限未満
        ("run_mode", 6),  # 上限超
        ("run_mode", 4),  # 未定義
        ("run_mode", 1.5),  # 整数型に小数
        ("run_mode", math.nan),
        ("limit_spd", float("inf")),
        ("limit_spd", "5"),
        ("mechPos", 0.0),  # 読み取り専用
        ("no_such_param", 1),
        (0xBEEF, 1),
    ],
)
def test_write_param_validation_errors(rig: Rig, key, value) -> None:
    m = rig.motor()
    with pytest.raises(ProtocolError):
        m.write_param(key, value)
    assert rig.tr.sent == []  # 何も送っていない


def test_write_param_integer_valued_float_ok(rig: Rig) -> None:
    rig.motor().write_param("run_mode", 2.0).result(2.0)
    assert rig.fake.params[1][0x7005] == 2


def test_save_and_set_zero(rig: Rig) -> None:
    m = rig.motor()
    # 実機 EL05 は type 22 に type 0(UID、宛先 0xFE)で応答する(HW-6)
    assert isinstance(m.save_params().result(2.0), UidResponse)
    assert isinstance(m.set_zero().result(2.0), Feedback)
    assert rig.types_sent() == [CommType.SAVE, CommType.SET_ZERO]
    assert rig.tr.sent[1].data[0] == 1


def test_set_active_report_with_period(rig: Rig) -> None:
    m = rig.motor()
    m.set_active_report(True, period_ms=20).result(2.0)
    # 周期 20 ms → N = 2(10 + 5·N)。EPScan_time を書いてから type 24
    assert rig.types_sent() == [CommType.PARAM_WRITE, CommType.ACTIVE_REPORT]
    assert rig.fake.params[1][0x7026] == 2
    assert rig.tr.sent[1].data[6] == 1
    m.set_active_report(False).result(2.0)
    assert rig.tr.sent[2].data[6] == 0
    with pytest.raises(ProtocolError):
        m.set_active_report(True, period_ms=5)


def test_active_report_frames_update_state(rig: Rig) -> None:
    m = rig.motor()
    for i in range(5):
        rig.tr.inject(feedback_frame(1, mode=2, position=0.1 * i, comm_type=CommType.ACTIVE_REPORT))
    assert wait_until(lambda: m.state.rx_rate >= 5)
    s = m.state
    assert s.mode == MotorMode.MOTOR and s.online and s.position == pytest.approx(0.4, abs=1e-3)


# ---------------------------------------------------------------------------
# 直列化・並行
# ---------------------------------------------------------------------------
def test_requests_are_serialised_per_motor() -> None:
    r = Rig()
    r.tr.reply_delay = lambda req: 0.03  # 応答が遅い間に次の要求が出ないこと
    try:
        m = r.motor(request_timeout=1.0)
        f1 = m.enable()
        f2 = m.write_param("limit_spd", 2.0)
        f3 = m.disable()  # 先頭へ割り込む(enable 実行中なので enable の次)
        time.sleep(0.01)
        assert r.types_sent() == [CommType.ENABLE]  # 1 件だけ in-flight
        for f in (f1, f2, f3):
            assert isinstance(f.result(3.0), Feedback)
        assert r.types_sent() == [CommType.ENABLE, CommType.STOP, CommType.PARAM_WRITE]
        # 各 Future は自分の応答で解決している
        assert f1.result().mode == MotorMode.MOTOR and f3.result().mode == MotorMode.RESET
    finally:
        r.bus.stop()


def test_two_motors_run_in_parallel_and_resolve_own_replies() -> None:
    r = Rig(ids=(1, 127))
    r.tr.reply_delay = lambda req: 0.05 if parse_id(req.can_id).dest == 1 else 0.0
    try:
        a, b = r.motor(1, request_timeout=1.0), r.motor(127, request_timeout=1.0)
        fa, fb = a.enable(), b.enable()
        assert fb.result(2.0).motor_id == 127
        assert not fa.done() or fa.result().motor_id == 1
        assert fa.result(2.0).motor_id == 1
    finally:
        r.bus.stop()


def test_failed_request_does_not_block_following_ones() -> None:
    r = Rig(ids=(1,))
    try:
        m = r.motor(request_timeout=0.1)
        r.tr.fail_sends = [Exception("boom")]  # 最初の送信が失敗
        f1 = m.enable()
        f2 = m.get_uid()
        with pytest.raises(Exception, match="boom|送信失敗"):
            f1.result(2.0)
        assert isinstance(f2.result(2.0), UidResponse)
    finally:
        r.bus.stop()


def test_timeout_when_motor_absent() -> None:
    r = Rig(ids=(2,))
    try:
        m = r.motor(1, request_timeout=0.1)
        with pytest.raises(RequestTimeout):
            m.enable().result(2.0)
        assert not m.state.online
    finally:
        r.bus.stop()


# ---------------------------------------------------------------------------
# 状態: unwrap / rx_rate / online
# ---------------------------------------------------------------------------
def test_position_unwrap_across_plus_wrap() -> None:
    u = PositionUnwrapper(25.14)
    out = [u.update(x) for x in (11.0, 12.0, -12.3, -11.5)]
    assert out[:2] == [11.0, 12.0]
    assert out[2] == pytest.approx(-12.3 + 25.14)  # +側へ周回
    assert out[3] == pytest.approx(-11.5 + 25.14)


def test_position_unwrap_across_minus_wrap_and_multi_turn() -> None:
    u = PositionUnwrapper(25.14)
    assert u.update(-12.0) == -12.0
    assert u.update(12.2) == pytest.approx(12.2 - 25.14)  # -側へ周回
    assert u.update(11.0) == pytest.approx(11.0 - 25.14)
    # 逆向きに周回し直すと補正が戻る
    assert u.update(-12.0) == pytest.approx(-12.0)  # 周回の補正が戻る
    assert u.update(12.4) == pytest.approx(12.4 - 25.14)
    assert u.update(-12.4) == pytest.approx(-12.4)
    assert u.update(12.4) == pytest.approx(12.4 - 25.14)


def test_motor_position_multi_from_feedback(rig: Rig) -> None:
    m = rig.motor()
    seq = [11.0, 12.3, -12.2, -11.0]
    for p in seq:
        rig.tr.inject(feedback_frame(1, position=p))
    assert wait_until(lambda: m.state.rx_rate >= 4)
    s = m.state
    assert s.position == pytest.approx(-11.0, abs=2e-3)
    assert s.position_multi == pytest.approx(-11.0 + 8 * math.pi, abs=2e-3)


def test_rx_rate_and_online_expiry(rig: Rig, monkeypatch) -> None:
    m = rig.motor()
    for _ in range(20):
        rig.tr.inject(feedback_frame(1))
    assert wait_until(lambda: m.state.rx_rate >= 20)
    assert m.state.online
    # 時計を進めると online=False、rx_rate は直近 1 s の窓で減る
    real = clock.now
    monkeypatch.setattr(clock, "now", lambda: real() + ONLINE_TIMEOUT_S + 1.5)
    s = m.state
    assert not s.online and s.rx_rate == 0.0
    assert s.last_rx is not None


def test_state_ignores_other_motors_and_version_frames(rig: Rig) -> None:
    m = rig.motor(1)
    rig.tr.inject(feedback_frame(2, position=3.0))
    rig.tr.inject(version_frame(1))  # 値が壊れた Feedback に見えるフレーム
    rig.tr.inject(feedback_frame(1, position=1.0))
    assert wait_until(lambda: m.state.rx_rate >= 1)
    assert m.state.position == pytest.approx(1.0, abs=1e-3)
    assert m.state.rx_rate == 1.0  # バージョン応答は数えない
    # Motor に直接 Feedback として渡されても (comm_type 2 かつ version パターン) 更新しない
    from edulite05.core.protocol.private import decode

    fb = decode(version_frame(1), CTX)
    assert isinstance(fb, Feedback)
    before = m.state
    m._on_message(fb)
    assert m.state == before


def test_close_unsubscribes(rig: Rig) -> None:
    m = rig.motor()
    m.close()
    rig.tr.inject(feedback_frame(1))
    time.sleep(0.1)
    assert m.state.last_rx is None


def test_save_params_accepts_type2_feedback_reply(rig: Rig) -> None:
    """他ファームのように type 2 で応答する場合も受理する。"""
    rig.fake.save_reply = "feedback"
    assert isinstance(rig.motor().save_params().result(2.0), Feedback)


def test_save_params_ignores_uid_reply_of_other_motor(rig: Rig) -> None:
    """別モータの UID 応答は type 22 の応答として受理しない。"""
    m = rig.motor()
    rig.fake.save_reply = "none"
    f = m.save_params()
    assert rig.tr.wait_sent(1)
    rig.tr.inject(CanFrame(make_id(CommType.GET_ID, 99, 0xFE), uid_of(99)))
    with pytest.raises(RequestTimeout):
        f.result(2.0)
