"""PythonCanTransport のテスト(python-can の virtual interface を使う)。

python-can が無い環境(extra 未導入)では virtual を使うテストはスキップする。
未導入時の挙動のテストは python-can の有無に関わらず動く。
"""

from __future__ import annotations

import sys
import threading
import uuid
from collections.abc import Iterator

import pytest

from edulite05.core import clock
from edulite05.core.errors import TransportError
from edulite05.core.transport import registry
from edulite05.core.transport.base import CanFrame, Direction
from edulite05.core.transport.pycan import PythonCanTransport, from_message, to_message


@pytest.fixture
def can_mod():
    return pytest.importorskip("can")


@pytest.fixture
def channel() -> str:
    return f"edulite05-test-{uuid.uuid4().hex}"


@pytest.fixture
def pair(can_mod, channel: str) -> Iterator[tuple[PythonCanTransport, PythonCanTransport]]:
    a = PythonCanTransport("virtual", channel)
    b = PythonCanTransport("virtual", channel)
    a.open()
    b.open()
    try:
        yield a, b
    finally:
        a.close()
        b.close()


@pytest.fixture
def no_sim(monkeypatch: pytest.MonkeyPatch) -> None:
    """sim の遅延 import を止める(registry のテストを sim の状態から切り離す)。"""
    monkeypatch.setattr(registry, "_load_lazy", lambda only=None: None)


def test_extended_roundtrip(pair) -> None:
    a, b = pair
    f = CanFrame(0x1200FD01, bytes.fromhex("0570000001000000"), timestamp=1.0)
    t0 = clock.now()
    a.send(f)
    r = b.recv(1.0)
    t1 = clock.now()
    assert r is not None
    assert (r.can_id, r.data, r.extended, r.direction) == (
        0x1200FD01,
        f.data,
        True,
        Direction.RX,
    )
    assert t0 <= r.timestamp <= t1  # 受信時に core.clock.now() が付く(送信側の値ではない)
    assert a.stats().tx_frames == 1
    s = b.stats()
    assert s.rx_frames == 1 and s.last_rx == r.timestamp


def test_standard_frame_and_empty_data(pair) -> None:
    a, b = pair
    a.send(CanFrame(0x123, b"\x01\x02", extended=False))
    a.send(CanFrame(0x7FF, b"", extended=False))
    a.send(CanFrame(0x123, b"\xaa", extended=True))  # 同じ値でも拡張として区別される
    r1, r2, r3 = (b.recv(1.0) for _ in range(3))
    assert r1 is not None and (r1.can_id, r1.data, r1.extended) == (0x123, b"\x01\x02", False)
    assert r2 is not None and (r2.can_id, r2.data, r2.extended) == (0x7FF, b"", False)
    assert r3 is not None and (r3.can_id, r3.data, r3.extended) == (0x123, b"\xaa", True)


def test_send_many_keeps_order(pair) -> None:
    a, b = pair
    frames = [CanFrame(0x100 + i, bytes([i] * 8)) for i in range(20)]
    a.send_many(frames)
    got = [b.recv(1.0) for _ in frames]
    assert [g.can_id for g in got if g] == [f.can_id for f in frames]
    assert a.stats().tx_frames == 20 and b.stats().rx_frames == 20
    a.send_many([])  # 空は何もしない
    assert a.stats().tx_frames == 20


def test_recv_timeout_returns_none(pair) -> None:
    _, b = pair
    t0 = clock.now()
    assert b.recv(0.05) is None
    assert 0.03 <= clock.now() - t0 < 0.5


def test_mapping_roundtrip(can_mod) -> None:
    for f in (
        CanFrame(0x1FFFFFFF, bytes(range(8))),
        CanFrame(0, b""),
        CanFrame(0x7FF, b"\xff", extended=False),
    ):
        msg = to_message(can_mod, f)
        assert msg.is_extended_id == f.extended and msg.arbitration_id == f.can_id
        back = from_message(msg, 5.0)
        assert back is not None
        assert (back.can_id, back.data, back.extended, back.timestamp) == (
            f.can_id,
            f.data,
            f.extended,
            5.0,
        )


def test_out_of_range_rejected_without_sending(pair) -> None:
    a, b = pair
    with pytest.raises(ValueError):
        a.send_many([CanFrame(0x100, b"\x01"), CanFrame(0x800, b"", extended=False)])
    with pytest.raises(ValueError):
        a.send(CanFrame(0x20000000, b""))
    with pytest.raises(ValueError):
        a.send(CanFrame(0x1, bytes(9)))
    assert b.recv(0.05) is None  # バッチは何も送っていない
    assert a.stats().tx_frames == 0


def test_error_and_remote_frames_dropped(can_mod, channel: str) -> None:
    t = PythonCanTransport("virtual", channel)
    t.open()
    raw = can_mod.Bus(interface="virtual", channel=channel)
    try:
        raw.send(can_mod.Message(arbitration_id=0x1, is_remote_frame=True, is_extended_id=False))
        raw.send(can_mod.Message(arbitration_id=0x2, is_error_frame=True, is_extended_id=False))
        raw.send(can_mod.Message(arbitration_id=0x3, data=b"\x09", is_extended_id=True))
        r = t.recv(1.0)
        assert r is not None and r.can_id == 0x3
        s = t.stats()
        assert s.parse_errors == 2 and s.rx_frames == 1
    finally:
        raw.shutdown()
        t.close()


def test_pacing(can_mod, channel: str) -> None:
    a = PythonCanTransport("virtual", channel, min_frame_gap_s=0.01)
    b = PythonCanTransport("virtual", channel)
    a.open()
    b.open()
    try:
        assert a.caps.max_fps_hint == 100
        t0 = clock.now()
        a.send_many([CanFrame(0x10 + i, b"") for i in range(6)])
        dt = clock.now() - t0
        assert dt >= 5 * 0.01 * 0.95  # 6 フレームで 5 区間以上
        got = [b.recv(1.0) for _ in range(6)]
        assert all(g is not None for g in got)
    finally:
        a.close()
        b.close()


def test_default_has_no_pacing(pair) -> None:
    a, _ = pair
    assert a.min_frame_gap_s == 0.0
    t0 = clock.now()
    a.send_many([CanFrame(0x10, b"")] * 50)
    assert clock.now() - t0 < 0.5


def test_negative_gap_rejected() -> None:
    with pytest.raises(ValueError):
        PythonCanTransport("virtual", "x", min_frame_gap_s=-1)


def test_caps() -> None:
    t = PythonCanTransport("slcan", "COM5")  # open しないので python-can は不要
    assert t.caps.standard_frames is True
    assert t.caps.hw_timestamp is False
    assert t.caps.max_fps_hint > 0
    assert t.caps.name == "pycan:slcan"


def test_close_is_clean(can_mod, channel: str) -> None:
    t = PythonCanTransport("virtual", channel)
    t.close()  # 未 open でも例外なし
    t.open()
    t.open()  # 二重 open は無害
    assert t.is_open
    t.close()
    t.close()
    assert not t.is_open
    with pytest.raises(TransportError):
        t.send(CanFrame(1, b""))
    with pytest.raises(TransportError):
        t.recv(0.01)
    t.open()  # 再 open できる
    t.close()


def test_close_while_recv_waiting(can_mod, channel: str) -> None:
    t = PythonCanTransport("virtual", channel)
    t.open()
    result: list[object] = []

    def rx() -> None:
        try:
            result.append(t.recv(0.5))
        except TransportError as e:
            result.append(e)

    th = threading.Thread(target=rx)
    th.start()
    end = clock.now() + 0.05
    while clock.now() < end:
        pass
    t.close()
    th.join(3.0)
    assert not th.is_alive()
    assert result  # None か TransportError(どちらでもハングしない)


def test_open_failure_is_transport_error(can_mod) -> None:
    t = PythonCanTransport("no_such_interface", "x")
    with pytest.raises(TransportError) as ei:
        t.open()
    assert "no_such_interface" in str(ei.value)
    assert not t.is_open


def test_registry_create_and_describe(can_mod, channel: str, no_sim: None) -> None:
    info = {i.name: i for i in registry.available()}["pycan"]
    assert info.available and info.unavailable_reason == ""
    assert "interface" in info.options_schema and "channel" in info.options_schema
    assert "virtual" in info.options_schema["interface"]["choices"]
    a = registry.create("pycan", interface="virtual", channel=channel, bitrate="500000")
    b = registry.create("pycan", interface="virtual", channel=channel)
    a.open()
    b.open()
    try:
        a.send(CanFrame(0x55, b"\x01", extended=False))
        r = b.recv(1.0)
        assert r is not None and r.can_id == 0x55 and not r.extended
    finally:
        a.close()
        b.close()


# -- python-can 未導入(extra の有無に関わらず実行する)----------------------------------
def test_missing_dependency_open_raises_japanese_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "can", None)  # import can → ImportError
    t = PythonCanTransport("virtual", "x")
    with pytest.raises(TransportError) as ei:
        t.open()
    assert "python-can" in str(ei.value) and "未導入" in str(ei.value)
    assert not t.is_open


def test_missing_dependency_marked_unavailable(
    monkeypatch: pytest.MonkeyPatch, no_sim: None
) -> None:
    monkeypatch.setitem(sys.modules, "can", None)
    infos = {i.name: i for i in registry.available()}
    info = infos["pycan"]
    assert info.available is False
    assert "python-can" in info.unavailable_reason
    assert info.options_schema  # フォームの定義は返す
    # 他のトランスポートは影響を受けない
    assert infos["robstride_at"].available is True
    assert infos["replay"].available is True
