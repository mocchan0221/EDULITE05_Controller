"""SimTransport(スレッド・遅延・欠落・レジストリ)。実時間で動くので判定は緩め。"""

import statistics
import threading
import time

import pytest

from edulite05.core import clock
from edulite05.core.errors import TransportError
from edulite05.core.protocol import private as pv
from edulite05.core.protocol.ids import parse_id
from edulite05.core.protocol.private import (
    FaultReport,
    Feedback,
    ParamResponse,
    UidResponse,
    decode,
)
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.transport import registry
from edulite05.core.transport.base import CanFrame, CanTransport, Direction
from edulite05.sim.transport import SimTransport, make_motors

H = 0xFD
M = 0x7F


@pytest.fixture
def make(profile):
    created: list[SimTransport] = []

    def _make(n=1, ids=None, **kw) -> SimTransport:
        t = SimTransport(make_motors(n, ids, profile), **kw)
        t.open()
        created.append(t)
        return t

    yield _make
    for t in created:
        t.close()


def drain(t: SimTransport, seconds: float) -> list[CanFrame]:
    out = []
    end = clock.now() + seconds
    while (left := end - clock.now()) > 0:
        f = t.recv(left)
        if f is not None:
            out.append(f)
    return out


# -- 基本 --------------------------------------------------------------------
def test_is_can_transport_and_caps(make):
    t = make()
    assert isinstance(t, CanTransport)
    assert t.is_open and t.caps.name == "sim" and t.caps.standard_frames
    t.close()
    assert not t.is_open
    t.close()  # 二重 close は無害


def test_send_when_closed_raises(profile):
    t = SimTransport(make_motors(1, None, profile))
    with pytest.raises(TransportError):
        t.send(pv.get_uid(M, H))


def test_recv_times_out_with_none(make):
    t = make()
    t0 = clock.now()
    assert t.recv(0.05) is None
    assert 0.04 <= clock.now() - t0 < 0.5


def test_uid_request_reply_and_metadata(make, ctx):
    t = make()
    t.send(pv.get_uid(M, H))
    f = t.recv(1.0)
    assert f is not None and f.direction is Direction.RX and f.timestamp > 0
    m = decode(f, ctx)
    assert isinstance(m, UidResponse) and m.motor_id == M
    s = t.stats()
    assert s.tx_frames == 1 and s.rx_frames == 1 and s.last_rx is not None


def test_scan_multiple_motors(make, ctx):
    t = make(3)
    assert [m.motor_id for m in t.motors] == [127, 1, 2]
    uids = {}
    for mid in range(1, 128):
        t.send(pv.get_uid(mid, H))
    for f in drain(t, 0.3):
        m = decode(f, ctx)
        assert isinstance(m, UidResponse)
        uids[m.motor_id] = m.uid
    assert sorted(uids) == [1, 2, 127]
    assert len(set(uids.values())) == 3
    assert uids[127].hex() == "40993bc000203006"


def test_custom_ids(make, ctx):
    t = make(ids=[5, 9])
    t.send(pv.get_uid(9, H))
    assert decode(t.recv(1.0), ctx).motor_id == 9
    t.send(pv.get_uid(127, H))
    assert t.recv(0.05) is None


def test_send_many_default_and_order(make, ctx):
    t = make()
    t.send_many([pv.read_param(M, H, 0x7005), pv.read_param(M, H, 0x7017)])
    got = [decode(t.recv(1.0), ctx).index for _ in range(2)]
    assert got == [0x7005, 0x7017]


def test_motor_id_change_is_routed(make, ctx):
    t = make()
    t.send(pv.set_can_id(M, H, 3))
    assert decode(t.recv(1.0), ctx).motor_id == 3
    t.send(pv.get_uid(M, H))
    assert t.recv(0.05) is None
    t.send(pv.get_uid(3, H))
    assert t.recv(1.0) is not None


# -- 物理スレッド ---------------------------------------------------------------
def test_physics_thread_runs_velocity_mode(make, ctx, profile):
    t = make()
    t.send(pv.write_param(M, H, 0x7005, profile.param("run_mode").dtype, int(RunMode.VELOCITY)))
    t.send(pv.enable(M, H))
    t.send(pv.write_param(M, H, 0x700A, profile.param("spd_ref").dtype, 5.0))
    time.sleep(1.0)  # acc_rad 10 rad/s² → 0.5 s で 5 rad/s
    drain(t, 0.05)
    t.send(pv.read_param(M, H, 0x701B))
    msgs = [decode(f, ctx) for f in drain(t, 0.1)]
    v = next(m.value for m in msgs if isinstance(m, ParamResponse))
    assert v == pytest.approx(5.0, abs=0.3)
    assert t.motor(M).enabled


def test_active_report_over_transport(make, ctx, profile):
    t = make()
    t.send(pv.set_active_report(M, H, True))
    frames = drain(t, 0.5)
    assert 25 <= len(frames) <= 60  # 10 ms 周期なら約 50(スレッド/OS のゆらぎを許容)
    assert all(parse_id(f.can_id).comm_type == 24 for f in frames)
    assert all(isinstance(decode(f, ctx), Feedback) for f in frames)
    ts = [f.timestamp for f in frames]
    gaps = [b - a for a, b in zip(ts, ts[1:], strict=False)]
    assert statistics.median(gaps) == pytest.approx(0.010, abs=0.004)
    t.send(pv.set_active_report(M, H, False))
    time.sleep(0.05)
    drain(t, 0.05)
    assert drain(t, 0.1) == []


def test_active_report_period_via_param(make, profile):
    t = make()
    t.send(pv.write_param(M, H, 0x7026, profile.param("EPScan_time").dtype, 5))  # 35 ms
    t.send(pv.set_active_report(M, H, True))
    frames = [f for f in drain(t, 0.6) if parse_id(f.can_id).comm_type == 24]
    assert 10 <= len(frames) <= 24  # 約 17


def test_fault_injection_via_transport(make, ctx):
    t = make()
    t.send(pv.enable(M, H))
    assert decode(t.recv(1.0), ctx).mode is MotorMode.MOTOR
    t.inject_fault(M, 3)  # 過電圧
    got = []
    for f in drain(t, 0.1):
        got.append(decode(f, ctx))
    reports = [m for m in got if isinstance(m, FaultReport)]
    assert len(reports) == 1 and reports[0].fault == 8
    assert not t.motor(M).enabled
    t.clear_faults(M)
    assert t.motor(M).faults == 0


# -- 遅延・欠落 -----------------------------------------------------------------
def test_latency_is_one_way_and_applied(make):
    t = make(latency_s=0.03)
    t0 = clock.now()
    t.send(pv.get_uid(M, H))
    assert t.recv(0.02) is None  # まだ往復していない
    f = t.recv(1.0)
    dt = clock.now() - t0
    assert f is not None and 0.055 <= dt < 0.5  # 往復 ≈ 2 × 30 ms


def test_zero_latency_replies_immediately(make):
    t = make(latency_s=0.0)
    t.send(pv.get_uid(M, H))
    assert t.recv(0.0) is not None  # send の戻りの時点で応答が入っている


def test_jitter_keeps_order(make, profile):
    t = make(latency_s=0.001, jitter_s=0.01, seed=1)
    for i in range(30):
        t.send(pv.read_param(M, H, 0x7005 if i % 2 == 0 else 0x7017))
    frames = drain(t, 0.5)
    assert len(frames) == 30
    assert [f.data[0] for f in frames] == [0x05, 0x17] * 15  # 到着順 = 送信順
    ts = [f.timestamp for f in frames]
    assert ts == sorted(ts)


def test_drop_next_tx_and_rx(make):
    t = make(latency_s=0.0)
    t.drop_next(tx=1)
    t.send(pv.get_uid(M, H))
    assert t.recv(0.05) is None
    t.send(pv.get_uid(M, H))
    assert t.recv(0.5) is not None
    t.drop_next(rx=1)
    t.send(pv.get_uid(M, H))
    assert t.recv(0.05) is None
    assert t.dropped_tx == 1 and t.dropped_rx == 1
    t.send(pv.get_uid(M, H))
    assert t.recv(0.5) is not None


def test_drop_rate_one_drops_everything_and_zero_drops_nothing(make):
    t = make(drop_rate=1.0)
    for _ in range(10):
        t.send(pv.get_uid(M, H))
    assert drain(t, 0.1) == []
    assert t.dropped_tx == 10
    t.drop_rate = 0.0
    t.send(pv.get_uid(M, H))
    assert t.recv(0.5) is not None


def test_drop_rate_is_statistical_and_seeded(make):
    t = make(drop_rate=0.5, seed=42, latency_s=0.0)
    n = 400
    for _ in range(n):
        t.send(pv.get_uid(M, H))
    got = len(drain(t, 0.05))
    # 往復で 2 回判定 → 生存率 ≈ 0.25
    assert 0.12 * n < got < 0.38 * n


def test_reply_drop_next_drops_only_replies_not_reports(make):
    t = make(latency_s=0.0)
    t.send(pv.set_active_report(M, H, True))  # ON は無応答、以後 10 ms ごとに type 24 の上報
    t.drop_next(reply=2)
    for _ in range(3):
        t.send(pv.get_uid(M, H))
    frames = drain(t, 0.2)
    uids = [f for f in frames if parse_id(f.can_id).comm_type == 0]
    reports = [f for f in frames if parse_id(f.can_id).comm_type == 24]
    assert len(uids) == 1 and t.dropped_replies == 2  # 応答は 3 件中 2 件が欠落
    assert len(reports) >= 8 and t.dropped_rx == 0  # 上報は落とさない


def test_reply_drop_rate_is_statistical_and_spares_reports(make):
    t = make(latency_s=0.0, reply_drop_rate=0.5, seed=7)
    n = 400
    for _ in range(n):
        t.send(pv.get_uid(M, H))
    got = len(drain(t, 0.05))
    assert 0.35 * n < got < 0.65 * n  # 往復ではなく応答側だけの判定 → 生存率 ≈ 0.5
    assert t.dropped_tx == 0 and t.dropped_replies == n - got
    t.reply_drop_rate = 1.0
    t.send(pv.set_active_report(M, H, True))
    assert len(drain(t, 0.1)) >= 5  # 上報は reply_drop_rate の対象外


# -- 同時アクセス ---------------------------------------------------------------
def test_concurrent_senders_and_receiver(make):
    t = make(latency_s=0.0)
    stop = threading.Event()
    received = []

    def rx():
        while not stop.is_set():
            f = t.recv(0.01)
            if f is not None:
                received.append(f)

    th = threading.Thread(target=rx)
    th.start()

    def tx():
        for _ in range(100):
            t.send(pv.get_uid(M, H))

    senders = [threading.Thread(target=tx) for _ in range(4)]
    for s in senders:
        s.start()
    for s in senders:
        s.join()
    time.sleep(0.2)
    stop.set()
    th.join()
    assert len(received) == 400
    assert t.stats().tx_frames == 400


# -- レジストリ -----------------------------------------------------------------
def test_registry_sim_is_available_with_options():
    infos = {i.name: i for i in registry.available()}
    assert "sim" in infos
    assert {"n", "ids", "latency_s", "drop_rate", "reply_drop_rate", "physics_hz"} <= set(
        infos["sim"].options_schema
    )


def test_registry_create_sim_with_options(ctx):
    t = registry.create("sim", n=2, latency_s=0.0, ids="10, 20")
    assert isinstance(t, SimTransport)
    assert [m.motor_id for m in t.motors] == [10, 20]
    t2 = registry.create("sim", n=3)
    assert [m.motor_id for m in t2.motors] == [127, 1, 2]
    t3 = registry.create("sim", report_comm_type=2)
    assert t3.motors[0].report_comm_type == 2
    t.open()
    try:
        t.send(pv.get_uid(20, H))
        assert decode(t.recv(1.0), ctx).motor_id == 20
    finally:
        t.close()


def test_reopen_after_close(make):
    t = make()
    t.close()
    t.open()
    t.send(pv.get_uid(M, H))
    assert t.recv(1.0) is not None
    t.close()
