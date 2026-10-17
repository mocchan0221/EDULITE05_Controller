"""candump -L 互換フレームログのテスト。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from edulite05.core import clock
from edulite05.core.transport.base import CanFrame, Direction
from edulite05.core.transport.framelog import FrameLogWriter, read_framelog


def test_line_format_and_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "f.log"
    frames = [
        CanFrame(0x1200FD01, bytes.fromhex("0570000001000000"), timestamp=100.123456),
        CanFrame(0x0300FD01, bytes(8), timestamp=100.2, direction=Direction.TX),
        CanFrame(0x123, b"\x01", extended=False, timestamp=100.3, channel=1),
        CanFrame(0x028001FD, b"", timestamp=100.4),
    ]
    with FrameLogWriter(p, wall_offset=1_696_000_000.0) as w:
        for f in frames:
            w.write(f)
    lines = p.read_text(encoding="ascii").splitlines()
    assert lines[0] == "(1696000100.123456) at0 1200FD01#0570000001000000"
    assert lines[1] == "(1696000100.200000) at0 0300FD01#0000000000000000 T"
    assert lines[2] == "(1696000100.300000) at1 123#01"
    assert lines[3] == "(1696000100.400000) at0 028001FD#"
    out = list(read_framelog(p))
    assert len(out) == len(frames)
    for a, b in zip(frames, out, strict=True):
        assert (b.can_id, b.data, b.extended, b.channel, b.direction) == (
            a.can_id,
            a.data,
            a.extended,
            a.channel,
            a.direction,
        )
        assert b.timestamp == pytest.approx(a.timestamp + 1_696_000_000.0, abs=1e-6)


def test_reads_candump_line_and_skips_blank(tmp_path: Path) -> None:
    p = tmp_path / "c.log"
    p.write_text(
        "(1696000000.123456) can0 1200FD01#0570000001000000\n\n# comment\n"
        "(1696000000.5) at0 0300FD01#0000000000000000 T\n",
        encoding="ascii",
    )
    a, b = list(read_framelog(p))
    assert a.can_id == 0x1200FD01 and a.direction is Direction.RX and a.channel == 0
    assert b.direction is Direction.TX


def test_bad_line_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.log"
    p.write_text("garbage\n", encoding="ascii")
    with pytest.raises(ValueError):
        list(read_framelog(p))


def test_thread_safe_writes(tmp_path: Path) -> None:
    p = tmp_path / "t.log"
    w = FrameLogWriter(p)

    def work(base: int) -> None:
        for i in range(200):
            w.write(CanFrame(base + i, b"\x01\x02", timestamp=1.0 + i))

    ts = [threading.Thread(target=work, args=(k * 1000,)) for k in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    w.close()
    assert len(list(read_framelog(p))) == 800


def test_default_offset_writes_wall_clock_epoch(tmp_path: Path) -> None:
    """既定のオフセット(time.time() - clock.now())で epoch 秒が書かれる。"""
    p = tmp_path / "w.log"
    with FrameLogWriter(p) as w:
        w.write(CanFrame(0x1, b"", timestamp=clock.now()))
    (f,) = list(read_framelog(p))
    assert f.timestamp == pytest.approx(time.time(), abs=1.0)
