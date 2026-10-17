"""BusManager のテスト(phase1.md §1.7)。FakeTransport 上で決定的に動かす。"""

from __future__ import annotations

import threading
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
from edulite05.core.bus import BusManager, Priority
from edulite05.core.device.profile import MotorProfile
from edulite05.core.errors import RequestTimeout, TransportError
from edulite05.core.events import BUS_ERROR, EventBus
from edulite05.core.protocol import private
from edulite05.core.protocol.ids import CommType, make_id, parse_id
from edulite05.core.protocol.private import (
    DecodeContext,
    Feedback,
    Message,
    ParamResponse,
    UidResponse,
    Unknown,
)
from edulite05.core.transport.base import CanFrame

PROFILE = MotorProfile.load_builtin("el05")
CTX = DecodeContext(HOST, PROFILE.ranges, PROFILE.param_dtypes())


def make_bus(responder=None, **kw) -> tuple[BusManager, FakeTransport]:
    tr = FakeTransport(responder)
    tr.open()
    bus = BusManager(tr, CTX, EventBus(), **kw)
    return bus, tr


@pytest.fixture
def started() -> Iterator[tuple[BusManager, FakeTransport]]:
    bus, tr = make_bus(FakeMotors([1, 2, 3, 127]))
    bus.start()
    yield bus, tr
    bus.stop()


def frame_n(n: int) -> CanFrame:
    """区別用のフレーム(宛先 = n)。"""
    return private.enable(n, HOST)


# ---------------------------------------------------------------------------
# TX: 優先度・FIFO・send_now
# ---------------------------------------------------------------------------
def test_priority_order_and_fifo_within_priority() -> None:
    bus, tr = make_bus()
    tr.gate = threading.Event()
    bus.start()
    try:
        bus.send(frame_n(10), Priority.REQUEST)  # TX スレッドが send で止まる
        assert tr.entered.wait(2.0)
        bus.send(frame_n(11), Priority.BACKGROUND)
        bus.send(frame_n(12), Priority.REQUEST)
        bus.send(frame_n(13), Priority.CONTROL)
        bus.send(frame_n(14), Priority.ESTOP)
        bus.send(frame_n(15), Priority.CONTROL)
        bus.send(frame_n(16), Priority.REQUEST)
        assert bus.stats().queue_len == 6
        tr.gate.set()
        assert tr.wait_sent(7)
    finally:
        bus.stop()
    dests = [i & 0xFF for i in tr.sent_ids()]
    assert dests == [10, 14, 13, 15, 12, 16, 11]


def test_send_now_bypasses_queue() -> None:
    bus, tr = make_bus()
    tr.send_delay = 0.02  # 1 件 20 ms
    bus.start()
    try:
        for n in range(20, 40):
            bus.send(frame_n(n), Priority.REQUEST)
        t0 = clock.now()
        bus.send_now([frame_n(99), frame_n(98)])
        assert clock.now() - t0 < 0.5
        # 20 件のバックログ(約 0.4 s)より十分前に出ている
        ids = [i & 0xFF for i in tr.sent_ids()]
        assert ids.index(99) < 6 and ids.index(98) == ids.index(99) + 1
        assert len(ids) < 20
    finally:
        bus.stop()


def test_send_now_sends_all_frames_even_if_send_many_fails() -> None:
    bus, tr = make_bus()
    bus.start()
    try:
        orig = tr.send_many
        calls = []

        def broken(frames) -> None:
            calls.append(len(frames))
            raise TransportError("write failed")

        tr.send_many = broken  # type: ignore[method-assign]
        bus.send_now([frame_n(1), frame_n(2)])  # 個別送信にフォールバックして全て通る
        assert [i & 0xFF for i in tr.sent_ids()] == [1, 2]
        tr.send_many = orig  # type: ignore[method-assign]
        tr.fail_sends = [TransportError("x"), TransportError("y")]
        with pytest.raises(TransportError):
            bus.send_now([frame_n(3), frame_n(4)])  # 両方失敗 → 例外
    finally:
        bus.stop()


def test_send_requires_running() -> None:
    bus, _ = make_bus()
    with pytest.raises(TransportError):
        bus.send(frame_n(1))
    with pytest.raises(TransportError):
        bus.request(frame_n(1), lambda m: True)


def test_tx_stamps_direction_and_tx_rate(started) -> None:
    bus, tr = started
    bus.send(frame_n(1))
    assert tr.wait_sent(1)
    assert tr.sent[0].direction.value == "tx" and tr.sent[0].timestamp > 0
    assert wait_until(lambda: bus.stats().tx_fps >= 1.0)


# ---------------------------------------------------------------------------
# request: 照合・タイムアウト・並行
# ---------------------------------------------------------------------------
def test_request_resolves_with_matching_reply(started) -> None:
    bus, _ = started
    fut = bus.request(
        private.get_uid(127, HOST),
        lambda m: isinstance(m, UidResponse) and m.motor_id == 127,
    )
    msg = fut.result(2.0)
    assert isinstance(msg, UidResponse) and msg.uid == uid_of(127)


def test_request_timeout_raises_and_counts() -> None:
    bus, _ = make_bus()  # 応答なし
    bus.start()
    try:
        t0 = clock.now()
        fut = bus.request(frame_n(1), lambda m: True, timeout=0.1)
        with pytest.raises(RequestTimeout):
            fut.result(2.0)
        assert 0.09 <= clock.now() - t0 < 1.0
        assert bus.stats().timeouts == 1
    finally:
        bus.stop()


def test_deadline_counts_from_send_not_from_queueing() -> None:
    """キューで待たされた分は timeout に含めない(送信時刻 + timeout)。"""
    bus, tr = make_bus(FakeMotors([1]))
    tr.gate = threading.Event()
    bus.start()
    try:
        bus.send(frame_n(5))
        assert tr.entered.wait(2.0)
        fut = bus.request(
            private.get_uid(1, HOST), lambda m: isinstance(m, UidResponse), timeout=0.2
        )
        time.sleep(0.35)  # timeout を超えて送信待ち
        tr.gate.set()
        assert isinstance(fut.result(2.0), UidResponse)
    finally:
        bus.stop()


def test_concurrent_requests_resolve_to_their_own_replies() -> None:
    bus, tr = make_bus(FakeMotors([1, 2, 3, 127]))
    # 応答を ID の逆順に遅延させて、到着順が要求順と逆になるようにする
    delays = {1: 0.06, 2: 0.03, 3: 0.0, 127: 0.045}
    tr.reply_delay = lambda req: delays[parse_id(req.can_id).dest]
    bus.start()
    try:
        futs = {
            mid: bus.request(
                private.get_uid(mid, HOST),
                lambda m, mid=mid: isinstance(m, UidResponse) and m.motor_id == mid,
                timeout=1.0,
            )
            for mid in (1, 2, 3, 127)
        }
        for mid, f in futs.items():
            r = f.result(2.0)
            assert isinstance(r, UidResponse) and r.motor_id == mid and r.uid == uid_of(mid)
    finally:
        bus.stop()


def test_concurrent_param_reads_same_motor_match_by_index(started) -> None:
    bus, _ = started
    idx = [PROFILE.param("run_mode").index, PROFILE.param("loc_kp").index]

    def matcher(i: int):
        return lambda m: isinstance(m, ParamResponse) and m.motor_id == 1 and m.index == i

    f0 = bus.request(private.read_param(1, HOST, idx[0]), matcher(idx[0]))
    f1 = bus.request(private.read_param(1, HOST, idx[1]), matcher(idx[1]))
    r0, r1 = f0.result(2.0), f1.result(2.0)
    assert isinstance(r0, ParamResponse) and r0.index == idx[0]
    assert isinstance(r1, ParamResponse) and r1.index == idx[1]


def test_reply_for_id_above_16_is_not_ignored(started) -> None:
    bus, _ = started
    seen: list[Message] = []
    bus.subscribe(seen.append)
    bus.request(frame_n(127), lambda m: isinstance(m, Feedback) and m.motor_id == 127).result(2.0)
    assert any(isinstance(m, Feedback) and m.motor_id == 127 for m in seen)


def test_unrelated_frames_do_not_resolve_pending() -> None:
    bus, tr = make_bus()
    bus.start()
    try:
        fut = bus.request(
            frame_n(2),
            lambda m: isinstance(m, Feedback) and m.motor_id == 2,
            timeout=0.3,
        )
        tr.wait_sent(1)
        tr.inject(feedback_frame(1))  # 別モータ
        tr.inject(feedback_frame(2, host=0x10))  # 別ホスト宛
        time.sleep(0.05)
        assert not fut.done()
        tr.inject(feedback_frame(2))
        assert fut.result(1.0).motor_id == 2  # type: ignore[union-attr]
    finally:
        bus.stop()


def test_reply_before_send_is_not_accepted() -> None:
    """送信前(キュー待ち)に届いた同種フレームで解決してはいけない。"""
    bus, tr = make_bus()
    tr.gate = threading.Event()
    bus.start()
    try:
        bus.send(frame_n(9))
        assert tr.entered.wait(2.0)
        fut = bus.request(frame_n(2), lambda m: isinstance(m, Feedback), timeout=0.5)
        tr.inject(feedback_frame(2))  # 要求はまだ送られていない
        time.sleep(0.1)
        assert not fut.done()
        tr.gate.set()
        assert tr.wait_sent(2)  # 要求が送られてから(= 受理可能になってから)応答を入れる
        tr.inject(feedback_frame(2))
        assert fut.result(1.0)
    finally:
        bus.stop()


def test_raw_match_is_checked_before_match_and_gets_version_reply() -> None:
    bus, tr = make_bus()
    bus.start()
    try:
        order: list[str] = []

        def raw(f: CanFrame) -> bool:
            order.append("raw")
            return f.data[:3] == bytes((0, 0xC4, 0x56))

        def match(m: Message) -> bool:
            order.append("match")
            return False

        fut = bus.request(private.read_version(1, HOST), match, 0.5, raw_match=raw)
        tr.wait_sent(1)
        tr.inject(version_frame(1))
        msg = fut.result(1.0)
        assert order[0] == "raw" and "match" not in order  # raw で確定したら match は呼ばない
        # バージョン応答は Feedback ではなく Unknown として扱われる(値が壊れているため)
        assert isinstance(msg, Unknown) and msg.reason == "version reply"
        assert msg.frame.data[3:7] == bytes((0x0A, 5, 0, 2))
    finally:
        bus.stop()


def test_version_reply_is_not_seen_as_feedback_by_subscribers(started) -> None:
    bus, tr = started
    seen: list[Message] = []
    bus.subscribe(seen.append)
    tr.inject(version_frame(1))
    tr.inject(feedback_frame(1, position=1.0))
    deadline = clock.now() + 2.0
    while len(seen) < 2 and clock.now() < deadline:
        time.sleep(0.005)
    assert isinstance(seen[0], Unknown) and seen[0].reason == "version reply"
    assert isinstance(seen[1], Feedback)


def test_matcher_exception_fails_only_that_request() -> None:
    bus, tr = make_bus()
    bus.start()
    try:
        bad = bus.request(frame_n(1), lambda m: 1 / 0, 0.5)
        tr.wait_sent(1)
        tr.inject(feedback_frame(1))
        with pytest.raises(ZeroDivisionError):
            bad.result(1.0)
        ok = bus.request(frame_n(1), lambda m: isinstance(m, Feedback), 0.5)
        tr.wait_sent(2)
        tr.inject(feedback_frame(1))
        assert ok.result(1.0)
    finally:
        bus.stop()


def test_cancelled_request_before_send_is_not_sent() -> None:
    bus, tr = make_bus()
    tr.gate = threading.Event()
    bus.start()
    try:
        bus.send(frame_n(9))
        assert tr.entered.wait(2.0)
        fut = bus.request(frame_n(2), lambda m: True, 0.5)
        assert fut.cancel()
        tr.gate.set()
        time.sleep(0.1)
        assert [i & 0xFF for i in tr.sent_ids()] == [9]
    finally:
        bus.stop()


def test_send_failure_fails_the_request_and_tx_continues() -> None:
    bus, tr = make_bus(FakeMotors([1]))
    tr.fail_sends = [TransportError("usb gone")]
    bus.start()
    try:
        fut = bus.request(private.get_uid(1, HOST), lambda m: True, 0.5)
        with pytest.raises(TransportError):
            fut.result(1.0)
        ok = bus.request(private.get_uid(1, HOST), lambda m: isinstance(m, UidResponse), 0.5)
        assert isinstance(ok.result(1.0), UidResponse)
    finally:
        bus.stop()


# ---------------------------------------------------------------------------
# RX: 購読者・耐障害
# ---------------------------------------------------------------------------
def test_subscriber_exception_does_not_break_rx() -> None:
    bus, tr = make_bus(FakeMotors([1]))
    errors: list[object] = []
    bus.events.subscribe(BUS_ERROR, errors.append)
    got: list[Message] = []

    def bad(_m: Message) -> None:
        raise RuntimeError("subscriber bug")

    bus.subscribe(bad)
    bus.subscribe(got.append)
    bus.start()
    try:
        for _ in range(3):
            tr.inject(feedback_frame(1))
        # 例外を出す購読者がいても、後続の購読者にも要求にも届く
        fut = bus.request(private.get_uid(1, HOST), lambda m: isinstance(m, UidResponse), 1.0)
        assert isinstance(fut.result(2.0), UidResponse)
        assert len([m for m in got if isinstance(m, Feedback)]) == 3
        assert errors and errors[0]["where"] == "subscriber"  # type: ignore[index]
    finally:
        bus.stop()


def test_unsubscribe(started) -> None:
    bus, tr = started
    got: list[Message] = []
    unsub = bus.subscribe(got.append)
    tr.inject(feedback_frame(1))
    deadline = clock.now() + 2.0
    while not got and clock.now() < deadline:
        time.sleep(0.005)
    unsub()
    n = len(got)
    tr.inject(feedback_frame(1))
    time.sleep(0.1)
    assert len(got) == n


def test_recv_exception_does_not_kill_rx_thread() -> None:
    bus, tr = make_bus()
    tr.fail_recvs = [TransportError("io"), RuntimeError("weird")]
    got: list[Message] = []
    bus.subscribe(got.append)
    bus.start()
    try:
        time.sleep(0.2)
        tr.inject(feedback_frame(1))
        deadline = clock.now() + 2.0
        while not got and clock.now() < deadline:
            time.sleep(0.005)
        assert got
    finally:
        bus.stop()


def test_garbage_frames_are_unknown_not_fatal(started) -> None:
    bus, tr = started
    got: list[Message] = []
    bus.subscribe(got.append)
    tr.inject(CanFrame(make_id(31, 0xABCD, HOST), b"\x01\x02"))  # 短い DLC
    tr.inject(CanFrame(0x123, bytes(8), extended=False))  # 標準フレーム
    tr.inject(CanFrame(make_id(CommType.FEEDBACK, 1, HOST), bytes(8)))
    deadline = clock.now() + 2.0
    while len(got) < 3 and clock.now() < deadline:
        time.sleep(0.005)
    assert isinstance(got[0], Unknown) and isinstance(got[1], Unknown)
    assert isinstance(got[2], Feedback)


# ---------------------------------------------------------------------------
# scan / stop / stats
# ---------------------------------------------------------------------------
def test_scan_finds_ids_including_127_sorted() -> None:
    bus, tr = make_bus(FakeMotors([5, 1, 127, 64, 2]))
    bus.start()
    try:
        res = bus.scan(gap_s=0.0, settle_s=0.1).result(5.0)
        assert [r.motor_id for r in res] == [1, 2, 5, 64, 127]
        assert res[-1].uid == uid_of(127)
        # 1..127 の全 ID に type 0 を送っている
        dests = {i & 0xFF for i in tr.sent_ids()}
        assert dests == set(range(1, 128))
    finally:
        bus.stop()


def test_scan_subset_and_progress_events() -> None:
    bus, _ = make_bus(FakeMotors([3, 100]))
    progress: list[object] = []
    bus.events.subscribe("bus.scan.progress", progress.append)
    bus.start()
    try:
        res = bus.scan(range(1, 10), gap_s=0.0, settle_s=0.05).result(5.0)
        assert [r.motor_id for r in res] == [3]  # 100 は範囲外
        assert progress and progress[-1] == {"done": 9, "total": 9}
    finally:
        bus.stop()


def test_scan_is_non_blocking_and_fails_if_bus_stops() -> None:
    bus, _ = make_bus()
    bus.start()
    t0 = clock.now()
    fut = bus.scan(gap_s=0.0, settle_s=5.0)
    assert clock.now() - t0 < 0.5
    bus.stop()
    with pytest.raises(TransportError):
        fut.result(3.0)


def test_stop_joins_threads_and_fails_pending() -> None:
    bus, _ = make_bus()
    bus.start()
    fut = bus.request(frame_n(1), lambda m: True, timeout=10.0)
    threads = [bus._rx_thread, bus._tx_thread]
    assert all(t is not None and t.is_alive() for t in threads)
    bus.stop()
    assert not any(t.is_alive() for t in threads if t is not None)
    with pytest.raises(TransportError):
        fut.result(1.0)
    bus.stop()  # 冪等
    assert not bus.running


def test_restart_after_stop() -> None:
    bus, tr = make_bus(FakeMotors([1]))
    bus.start()
    bus.stop()
    bus.start()
    try:
        assert isinstance(
            bus.request(private.get_uid(1, HOST), lambda m: isinstance(m, UidResponse)).result(2),
            UidResponse,
        )
    finally:
        bus.stop()


def test_stats_rates_queue_and_parse_errors() -> None:
    bus, tr = make_bus()
    bus.start()
    try:
        for _ in range(10):
            tr.inject(feedback_frame(1))
        for n in range(5):
            bus.send(frame_n(n))
        assert tr.wait_sent(5)
        deadline = clock.now() + 2.0
        while bus.stats().rx_fps < 10 and clock.now() < deadline:
            time.sleep(0.005)
        s = bus.stats()
        assert s.rx_fps >= 10 and s.tx_fps >= 5 and s.queue_len == 0
        assert s.timeouts == 0 and s.parse_errors == 0
        tr._stats.parse_errors = 4  # transport 側のカウンタを反映する
        assert bus.stats().parse_errors == 4
    finally:
        bus.stop()


def test_framelog_records_tx_and_rx(tmp_path) -> None:
    from edulite05.core.transport.framelog import FrameLogWriter, read_framelog

    path = tmp_path / "f.log"
    with FrameLogWriter(path) as log:
        bus, tr = make_bus(FakeMotors([1]), framelog=log)
        bus.start()
        try:
            bus.request(private.get_uid(1, HOST), lambda m: isinstance(m, UidResponse), 1.0).result(
                2.0
            )
        finally:
            bus.stop()
    frames = list(read_framelog(path))
    assert [f.direction.value for f in frames] == ["tx", "rx"]
