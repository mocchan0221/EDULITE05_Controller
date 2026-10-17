"""シリアルポート列挙。公式 USB-CAN(CH340, VID:PID 1A86:7523)を優先する。"""

from __future__ import annotations

from dataclasses import dataclass

OFFICIAL_VID_PID = (0x1A86, 0x7523)


@dataclass(frozen=True, slots=True)
class PortInfo:
    device: str
    description: str
    preferred: bool


def list_serial_ports() -> list[PortInfo]:
    """利用可能な COM ポート。公式アダプタ(1A86:7523)を先頭にする。"""
    try:
        from serial.tools import list_ports
    except ImportError:  # pyserial が無い環境
        return []
    ports = [
        PortInfo(
            p.device,
            p.description or p.device,
            (p.vid, p.pid) == OFFICIAL_VID_PID,
        )
        for p in list_ports.comports()
    ]
    return sorted(ports, key=lambda x: (not x.preferred, x.device))
