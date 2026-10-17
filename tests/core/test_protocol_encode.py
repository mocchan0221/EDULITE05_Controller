"""エンコーダのバイト厳密テスト(el05_protocol.md §2 の全バイト例、ホスト 0xFD・モータ 0x7F)。"""

import pytest

from edulite05.core.device.profile import MotorProfile
from edulite05.core.errors import ProtocolError
from edulite05.core.protocol import private as pv
from edulite05.core.protocol.ids import CommType, parse_id
from edulite05.core.protocol.types import DType
from edulite05.core.transport.base import Direction

H = 0xFD
M = 0x7F


@pytest.fixture(scope="module")
def ranges():
    return MotorProfile.load_builtin("el05").ranges


def check(frame, can_id, data_hex):
    assert frame.can_id == can_id, f"{frame.can_id:08X} != {can_id:08X}"
    assert frame.data == bytes.fromhex(data_hex)
    assert frame.extended is True
    assert len(frame.data) == 8  # DLC は常に 8
    assert frame.direction is Direction.TX


def test_type0_get_uid():
    check(pv.get_uid(M, H), 0x0000FD7F, "0000000000000000")


def test_type1_mit_command_example(ranges):
    # §2.2: p=0, v=0, Kp=1, Kd=1, t_ff=0 → ID 0x017FFF7F、データ 7F FF 7F FF 00 83 33 33
    f = pv.mit_command(M, H, ranges, 0.0, 0.0, 1.0, 1.0, 0.0)
    check(f, 0x017FFF7F, "7FFF7FFF00833333")


def test_type1_torque_in_id_bits_23_8(ranges):
    f = pv.mit_command(M, H, ranges, 0.0, 0.0, 0.0, 0.0, 6.0)
    assert parse_id(f.can_id).data2 == 0xFFFF
    f = pv.mit_command(M, H, ranges, 0.0, 0.0, 0.0, 0.0, -6.0)
    assert parse_id(f.can_id).data2 == 0x0000
    # 0 N·m は 0x7FFF(0x0000 だと −6 N·m 指令になる)
    f = pv.mit_command(M, H, ranges, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert parse_id(f.can_id).data2 == 0x7FFF


def test_type1_big_endian_fields_and_clamp(ranges):
    f = pv.mit_command(M, H, ranges, 100.0, -100.0, 1000.0, -1.0, 0.0)
    assert f.data == bytes.fromhex("FFFF0000FFFF0000")
    f = pv.mit_command(M, H, ranges, 12.57, 50.0, 500.0, 5.0, 0.0)
    assert f.data == bytes.fromhex("FFFFFFFFFFFFFFFF")


def test_type1_manual_motion_values(ranges):
    # §2.2 動作例: v=1 rad/s, Kd=1 → B2–B3 = int(51*65535/100)(BE)、B6–B7 = 0x3333
    f = pv.mit_command(M, H, ranges, 0.0, 1.0, 0.0, 1.0, 0.0)
    assert f.data[2:4] == int(51.0 * 65535 / 100.0).to_bytes(2, "big")
    assert f.data[6:8] == bytes.fromhex("3333")


def test_type3_enable():
    check(pv.enable(M, H), 0x0300FD7F, "0000000000000000")


def test_type4_stop_and_clear_fault():
    check(pv.stop(M, H), 0x0400FD7F, "0000000000000000")
    check(pv.stop(M, H, clear_fault=False), 0x0400FD7F, "0000000000000000")
    check(pv.stop(M, H, clear_fault=True), 0x0400FD7F, "0100000000000000")


def test_version_read_type4_and_alt():
    check(pv.read_version(M, H), 0x0400FD7F, "00C4000000000000")
    check(pv.read_version(M, H, alt=True), 0x1A00FD7F, "00C4000000000000")


def test_type6_set_zero():
    check(pv.set_zero(M, H), 0x0600FD7F, "0100000000000000")


def test_type7_set_can_id():
    check(pv.set_can_id(M, H, 0x01), 0x0701FD7F, "0000000000000000")
    with pytest.raises(ProtocolError):
        pv.set_can_id(M, H, 128)


def test_type17_read_param():
    # §2.9: loc_kp(0x701E)読出
    check(pv.read_param(M, H, 0x701E), 0x1100FD7F, "1E70000000000000")
    check(pv.read_param(M, H, 0x7005), 0x1100FD7F, "0570000000000000")


def test_type18_write_param_examples():
    # AT 例(§1.4): run_mode(0x7005)← 1、モータ 1
    check(pv.write_param(1, H, 0x7005, DType.UINT8, 1), 0x1200FD01, "0570000001000000")
    # §2.10: run_mode ← 5 / loc_ref ← 1.0 / limit_spd ← 5.0
    check(pv.write_param(M, H, 0x7005, DType.UINT8, 5), 0x1200FD7F, "0570000005000000")
    check(pv.write_param(M, H, 0x7016, DType.FLOAT, 1.0), 0x1200FD7F, "167000000000803F")
    check(pv.write_param(M, H, 0x7017, DType.FLOAT, 5.0), 0x1200FD7F, "177000000000A040")


def test_type18_other_dtypes():
    check(pv.write_param(M, H, 0x7026, DType.UINT16, 2), 0x1200FD7F, "2670000002000000")
    check(pv.write_param(M, H, 0x7028, DType.UINT32, 20000), 0x1200FD7F, "28700000204E0000")


def test_type18_rejects_bad_value():
    with pytest.raises(ProtocolError):
        pv.write_param(M, H, 0x7005, DType.UINT8, 300)


def test_type22_save():
    check(pv.save_params(M, H), 0x1600FD7F, "0102030405060708")


def test_type23_baud():
    check(pv.set_baud(M, H, 1), 0x1700FD7F, "0102030405060100")
    check(pv.set_baud(M, H, 4), 0x1700FD7F, "0102030405060400")
    for bad in (0, 5):
        with pytest.raises(ProtocolError):
            pv.set_baud(M, H, bad)


def test_type24_active_report():
    check(pv.set_active_report(M, H, True), 0x1800FD7F, "0102030405060100")
    check(pv.set_active_report(M, H, False), 0x1800FD7F, "0102030405060000")


def test_type25_protocol():
    check(pv.switch_protocol(M, H, 0), 0x1900FD7F, "0102030405060000")
    check(pv.switch_protocol(M, H, 1), 0x1900FD7F, "0102030405060100")
    check(pv.switch_protocol(M, H, 2), 0x1900FD7F, "0102030405060200")
    with pytest.raises(ProtocolError):
        pv.switch_protocol(M, H, 3)


def test_at_example_id_conversion():
    """§1.4 AT 例の ID 変換: 0x1200FD01 → 0x9007E80C(= (id << 3) | 0x04)。"""
    f = pv.write_param(1, H, 0x7005, DType.UINT8, 1)
    assert ((f.can_id << 3) | 0x04) == 0x9007E80C
    assert parse_id(f.can_id).comm_type == CommType.PARAM_WRITE


def test_invalid_ids_rejected():
    with pytest.raises(ProtocolError):
        pv.enable(256, H)
    with pytest.raises(ProtocolError):
        pv.enable(M, -1)
    with pytest.raises(ProtocolError):
        pv.read_param(M, H, 0x10000)


def test_other_motor_and_host_ids():
    check(pv.enable(0x01, 0xFF), 0x0300FF01, "0000000000000000")
    check(pv.get_uid(0x00, 0x00), 0x00000000, "0000000000000000")


def test_restore_from_mit_standard_frame():
    # §4.11: 標準フレーム、ID = 0x000 | モータ ID、データ FF×6 00 FD(指令 8、F_CMD = 0)
    f = pv.restore_from_mit(M)
    assert f.extended is False and f.can_id == 0x07F and f.direction is Direction.TX
    assert f.data == bytes.fromhex("FFFFFFFFFFFF00FD")
    assert pv.restore_from_mit(1).can_id == 0x001
    for bad in (-1, 128):
        with pytest.raises(ProtocolError):
            pv.restore_from_mit(bad)
    # 停止(`.. FF FD`)とは B6 だけが違う(§4.4 の衝突)
    assert f.data[6] == 0x00


def test_restore_from_canopen_extended_frame():
    # §6.4: 拡張フレーム ID 0xFFF、データ 01..06 F_CMD 00(F_CMD = 0 = 独自)
    f = pv.restore_from_canopen()
    assert f.extended is True and f.can_id == 0x00000FFF and f.direction is Direction.TX
    assert f.data == bytes.fromhex("0102030405060000")
    assert pv.restore_from_canopen(2).data == bytes.fromhex("0102030405060200")
    with pytest.raises(ProtocolError):
        pv.restore_from_canopen(3)
