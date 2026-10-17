"""デコーダ(el05_protocol.md §2.1/2.3/2.6/2.9/2.11/2.14)。"""

import pytest

from edulite05.core.device.profile import MotorProfile
from edulite05.core.protocol import private as pv
from edulite05.core.protocol.private import (
    DecodeContext,
    FaultReport,
    Feedback,
    ParamResponse,
    UidResponse,
    Unknown,
    VersionInfo,
    decode,
    try_parse_version,
)
from edulite05.core.protocol.types import DType, MotorMode
from edulite05.core.transport.base import CanFrame, Direction

H = 0xFD
M = 0x7F
ZERO_FB = "7FFF7FFF7FFF0000"


@pytest.fixture(scope="module")
def ctx():
    prof = MotorProfile.load_builtin("el05")
    return DecodeContext(H, prof.ranges, prof.param_dtypes())


def rx(can_id: int, data_hex: str, **kw) -> CanFrame:
    return CanFrame(can_id, bytes.fromhex(data_hex), timestamp=1.0, direction=Direction.RX, **kw)


def fb_id(comm_type=2, mode=0, fault=0, motor=M, host=H) -> int:
    return (comm_type << 24) | (mode << 22) | (fault << 16) | (motor << 8) | host


# -- type 2 / 24 ------------------------------------------------------------
def test_feedback_center_values(ctx):
    f = rx(fb_id(mode=2), ZERO_FB)
    m = decode(f, ctx)
    assert isinstance(m, Feedback)
    assert m.motor_id == M and m.comm_type == 2
    assert m.mode is MotorMode.MOTOR
    assert m.fault_bits == 0
    assert m.position == pytest.approx(0.0, abs=1e-3)
    assert m.velocity == pytest.approx(0.0, abs=1e-2)
    assert m.torque == pytest.approx(0.0, abs=1e-3)
    assert m.temperature == 0.0
    assert m.frame is f


def test_feedback_big_endian_and_scaling(ctx):
    # 各フィールドは BE。B0–B1 = 0x0000 → -12.57、B2–B3 = 0xFFFF → +50、B4–B5 = 0x0000 → -6
    m = decode(rx(fb_id(), "0000FFFF00000000"), ctx)
    assert m.position == pytest.approx(-12.57)
    assert m.velocity == pytest.approx(50.0)
    assert m.torque == pytest.approx(-6.0)
    # 0x0100 は BE なら 256(LE なら 1)。位置の式 u*25.14/65535 - 12.57
    m = decode(rx(fb_id(), "01007FFF7FFF0000"), ctx)
    assert m.position == pytest.approx(256 * 25.14 / 65535 - 12.57)


def test_feedback_temperature(ctx):
    assert decode(rx(fb_id(), "7FFF7FFF7FFF012C"), ctx).temperature == pytest.approx(30.0)
    assert decode(rx(fb_id(), "7FFF7FFF7FFF0152"), ctx).temperature == pytest.approx(33.8)
    # 負温度は int16 として解釈(§2.3)
    assert decode(rx(fb_id(), "7FFF7FFF7FFFFFF6"), ctx).temperature == pytest.approx(-1.0)


@pytest.mark.parametrize(
    ("mode_bits", "mode"), [(0, MotorMode.RESET), (1, MotorMode.CALI), (2, MotorMode.MOTOR)]
)
def test_feedback_modes(ctx, mode_bits, mode):
    assert decode(rx(fb_id(mode=mode_bits), ZERO_FB), ctx).mode is mode


def test_feedback_invalid_mode_is_unknown(ctx):
    m = decode(rx(fb_id(mode=3), ZERO_FB), ctx)
    assert isinstance(m, Unknown) and "mode" in m.reason


@pytest.mark.parametrize("bit", range(6))
def test_feedback_fault_bits(ctx, bit):
    m = decode(rx(fb_id(mode=2, fault=1 << bit), ZERO_FB), ctx)
    assert m.fault_bits == 1 << bit
    assert m.mode is MotorMode.MOTOR  # 故障ビットがモードに混ざらない


def test_feedback_all_faults_and_motor_id_from_bit15_8(ctx):
    m = decode(rx(fb_id(mode=1, fault=0x3F, motor=0x05), ZERO_FB), ctx)
    assert m.fault_bits == 0x3F and m.motor_id == 0x05 and m.mode is MotorMode.CALI


def test_active_report_type24_accepted(ctx):
    m = decode(rx(fb_id(comm_type=24, mode=2), "7FFF7FFF7FFF012C"), ctx)
    assert isinstance(m, Feedback) and m.comm_type == 24
    assert m.temperature == pytest.approx(30.0)


def test_feedback_not_for_host(ctx):
    m = decode(rx(fb_id(host=0x01), ZERO_FB), ctx)
    assert isinstance(m, Unknown) and m.reason == "not for host"


def test_custom_host_id():
    prof = MotorProfile.load_builtin()
    c = DecodeContext(0x01, prof.ranges, {})
    assert isinstance(decode(rx(fb_id(host=0x01), ZERO_FB), c), Feedback)
    assert isinstance(decode(rx(fb_id(host=0xFD), ZERO_FB), c), Unknown)


# -- version -----------------------------------------------------------------
def test_version_response(ctx):
    # §2.6: 応答は type 2 の ID、B0..B2 = 00 C4 56、B3–B6 = バージョン
    f = rx(0x02007FFD, "00C4560100050400")
    v = try_parse_version(f)
    assert v == VersionInfo(M, "1.0.5.4", bytes.fromhex("00C4560100050400"))
    assert v.raw == f.data
    # 同じフレームは type 2 のフィードバックとしても読める(判別は呼び出し側の時間窓)
    assert isinstance(decode(f, ctx), Feedback)


def test_version_rejects_others():
    assert try_parse_version(rx(0x02007FFD, ZERO_FB)) is None
    assert try_parse_version(rx(0x11007FFD, "00C4560100050400")) is None  # type 17
    assert try_parse_version(rx(0x02007FFD, "00C456")) is None  # 短い


# -- type 17 -------------------------------------------------------------------
def test_param_float_example(ctx):
    # §2.9: loc_kp 応答 1E 70 00 00 00 00 F0 41 = 30.0
    m = decode(rx(0x11007FFD, "1E7000000000F041"), ctx)
    assert isinstance(m, ParamResponse)
    assert (m.motor_id, m.index, m.ok) == (M, 0x701E, True)
    assert m.value == pytest.approx(30.0)
    assert m.raw == bytes.fromhex("0000F041")


def test_param_uint8_uses_b4(ctx):
    m = decode(rx(0x11007FFD, "0570000002AABBCC"), ctx)  # B5..B7 はゴミでも無視
    assert m.value == 2 and isinstance(m.value, int)


def test_param_uint16_uint32_int16(ctx):
    assert decode(rx(0x11007FFD, "2670000003010000"), ctx).value == 0x0103  # EPScan_time
    assert decode(rx(0x11007FFD, "28700000204E0000"), ctx).value == 20000  # canTimeout
    c2 = DecodeContext(H, ctx.ranges, {0x3005: DType.INT16})
    assert decode(rx(0x11007FFD, "0530000000FF0000"), c2).value == -256
    assert decode(rx(0x11007FFD, "05300000F6FF0000"), c2).value == -10


def test_param_failure(ctx):
    m = decode(rx(0x11017FFD, "1E70000000000000"), ctx)
    assert isinstance(m, ParamResponse)
    assert m.ok is False and m.value is None and m.index == 0x701E


def test_param_unknown_dtype_has_no_value(ctx):
    m = decode(rx(0x11007FFD, "0070000012345678"), ctx)  # 0x7000 はプロファイルに無い
    assert m.ok is True and m.value is None
    assert m.raw == bytes.fromhex("12345678")


def test_param_all_profile_dtypes_decode(ctx):
    prof = MotorProfile.load_builtin()
    for p in prof.params:
        raw = pv.write_param(M, H, p.index, p.dtype, p.default).data  # 書込と同じレイアウト
        m = decode(rx(0x11007FFD, raw.hex()), ctx)
        assert m.index == p.index
        assert m.value == pytest.approx(p.default, rel=1e-6)


# -- type 21 ------------------------------------------------------------------
def test_fault_report(ctx):
    # B0–B3 = fault(LE)、B4–B7 = warning(LE)
    m = decode(rx(0x15007FFD, "0100010001000000"), ctx)
    assert isinstance(m, FaultReport)
    assert (m.motor_id, m.fault, m.warning) == (M, 0x00010001, 1)


def test_fault_report_byte_order(ctx):
    m = decode(rx(0x15007FFD, "0040000000000080"), ctx)  # 0x00004000 = bit14、warning 0x80000000
    assert (m.fault, m.warning) == (0x4000, 0x80000000)


# -- type 0 -------------------------------------------------------------------
def test_uid_response(ctx):
    # §2.1: 応答 ID 0x00007FFE(宛先 0xFE)
    m = decode(rx(0x00007FFE, "0102030405060708"), ctx)
    assert isinstance(m, UidResponse)
    assert m.motor_id == M and m.uid == bytes.fromhex("0102030405060708")


def test_uid_response_from_type7_new_id(ctx):
    m = decode(rx(0x000001FE, "AABBCCDDEEFF0011"), ctx)
    assert isinstance(m, UidResponse) and m.motor_id == 0x01


def test_uid_request_is_not_for_host(ctx):
    # 自分が送った要求(宛先 = モータ)を拾っても Unknown
    m = decode(rx(0x0000FD7F, "0000000000000000"), ctx)
    assert isinstance(m, Unknown) and m.reason == "not for host"


# -- Unknown ------------------------------------------------------------------
@pytest.mark.parametrize("ct", [3, 4, 18, 22])
def test_host_commands_are_not_for_host(ctx, ct):
    m = decode(rx((ct << 24) | (H << 8) | M, "0000000000000000"), ctx)
    assert isinstance(m, Unknown) and m.reason == "not for host"


def test_unsupported_type_to_host(ctx):
    can_id = (5 << 24) | (M << 8) | H
    m = decode(rx(can_id, "0000000000000000"), ctx)
    assert isinstance(m, Unknown) and m.frame.can_id == can_id


def test_non_type0_to_0xfe_is_unknown(ctx):
    assert isinstance(decode(rx(fb_id(host=0xFE), ZERO_FB), ctx), Unknown)


def test_standard_frame_is_unknown(ctx):
    assert isinstance(decode(CanFrame(0x0FD, bytes(8), extended=False), ctx), Unknown)


def test_short_frame_is_unknown(ctx):
    assert isinstance(decode(rx(fb_id(), "7FFF"), ctx), Unknown)


def test_encoder_scaling_consistent_with_decoder(ctx):
    """type 1 の指令値スケーリングが、同じレンジのデコード側と 1 LSB 以内で整合する。"""
    f = pv.mit_command(M, H, ctx.ranges, 1.0, 2.0, 10.0, 0.5, 0.0)
    p, v = int.from_bytes(f.data[0:2], "big"), int.from_bytes(f.data[2:4], "big")
    m = decode(rx(fb_id(mode=2), f"{p:04X}{v:04X}7FFF0000"), ctx)
    assert abs(m.position - 1.0) <= 25.14 / 65535 and abs(m.velocity - 2.0) <= 100 / 65535
