"""29bit 拡張 ID の組立・分解(el05_protocol.md §1.2)。

    bit 28..24 通信タイプ(5bit) | bit 23..8 データ領域 2(16bit) | bit 7..0 宛先(8bit)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class CommType(IntEnum):
    """私有プロトコルの通信タイプ(el05_protocol.md §2)。"""

    GET_ID = 0
    MIT_CTRL = 1  # 運控指令(type 1)
    FEEDBACK = 2
    ENABLE = 3
    STOP = 4
    SET_ZERO = 6
    SET_CAN_ID = 7
    PARAM_READ = 17
    PARAM_WRITE = 18
    FAULT = 21
    SAVE = 22
    BAUD = 23
    ACTIVE_REPORT = 24
    PROTOCOL = 25
    VERSION_ALT = 26  # 参考コードのみ(§2.6 注意 3)


@dataclass(frozen=True, slots=True)
class IdFields:
    comm_type: int  # 5bit(未定義のタイプもあるので CommType ではなく整数)
    data2: int  # 16bit
    dest: int  # 8bit


def make_id(comm_type: int, data2: int, dest: int) -> int:
    """`CAN_ID = (type & 0x1F) << 24 | (data2 & 0xFFFF) << 8 | (dest & 0xFF)`(§1.2)。"""
    return ((int(comm_type) & 0x1F) << 24) | ((data2 & 0xFFFF) << 8) | (dest & 0xFF)


def parse_id(can_id: int) -> IdFields:
    return IdFields(
        comm_type=(can_id >> 24) & 0x1F,
        data2=(can_id >> 8) & 0xFFFF,
        dest=can_id & 0xFF,
    )
