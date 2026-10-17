"""故障ビットの意味(el05_protocol.md §2.3 / §2.11)。

type 2 の故障フィールド(ID bit21..16 を右シフトした 6bit)と、type 21 / 0x3022 faultSta の
ビット定義は別物(SDK は混在させているので注意、§8.2)。
"""

from __future__ import annotations

from typing import NamedTuple


class FaultInfo(NamedTuple):
    bit: int
    key: str
    name: str  # 日本語名
    description: str


# type 2 故障フィールド(ID >> 16 & 0x3F)。出典 §2.3
FEEDBACK_FAULTS: tuple[FaultInfo, ...] = (
    FaultInfo(0, "undervoltage", "低電圧", "母線電圧が下限(12 V)を下回りました。"),
    FaultInfo(1, "overcurrent", "三相電流(過電流)", "三相電流が過電流になりました。"),
    FaultInfo(2, "overtemp", "過温", "モータ温度が保護温度(135 ℃)を超えました。"),
    FaultInfo(3, "encoder", "磁気エンコーダ故障", "磁気エンコーダの読出に異常があります。"),
    FaultInfo(4, "stall", "ストール過負荷", "ストール過負荷の保護が働きました。"),
    FaultInfo(5, "uncalibrated", "未校正", "エンコーダが未校正です。"),
)

# type 21 の fault(uint32)。出典 §2.11(CN が正)
FAULT21_BITS: tuple[FaultInfo, ...] = (
    FaultInfo(0, "motor_overtemp", "モータ過温", "サーミスタ温度が 135 ℃ を超えました。"),
    FaultInfo(1, "driver_ic", "ドライバ IC 故障", "ドライバ IC が故障を報告しました。"),
    FaultInfo(2, "undervoltage", "低電圧", "母線電圧が 12 V を下回りました。"),
    FaultInfo(3, "overvoltage", "過電圧", "母線電圧が 60 V を超えました。"),
    FaultInfo(4, "phase_b_overcurrent", "B 相電流過電流", "B 相電流サンプリングが過電流です。"),
    FaultInfo(5, "phase_c_overcurrent", "C 相電流過電流", "C 相電流サンプリングが過電流です。"),
    FaultInfo(7, "encoder_uncalibrated", "エンコーダ未校正", "エンコーダが未校正です。"),
    FaultInfo(8, "hw_id", "ハードウェア識別故障", "ハードウェア識別に失敗しました。"),
    FaultInfo(9, "position_init", "位置初期化故障", "位置の初期化に失敗しました。"),
    FaultInfo(14, "stall", "ストール過負荷", "ストール過負荷アルゴリズムの保護が働きました。"),
    FaultInfo(16, "phase_a_overcurrent", "A 相電流過電流", "A 相電流サンプリングが過電流です。"),
)

# type 21 の warning(uint32)。出典 §2.11
WARNING21_BITS: tuple[FaultInfo, ...] = (
    FaultInfo(
        0, "motor_overtemp_warn", "モータ過温警告", "モータ温度が警告温度(既定 135 ℃)に達しました。"
    ),
)


def _describe(bits: int, table: tuple[FaultInfo, ...]) -> list[FaultInfo]:
    return [f for f in table if bits >> f.bit & 1]


def describe_feedback_faults(bits: int) -> list[FaultInfo]:
    """type 2 の 6bit 故障フィールド → 立っているビットの情報(bit 昇順)。"""
    return _describe(bits & 0x3F, FEEDBACK_FAULTS)


def describe_fault21(fault: int, warning: int) -> list[FaultInfo]:
    """type 21 の fault / warning → 立っているビットの情報(fault、warning の順)。

    テーブルに無いビットは `key="unknown_fault_bitN"` / `"unknown_warning_bitN"` で末尾に付ける。
    """
    out = _describe(fault, FAULT21_BITS) + _describe(warning, WARNING21_BITS)
    known_f = {f.bit for f in FAULT21_BITS}
    known_w = {f.bit for f in WARNING21_BITS}
    for bit in range(32):
        if fault >> bit & 1 and bit not in known_f:
            out.append(FaultInfo(bit, f"unknown_fault_bit{bit}", f"不明な故障 bit{bit}", ""))
    for bit in range(32):
        if warning >> bit & 1 and bit not in known_w:
            out.append(FaultInfo(bit, f"unknown_warning_bit{bit}", f"不明な警告 bit{bit}", ""))
    return out
