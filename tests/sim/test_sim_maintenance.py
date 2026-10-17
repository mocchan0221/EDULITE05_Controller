"""SimMotor の保守操作(type 6/7/22/23/25 と MIT・CANopen からの復帰、power_cycle)。"""

from __future__ import annotations

from edulite05.core.protocol import private as pv
from edulite05.core.protocol.private import UidResponse
from edulite05.core.protocol.types import RunMode
from edulite05.core.transport.base import CanFrame
from edulite05.sim.motor_model import is_persistent_index

H = 0xFD
M = 0x7F


def uid_of(bench, frame: CanFrame) -> UidResponse:
    r = pv.decode(frame, bench.ctx)
    assert isinstance(r, UidResponse)
    return r


def test_type6_zero_only_in_operation_and_csp(bench):
    m = bench.m
    for mode, effective in [
        (RunMode.OPERATION, True),
        (RunMode.CSP, True),
        (RunMode.PP, False),
        (RunMode.VELOCITY, False),
        (RunMode.CURRENT, False),
    ]:
        m.position = 1.0
        bench.set_mode(mode)
        bench.one(pv.set_zero(M, H))  # 応答(type 2)は常に返る
        assert (abs(m.position) < 1e-9) is effective, mode


def test_type7_changes_id_and_replies_from_new_id(bench):
    m = bench.m
    reply = bench.one(pv.set_can_id(M, H, 5))
    r = uid_of(bench, reply)
    assert r.motor_id == 5 and r.uid == m.uid and m.motor_id == 5
    assert bench.ask(pv.get_uid(M, H)) == []  # 旧 ID にはもう応答しない
    assert uid_of(bench, bench.one(pv.get_uid(5, H))).motor_id == 5
    # 続けて別の ID へも変えられる
    bench.one(pv.set_can_id(5, H, 6))
    assert m.motor_id == 6


def test_power_cycle_drops_70xx_even_after_type22(bench):
    m = bench.m
    bench.write("cur_kp", 0.2)
    bench.one(pv.save_params(M, H))
    bench.write("cur_ki", 0.05)
    bench.enable()
    m.active_report = True
    m.power_cycle()
    assert abs(m.get_param("cur_kp") - 0.2) > 1e-3  # 保存後でも 0x70xx は工場値へ(HW-6)
    assert abs(m.get_param("cur_ki") - 0.0158) < 1e-9
    assert not m.enabled and not m.active_report


def test_type22_persists_only_702b_and_20xx(bench):
    """§2.10: 永続化されるのは 0x20xx と 0x702A / 0x702B だけ。"""
    assert is_persistent_index(0x702B) and is_persistent_index(0x702A) and is_persistent_index(0x2001)
    assert not is_persistent_index(0x7026) and not is_persistent_index(0x7010)
    m = bench.m
    bench.write("add_offset", 0.5)
    bench.one(pv.save_params(M, H))
    m.power_cycle()
    assert abs(m.get_param("add_offset") - 0.5) < 1e-6


def test_type23_and_25_pending_until_power_cycle(bench):
    m = bench.m
    assert uid_of(bench, bench.one(pv.set_baud(M, H, 3))).motor_id == M
    assert uid_of(bench, bench.one(pv.switch_protocol(M, H, 2))).motor_id == M
    assert (m.baud_code, m.protocol) == (3, 2)
    assert (m.active_baud_code, m.active_protocol) == (1, 0)
    bench.one(pv.stop(M, H))  # 再投入前は私有プロトコルのまま
    m.power_cycle()
    assert (m.active_baud_code, m.active_protocol) == (3, 2)


def test_mit_motor_ignores_private_frames_and_answers_only_restore(bench):
    m = bench.m
    bench.one(pv.switch_protocol(M, H, 2))
    m.power_cycle()
    for f in (
        pv.get_uid(M, H),
        pv.enable(M, H),
        pv.stop(M, H),
        pv.read_param(M, H, 0x7005),
        pv.switch_protocol(M, H, 0),
        pv.restore_from_canopen(0),  # CANopen 用の復帰フレームには反応しない
        pv.restore_from_mit(1),  # 別 ID 宛て
    ):
        assert bench.ask(f) == []
    assert m.protocol == 2
    out = bench.one(pv.restore_from_mit(M))
    assert out.extended is False and out.can_id == M and out.data == m.uid
    assert m.protocol == 0 and m.active_protocol == 2  # 再投入まで変わらない
    m.power_cycle()
    assert m.active_protocol == 0
    assert uid_of(bench, bench.one(pv.get_uid(M, H))).uid == m.uid


def test_canopen_motor_ignores_private_frames_and_answers_only_restore(bench):
    m = bench.m
    bench.one(pv.switch_protocol(M, H, 1))
    m.power_cycle()
    assert m.active_protocol == 1
    for f in (pv.get_uid(M, H), pv.enable(M, H), pv.restore_from_mit(M)):
        assert bench.ask(f) == []
    out = bench.one(pv.restore_from_canopen(0))
    assert out.extended is False and out.can_id == M and out.data == m.uid
    m.power_cycle()
    assert m.active_protocol == 0
    assert uid_of(bench, bench.one(pv.get_uid(M, H))).motor_id == M
