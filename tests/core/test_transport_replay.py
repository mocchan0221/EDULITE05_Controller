"""ReplayTransport(フレームログ再生)のテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from edulite05.core import clock
from edulite05.core.errors import TransportError
from edulite05.core.transport import registry
from edulite05.core.transport.base import CanFrame, Direction
from edulite05.core.transport.framelog import FrameLogWriter
from edulite05.core.transport.replay import ReplayTransport

WALL = 1_696_000_000.0


def _write_log(path: Path, spec: list[tuple[float, int, Direction]]) -> None:
    """(相対秒, can_id, 方向) のログを作る。データには連番を入れる。"""
    with FrameLogWriter(path, wall_offset=WALL) as w:
        for i, (t, cid, d) in enumerate(spec):
            w.write(CanFrame(cid, bytes([i]), timestamp=100.0 + t, direction=d))


@pytest.fixture
def log(tmp_path: Path) -> Path:
    p = tmp_path / "r.log"
    rx, tx = Direction.RX, Direction.TX
    _write_log(
        p,
        [
            (0.00, 0x11, rx),
            (0.02, 0x99, tx),  # TX は再生されない
            (0.05, 0x12, rx),
            (0.10, 0x13, rx),
            (0.20, 0x14, rx),
        ],
    )
    return p


def _drain(t: ReplayTransport, n: int, timeout: float = 3.0) -> list[CanFrame]:
    out: list[CanFrame] = []
    end = clock.now() + timeout
    while len(out) < n and clock.now() < end:
        f = t.recv(0.5)
        if f is not None:
            out.append(f)
    return out


@pytest.fixture
def no_sim(monkeypatch: pytest.MonkeyPatch) -> None:
    """sim の遅延 import を止める(registry のテストを sim の状態から切り離す)。"""
    monkeypatch.setattr(registry, "_load_lazy", lambda only=None: None)


def test_rx_only_with_original_intervals(log: Path) -> None:
    t = ReplayTransport(log)
    t.open()
    try:
        got = _drain(t, 4)
        assert [f.can_id for f in got] == [0x11, 0x12, 0x13, 0x14]
        assert all(f.direction is Direction.RX for f in got)
        dts = [b.timestamp - a.timestamp for a, b in zip(got, got[1:], strict=False)]
        for dt, want in zip(dts, (0.05, 0.05, 0.10), strict=True):
            assert dt == pytest.approx(want, abs=0.03)
        assert got[0].timestamp <= clock.now()  # ログの壁時計ではなく clock.now() 基準
    finally:
        t.close()


def test_speed_10(log: Path) -> None:
    t = ReplayTransport(log, speed=10.0)
    t.open()
    try:
        t0 = clock.now()
        got = _drain(t, 4)
        total = clock.now() - t0
        assert len(got) == 4
        assert 0.01 <= got[-1].timestamp - got[0].timestamp <= 0.08  # 0.2 s / 10
        assert total < 0.2
    finally:
        t.close()


def test_speed_scales_between_1_and_10(log: Path) -> None:
    spans = {}
    for sp in (1.0, 10.0):
        t = ReplayTransport(log, speed=sp)
        t.open()
        got = _drain(t, 4)
        t.close()
        spans[sp] = got[-1].timestamp - got[0].timestamp
    assert spans[1.0] / spans[10.0] == pytest.approx(10.0, rel=0.5)


def test_bad_speed_rejected(log: Path) -> None:
    for sp in (0, -1.0, float("nan")):
        with pytest.raises(ValueError):
            ReplayTransport(log, speed=sp)


def test_end_of_log_and_progress(log: Path) -> None:
    t = ReplayTransport(log, speed=20.0)
    t.open()
    try:
        s0 = t.stats()
        assert (s0.total, s0.position, s0.finished, s0.progress) == (4, 0, False, 0.0)
        got = _drain(t, 4)
        assert len(got) == 4
        s = t.stats()
        assert s.finished and s.position == 4 and s.progress == 1.0
        assert s.rx_frames == 4 and s.last_rx == got[-1].timestamp
        # 終端後は None(timeout は待つ = アイドルなバスと同じ)
        t0 = clock.now()
        assert t.recv(0.05) is None
        assert clock.now() - t0 >= 0.04
        assert t.recv(0.0) is None
        assert t.stats().loops == 0
    finally:
        t.close()


def test_progress_midway(log: Path) -> None:
    t = ReplayTransport(log)
    t.open()
    try:
        _drain(t, 2)
        s = t.stats()
        assert s.position == 2 and not s.finished and s.progress == pytest.approx(0.5)
    finally:
        t.close()


def test_loop(log: Path) -> None:
    t = ReplayTransport(log, speed=20.0, loop=True)
    t.open()
    try:
        got = _drain(t, 9)
        assert [f.can_id for f in got] == [0x11, 0x12, 0x13, 0x14] * 2 + [0x11]
        assert all(b.timestamp >= a.timestamp for a, b in zip(got, got[1:], strict=False))
        s = t.stats()
        assert s.loops == 2 and not s.finished
    finally:
        t.close()


def test_loop_seam_interval(tmp_path: Path) -> None:
    """周回のつなぎ目は 0 ではなく、ログの平均フレーム間隔(ここでは 0.1 s)。"""
    p = tmp_path / "seam.log"
    _write_log(p, [(0.0, 0x21, Direction.RX), (0.1, 0x22, Direction.RX)])
    t = ReplayTransport(p, loop=True)
    t.open()
    try:
        got = _drain(t, 3)
        assert [f.can_id for f in got] == [0x21, 0x22, 0x21]
        assert got[2].timestamp - got[1].timestamp == pytest.approx(0.1, abs=0.04)
    finally:
        t.close()


def test_tx_discarded(log: Path) -> None:
    t = ReplayTransport(log)
    with pytest.raises(TransportError):
        t.send(CanFrame(1, b""))  # 未 open
    t.open()
    try:
        t.send(CanFrame(0x1, b"\x01"))
        t.send_many([CanFrame(0x2, b""), CanFrame(0x3, b"")])
        assert t.stats().tx_frames == 3
        assert t.stats().rx_frames == 0  # 受信側へは回り込まない
    finally:
        t.close()


def test_read_only_caps(log: Path) -> None:
    t = ReplayTransport(log)
    assert t.caps.read_only is True
    assert t.caps.name == "replay"
    assert t.caps.standard_frames is True


def test_standard_and_extended_frames(tmp_path: Path) -> None:
    p = tmp_path / "s.log"
    p.write_text(
        "(1696000000.000000) can0 123#0102\n(1696000000.010000) can0 1200FD01#05\n",
        encoding="ascii",
    )
    t = ReplayTransport(p, speed=10.0)
    t.open()
    try:
        a, b = _drain(t, 2)
        assert (a.can_id, a.extended, a.data) == (0x123, False, b"\x01\x02")
        assert (b.can_id, b.extended) == (0x1200FD01, True)
    finally:
        t.close()


def test_open_errors(tmp_path: Path) -> None:
    with pytest.raises(TransportError):
        ReplayTransport(tmp_path / "nope.log").open()
    bad = tmp_path / "bad.log"
    bad.write_text("garbage\n", encoding="ascii")
    with pytest.raises(TransportError):
        ReplayTransport(bad).open()
    only_tx = tmp_path / "tx.log"
    _write_log(only_tx, [(0.0, 0x1, Direction.TX)])
    with pytest.raises(TransportError):
        ReplayTransport(only_tx).open()


def test_close_and_reopen(log: Path) -> None:
    t = ReplayTransport(log, speed=50.0)
    t.close()  # 未 open でも例外なし
    t.open()
    assert t.is_open
    assert len(_drain(t, 4)) == 4
    t.close()
    t.close()
    assert not t.is_open
    with pytest.raises(TransportError):
        t.recv(0.01)
    t.open()  # 先頭から再生し直す
    got = _drain(t, 1)
    assert got and got[0].can_id == 0x11
    assert t.stats().finished is False
    t.close()


def test_registry(log: Path, no_sim: None) -> None:
    info = {i.name: i for i in registry.available()}["replay"]
    assert info.available
    assert info.options_schema["path"]["required"] is True
    assert info.options_schema["speed"]["default"] == 1.0
    assert "loop" in info.options_schema
    t = registry.create("replay", path=str(log), speed="50", loop=False)
    assert isinstance(t, ReplayTransport)
    t.open()
    try:
        assert len(_drain(t, 4)) == 4
    finally:
        t.close()
