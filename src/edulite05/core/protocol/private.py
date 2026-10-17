"""私有プロトコルのエンコーダ/デコーダ(el05_protocol.md §2)。

純粋関数のみ(I/O・状態なし)。エンコーダは送信用 `CanFrame`(29bit 拡張、DLC 8、
direction=TX)を返す。デコーダは受信 `CanFrame` を `Message` に変換する。

byte order の要点(§2 の注意):
- type 1/2 のデータ領域は全フィールド **ビッグエンディアン**
- type 17/18 の index と値は **リトルエンディアン**(uint8 は B4 のみ)
- type 1 のフィードフォワードトルクは **ID bit23..8**(0 N·m = 0x7FFF)
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from dataclasses import dataclass

from edulite05.core.errors import ProtocolError
from edulite05.core.protocol.ids import CommType, make_id, parse_id
from edulite05.core.protocol.scaling import float_to_uint, uint_to_float
from edulite05.core.protocol.types import DType, MotorMode, Ranges, pack_value, unpack_value
from edulite05.core.transport.base import CanFrame, Direction

BROADCAST_ID = 0xFE  # type 0 応答の宛先(§1.3)
_ZERO8 = bytes(8)
_VERSION_MAGIC = bytes((0x00, 0xC4, 0x56))  # §2.6 注意 2


# ---------------------------------------------------------------------------
# エンコーダ
# ---------------------------------------------------------------------------
def _check_id(value: int, name: str, hi: int = 0xFF) -> int:
    if not 0 <= value <= hi:
        raise ProtocolError(f"{name} が範囲外です: {value}")
    return value


def _frame(comm_type: CommType, data2: int, dest: int, data: bytes) -> CanFrame:
    _check_id(dest, "宛先 ID")
    if len(data) != 8:  # pragma: no cover - 内部の取り違え防止
        raise AssertionError("DLC は常に 8")
    return CanFrame(make_id(comm_type, data2, dest), data, True, 0.0, 0, Direction.TX)


def _host_frame(comm_type: CommType, motor: int, host: int, data: bytes = _ZERO8) -> CanFrame:
    """一般形: data2 = ホスト ID(bit15..8)、bit23..16 = 0(§1.2)。"""
    return _frame(comm_type, _check_id(host, "ホスト ID"), motor, data)


def _cmd_byte6(f_cmd: int) -> bytes:
    """type 22/23/24/25 共通: `01 02 03 04 05 06 F_CMD 00`(F_CMD = B6)。"""
    return bytes((1, 2, 3, 4, 5, 6, f_cmd & 0xFF, 0))


def get_uid(motor: int, host: int) -> CanFrame:
    """type 0: デバイス ID / UID 取得(§2.1)。"""
    return _host_frame(CommType.GET_ID, motor, host)


def enable(motor: int, host: int) -> CanFrame:
    """type 3: 有効化(§2.4)。"""
    return _host_frame(CommType.ENABLE, motor, host)


def stop(motor: int, host: int, clear_fault: bool = False) -> CanFrame:
    """type 4: 停止。`clear_fault=True` で B0=1(故障クリア)(§2.5)。"""
    data = bytes((1 if clear_fault else 0, 0, 0, 0, 0, 0, 0, 0))
    return _host_frame(CommType.STOP, motor, host, data)


def mit_command(
    motor: int,
    host: int,
    ranges: Ranges,
    position: float,
    velocity: float,
    kp: float,
    kd: float,
    torque: float,
) -> CanFrame:
    """type 1: 運控指令(§2.2)。t_ff は ID bit23..8、他は B0..B7 の u16 ビッグエンディアン。

    `host` は ID に入らない(bit15..8 は t_ff が使う)。値はレンジでクランプされる。
    """
    _check_id(host, "ホスト ID")
    t_ff = float_to_uint(torque, *ranges.torque, 16)
    data = struct.pack(
        ">HHHH",
        float_to_uint(position, *ranges.position, 16),
        float_to_uint(velocity, *ranges.velocity, 16),
        float_to_uint(kp, *ranges.kp, 16),
        float_to_uint(kd, *ranges.kd, 16),
    )
    return _frame(CommType.MIT_CTRL, t_ff, motor, data)


def read_param(motor: int, host: int, index: int) -> CanFrame:
    """type 17: 単一パラメータ読出要求(§2.9)。index は LE。"""
    _check_id(index, "パラメータ index", 0xFFFF)
    data = struct.pack("<HHI", index, 0, 0)
    return _host_frame(CommType.PARAM_READ, motor, host, data)


def write_param(motor: int, host: int, index: int, dtype: DType, value: int | float) -> CanFrame:
    """type 18: 単一パラメータ書込(§2.10)。値は LE で B4 から詰める(uint8 は B4)。"""
    _check_id(index, "パラメータ index", 0xFFFF)
    data = struct.pack("<HH", index, 0) + pack_value(dtype, value)
    return _host_frame(CommType.PARAM_WRITE, motor, host, data)


def set_zero(motor: int, host: int) -> CanFrame:
    """type 6: 機械ゼロ点設定(B0=1)(§2.7)。"""
    return _host_frame(CommType.SET_ZERO, motor, host, bytes((1, 0, 0, 0, 0, 0, 0, 0)))


def set_can_id(motor: int, host: int, new_id: int) -> CanFrame:
    """type 7: CAN ID 変更。data2 = 新 ID(bit23..16) | ホスト(bit15..8)(§2.8)。"""
    _check_id(new_id, "新 CAN ID", 127)
    _check_id(host, "ホスト ID")
    return _frame(CommType.SET_CAN_ID, (new_id << 8) | host, motor, _ZERO8)


def save_params(motor: int, host: int) -> CanFrame:
    """type 22: パラメータ保存。データ `01 02 .. 08`(§2.12)。"""
    return _host_frame(CommType.SAVE, motor, host, bytes(range(1, 9)))


def set_baud(motor: int, host: int, code: int) -> CanFrame:
    """type 23: ボーレート変更。code 1=1M, 2=500k, 3=250k, 4=125k(§2.13)。再投入後有効。"""
    if not 1 <= code <= 4:
        raise ProtocolError(f"ボーレートコードが範囲外です: {code}")
    return _host_frame(CommType.BAUD, motor, host, _cmd_byte6(code))


def set_active_report(motor: int, host: int, on: bool) -> CanFrame:
    """type 24: 主動上報 ON/OFF(F_CMD = B6)(§2.14)。"""
    return _host_frame(CommType.ACTIVE_REPORT, motor, host, _cmd_byte6(1 if on else 0))


def switch_protocol(motor: int, host: int, protocol: int) -> CanFrame:
    """type 25: プロトコル切替。0=独自, 1=CANopen, 2=MIT(§2.15)。再投入後有効。"""
    if protocol not in (0, 1, 2):
        raise ProtocolError(f"プロトコル番号が範囲外です: {protocol}")
    return _host_frame(CommType.PROTOCOL, motor, host, _cmd_byte6(protocol))


_MIT_RESTORE_DATA = bytes((0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0xFD))
CANOPEN_RESTORE_ID = 0xFFF  # §6.4


def restore_from_mit(motor_id: int) -> CanFrame:
    """MIT プロトコルのモータを独自プロトコルへ戻す: **11bit 標準フレーム**(§4.11)。

    ID = `0x000 | motor_id`(kind 0 の特殊コマンド。§4.1 の `STD_ID = (kind << 8) | id`)、
    データ `FF FF FF FF FF FF 00 FD`(指令 8、F_CMD = 0 = 独自)。応答は標準フレーム
    (ID = モータ ID、データ = UID)。**再投入後有効**。

    注意: 末尾 `FD` は停止(B6 = FF)と衝突する。B6 = 00 なのでプロトコル変更になる(§4.4)。
    標準フレームの AT アダプタ上の表現は未検証(Q-T1)。
    """
    _check_id(motor_id, "モータ ID", 127)
    return CanFrame(motor_id, _MIT_RESTORE_DATA, False, 0.0, 0, Direction.TX)


def restore_from_canopen(protocol: int = 0) -> CanFrame:
    """CANopen のモータを独自プロトコルへ戻す: **拡張フレーム ID 0xFFF**(§6.4)。

    データ `01 02 03 04 05 06 F_CMD 00`(F_CMD = B6: 0 独自 / 1 CANopen / 2 MIT)。
    モータ ID はフレームに含まれない(ブロードキャスト的。バス上の CANopen モータ全部が受ける)。
    応答は標準フレーム(ID = モータ ID、データ = UID)。**再投入後有効**。
    """
    if protocol not in (0, 1, 2):
        raise ProtocolError(f"プロトコル番号が範囲外です: {protocol}")
    return CanFrame(
        CANOPEN_RESTORE_ID, _cmd_byte6(protocol), True, 0.0, 0, Direction.TX
    )


def read_version(motor: int, host: int, alt: bool = False) -> CanFrame:
    """バージョン読出要求 `00 C4 00 ..`(§2.6)。type 4(既定)/ alt=True で type 26。

    type 4 は運転中に送ると停止する可能性がある(停止状態でのみ使う)。
    """
    data = bytes((0x00, 0xC4, 0, 0, 0, 0, 0, 0))
    ct = CommType.VERSION_ALT if alt else CommType.STOP
    return _host_frame(ct, motor, host, data)


# ---------------------------------------------------------------------------
# デコーダ
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DecodeContext:
    host_id: int
    ranges: Ranges
    param_dtypes: Mapping[int, DType]


@dataclass(frozen=True, slots=True)
class Feedback:
    """type 2 / 24 のフィードバック(§2.3)。fault_bits は ID bit21..16 の 6bit(右詰め)。"""

    motor_id: int
    comm_type: int  # 2 または 24
    mode: MotorMode
    fault_bits: int
    position: float  # rad(±4π で周期ループ)
    velocity: float  # rad/s
    torque: float  # N·m
    temperature: float  # ℃
    frame: CanFrame


@dataclass(frozen=True, slots=True)
class ParamResponse:
    """type 17 応答(§2.9)。raw は B4–B7 の 4 バイト。dtype 不明/失敗時は value=None。"""

    motor_id: int
    index: int
    ok: bool
    value: int | float | None
    raw: bytes
    frame: CanFrame


@dataclass(frozen=True, slots=True)
class FaultReport:
    """type 21 故障フィードバック(§2.11)。"""

    motor_id: int
    fault: int
    warning: int
    frame: CanFrame


@dataclass(frozen=True, slots=True)
class UidResponse:
    """type 0 応答(宛先 0xFE、§2.1)。uid は B0–B7 そのまま。"""

    motor_id: int
    uid: bytes
    frame: CanFrame


@dataclass(frozen=True, slots=True)
class Unknown:
    frame: CanFrame
    reason: str
    motor_id: int | None = None


Message = Feedback | ParamResponse | FaultReport | UidResponse | Unknown


@dataclass(frozen=True, slots=True)
class VersionInfo:
    motor_id: int
    version: str  # B3–B6 を "." で連結(10 進)。表記と実機の対応は要確認(§2.6)
    raw: bytes  # 8 バイト全体


def decode(frame: CanFrame, ctx: DecodeContext) -> Message:
    """受信フレームを分類・デコードする。

    宛先がホスト ID でも 0xFE でもないフレームは `Unknown("not for host")`。
    """
    if not frame.extended:
        return Unknown(frame, "standard frame")
    f = parse_id(frame.can_id)
    motor_id = (f.data2) & 0xFF  # bit15..8
    if f.dest not in (ctx.host_id, BROADCAST_ID):
        return Unknown(frame, "not for host", motor_id)
    data = frame.data
    if len(data) < 8:
        return Unknown(frame, f"short frame (DLC {len(data)})", motor_id)

    if f.dest == BROADCAST_ID:
        if f.comm_type == CommType.GET_ID:
            return UidResponse(motor_id, bytes(data[:8]), frame)
        return Unknown(frame, f"unexpected type {f.comm_type} to 0xFE", motor_id)
    if f.dest == ctx.host_id:
        if f.comm_type in (CommType.FEEDBACK, CommType.ACTIVE_REPORT):
            return _decode_feedback(frame, f.comm_type, f.data2, motor_id, ctx)
        if f.comm_type == CommType.PARAM_READ:
            return _decode_param(frame, f.data2, motor_id, ctx)
        if f.comm_type == CommType.FAULT:
            fault, warning = struct.unpack_from("<II", data)
            return FaultReport(motor_id, fault, warning, frame)
    return Unknown(frame, f"unsupported type {f.comm_type}", motor_id)


def _decode_feedback(
    frame: CanFrame, comm_type: int, data2: int, motor_id: int, ctx: DecodeContext
) -> Message:
    mode_bits = (data2 >> 14) & 0x3  # ID bit23..22
    fault_bits = (data2 >> 8) & 0x3F  # ID bit21..16
    try:
        mode = MotorMode(mode_bits)
    except ValueError:
        return Unknown(frame, f"invalid mode {mode_bits}", motor_id)
    p, v, t, temp = struct.unpack_from(">HHHh", frame.data)  # 全フィールド BE
    r = ctx.ranges
    return Feedback(
        motor_id=motor_id,
        comm_type=comm_type,
        mode=mode,
        fault_bits=fault_bits,
        position=uint_to_float(p, *r.position, 16),
        velocity=uint_to_float(v, *r.velocity, 16),
        torque=uint_to_float(t, *r.torque, 16),
        temperature=temp / 10.0,
        frame=frame,
    )


def _decode_param(frame: CanFrame, data2: int, motor_id: int, ctx: DecodeContext) -> Message:
    ok = (data2 >> 8) == 0  # ID bit23..16: 0 = 成功、1 = 失敗
    index = struct.unpack_from("<H", frame.data)[0]
    raw = bytes(frame.data[4:8])
    value: int | float | None = None
    dtype = ctx.param_dtypes.get(index)
    if ok and dtype is not None:
        value = unpack_value(dtype, raw)
    return ParamResponse(motor_id, index, ok, value, raw, frame)


def try_parse_version(frame: CanFrame) -> VersionInfo | None:
    """type 2 の ID でデータが `00 C4 56 ..` ならバージョン応答(§2.6)。版数は B3–B6。"""
    if not frame.extended or len(frame.data) < 8:
        return None
    f = parse_id(frame.can_id)
    if f.comm_type != CommType.FEEDBACK or frame.data[:3] != _VERSION_MAGIC:
        return None
    raw = bytes(frame.data[:8])
    return VersionInfo((f.data2) & 0xFF, ".".join(str(b) for b in raw[3:7]), raw)
