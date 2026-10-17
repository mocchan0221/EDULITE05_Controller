"""SimMotor.handle の応答がバイト単位で el05_protocol.md / 実機(§8.0)と一致すること。"""

import math
import struct

import pytest

from edulite05.core.protocol import private as pv
from edulite05.core.protocol.ids import parse_id
from edulite05.core.protocol.private import (
    FaultReport,
    Feedback,
    ParamResponse,
    UidResponse,
    decode,
    try_parse_version,
)
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.transport.base import CanFrame
from edulite05.sim.motor_model import BENCH_UID, SimMotor

H = 0xFD
M = 0x7F


def hexs(f: CanFrame) -> str:
    return f"{f.can_id:08X} {f.data.hex(' ').upper()}"


# -- type 0 -----------------------------------------------------------------
def test_type0_uid(bench, ctx):
    r = bench.one(pv.get_uid(M, H))
    assert hexs(r) == "00007FFE 40 99 3B C0 00 20 30 06"  # hw_bench.md の実機と同じ
    assert r.extended and len(r.data) == 8
    m = decode(r, ctx)
    assert isinstance(m, UidResponse) and m.motor_id == M and m.uid == BENCH_UID


def test_ignores_other_ids_standard_frames_and_short_dlc(bench):
    assert bench.ask(pv.get_uid(1, H)) == []
    f = pv.get_uid(M, H)
    assert bench.ask(CanFrame(f.can_id, f.data, extended=False)) == []
    assert bench.ask(CanFrame(f.can_id, f.data[:4])) == []


# -- type 2 / 3 / 4 ---------------------------------------------------------
def test_idle_feedback_bytes_match_hardware_example(bench):
    # 実機の停止中フィードバックと同じ並び: 02007FFD ... 温度 29.0 ℃(B6–B7 = 0x0122)
    r = bench.stop()
    assert hexs(r) == "02007FFD 7F FF 7F FF 7F FF 01 22"


def test_enable_sets_motor_mode_bits(bench, ctx):
    r = bench.enable()
    assert r.can_id == 0x02807FFD  # bit23..22 = 2
    m = decode(r, ctx)
    assert isinstance(m, Feedback) and m.mode is MotorMode.MOTOR and m.fault_bits == 0
    assert bench.m.enabled


def test_stop_resets_mode(bench, ctx):
    bench.enable()
    r = bench.stop()
    assert r.can_id == 0x02007FFD
    assert decode(r, ctx).mode is MotorMode.RESET
    assert not bench.m.enabled


def test_version_read_type4_matches_hardware(bench):
    r = bench.one(pv.read_version(M, H))
    assert hexs(r) == "02007FFD 00 C4 56 0A 05 00 02 07"
    v = try_parse_version(r)
    assert v is not None and v.motor_id == M and v.version == "10.5.0.2"


def test_version_read_does_not_stop_motor(bench):
    bench.enable()
    r = bench.one(pv.read_version(M, H))
    assert parse_id(r.can_id).data2 >> 14 == 2  # モード bits は Motor のまま
    assert bench.m.enabled


def test_type26_returns_plain_feedback_not_version(bench):
    r = bench.one(pv.read_version(M, H, alt=True))
    assert try_parse_version(r) is None
    assert hexs(r) == "02007FFD 7F FF 7F FF 7F FF 01 22"


def test_feedback_encodes_state_big_endian(bench, ctx):
    m = bench.m
    m.position, m.velocity, m.torque, m.temperature = 1.0, -10.0, 0.5, 33.8
    f = m.feedback_frame()
    msg = decode(f, ctx)
    assert msg.position == pytest.approx(1.0, abs=1e-3)
    assert msg.velocity == pytest.approx(-10.0, abs=2e-3)
    assert msg.torque == pytest.approx(0.5, abs=1e-3)
    assert msg.temperature == pytest.approx(33.8)
    p, v, t, temp = struct.unpack(">HHHh", f.data)
    assert temp == 338 and p > 0x7FFF and v < 0x7FFF and t > 0x7FFF


def test_position_wraps_at_4pi(bench, ctx):
    bench.m.position = 4 * math.pi + 1.0  # 4π の外 → ±4π で周期ループ(実機 §8.0: 13.02 → -12.11)
    msg = decode(bench.m.feedback_frame(), ctx)
    assert msg.position == pytest.approx(-4 * math.pi + 1.0, abs=1e-3)
    assert bench.m.get_param("mechPos") == pytest.approx(4 * math.pi + 1.0)  # 折り返さない


# -- type 1 -----------------------------------------------------------------
def test_type1_replies_with_type2_and_latches_command(bench, profile):
    f = pv.mit_command(M, H, profile.ranges, 0.0, 0.0, 1.0, 1.0, 0.0)
    assert hexs(f) == "017FFF7F 7F FF 7F FF 00 83 33 33"  # el05_protocol.md §2.2 の例
    r = bench.one(f)
    assert parse_id(r.can_id).comm_type == 2
    c = bench.m._mit
    assert c.kp == pytest.approx(1.0, abs=0.01) and c.kd == pytest.approx(1.0, abs=1e-3)
    assert c.torque == pytest.approx(0.0, abs=1e-3)


def test_type1_does_not_enable(bench):
    bench.one(pv.mit_command(M, H, bench.p.ranges, 1.0, 0, 10, 1, 0))
    assert not bench.m.enabled


# -- type 17 ----------------------------------------------------------------
def test_param_read_float_doc_example(bench):
    # §2.9 の例: loc_kp 読出 → 応答 `1E 70 00 00 00 00 F0 41`(30.0f)。出荷値は 30
    r = bench.one(pv.read_param(M, H, 0x701E))
    assert hexs(r) == "11007FFD 1E 70 00 00 00 00 F0 41"


def test_param_read_uint8_uses_b4_only(bench):
    r = bench.one(pv.read_param(M, H, 0x7005))
    assert hexs(r) == "11007FFD 05 70 00 00 00 00 00 00"
    bench.set_mode(RunMode.CSP)
    r = bench.one(pv.read_param(M, H, 0x7005))
    assert r.data == bytes.fromhex("0570000005000000")


def test_param_read_uint16_and_decode(bench):
    bench.m.set_param("EPScan_time", 0x1234)
    r = bench.one(pv.read_param(M, H, 0x7026))
    assert r.data == bytes.fromhex("2670000034120000")
    m = bench.read("EPScan_time")
    assert isinstance(m, ParamResponse) and m.ok and m.value == 0x1234


def test_param_read_all_profile_params_ok_except_unreadable(bench, profile):
    for p in profile.params:
        m = bench.read(p.index)
        assert isinstance(m, ParamResponse)
        if p.index in (0x702C, 0x702D, 0x702E):
            assert not m.ok
        else:
            assert m.ok, p.key
            assert m.motor_id == M and m.index == p.index


@pytest.mark.parametrize("index", [0x702C, 0x702D, 0x702E, 0x7007, 0x7008, 0x7009, 0x700C, 0x7023])
def test_param_read_fail_reply_layout(bench, index):
    r = bench.one(pv.read_param(M, H, index))
    assert r.can_id == 0x11017FFD  # bit23..16 = 0x01
    assert r.data == struct.pack("<HHI", index, 0, 0)


def test_param_read_hidden_0x701d_is_ok(bench):
    r = bench.one(pv.read_param(M, H, 0x701D))
    assert hexs(r) == "11007FFD 1D 70 00 00 03 00 00 00"


def test_param_defaults_match_hardware(bench):
    expect = {
        "run_mode": 0, "iq_ref": 0.0, "spd_ref": 0.0, "limit_torque": 6.0, "cur_kp": 0.125,
        "cur_ki": 0.0158, "cur_filt_gain": 0.1, "limit_spd": 50.0, "limit_cur": 11.0,
        "loc_kp": 30.0, "spd_kp": 6.0, "spd_ki": 0.02, "spd_filt_gain": 0.5, "acc_rad": 10.0,
        "vel_max": 10.0, "acc_set": 10.0, "EPScan_time": 0, "canTimeout": 0, "zero_sta": 0,
        "add_offset": 0.0, "VBUS": 24.49,
    }  # fmt: skip
    for key, want in expect.items():
        got = bench.read(key).value
        assert got == pytest.approx(want, rel=1e-6, abs=1e-6), key


def test_mech_and_vbus_params_are_live(bench):
    bench.m.position, bench.m.velocity = 3.5, -2.0
    assert bench.read("mechPos").value == pytest.approx(3.5, rel=1e-6)
    assert bench.read("mechVel").value == pytest.approx(-2.0, rel=1e-6)
    assert bench.read("VBUS").value == pytest.approx(24.49, rel=1e-6)


def test_loc_ref_tracks_position_while_disabled(bench):
    bench.m.position = 2.038
    bench.m.step(0.002)
    assert bench.read("loc_ref").value == pytest.approx(2.038, abs=1e-5)
    assert bench.read("mechPos").value == pytest.approx(2.038, abs=1e-5)


def test_loc_ref_written_while_disabled_is_kept(bench):
    bench.write("loc_ref", 1.0)
    bench.m.step(0.01)
    assert bench.read("loc_ref").value == 1.0


# -- type 18 ----------------------------------------------------------------
def test_write_param_doc_examples_reply_type2_and_apply(bench):
    f = pv.write_param(M, H, 0x7005, bench.p.param(0x7005).dtype, 5)
    assert hexs(f) == "1200FD7F 05 70 00 00 05 00 00 00"
    r = bench.one(f)
    assert r.can_id == 0x02007FFD
    assert bench.m.run_mode == RunMode.CSP
    bench.write("loc_ref", 1.0)
    bench.write("limit_spd", 5.0)
    assert bench.read("loc_ref").value == 1.0
    assert bench.read("limit_spd").value == 5.0


def test_run_mode_change_rejected_while_running(bench):
    bench.set_mode(RunMode.VELOCITY)
    bench.enable()
    r = bench.write("run_mode", 3)
    assert parse_id(r.can_id).comm_type == 2  # 応答は返るが
    assert bench.m.run_mode == RunMode.VELOCITY  # 反映されない
    assert bench.read("run_mode").value == 2
    bench.stop()
    bench.write("run_mode", 3)
    assert bench.read("run_mode").value == 3


def test_run_mode_undefined_value_rejected(bench):
    bench.write("run_mode", 4)
    assert bench.m.run_mode == RunMode.OPERATION


def test_write_ignored_for_readonly_unknown_and_out_of_range(bench):
    bench.one(pv.write_param(M, H, 0x701C, bench.p.param(0x701C).dtype, 12.0))  # VBUS は読出専用
    assert bench.read("VBUS").value == pytest.approx(24.49, rel=1e-6)
    r = bench.one(pv.write_param(M, H, 0x7007, bench.p.param(0x7005).dtype, 1))  # 未定義
    assert parse_id(r.can_id).comm_type == 2
    bench.write("limit_cur", 50.0)  # 範囲外(0..11)
    assert bench.read("limit_cur").value == 11.0
    bench.write("spd_ref", float("nan"))
    assert bench.read("spd_ref").value == 0.0


# -- type 6 / 7 / 22 / 23 / 25 -----------------------------------------------
def test_set_zero_in_csp_moves_reference_not_motor(bench):
    bench.set_mode(RunMode.CSP)
    bench.m.position = 2.0
    r = bench.one(pv.set_zero(M, H))
    assert parse_id(r.can_id).comm_type == 2
    assert bench.m.position == 0.0
    bench.m.step(0.002)
    assert bench.read("loc_ref").value == pytest.approx(0.0, abs=1e-6)


def test_set_zero_ignored_in_pp_and_current_mode(bench):
    for mode in (RunMode.PP, RunMode.CURRENT):
        bench.set_mode(mode)
        bench.m.position = 2.0
        bench.one(pv.set_zero(M, H))
        assert bench.m.position == 2.0


def test_set_can_id_replies_type0_from_new_id(bench, ctx):
    r = bench.one(pv.set_can_id(M, H, 1))
    assert r.can_id == 0x000001FE
    assert r.data == BENCH_UID
    m = decode(r, ctx)
    assert isinstance(m, UidResponse) and m.motor_id == 1
    assert bench.ask(pv.get_uid(M, H)) == []  # 旧 ID はもう応答しない
    assert len(bench.ask(pv.get_uid(1, H))) == 1


def test_set_can_id_out_of_range_is_ignored(bench):
    bench.m.handle(CanFrame(0x07C8FD7F, bytes(8)))  # 新 ID = 0xC8 (200) > 127
    assert bench.m.motor_id == M


def test_save_params_replies_type0_uid_and_keeps_70xx_runtime(bench):
    """実機(HW-6): type 22 は type 0(UID、宛先 0xFE)で応答し、0x70xx は保存しない。"""
    bench.write("limit_spd", 3.0)
    r = bench.one(pv.save_params(M, H))
    assert r.can_id == 0x00007FFE
    assert bytes(r.data) == bench.m.uid
    assert bench.m.saved_params[0x7017] != pytest.approx(3.0)  # 保存されない


def test_baud_and_protocol_reply_type0(bench):
    for f in (pv.set_baud(M, H, 2), pv.switch_protocol(M, H, 2)):
        r = bench.one(f)
        assert hexs(r) == "00007FFE 40 99 3B C0 00 20 30 06"
    assert bench.m.baud_code == 2 and bench.m.protocol == 2


def test_unknown_comm_types_get_no_reply(bench):
    for t in (5, 8, 16, 19, 20):
        assert bench.ask(CanFrame((t << 24) | (H << 8) | M, bytes(8))) == []


# -- 主動上報(type 24) -------------------------------------------------------
def collect(motor: SimMotor, seconds: float, dt: float = 0.001) -> list[tuple[float, CanFrame]]:
    out = []
    for _ in range(round(seconds / dt)):
        for f in motor.step(dt):
            out.append((motor.t, f))
    return out


def test_active_report_on_is_silent_then_streams_type24(bench, ctx):
    assert bench.ask(pv.set_active_report(M, H, True)) == []
    frames = collect(bench.m, 0.1)
    assert len(frames) == 10  # 既定(EPScan_time=0 → 10 ms)
    for _, f in frames:
        assert parse_id(f.can_id).comm_type == 24
        m = decode(f, ctx)
        assert isinstance(m, Feedback) and m.comm_type == 24 and m.motor_id == M


@pytest.mark.parametrize(("n", "period_ms"), [(0, 10), (1, 15), (2, 20), (3, 25), (11, 65)])
def test_active_report_period_follows_epscan_time(bench, n, period_ms):
    bench.write("EPScan_time", n)
    bench.ask(pv.set_active_report(M, H, True))
    frames = collect(bench.m, 0.6)
    times = [t for t, _ in frames]
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert gaps and all(abs(g - period_ms / 1000) <= 0.0011 for g in gaps)
    assert len(frames) == pytest.approx(600 / period_ms, abs=1)


def test_active_report_off_answers_one_type2_and_stops(bench):
    bench.ask(pv.set_active_report(M, H, True))
    collect(bench.m, 0.05)
    out = bench.ask(pv.set_active_report(M, H, False))
    assert len(out) == 1 and parse_id(out[0].can_id).comm_type == 2
    assert collect(bench.m, 0.1) == []


def test_active_report_comm_type_configurable(profile):
    m = SimMotor(motor_id=M, profile=profile, report_comm_type=2)
    m.handle(pv.set_active_report(M, H, True))
    frames = collect(m, 0.05)
    assert frames and all(parse_id(f.can_id).comm_type == 2 for _, f in frames)
    with pytest.raises(ValueError):
        SimMotor(motor_id=M, profile=profile, report_comm_type=3)


def test_active_report_carries_live_state_and_mode(bench, ctx):
    bench.enable()
    bench.ask(pv.set_active_report(M, H, True))
    (_, f), *_ = collect(bench.m, 0.011)
    assert decode(f, ctx).mode is MotorMode.MOTOR


# -- 故障 --------------------------------------------------------------------
def test_inject_fault_emits_type21_and_sets_feedback_bits(bench, ctx):
    bench.enable()
    bench.m.inject_fault(2)  # 低電圧(type 21 bit2 / type 2 bit0)
    out = bench.m.step(0.0)
    assert len(out) == 1
    f = out[0]
    assert hexs(f) == "15007FFD 04 00 00 00 00 00 00 00"
    m = decode(f, ctx)
    assert isinstance(m, FaultReport) and m.fault == 4 and m.warning == 0
    r = bench.stop()
    fb = decode(r, ctx)
    assert fb.fault_bits == 0b000001 and fb.mode is MotorMode.RESET
    assert r.can_id == 0x02017FFD  # bit16 = 低電圧


def test_fault_makes_motor_stop_and_blocks_enable_until_cleared(bench, ctx):
    bench.enable()
    bench.m.inject_fault(16)  # A 相過電流 → type 2 bit1(三相電流)
    assert not bench.m.enabled
    fb = decode(bench.enable(), ctx)
    assert fb.mode is MotorMode.RESET and fb.fault_bits == 0b000010
    fb = decode(bench.stop(clear=True), ctx)
    assert fb.fault_bits == 0 and bench.m.faults == 0
    assert decode(bench.enable(), ctx).mode is MotorMode.MOTOR


def test_fault_bit_positions_in_feedback_id(bench):
    for bit21, fb_bit in ((0, 2), (2, 0), (14, 4), (7, 5)):
        bench.m.clear_faults()
        bench.m.inject_fault(bit21)
        assert (bench.stop().can_id >> 16) & 0x3F == 1 << fb_bit


def test_overtemp_trips_with_warning_bit(bench, ctx):
    bench.m.temperature = 136.0
    out = bench.m.step(0.001)
    reports = [m for m in (decode(f, ctx) for f in out) if isinstance(m, FaultReport)]
    assert len(reports) == 1
    assert reports[0].fault & 1 and reports[0].warning & 1


def test_can_timeout_disables_motor(bench):
    bench.write("canTimeout", 20000)  # 1 s
    bench.enable()
    bench.m.step(0.5)
    assert bench.m.enabled
    bench.m.step(0.6)
    assert not bench.m.enabled
