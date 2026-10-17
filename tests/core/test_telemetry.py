"""TelemetryHub / Recorder のテスト(phase2.md §1.6)。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pytest

from edulite05.core import clock
from edulite05.core.telemetry import (
    ChannelInfo,
    Recorder,
    TelemetryHub,
    register_motor_channels,
)


def test_register_and_channels() -> None:
    hub = TelemetryHub(capacity_s=1, max_rate_hz=10)
    hub.register("m1.pos", "rad", 1, "feedback", "pos")
    hub.register("m1.pos", "rad", 1, "feedback", "pos")  # 同一内容は冪等
    hub.register("x", "", None, "derived", "X")
    assert hub.channels() == [
        ChannelInfo("m1.pos", "rad", 1, "feedback", "pos"),
        ChannelInfo("x", "", None, "derived", "X"),
    ]
    with pytest.raises(ValueError):
        hub.register("m1.pos", "deg", 1, "feedback", "pos")
    with pytest.raises(KeyError):
        hub.append("nope", 0.0, 0.0)
    with pytest.raises(KeyError):
        hub.window(["nope"], 1.0)
    with pytest.raises(KeyError):
        hub.latest("nope")


def test_standard_channels() -> None:
    hub = TelemetryHub()
    names = register_motor_channels(hub, 3)
    assert "m3.pos" in names and "m3.cmd.cur" in names and len(names) == 12
    assert {c.motor_id for c in hub.channels()} == {3}
    assert hub.info("m3.cmd.tq").kind == "command"


def test_window_latest_basic() -> None:
    hub = TelemetryHub(capacity_s=10, max_rate_hz=100)
    hub.register("a", "u", None, "k", "A")
    assert hub.latest("a") is None
    t, v = hub.window(["a"], 5.0, until=100.0)["a"]
    assert len(t) == 0 and len(v) == 0
    for i in range(100):
        hub.append("a", 100.0 + i * 0.1, float(i))
    assert hub.latest("a") == (pytest.approx(109.9), 99.0)
    t, v = hub.window(["a"], 1.0, until=109.9)["a"]
    assert t[0] == pytest.approx(108.9) and t[-1] == pytest.approx(109.9)
    assert len(t) == 11 and v[-1] == 99.0
    # コピーである
    v[:] = -1
    assert hub.window(["a"], 1.0, until=109.9)["a"][1][-1] == 99.0
    # until 以前のみ
    t, _ = hub.window(["a"], 100.0, until=102.0)["a"]
    assert t[-1] <= 102.0 and len(t) == 21
    # until 省略 = clock.now()(古いサンプルは範囲外)
    assert len(hub.window(["a"], 1.0)["a"][0]) == 0
    now = clock.now()
    hub.append("a", now, 1.0)
    assert len(hub.window(["a"], 1.0)["a"][0]) == 1


def test_wraparound() -> None:
    hub = TelemetryHub(capacity_s=1, max_rate_hz=10)  # 10 サンプル
    assert hub.capacity == 10
    hub.register("a", "", None, "k", "A")
    for i in range(37):
        hub.append("a", float(i), float(i) * 2)
        t, v = hub.window(["a"], 1000.0, until=float(i))["a"]
        exp = np.arange(max(0, i - 9), i + 1, dtype=float)
        assert np.array_equal(t, exp) and np.array_equal(v, exp * 2)
    t, v = hub.window(["a"], 3.0, until=36.0)["a"]
    assert list(t) == [33.0, 34.0, 35.0, 36.0]
    assert hub.latest("a") == (36.0, 72.0)
    t, _ = hub.window(["a"], 1000.0, until=30.0)["a"]  # 古いものは失われている
    assert list(t) == [27.0, 28.0, 29.0, 30.0]
    hub.clear()
    assert hub.latest("a") is None and len(hub.window(["a"], 100.0, until=36.0)["a"][0]) == 0


def test_concurrent_writer_reader_no_torn_samples() -> None:
    hub = TelemetryHub(capacity_s=1, max_rate_hz=500)  # 小さくして頻繁に周回させる
    hub.register("a", "", None, "k", "A")
    hub.register("b", "", None, "k", "B")
    stop = threading.Event()
    n_written = 0

    def writer() -> None:
        nonlocal n_written
        i = 0
        while not stop.is_set():
            t = float(i)
            hub.append("a", t, t * 3 + 1)
            hub.append("b", t, -t)
            i += 1
        n_written = i

    errors: list[str] = []

    def reader() -> None:
        reads = 0
        while not stop.is_set():
            res = hub.window(["a", "b"], 1e9, until=1e18)
            for name, (t, v) in res.items():
                if len(t) != len(v):
                    errors.append(f"{name}: length mismatch")
                    return
                if len(t) == 0:
                    continue
                exp = t * 3 + 1 if name == "a" else -t
                if not np.array_equal(v, exp):
                    errors.append(f"{name}: torn sample")
                    return
                if len(t) > 1 and not np.all(np.diff(t) == 1.0):
                    errors.append(f"{name}: gap or disorder")
                    return
            last = hub.latest("a")
            if last is not None and last[1] != last[0] * 3 + 1:
                errors.append("latest torn")
                return
            reads += 1

    wt = threading.Thread(target=writer)
    rts = [threading.Thread(target=reader) for _ in range(2)]
    wt.start()
    for r in rts:
        r.start()
    time.sleep(0.6)
    stop.set()
    wt.join()
    for r in rts:
        r.join()
    assert errors == []
    assert n_written > hub.capacity  # 周回している


def test_recorder_csv(tmp_path: Path) -> None:
    hub = TelemetryHub(capacity_s=1, max_rate_hz=100)
    hub.register("m1.pos", "rad", 1, "feedback", "pos")
    hub.register("m1.vel", "rad/s", 1, "feedback", "vel")
    hub.register("m1.tq", "N·m", 1, "feedback", "tq")
    rec = Recorder(hub)
    path = tmp_path / "sub" / "rec.csv"
    assert rec.stop() == 0
    hub.append("m1.pos", clock.now(), 9.0)  # 開始前は記録されない
    rec.start(path, ["m1.pos", "m1.vel"], profile_name="EL05", tier="low", extra={"note": "a\nb"})
    assert rec.recording
    with pytest.raises(RuntimeError):
        rec.start(path, ["m1.pos"])
    t = clock.now()
    hub.append("m1.pos", t + 0.001, 1.5)
    hub.append("m1.tq", t + 0.002, 5.0)  # 選択外
    hub.append("m1.vel", t + 0.003, -2.0)
    hub.append("m1.pos", t + 0.004, 2.5)
    assert rec.stop() == 3
    assert not rec.recording and rec.error is None
    hub.append("m1.pos", t + 0.1, 7.0)  # 停止後は記録されない

    lines = path.read_text(encoding="utf-8").splitlines()
    meta = [ln for ln in lines if ln.startswith("# ")]
    data = [ln for ln in lines if not ln.startswith("# ")]
    assert "# profile: EL05" in meta and "# tier: low" in meta
    assert any(m.startswith("# started: ") for m in meta)
    assert any(m.startswith("# clock_t0: ") for m in meta)
    assert "# note: a b" in meta
    assert any(m.startswith("# channel: m1.vel unit=rad/s motor=1") for m in meta)
    assert data[0] == "time_s,m1.pos,m1.vel"
    rows = [r.split(",") for r in data[1:]]
    assert [r[1:] for r in rows] == [["1.5", ""], ["1.5", "-2"], ["2.5", "-2"]]
    times = [float(r[0]) for r in rows]
    assert times == sorted(times) and all(x >= 0 for x in times)


def test_recorder_unknown_channel(tmp_path: Path) -> None:
    rec = Recorder(TelemetryHub())
    with pytest.raises(KeyError):
        rec.start(tmp_path / "x.csv", ["nope"])
    with pytest.raises(ValueError):
        rec.start(tmp_path / "x.csv", [])
    assert not rec.recording
