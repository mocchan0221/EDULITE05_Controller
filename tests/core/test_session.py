"""Session のテスト(phase1.md §1.9)。FakeTransport 上で動かす。"""

from __future__ import annotations

import atexit

import pytest
from fakes import HOST, FakeMotors, FakeTransport, uid_of, wait_until

from edulite05.core.bus import Priority
from edulite05.core.device.motor import Motor
from edulite05.core.device.profile import MotorProfile
from edulite05.core.errors import EduliteError
from edulite05.core.events import CONNECTION_CHANGED, MOTOR_DISCOVERED
from edulite05.core.protocol import private
from edulite05.core.protocol.ids import CommType, parse_id
from edulite05.core.session import Session

PROFILE = MotorProfile.load_builtin("el05")


def make_session(ids=(1, 2, 127), **kw) -> tuple[Session, FakeTransport]:
    tr = FakeTransport(FakeMotors(ids, PROFILE))
    return Session(tr, PROFILE, HOST, **kw), tr


def stop_dests(tr: FakeTransport) -> list[int]:
    return [f.can_id & 0xFF for f in tr.sent if parse_id(f.can_id).comm_type == CommType.STOP]


def test_open_close_lifecycle() -> None:
    s, tr = make_session()
    events: list[object] = []
    s.events.subscribe(CONNECTION_CHANGED, events.append)
    assert not tr.is_open
    s.open()
    assert tr.is_open and s.bus.running
    s.close()
    assert not tr.is_open and not s.bus.running
    assert events == [{"connected": True}, {"connected": False}]
    s.close()  # 冪等
    with pytest.raises(EduliteError):
        s.open()  # 1 回限り


def test_scan_registers_motors_including_id_127() -> None:
    s, _ = make_session(ids=(3, 127, 64))
    discovered: list[object] = []
    s.events.subscribe(MOTOR_DISCOVERED, discovered.append)
    with s:
        motors = s.scan(gap_s=0.0, settle_s=0.1).result(5.0)
        assert [m.motor_id for m in motors] == [3, 64, 127]
        assert all(isinstance(m, Motor) for m in motors)
        assert set(s.motors) == {3, 64, 127}
        assert s.motors[127].uid == uid_of(127)
        assert [d["motor_id"] for d in discovered] == [3, 64, 127]  # type: ignore[index]
        # 再スキャンしても同じ Motor を再利用し、discovered は再通知しない
        again = s.scan(range(1, 10), gap_s=0.0, settle_s=0.05).result(5.0)
        assert again == [s.motors[3]] and again[0] is motors[0]
        assert len(discovered) == 3


def test_scanned_motor_is_usable() -> None:
    s, _ = make_session()
    with s:
        s.scan(gap_s=0.0, settle_s=0.1).result(5.0)
        m = s.motors[127]
        assert m.enable().result(2.0).motor_id == 127
        assert m.state.mode is not None and m.state.online


def test_motor_get_or_create() -> None:
    s, _ = make_session()
    m = s.motor(7)
    assert s.motor(7) is m and s.motors == {7: m}


def test_close_sends_stop_to_all_known_motors_before_closing_transport() -> None:
    s, tr = make_session()
    s.open()
    s.scan(gap_s=0.0, settle_s=0.1).result(5.0)
    s.motors[2].enable().result(2.0)
    n_before = len(tr.sent)
    s.close()
    # close 時点までに stop(type 4, B0=0)が全モータへ送られている
    assert tr.closed_after_sent is not None and tr.closed_after_sent >= n_before + 3
    tail = tr.sent[n_before:]
    assert sorted(f.can_id & 0xFF for f in tail) == [1, 2, 127]
    assert all(parse_id(f.can_id).comm_type == CommType.STOP and f.data[0] == 0 for f in tail)
    assert all(f.direction.value == "tx" for f in tail)


def test_close_stops_motors_even_if_tx_queue_is_backlogged() -> None:
    """send_now なのでキューが詰まっていても stop が出る。"""
    s, tr = make_session()
    s.open()
    s.motor(1)
    s.motor(2)
    tr.send_delay = 0.02
    for _ in range(30):
        s.bus.send(private.get_uid(9, HOST), Priority.BACKGROUND)
    s.close()
    assert set(stop_dests(tr)) >= {1, 2}
    assert len(tr.sent) < 30  # バックログは捨てられ、全部は送られていない


def test_close_without_motors_sends_nothing() -> None:
    s, tr = make_session()
    s.open()
    s.close()
    assert tr.sent == []


def test_close_survives_send_failure() -> None:
    s, tr = make_session()
    s.open()
    s.motor(1)
    tr.fail_sends = [OSError("unplugged")]
    s.close()  # 例外にならない
    assert not tr.is_open


def test_context_manager_closes_on_exception() -> None:
    s, tr = make_session()
    with pytest.raises(RuntimeError), s:
        s.motor(5)
        raise RuntimeError("user error")
    assert not tr.is_open and 5 in stop_dests(tr)


def test_atexit_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[object] = []
    unregistered: list[object] = []
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: registered.append(fn))
    monkeypatch.setattr(atexit, "unregister", lambda fn: unregistered.append(fn))
    s, _ = make_session()
    s.open()
    assert registered == [s.close]
    s.close()
    assert unregistered == [s.close]


def test_open_failure_cleans_up() -> None:
    s, tr = make_session()

    def boom() -> None:
        raise OSError("no port")

    tr.open = boom  # type: ignore[method-assign]
    with pytest.raises(OSError):
        s.open()
    assert not s.bus.running


def test_framelog_path_records_frames(tmp_path) -> None:
    path = tmp_path / "frames.log"
    s, _ = make_session(framelog_path=str(path))
    with s:
        s.scan(range(1, 4), gap_s=0.0, settle_s=0.05).result(5.0)
        assert wait_until(lambda: path.read_text().count("\n") >= 4)
    lines = path.read_text().splitlines()
    assert sum(1 for line in lines if line.endswith(" T")) >= 3
