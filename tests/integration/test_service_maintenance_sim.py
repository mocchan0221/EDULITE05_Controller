"""MotorService の保守操作を SimTransport に対して動かす統合テスト(phase3.md §1)。

対象: set_zero / set_can_id / set_report_period / set_can_timeout / set_baud / switch_protocol /
restore_protocol / write_param / read_all_params / save_params / recover。
前提の拒否(有効なモータ・E-STOP ラッチ・処理中)、運転系パラメータの拒否、ID 変更後の付け替え
(新 ID で運転)、MIT / CANopen からの復帰、冪等でない操作が再送されないこと。
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from edulite05.core import clock
from edulite05.core.control.controllers import Velocity
from edulite05.core.control.params import VelocityParams
from edulite05.core.control.service import (
    MOTION_PARAM_KEYS,
    SERVICE_MAINTENANCE,
    SERVICE_MOTORS_CHANGED,
    IdChangeResult,
    MaintenanceResult,
    MotorService,
    UiState,
)
from edulite05.core.device.profile import MotorProfile
from edulite05.core.errors import ProtocolError, RequestTimeout, SafetyError
from edulite05.core.protocol.ids import CommType, parse_id
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.session import Session
from edulite05.sim.motor_model import SimMotor
from edulite05.sim.transport import SimTransport, make_motors

PROFILE = MotorProfile.load_builtin("el05")
A, B = 127, 1


def wait_until(pred: Callable[[], object], timeout: float = 3.0) -> bool:
    end = clock.now() + timeout
    while clock.now() < end:
        if pred():
            return True
        time.sleep(0.005)
    return bool(pred())


class Rig:
    def __init__(self, n: int = 2, **sim_kwargs: Any) -> None:
        self.sim = SimTransport(make_motors(n, profile=PROFILE), **sim_kwargs)
        self.session = Session(self.sim, PROFILE)
        self.session.open()
        self.svc = MotorService(self.session)
        self.events: list[tuple[str, Any]] = []
        for topic in (SERVICE_MAINTENANCE, SERVICE_MOTORS_CHANGED):
            self.session.events.subscribe(topic, lambda p, t=topic: self.events.append((t, p)))
        for mid in (A, B)[:n]:
            self.session.motor(mid)
        self.svc.open()
        assert wait_until(lambda: all(self.svc.ui_state(i) is UiState.DISABLED for i in (A, B)[:n]))

    def topics(self, topic: str) -> list[Any]:
        return [p for t, p in list(self.events) if t == topic]

    def close(self) -> None:
        self.svc.close()
        self.session.close()


@pytest.fixture
def rig() -> Iterator[Rig]:
    r = Rig()
    yield r
    r.close()


def sm(rig: Rig, mid: int) -> SimMotor:
    return rig.sim.motor(mid)


def tap(
    motor: SimMotor,
    *,
    drop_reply: dict[int, int] | None = None,
    swallow: frozenset[int] = frozenset(),
) -> list[int]:
    """`motor` が受けた(宛先が自分の)通信タイプを記録する。

    `swallow`: そのタイプは処理せず無視する(モータが反応しない)。`drop_reply`: タイプごとに
    最初の n 回は処理するが応答を捨てる(応答の欠落)。戻り値のリストに通信タイプが追記される。
    """
    calls: list[int] = []
    orig = motor.handle
    budget = dict(drop_reply or {})

    def handle(frame):  # type: ignore[no-untyped-def]
        ct = -1
        mine = False
        if frame.extended:
            f = parse_id(frame.can_id)
            ct, mine = f.comm_type, f.dest == motor.motor_id
        if mine:
            calls.append(ct)
            if ct in swallow:
                return []
        out = orig(frame)
        if mine and budget.get(ct, 0) > 0:
            budget[ct] -= 1
            return []
        return out

    motor.handle = handle  # type: ignore[method-assign]
    return calls


# ---------------------------------------------------------------- set_zero
def test_set_zero_in_operation_mode(rig: Rig) -> None:
    m = sm(rig, A)
    m.position = 1.5
    res = rig.svc.set_zero(A).result(5.0)
    assert isinstance(res, MaintenanceResult) and res.verified and not res.needs_power_cycle
    assert abs(m.position) < 1e-6
    assert "ゼロ" in res.message
    assert m.run_mode == int(RunMode.OPERATION)  # もともと 0 なら変更しない
    ev = rig.topics(SERVICE_MAINTENANCE)[-1]
    assert ev["op"] == "set_zero" and ev["ok"] and ev["message"] == res.message


@pytest.mark.parametrize("run_mode", [RunMode.VELOCITY, RunMode.PP, RunMode.CURRENT])
def test_set_zero_switches_run_mode_first(rig: Rig, run_mode: RunMode) -> None:
    # type 6 は run_mode 0 / 5 でしか効かない(§2.7)。それ以外なら 0 へ変えてから送る
    m = sm(rig, A)
    m.position = -2.0
    m.set_param("run_mode", int(run_mode))
    res = rig.svc.set_zero(A).result(5.0)
    assert abs(m.position) < 1e-6 and m.run_mode == int(RunMode.OPERATION)
    assert "run_mode" in res.message


def test_set_zero_keeps_csp(rig: Rig) -> None:
    m = sm(rig, A)
    m.position = 0.8
    m.set_param("run_mode", int(RunMode.CSP))
    rig.svc.set_zero(A).result(5.0)
    assert abs(m.position) < 1e-6 and m.run_mode == int(RunMode.CSP)
    assert abs(m.get_param("loc_ref")) < 1e-6


def test_set_zero_lost_reply_is_verified_by_position_and_not_retried(rig: Rig) -> None:
    m = sm(rig, A)
    m.position = 1.0
    calls = tap(m, drop_reply={int(CommType.SET_ZERO): 1})
    res = rig.svc.set_zero(A).result(5.0)
    assert abs(m.position) < 1e-6 and "欠落" in res.message
    assert calls.count(int(CommType.SET_ZERO)) == 1  # 再送しない


def test_set_zero_unanswered_fails_without_retry(rig: Rig) -> None:
    m = sm(rig, A)
    m.position = 1.0
    calls = tap(m, swallow=frozenset({int(CommType.SET_ZERO)}))
    with pytest.raises(RequestTimeout):
        rig.svc.set_zero(A).result(5.0)
    assert calls.count(int(CommType.SET_ZERO)) == 1
    assert m.position == pytest.approx(1.0)
    ev = rig.topics(SERVICE_MAINTENANCE)[-1]
    assert ev["op"] == "set_zero" and not ev["ok"]
    assert rig.svc.ui_state(A) is UiState.DISABLED and not rig.svc.is_busy(A)


# ---------------------------------------------------------------- 前提(拒否)
OPS: dict[str, Callable[[MotorService, int], Any]] = {
    "set_zero": lambda s, i: s.set_zero(i),
    "set_can_id": lambda s, i: s.set_can_id(i, 55),
    "set_report_period": lambda s, i: s.set_report_period(i, 20),
    "set_can_timeout": lambda s, i: s.set_can_timeout(i, 0.5),
    "set_baud": lambda s, i: s.set_baud(i, 2),
    "switch_protocol": lambda s, i: s.switch_protocol(i, "mit"),
    "write_param": lambda s, i: s.write_param(i, "cur_kp", 0.2),
    "read_all_params": lambda s, i: s.read_all_params(i),
    "save_params": lambda s, i: s.save_params(i),
    "recover": lambda s, i: s.recover(i),
}


@pytest.mark.parametrize("op", sorted(OPS))
def test_ops_rejected_while_enabled(rig: Rig, op: str) -> None:
    rig.svc.enable(A, RunMode.VELOCITY).result(5.0)
    m = sm(rig, A)
    before = dict(m.params), m.baud_code, m.protocol, m.motor_id
    with pytest.raises(ProtocolError, match="DISABLED"):
        OPS[op](rig.svc, A)
    assert (dict(m.params), m.baud_code, m.protocol, m.motor_id) == before
    assert rig.svc.ui_state(A) is UiState.ENABLED and not rig.svc.is_busy(A)
    rig.svc.disable([A]).result(3.0)


@pytest.mark.parametrize("op", sorted(OPS))
def test_ops_rejected_while_estop_latched(rig: Rig, op: str) -> None:
    rig.svc.estop()
    with pytest.raises(SafetyError):
        OPS[op](rig.svc, A)
    rig.svc.release_estop()


def test_restore_rejected_while_latched_or_enabled(rig: Rig) -> None:
    rig.svc.enable(B, RunMode.VELOCITY).result(5.0)
    with pytest.raises(ProtocolError, match="有効"):
        rig.svc.restore_protocol(77, "mit")
    rig.svc.disable([B]).result(3.0)
    rig.svc.estop()
    with pytest.raises(SafetyError):
        rig.svc.restore_protocol(77, "mit")
    with pytest.raises(SafetyError):
        rig.svc.restore_protocol(77, "canopen")
    rig.svc.release_estop()


def test_ops_rejected_while_busy_and_enable_blocked() -> None:
    r = Rig(latency_s=0.01)  # 1 往復 20 ms: read_all_params が十分長く続く
    try:
        fut = r.svc.read_all_params(A)
        assert r.svc.is_busy(A)
        with pytest.raises(ProtocolError, match="処理中"):
            r.svc.set_zero(A)
        with pytest.raises(ProtocolError, match="処理中"):
            r.svc.enable(A, RunMode.VELOCITY)
        res = fut.result(30.0)
        assert len(res) == len(PROFILE.params)
        assert wait_until(lambda: not r.svc.is_busy(A))
        r.svc.enable(A, RunMode.VELOCITY).result(5.0)  # 終われば有効化できる
    finally:
        r.close()


# ---------------------------------------------------------------- set_can_id
def test_set_can_id_rekeys_everything_and_operates_under_new_id(rig: Rig) -> None:
    m = sm(rig, A)
    hub = rig.svc.telemetry
    assert wait_until(lambda: hub.latest("m127.pos") is not None)
    res = rig.svc.set_can_id(A, 5).result(5.0)
    assert isinstance(res, IdChangeResult) and res == 5 and res.old_id == A
    assert "127" in res.message and "5" in res.message
    assert m.motor_id == 5 and sm(rig, 5) is m
    # Session / MotorService / TelemetryHub の登録
    assert sorted(rig.session.motors) == [B, 5]
    assert rig.session.motor(5).motor_id == 5 and rig.session.motor(5).state.motor_id == 5
    assert rig.svc.motor_ids() == [B, 5]
    assert rig.svc.motor(5) is rig.session.motors[5]
    assert hub.has("m5.pos") and not hub.has("m127.pos")
    assert hub.info("m5.pos").motor_id == 5 and "m5 " in hub.info("m5.pos").label
    assert rig.svc.ui_state(A) is UiState.OFFLINE  # 旧 ID はもう無い
    # イベント
    ch = rig.topics(SERVICE_MOTORS_CHANGED)
    assert ch and ch[-1]["motor_ids"] == [B, 5] and ch[-1]["renamed"] == {A: 5}
    ev = rig.topics(SERVICE_MAINTENANCE)[-1]
    assert ev["op"] == "set_can_id" and ev["ok"] and ev["motor_id"] == 5
    # 新 ID で監視が続き、運転できる
    assert wait_until(lambda: rig.svc.ui_state(5) is UiState.DISABLED)
    assert wait_until(lambda: (hub.latest("m5.pos") or (0.0, 0.0))[0] > clock.now() - 0.5)
    rig.svc.enable(5, RunMode.VELOCITY).result(5.0)
    rig.svc.start([5], Velocity(VelocityParams(velocity=2.0, accel=50.0)))
    assert wait_until(lambda: abs(m.velocity - 2.0) < 0.15)
    assert rig.svc.ui_state(5) is UiState.RUNNING
    rig.svc.stop_and_disable([5]).result(5.0)
    assert m.mode is MotorMode.RESET and rig.svc.ui_state(5) is UiState.DISABLED
    # 他のモータは無関係
    assert rig.svc.ui_state(B) is UiState.DISABLED
    # 保守操作も新 ID で
    rig.svc.set_report_period(5, 20).result(5.0)
    with pytest.raises(ProtocolError, match="登録されていません"):
        rig.svc.set_zero(A)  # 旧 ID はもう無い


def test_set_can_id_rejects_bad_ids(rig: Rig) -> None:
    with pytest.raises(ProtocolError):
        rig.svc.set_can_id(A, 128)
    with pytest.raises(ProtocolError):
        rig.svc.set_can_id(A, A)
    with pytest.raises(ProtocolError, match="既に"):
        rig.svc.set_can_id(A, B)  # バスにいる別のモータの ID
    assert sm(rig, A).motor_id == A and not rig.svc.is_busy(A)


def test_set_can_id_lost_reply_is_confirmed_by_probe_and_not_retried(rig: Rig) -> None:
    m = sm(rig, A)
    calls = tap(m, drop_reply={int(CommType.SET_CAN_ID): 1})
    res = rig.svc.set_can_id(A, 9).result(5.0)
    assert res == 9 and m.motor_id == 9 and rig.svc.motor_ids() == [B, 9]
    assert calls.count(int(CommType.SET_CAN_ID)) == 1


def test_set_can_id_unanswered_keeps_old_registration(rig: Rig) -> None:
    m = sm(rig, A)
    calls = tap(m, swallow=frozenset({int(CommType.SET_CAN_ID)}))
    with pytest.raises(RequestTimeout, match="応答がありません"):
        rig.svc.set_can_id(A, 9).result(8.0)
    assert calls.count(int(CommType.SET_CAN_ID)) == 1
    assert m.motor_id == A and rig.svc.motor_ids() == [B, A]
    assert sorted(rig.session.motors) == [B, A]
    assert not rig.topics(SERVICE_MOTORS_CHANGED)
    assert not rig.svc.is_busy(A) and rig.svc.telemetry.has("m127.pos")


# ---------------------------------------------------------------- 上報周期 / canTimeout
def test_set_report_period(rig: Rig) -> None:
    m = sm(rig, A)
    res = rig.svc.set_report_period(A, 25).result(5.0)
    assert "25" in res.message
    assert m.get_param("EPScan_time") == 3 and m.report_period == pytest.approx(0.025)
    assert m.active_report
    # 以後の主動上報 ON(有効化 → 無効化)でも同じ周期を使う
    rig.svc.enable(A, RunMode.VELOCITY).result(5.0)
    rig.svc.disable([A]).result(3.0)
    assert wait_until(lambda: m.active_report)
    assert m.get_param("EPScan_time") == 3
    # 10 ms(N=0)へ戻す
    rig.svc.set_report_period(A, 10).result(5.0)
    assert m.get_param("EPScan_time") == 0 and m.report_period == pytest.approx(0.010)


@pytest.mark.parametrize("bad", [0, 5, 9, 11, 12, 503, 505, 10_000, -10, 10.0, "20", None, True])
def test_set_report_period_validation(rig: Rig, bad: Any) -> None:
    with pytest.raises(ProtocolError):
        rig.svc.set_report_period(A, bad)
    assert sm(rig, A).get_param("EPScan_time") == 0 and not rig.svc.is_busy(A)


def test_set_report_period_accepts_range_edges(rig: Rig) -> None:
    rig.svc.set_report_period(A, 500).result(5.0)
    assert sm(rig, A).get_param("EPScan_time") == 98
    rig.svc.set_report_period(A, 10).result(5.0)


def test_set_can_timeout(rig: Rig) -> None:
    m = sm(rig, A)
    res = rig.svc.set_can_timeout(A, 0.25).result(5.0)
    assert m.get_param("canTimeout") == 5000 and res.verified and res.value == 5000
    assert "0.25" in res.message
    res = rig.svc.set_can_timeout(A, 0).result(5.0)
    assert m.get_param("canTimeout") == 0 and "無効" in res.message
    res = rig.svc.set_can_timeout(A, 1.0).result(5.0)
    assert m.get_param("canTimeout") == 20000


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), "1", None, True, 1e9])
def test_set_can_timeout_validation(rig: Rig, bad: Any) -> None:
    with pytest.raises(ProtocolError):
        rig.svc.set_can_timeout(A, bad)
    assert sm(rig, A).get_param("canTimeout") == 0


# ---------------------------------------------------------------- baud / protocol
def test_set_baud_needs_power_cycle_and_is_not_retried(rig: Rig) -> None:
    m = sm(rig, A)
    calls = tap(m, drop_reply={int(CommType.BAUD): 1})
    with pytest.raises(RequestTimeout):  # 応答なし → 再送しない
        rig.svc.set_baud(A, 2).result(5.0)
    assert calls.count(int(CommType.BAUD)) == 1
    res = rig.svc.set_baud(A, 3).result(5.0)
    assert res.needs_power_cycle and "電源の再投入が必要です" in res.message
    assert "250" in res.message and res.value == 3
    assert calls.count(int(CommType.BAUD)) == 2
    assert m.baud_code == 3 and m.active_baud_code == 1  # 再投入までは変わらない
    rig.sim.power_cycle(A)
    assert m.active_baud_code == 3
    assert wait_until(lambda: m.active_report is False)  # 再投入で主動上報は OFF


@pytest.mark.parametrize("bad", [0, 5, -1, "2", None, True])
def test_set_baud_validation(rig: Rig, bad: Any) -> None:
    with pytest.raises(ProtocolError):
        rig.svc.set_baud(A, bad)
    assert sm(rig, A).baud_code == 1


def test_switch_protocol_pending_until_power_cycle_and_not_retried(rig: Rig) -> None:
    m = sm(rig, A)
    calls = tap(m, drop_reply={int(CommType.PROTOCOL): 1})
    with pytest.raises(RequestTimeout):
        rig.svc.switch_protocol(A, 2).result(5.0)
    assert calls.count(int(CommType.PROTOCOL)) == 1
    res = rig.svc.switch_protocol(A, "mit").result(5.0)
    assert res.needs_power_cycle and "MIT" in res.message
    assert "電源の再投入が必要です" in res.message
    assert m.protocol == 2 and m.active_protocol == 0
    assert calls.count(int(CommType.PROTOCOL)) == 2
    # 0 / "canopen" など
    assert rig.svc.switch_protocol(A, 0).result(5.0).value == 0 and m.protocol == 0
    assert rig.svc.switch_protocol(A, "CANopen").result(5.0).value == 1 and m.protocol == 1
    rig.svc.switch_protocol(A, "private").result(5.0)
    assert m.protocol == 0


@pytest.mark.parametrize("bad", [3, -1, "ethernet", None, True])
def test_switch_protocol_validation(rig: Rig, bad: Any) -> None:
    with pytest.raises(ProtocolError):
        rig.svc.switch_protocol(A, bad)
    assert sm(rig, A).protocol == 0


# ---------------------------------------------------------------- restore_protocol
def _to_foreign(rig: Rig, protocol: str, mid: int = A) -> SimMotor:
    """モータを MIT / CANopen にして再投入する(私有プロトコルでは無応答になる)。"""
    m = sm(rig, mid)
    rig.svc.switch_protocol(mid, protocol).result(5.0)
    rig.sim.power_cycle(mid)
    assert m.active_protocol == {"mit": 2, "canopen": 1}[protocol]
    with pytest.raises(RequestTimeout):
        rig.session.motor(mid).get_uid().result(5.0)
    return m


@pytest.mark.parametrize("source", ["mit", "canopen"])
def test_restore_protocol_from_foreign_protocol(rig: Rig, source: str) -> None:
    m = _to_foreign(rig, source)
    # スキャンには出てこない(モータ未選択でも復帰できる前提)
    assert A not in [x.motor_id for x in rig.session.scan(range(1, 128), settle_s=0.1).result(5.0)]
    res = rig.svc.restore_protocol(A, source).result(10.0)
    assert res.verified and res.needs_power_cycle
    assert "電源の再投入が必要です" in res.message
    assert "未検証" not in res.message  # シミュレータは標準フレームの扱いが確定している
    assert m.protocol == 0 and m.active_protocol == source_code(source)  # 再投入までは変わらない
    rig.sim.power_cycle(A)
    assert m.active_protocol == 0
    assert rig.svc.recover(A).result(5.0) is True
    assert wait_until(lambda: rig.svc.ui_state(A) is UiState.DISABLED)
    assert wait_until(lambda: m.active_report)
    # 通常どおり運転できる
    rig.svc.enable(A, RunMode.VELOCITY).result(5.0)
    rig.svc.start([A], Velocity(VelocityParams(velocity=1.0, accel=50.0)))
    assert wait_until(lambda: abs(m.velocity - 1.0) < 0.15)
    rig.svc.stop_and_disable([A]).result(5.0)


def source_code(source: str) -> int:
    return {"mit": 2, "canopen": 1}[source]


@pytest.mark.parametrize("source", ["mit", "canopen"])
def test_restore_marks_unverified_on_at_adapter(rig: Rig, source: str) -> None:
    _to_foreign(rig, source)
    rig.sim.caps = dataclasses.replace(rig.sim.caps, name="robstride_at")  # AT アダプタ扱い
    res = rig.svc.restore_protocol(A, source).result(10.0)
    assert "未検証" in res.message and "電源の再投入が必要です" in res.message
    assert not res.verified  # AT での標準フレームの表現は未検証(Q-T1)
    assert sm(rig, A).protocol == 0  # 送信自体は通る


def test_restore_without_reply_reports_unconfirmed_and_retries(rig: Rig) -> None:
    # 該当モータがいない(または標準フレームが通らない): 例外にせず未確認として返す。
    # 復帰指令は冪等なので応答がなければ再送する(計 3 回)
    sent: list[Any] = []
    orig = rig.sim.send
    rig.sim.send = lambda f: (sent.append(f), orig(f))[1]  # type: ignore[method-assign]
    res = rig.svc.restore_protocol(77, "mit").result(10.0)
    assert not res.verified and "確認できませんでした" in res.message
    assert "電源の再投入が必要です" in res.message
    std = [f for f in sent if not f.extended]
    assert len(std) == 3 and all(f.can_id == 77 for f in std)
    assert all(f.data == bytes.fromhex("FFFFFFFFFFFF00FD") for f in std)
    sent.clear()
    rig.svc.restore_protocol(77, "canopen").result(10.0)
    ext = [f for f in sent if f.extended and f.can_id == 0xFFF]
    assert len(ext) == 3 and all(f.data == bytes.fromhex("0102030405060000") for f in ext)


def test_restore_validation_and_caps(rig: Rig) -> None:
    with pytest.raises(ProtocolError):
        rig.svc.restore_protocol(A, "private")
    with pytest.raises(ProtocolError):
        rig.svc.restore_protocol(128, "mit")
    with pytest.raises(ProtocolError):
        rig.svc.restore_protocol(-1, "canopen")
    rig.sim.caps = dataclasses.replace(rig.sim.caps, standard_frames=False)
    with pytest.raises(ProtocolError, match="標準"):
        rig.svc.restore_protocol(A, "mit")


def test_restore_only_reacts_to_matching_frame(rig: Rig) -> None:
    # MIT のモータは CANopen の復帰フレームには反応しない(逆も同様)。他の ID も無視
    m = _to_foreign(rig, "mit")
    res = rig.svc.restore_protocol(A, "canopen").result(10.0)
    assert not res.verified and m.protocol == 2  # 応答なし・設定も変わらない
    rig.svc.restore_protocol(B, "mit").result(10.0)  # 別の ID 宛て
    assert m.protocol == 2
    assert rig.svc.restore_protocol(A, "mit").result(10.0).verified and m.protocol == 0


# ---------------------------------------------------------------- write_param / read_all / save
def test_write_param_verifies_by_readback(rig: Rig) -> None:
    m = sm(rig, A)
    res = rig.svc.write_param(A, "cur_kp", 0.2).result(5.0)
    assert res.verified and m.get_param("cur_kp") == pytest.approx(0.2, rel=1e-6)
    assert "cur_kp" in res.message
    res = rig.svc.write_param(A, 0x7010, 0.3).result(5.0)  # index でも
    assert m.get_param("cur_kp") == pytest.approx(0.3, rel=1e-6)
    ev = rig.topics(SERVICE_MAINTENANCE)[-1]
    assert ev["op"] == "write_param" and ev["ok"]


def test_write_param_retries_lost_reply(rig: Rig) -> None:
    # 冪等な操作は再送する(応答欠落 1 回 → 計 2 回送って成功)
    m = sm(rig, A)
    calls = tap(m, drop_reply={int(CommType.PARAM_WRITE): 1})
    res = rig.svc.write_param(A, "spd_kp", 7.0).result(5.0)
    assert res.verified and m.get_param("spd_kp") == pytest.approx(7.0)
    assert calls.count(int(CommType.PARAM_WRITE)) == 2


def test_write_param_unreadable_is_unverified(rig: Rig) -> None:
    # 0x702E dcc_set は実機で読出失敗(§8.0): 書込は通るが読み戻せない
    pd = PROFILE.param("dcc_set")
    res = rig.svc.write_param(A, "dcc_set", pd.default).result(5.0)
    assert not res.verified and "確認できていません" in res.message


@pytest.mark.parametrize("key", sorted(MOTION_PARAM_KEYS))
def test_write_param_rejects_motion_params(rig: Rig, key: str) -> None:
    m = sm(rig, A)
    before = dict(m.params)
    for ref in (key, PROFILE.param(key).index):
        with pytest.raises(ProtocolError, match="運転系"):
            rig.svc.write_param(A, ref, 0)
    assert dict(m.params) == before and not rig.svc.is_busy(A)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("VBUS", 12.0),
        ("mechPos", 0.0),
        ("cur_filt_gain", 2.0),
        ("cur_kp", float("nan")),
        ("zero_sta", 0.5),
        ("nope", 1),
    ],
)
def test_write_param_rejects_invalid(rig: Rig, key: str, value: Any) -> None:
    before = dict(sm(rig, A).params)
    with pytest.raises(ProtocolError):
        rig.svc.write_param(A, key, value)
    assert dict(sm(rig, A).params) == before and not rig.svc.is_busy(A)


def test_read_all_params(rig: Rig) -> None:
    res = rig.svc.read_all_params(A).result(10.0)
    assert set(res) == {p.index for p in PROFILE.params}
    assert res[0x7005].ok and res[0x7005].value == 0
    assert not res[0x702E].ok  # 実機で読出失敗のパラメータ(§8.0)
    ev = rig.topics(SERVICE_MAINTENANCE)[-1]
    assert ev["op"] == "read_all_params" and ev["ok"]


def test_save_params_does_not_persist_70xx(rig: Rig) -> None:
    m = sm(rig, A)
    rig.svc.write_param(A, "cur_kp", 0.2).result(5.0)
    res = rig.svc.save_params(A).result(5.0)
    assert "保存" in res.message and "工場値に戻ります" in res.message
    rig.svc.write_param(A, "cur_ki", 0.05).result(5.0)
    rig.sim.power_cycle(A)
    assert m.get_param("cur_kp") != pytest.approx(0.2, rel=1e-6)  # 保存後でも 0x70xx は戻る
    assert m.get_param("cur_ki") == pytest.approx(0.0158)


def test_runtime_param_messages_do_not_claim_persistence(rig: Rig) -> None:
    w = rig.svc.write_param(A, "cur_kp", 0.2).result(5.0)
    assert "保存するには" not in w.message and "工場値に戻ります" in w.message
    t = rig.svc.set_can_timeout(A, 1.0).result(5.0)
    assert "電源を切るまで有効" in t.message and "有効化のたびに" in t.message
    r = rig.svc.set_report_period(A, 50).result(5.0)
    assert "電源を切るまで有効" in r.message


def test_save_params_is_not_retried(rig: Rig) -> None:
    m = sm(rig, A)
    calls = tap(m, drop_reply={int(CommType.SAVE): 1})
    with pytest.raises(RequestTimeout):
        rig.svc.save_params(A).result(5.0)
    assert calls.count(int(CommType.SAVE)) == 1
    rig.svc.save_params(A).result(5.0)
    assert calls.count(int(CommType.SAVE)) == 2


# ---------------------------------------------------------------- 後始末・復帰
def test_monitoring_resumes_after_maintenance(rig: Rig) -> None:
    m = sm(rig, A)
    rig.svc.set_can_timeout(A, 0.5).result(5.0)
    assert wait_until(lambda: m.active_report)  # 操作の後に主動上報が ON へ戻る
    assert rig.svc.ui_state(A) is UiState.DISABLED and not rig.svc.is_busy(A)
    # 失敗しても busy・監視は戻る
    with pytest.raises(ProtocolError):
        rig.svc.write_param(A, "cur_filt_gain", 5.0)
    assert not rig.svc.is_busy(A)
    rig.svc.enable(A, RunMode.VELOCITY).result(5.0)  # 保守の後も有効化できる
    rig.svc.disable([A]).result(3.0)


def test_recover_reports_no_answer(rig: Rig) -> None:
    _to_foreign(rig, "mit")  # 私有プロトコルでは応答しない
    assert rig.svc.recover(A).result(5.0) is False
