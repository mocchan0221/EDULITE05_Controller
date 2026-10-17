"""ID 組立・分解(el05_protocol.md §1.2)。"""

import pytest

from edulite05.core.protocol.ids import CommType, IdFields, make_id, parse_id


def test_make_id_examples():
    # §2.1 type 0 要求 / 応答
    assert make_id(CommType.GET_ID, 0xFD, 0x7F) == 0x0000FD7F
    assert make_id(CommType.GET_ID, 0x7F, 0xFE) == 0x00007FFE
    # §2.4 / §2.5 / §2.7
    assert make_id(CommType.ENABLE, 0xFD, 0x7F) == 0x0300FD7F
    assert make_id(CommType.STOP, 0xFD, 0x7F) == 0x0400FD7F
    assert make_id(CommType.SET_ZERO, 0xFD, 0x7F) == 0x0600FD7F
    # §2.8 type 7: data2 = (新 ID << 8) | ホスト
    assert make_id(CommType.SET_CAN_ID, (0x01 << 8) | 0xFD, 0x7F) == 0x0701FD7F
    # §1.4 AT 例: type 18, ホスト 0xFD, モータ 1
    assert make_id(CommType.PARAM_WRITE, 0xFD, 0x01) == 0x1200FD01
    assert make_id(CommType.FEEDBACK, 0x7F, 0xFD) == 0x02007FFD


def test_make_id_masks_fields():
    assert make_id(0x1FF, 0x1FFFF, 0x1FF) == (0x1F << 24) | (0xFFFF << 8) | 0xFF


def test_parse_id():
    assert parse_id(0x1200FD01) == IdFields(18, 0x00FD, 0x01)
    assert parse_id(0x017FFF7F) == IdFields(1, 0x7FFF, 0x7F)  # type 1: data2 = トルク u16


@pytest.mark.parametrize("ct", list(CommType))
@pytest.mark.parametrize("data2", [0, 0x00FD, 0x7FFF, 0xFFFF])
@pytest.mark.parametrize("dest", [0, 1, 0x7F, 0xFE, 0xFF])
def test_roundtrip(ct, data2, dest):
    assert parse_id(make_id(ct, data2, dest)) == IdFields(int(ct), data2, dest)


def test_comm_type_values():
    assert {c.name: int(c) for c in CommType} == {
        "GET_ID": 0, "MIT_CTRL": 1, "FEEDBACK": 2, "ENABLE": 3, "STOP": 4, "SET_ZERO": 6,
        "SET_CAN_ID": 7, "PARAM_READ": 17, "PARAM_WRITE": 18, "FAULT": 21, "SAVE": 22,
        "BAUD": 23, "ACTIVE_REPORT": 24, "PROTOCOL": 25, "VERSION_ALT": 26,
    }
