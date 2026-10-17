"""故障ビット表(el05_protocol.md §2.3 / §2.11)。"""

from edulite05.core.protocol.faults import (
    FAULT21_BITS,
    FEEDBACK_FAULTS,
    WARNING21_BITS,
    describe_fault21,
    describe_feedback_faults,
)


def test_feedback_fault_table():
    # §2.3「正規化」表: 0 低電圧, 1 三相電流, 2 過温, 3 磁気エンコーダ, 4 ストール過負荷, 5 未校正
    assert [f.bit for f in FEEDBACK_FAULTS] == [0, 1, 2, 3, 4, 5]
    assert [f.key for f in FEEDBACK_FAULTS] == [
        "undervoltage", "overcurrent", "overtemp", "encoder", "stall", "uncalibrated",
    ]
    assert all(f.name and f.description for f in FEEDBACK_FAULTS)


def test_fault21_table_bits():
    assert sorted(f.bit for f in FAULT21_BITS) == [0, 1, 2, 3, 4, 5, 7, 8, 9, 14, 16]
    assert [f.bit for f in WARNING21_BITS] == [0]


def test_describe_feedback_faults():
    assert describe_feedback_faults(0) == []
    assert [f.key for f in describe_feedback_faults(0b000101)] == ["undervoltage", "overtemp"]
    assert len(describe_feedback_faults(0x3F)) == 6
    assert [f.key for f in describe_feedback_faults(0b100000)] == ["uncalibrated"]
    assert describe_feedback_faults(0x40) == []  # 6bit 以外は無視


def test_describe_fault21():
    assert describe_fault21(0, 0) == []
    got = describe_fault21(0x00010001, 1)
    assert [f.bit for f in got] == [0, 16, 0]
    assert [f.key for f in got] == ["motor_overtemp", "phase_a_overcurrent", "motor_overtemp_warn"]
    assert [f.key for f in describe_fault21(1 << 14, 0)] == ["stall"]


def test_describe_fault21_unknown_bits():
    got = describe_fault21(1 << 20, 1 << 5)
    assert [f.key for f in got] == ["unknown_fault_bit20", "unknown_warning_bit5"]
