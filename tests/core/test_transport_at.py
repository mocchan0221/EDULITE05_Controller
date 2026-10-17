"""AT フレームのエンコード/パーサ/トランスポートのテスト(can_transport.md §2.4)。"""

from __future__ import annotations

import time

import pytest

from edulite05.core import clock
from edulite05.core.errors import TransportError
from edulite05.core.transport import registry
from edulite05.core.transport.at_serial import (
    AtFrameParser,
    AtSerialTransport,
    PortInfo,
    encode_at,
    list_ports,
)
from edulite05.core.transport.base import CanFrame, Direction

H = bytes.fromhex

ZEROS = "00 00 00 00 00 00 00 00"

# (AT バイト列, CAN ID, data)
EXAMPLES = [
    # 例 1: type 18 write run_mode=1(ベンダ例)
    ("41 54 90 07 E8 0C 08 05 70 00 00 01 00 00 00 0D 0A", 0x1200FD01, "05 70 00 00 01 00 00 00"),
    # 例 2: enable → モータ 0x7F / モータ 1
    ("41 54 18 07 EB FC 08 00 00 00 00 00 00 00 00 0D 0A", 0x0300FD7F, ZEROS),
    ("41 54 18 07 E8 0C 08 00 00 00 00 00 00 00 00 0D 0A", 0x0300FD01, ZEROS),
    # 例 3: フィードバック type 2
    ("41 54 14 00 0F EC 08 80 00 7F FF 80 00 01 18 0D 0A", 0x028001FD, "80 00 7F FF 80 00 01 18"),
    # 例 4: MIT type 1
    ("41 54 0B FF F8 0C 08 7F FF 7F FF 05 1E 19 99 0D 0A", 0x017FFF01, "7F FF 7F FF 05 1E 19 99"),
]


@pytest.mark.parametrize(("at", "can_id", "data"), EXAMPLES)
def test_encode_examples(at: str, can_id: int, data: str) -> None:
    assert encode_at(CanFrame(can_id, H(data))) == H(at)


@pytest.mark.parametrize(("at", "can_id", "data"), EXAMPLES)
def test_decode_examples(at: str, can_id: int, data: str) -> None:
    (f,) = AtFrameParser().feed(H(at), 1.5)
    assert f.can_id == can_id
    assert f.data == H(data)
    assert f.extended is True
    assert f.timestamp == 1.5
    assert f.direction is Direction.RX


def test_encode_dlc_variants() -> None:
    assert encode_at(CanFrame(0x1200FD01, b"")) == H("41 54 90 07 E8 0C 00 0D 0A")
    assert len(encode_at(CanFrame(1, b"\x01\x02\x03"))) == 12
    with pytest.raises(ValueError):
        encode_at(CanFrame(1, bytes(9)))
    with pytest.raises(ValueError):
        encode_at(CanFrame(1 << 29, b""))


def test_standard_frame_roundtrip() -> None:
    f = CanFrame(0x123, b"\xaa\xbb", extended=False)
    raw = encode_at(f)
    assert raw[2:6] == (0x123 << 21).to_bytes(4, "big")
    (g,) = AtFrameParser().feed(raw)
    assert (g.can_id, g.data, g.extended) == (0x123, b"\xaa\xbb", False)


STREAM = b"".join(H(a) for a, _, _ in EXAMPLES)


def _expected() -> list[tuple[int, bytes]]:
    return [(cid, H(d)) for _, cid, d in EXAMPLES]


def _as_tuples(frames: list[CanFrame]) -> list[tuple[int, bytes]]:
    return [(f.can_id, f.data) for f in frames]


def test_stream_in_one_chunk() -> None:
    p = AtFrameParser()
    assert _as_tuples(p.feed(STREAM)) == _expected()
    assert (p.parse_errors, p.resyncs, p.pending) == (0, 0, 0)


@pytest.mark.parametrize("split", range(1, len(STREAM)))
def test_chunked_at_every_position(split: int) -> None:
    p = AtFrameParser()
    out = p.feed(STREAM[:split]) + p.feed(STREAM[split:])
    assert _as_tuples(out) == _expected()
    assert p.parse_errors == 0
    assert p.pending == 0


def test_byte_by_byte() -> None:
    p = AtFrameParser()
    out: list[CanFrame] = []
    for b in STREAM:
        out += p.feed(bytes([b]))
    assert _as_tuples(out) == _expected()
    assert (p.parse_errors, p.resyncs) == (0, 0)


def test_garbage_before_and_between() -> None:
    a, b = H(EXAMPLES[0][0]), H(EXAMPLES[3][0])
    p = AtFrameParser()
    out = p.feed(b"\xff\x00xyz" + a + b"\x00\x01\x02" + b + b"junk")
    assert _as_tuples(out) == [
        (EXAMPLES[0][1], H(EXAMPLES[0][2])),
        (EXAMPLES[3][1], H(EXAMPLES[3][2])),
    ]
    assert p.resyncs >= 2


def test_data_containing_at_and_crlf() -> None:
    # データ中の 41 54 / 0D 0A はフレーム境界として扱わない
    f1 = CanFrame(0x1200FD01, H("41 54 0D 0A 41 54 08 00"))
    f2 = CanFrame(0x0300FD01, H("0D 0A 0D 0A 41 54 00 00"))
    f3 = CanFrame(0x0300FD02, H("41 54"))
    raw = encode_at(f1) + encode_at(f2) + encode_at(f3)
    for split in range(1, len(raw)):
        p = AtFrameParser()
        out = p.feed(raw[:split]) + p.feed(raw[split:])
        assert [(f.can_id, f.data) for f in out] == [
            (f1.can_id, f1.data),
            (f2.can_id, f2.data),
            (f3.can_id, f3.data),
        ]
        assert p.parse_errors == 0


def test_bad_dlc_resync() -> None:
    good = H(EXAMPLES[0][0])
    bad = H("41 54 90 07 E8 0C 09 01 02 03")  # DLC=9
    p = AtFrameParser()
    out = p.feed(bad + good)
    assert _as_tuples(out) == [(EXAMPLES[0][1], H(EXAMPLES[0][2]))]
    assert p.parse_errors == 1
    assert p.resyncs >= 1


def test_bad_terminator_resync() -> None:
    good = H(EXAMPLES[0][0])
    bad = bytearray(H(EXAMPLES[3][0]))
    bad[-1] = 0x00  # 0D 00
    p = AtFrameParser()
    out = p.feed(bytes(bad) + good)
    assert _as_tuples(out) == [(EXAMPLES[0][1], H(EXAMPLES[0][2]))]
    assert p.parse_errors == 1
    assert p.resyncs >= 1


def test_truncated_frame_then_new_frame() -> None:
    # 途中で切れたフレームの直後に完全なフレーム: 切れた方は捨て、後続は取り出す
    good = H(EXAMPLES[0][0])
    p = AtFrameParser()
    out = p.feed(good[:10] + good + good)
    assert len(out) >= 1
    assert out[-1].can_id == EXAMPLES[0][1]
    assert p.parse_errors >= 1


# --- Transport(偽シリアル) --------------------------------------------------


class FakeSerial:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        self.rx = bytearray()
        self.written: list[bytes] = []
        self.write_times: list[float] = []
        self.is_open = True
        self.input_reset = 0
        self.fail_reads = 0
        self.fail_write = False

    @property
    def in_waiting(self) -> int:
        return len(self.rx)

    def reset_input_buffer(self) -> None:
        self.input_reset += 1
        self.rx.clear()

    def read(self, n: int = 1) -> bytes:
        if self.fail_reads > 0:
            self.fail_reads -= 1
            raise OSError("ClearCommError: readiness to read but returned no data")
        out = bytes(self.rx[:n])
        del self.rx[:n]
        if not out:
            time.sleep(0.001)
        return out

    def write(self, data: bytes) -> int:
        if self.fail_write:
            raise OSError("write timeout")
        self.write_times.append(clock.now())
        self.written.append(bytes(data))
        return len(data)

    def close(self) -> None:
        self.is_open = False


_OPENED: list[AtSerialTransport] = []


@pytest.fixture(autouse=True)
def _close_opened_transports():
    """`make()` で開いたトランスポートを、テストが close し忘れても閉じる(受信スレッドを残さない)。"""
    yield
    while _OPENED:
        _OPENED.pop().close()  # close は 2 回呼んでもよい


def make(port: str = "COMX", **kwargs: object) -> tuple[AtSerialTransport, list[FakeSerial]]:
    made: list[FakeSerial] = []

    def factory(*a: object, **k: object) -> FakeSerial:
        s = FakeSerial(*a, **k)
        made.append(s)
        return s

    t = AtSerialTransport(port, serial_factory=factory, **kwargs)  # type: ignore[arg-type]
    t.open()
    _OPENED.append(t)
    return t, made


def test_open_parameters_and_reset() -> None:
    t, made = make("COM9")
    s = made[0]
    assert s.args == ("COM9", 921600)
    assert s.kwargs == {"timeout": 0.005, "write_timeout": 0.2, "exclusive": True}
    assert s.input_reset == 1
    assert s.written == []  # AT 設定コマンドは送らない
    assert t.is_open
    t.close()
    assert not t.is_open


def test_open_failure_is_transport_error() -> None:
    def boom(*a: object, **k: object) -> FakeSerial:
        raise OSError("nope")

    with pytest.raises(TransportError):
        AtSerialTransport("COMX", serial_factory=boom).open()


def test_send_and_send_many_one_write_per_frame() -> None:
    t, made = make(min_frame_gap_s=0)
    s = made[0]
    f1 = CanFrame(0x1200FD01, H("05 70 00 00 01 00 00 00"))
    f2 = CanFrame(0x0300FD7F, bytes(8))
    t.send(f1)
    assert s.written == [H(EXAMPLES[0][0])]
    t.send_many([f1, f2, f1])
    # 連結しない(ペーシングのためフレームごとに write)
    assert s.written[1:] == [encode_at(f1), encode_at(f2), encode_at(f1)]
    t.send_many([])
    assert len(s.written) == 4
    assert t.stats().tx_frames == 4


GAP_TOL_S = 0.0002  # 判定の許容(クロック分解能・スレッド遅延は gap を伸ばす方向にしか働かない)


def _gaps(times: list[float]) -> list[float]:
    return [b - a for a, b in zip(times, times[1:], strict=False)]


def test_default_gap_is_1ms_and_caps() -> None:
    t, _ = make()
    assert t.min_frame_gap_s == 0.001
    assert t.caps.max_fps_hint == 1000
    t0, _ = make(min_frame_gap_s=0)
    assert t0.caps.max_fps_hint == AtSerialTransport.caps.max_fps_hint
    t2, _ = make(min_frame_gap_s=0.002)
    assert t2.caps.max_fps_hint == 500
    with pytest.raises(ValueError):
        AtSerialTransport("COMX", min_frame_gap_s=-0.001)


def test_send_is_paced() -> None:
    t, made = make(min_frame_gap_s=0.001)
    f = CanFrame(0x1200FD01, bytes(8))
    for _ in range(20):
        t.send(f)  # 連続呼び出し(間に待ちを入れない)
    gaps = _gaps(made[0].write_times)
    assert len(gaps) == 19
    assert min(gaps) >= 0.001 - GAP_TOL_S, min(gaps)
    t.close()


def test_send_many_is_paced_and_not_concatenated() -> None:
    t, made = make(min_frame_gap_s=0.002)
    frames = [CanFrame(0x1200FD01 + i, bytes(8)) for i in range(8)]
    t0 = clock.now()
    t.send_many(frames)
    elapsed = clock.now() - t0
    s = made[0]
    assert s.written == [encode_at(f) for f in frames]  # 順序どおり・1 フレーム 1 write
    assert min(_gaps(s.write_times)) >= 0.002 - GAP_TOL_S
    assert elapsed >= 7 * 0.002 - GAP_TOL_S  # N フレームで約 (N-1) × gap
    # send_many と send の間もペーシング
    t.send(frames[0])
    assert s.write_times[-1] - s.write_times[-2] >= 0.002 - GAP_TOL_S
    assert t.stats().tx_frames == 9
    t.close()


def test_pacing_is_thread_safe() -> None:
    import threading

    t, made = make(min_frame_gap_s=0.001)
    f = CanFrame(0x1200FD01, bytes(8))

    def worker() -> None:
        for _ in range(10):
            t.send(f)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    times = made[0].write_times
    assert len(times) == 40
    assert min(_gaps(times)) >= 0.001 - GAP_TOL_S
    t.close()


def test_gap_zero_does_not_wait() -> None:
    t, made = make(min_frame_gap_s=0)
    f = CanFrame(0x1200FD01, bytes(8))
    t0 = clock.now()
    for _ in range(200):
        t.send(f)
    assert clock.now() - t0 < 0.1  # 200 フレームが 1 ms 間隔なら 0.2 s かかる
    assert len(made[0].written) == 200
    t.close()


def test_send_many_write_error_counts_sent_frames() -> None:
    t, made = make(min_frame_gap_s=0)
    s = made[0]
    orig = s.write
    calls = 0

    def flaky(data: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("write timeout")
        return orig(data)

    s.write = flaky  # type: ignore[method-assign]
    with pytest.raises(TransportError):
        t.send_many([CanFrame(1, b"")] * 5)
    assert t.stats().tx_frames == 2 and t.stats().io_errors == 1
    t.close()


def test_send_errors() -> None:
    t, made = make()
    made[0].fail_write = True
    with pytest.raises(TransportError):
        t.send(CanFrame(1, b""))
    assert t.stats().io_errors == 1
    t.close()
    with pytest.raises(TransportError):
        t.send(CanFrame(1, b""))


def test_recv_frames_and_stats() -> None:
    t, made = make()
    s = made[0]
    t0 = clock.now()  # 読取りスレッドが即座に読むので、データを入れる前に取る
    s.rx += b"\x00\x11" + STREAM
    got = []
    while (f := t.recv(0.02)) is not None:
        got.append(f)
    assert _as_tuples(got) == _expected()
    assert all(f.direction is Direction.RX and f.timestamp >= t0 for f in got)
    st = t.stats()
    assert st.rx_frames == len(EXAMPLES)
    assert st.last_rx is not None
    assert st.resyncs >= 1  # 先頭のゴミ
    st.rx_frames = 999  # stats はコピー
    assert t.stats().rx_frames == len(EXAMPLES)


def test_recv_partial_frame_waits_for_rest() -> None:
    t, made = make()
    s = made[0]
    raw = H(EXAMPLES[0][0])
    s.rx += raw[:10]
    assert t.recv(0.02) is None
    s.rx += raw[10:]
    f = t.recv(0.2)
    assert f is not None and f.can_id == 0x1200FD01


def test_recv_timeout_returns_none() -> None:
    t, _ = make()
    t0 = clock.now()
    assert t.recv(0.03) is None
    assert clock.now() - t0 >= 0.03


def test_recv_transient_read_errors_are_retried() -> None:
    t, made = make()
    s = made[0]
    s.rx += H(EXAMPLES[0][0])
    s.fail_reads = 3
    f = t.recv(0.5)
    assert f is not None and f.can_id == 0x1200FD01
    assert t.stats().io_errors == 3


def test_recv_persistent_error_raises() -> None:
    t, made = make()
    made[0].fail_reads = 10_000
    with pytest.raises(TransportError):
        t.recv(5.0)


def test_bad_bytes_counted_in_transport_stats() -> None:
    t, made = make()
    made[0].rx += H("41 54 90 07 E8 0C 09 01 02 03") + H(EXAMPLES[0][0])
    f = t.recv(0.2)
    assert f is not None
    st = t.stats()
    assert st.parse_errors == 1 and st.resyncs >= 1


def test_list_ports_prefers_ch340(monkeypatch: pytest.MonkeyPatch) -> None:
    class P:
        def __init__(self, device: str, desc: str, vid: int | None, pid: int | None) -> None:
            self.device, self.description, self.vid, self.pid = device, desc, vid, pid

    from serial.tools import list_ports as lp

    monkeypatch.setattr(
        lp,
        "comports",
        lambda: [
            P("COM1", "x", None, None),
            P("COM7", "CH340", 0x1A86, 0x7523),
            P("COM3", "y", 1, 2),
        ],
    )
    ports = list_ports()
    assert [p.device for p in ports] == ["COM7", "COM1", "COM3"]
    assert ports[0] == PortInfo("COM7", "CH340", 0x1A86, 0x7523, True)
    assert not ports[1].preferred


def test_registry_robstride_at() -> None:
    assert "robstride_at" in [i.name for i in registry.available()]
    t = registry.create("robstride_at", port="COM7", serial_factory=FakeSerial)
    assert isinstance(t, AtSerialTransport) and t.port == "COM7"
    with pytest.raises(KeyError):
        registry.create("nonexistent")
    info = next(i for i in registry.available() if i.name == "robstride_at")
    assert "port" in info.options_schema
    assert info.options_schema["min_frame_gap_s"]["default"] == 0.001
    assert t.min_frame_gap_s == 0.001
    t2 = registry.create(
        "robstride_at", port="COM7", serial_factory=FakeSerial, min_frame_gap_s="0"
    )
    assert isinstance(t2, AtSerialTransport) and t2.min_frame_gap_s == 0.0


def test_registry_register_custom() -> None:
    registry.register(
        "dummy_x",
        lambda **kw: AtSerialTransport("D", **kw),
        registry.TransportInfo("", "ダミー"),
    )
    try:
        assert any(i.name == "dummy_x" and i.label == "ダミー" for i in registry.available())
        assert isinstance(registry.create("dummy_x"), AtSerialTransport)
    finally:
        registry._entries.pop("dummy_x", None)
