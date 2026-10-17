"""冪等な要求の再送(Motor の `retries`)のテスト。FakeTransport の応答器で応答を落とす。"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator

import pytest
from fakes import HOST, FakeMotors, FakeTransport, uid_of

from edulite05.core.bus import BusManager
from edulite05.core.device.motor import DEFAULT_RETRIES, Motor
from edulite05.core.device.profile import MotorProfile
from edulite05.core.errors import RequestTimeout
from edulite05.core.events import MOTOR_RETRY, EventBus
from edulite05.core.protocol import private
from edulite05.core.protocol.ids import CommType, parse_id
from edulite05.core.protocol.private import DecodeContext, Feedback, ParamResponse, UidResponse
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.transport.base import CanFrame

PROFILE = MotorProfile.load_builtin("el05")
CTX = DecodeContext(HOST, PROFILE.ranges, PROFILE.param_dtypes())
MID = 1
TIMEOUT = 0.05


class Rig:
    """`drop(n)`: n 番目(1 始まり)の送信への応答を落とす述語。"""

    def __init__(self, drop: Callable[[int], bool]) -> None:
        self.fake = FakeMotors((MID,), PROFILE)
        self.tr = FakeTransport(self._respond)
        self.drop = drop
        self.tr.open()
        self.events = EventBus()
        self.retry_events: list[dict] = []
        self.events.subscribe(MOTOR_RETRY, self.retry_events.append)
        self.bus = BusManager(self.tr, CTX, self.events)
        self.bus.start()
        self.motor = Motor(self.bus, MID, PROFILE, HOST, request_timeout=TIMEOUT)

    def _respond(self, frame: CanFrame) -> Iterable[CanFrame]:
        replies = self.fake(frame)  # FakeMotors はステートを更新して応答を返す
        return [] if self.drop(len(self.tr.sent)) else replies

    def close(self) -> None:
        self.bus.stop()


@pytest.fixture
def make() -> Iterator[Callable[[Callable[[int], bool]], Rig]]:
    rigs: list[Rig] = []

    def factory(drop: Callable[[int], bool]) -> Rig:
        r = Rig(drop)
        rigs.append(r)
        return r

    yield factory
    for r in rigs:
        r.close()


# (名前, 呼び出し, 期待する応答型)
IDEMPOTENT: list[tuple[str, Callable[[Motor], object], type]] = [
    ("read_param", lambda m: m.read_param("loc_kp"), ParamResponse),
    ("write_param", lambda m: m.write_param("limit_torque", 0.5), Feedback),
    ("enable", lambda m: m.enable(), Feedback),
    ("disable", lambda m: m.disable(), Feedback),
    ("get_uid", lambda m: m.get_uid(), UidResponse),
]


def test_default_retries_is_two() -> None:
    assert DEFAULT_RETRIES == 2


@pytest.mark.parametrize(("name", "call", "typ"), IDEMPOTENT, ids=[c[0] for c in IDEMPOTENT])
@pytest.mark.parametrize("lost", [1, 2])
def test_idempotent_requests_recover_from_lost_replies(make, name, call, typ, lost) -> None:
    r = make(lambda n: n <= lost)  # 最初の `lost` 回の応答が欠落
    res = call(r.motor).result(2.0)
    assert isinstance(res, typ)
    assert len(r.tr.sent) == lost + 1  # 試行ごとにフレームを再送
    assert len({f.data for f in r.tr.sent}) == 1 and len({f.can_id for f in r.tr.sent}) == 1
    assert r.motor.retry_count == lost
    assert r.bus.stats().retries == lost
    assert [e["attempt"] for e in r.retry_events] == list(range(2, lost + 2))
    assert all(e["motor_id"] == MID for e in r.retry_events)


def test_set_run_mode_and_active_report_recover(make) -> None:
    r = make(lambda n: n == 1 or n == 3)  # 各要求の最初の応答が欠落
    m = r.motor
    m.disable().result(2.0)  # 1 回目の応答を落として再送(n=1,2)
    assert m.state.mode == MotorMode.RESET
    m.set_run_mode(RunMode.CSP).result(2.0)  # n=3 を落とす → n=4 で成功
    assert r.fake.params[MID][0x7005] == int(RunMode.CSP)
    assert m.retry_count == 2
    r2 = make(lambda n: n == 1)
    r2.motor.set_active_report(False).result(2.0)  # OFF の応答(type 2)が欠落 → 再送
    assert r2.motor.retry_count == 1 and len(r2.tr.sent) == 2


def test_reply_lost_on_every_attempt_times_out(make) -> None:
    r = make(lambda n: True)
    t0 = time.monotonic()
    with pytest.raises(RequestTimeout):
        r.motor.read_param("loc_kp").result(5.0)
    assert len(r.tr.sent) == 1 + DEFAULT_RETRIES  # 計 3 回送って諦める
    assert time.monotonic() - t0 >= 3 * TIMEOUT * 0.9
    assert r.motor.retry_count == DEFAULT_RETRIES
    # 失敗した要求が後続をブロックしない
    r.drop = lambda n: False
    assert r.motor.read_param("loc_kp").result(2.0).ok


def test_retries_zero_means_single_attempt(make) -> None:
    r = make(lambda n: True)
    r.motor.retries = 0
    with pytest.raises(RequestTimeout):
        r.motor.write_param("limit_torque", 0.5).result(2.0)
    assert len(r.tr.sent) == 1 and r.motor.retry_count == 0 and r.retry_events == []


@pytest.mark.parametrize("name", ["save_params", "set_zero"])
def test_non_idempotent_requests_are_not_retried(make, name) -> None:
    r = make(lambda n: True)
    with pytest.raises(RequestTimeout):
        getattr(r.motor, name)().result(2.0)
    assert len(r.tr.sent) == 1
    assert r.motor.retry_count == 0 and r.retry_events == []


def test_late_reply_to_earlier_attempt_satisfies_next_attempt(make) -> None:
    """試行 1 の応答が遅れて、試行 2 の送信後に届いたら試行 2 の応答として受理する。"""
    r = make(lambda n: n == 2)  # 試行 2 自体への応答は無い
    r.tr.reply_delay = lambda req: 0.075 if len(r.tr.sent) == 1 else 0.0
    res = r.motor.read_param("loc_kp").result(2.0)
    assert res.ok
    assert len(r.tr.sent) == 2  # 3 回目は送らない
    assert r.motor.retry_count == 1


def test_reply_received_before_tx_is_not_accepted(make) -> None:
    """要求の送信前に届いていた(古い)応答では解決しない。送信後の応答だけを受理する。"""
    r = make(lambda n: True)  # 応答器は全部落とす
    m = r.motor
    stale = FakeMotors((MID,), PROFILE)(
        private.read_param(MID, HOST, PROFILE.param("loc_kp").index)
    )
    for fr in stale:
        r.tr.inject(fr)
    time.sleep(0.02)  # 古い応答は RX 済み
    with pytest.raises(RequestTimeout):
        m.read_param("loc_kp").result(5.0)
    assert len(r.tr.sent) == 1 + DEFAULT_RETRIES


def test_uid_matches_bench(make) -> None:
    r = make(lambda n: n == 1)
    assert r.motor.get_uid().result(2.0).uid == uid_of(MID)
    assert parse_id(r.tr.sent[0].can_id).comm_type == CommType.GET_ID
