"""フレームレベルの EL05 シミュレータ(1 台分)。

`SimMotor.handle(frame)` が要求フレームへの応答を返し、`SimMotor.step(dt)` が 1 自由度の
物理と主動上報・故障フレームを進める。**プロトコル応答は el05_protocol.md と実機(§8.0)に
合わせる**。数値(J・b・Kt・温度モデル)は「それらしい値」で `SimPhysics` にまとめてある。

スレッド安全: すべての公開メソッドは内部の RLock で保護する(物理スレッドとテスト/GUI から
同時に触れる)。時刻は `step()` で進む内部クロック `t`(秒)で、実時間の時計
(`core.clock.now()`)には依存しない。
"""

from __future__ import annotations

import math
import struct
import threading
from dataclasses import dataclass, field

from edulite05.core.device.profile import MotorProfile
from edulite05.core.protocol.ids import CommType, make_id, parse_id
from edulite05.core.protocol.private import BROADCAST_ID
from edulite05.core.protocol.scaling import float_to_uint, uint_to_float
from edulite05.core.protocol.types import MotorMode, RunMode, pack_value, unpack_value
from edulite05.core.transport.base import CanFrame

# docs/research/hw_bench.md の実機(ID 127)
BENCH_MOTOR_ID = 127
BENCH_UID = bytes.fromhex("40993BC000203006")
# el05_protocol.md §8.0: バージョン応答 `00 C4 56 | 0A 05 00 02 | 07`
BENCH_VERSION = bytes.fromhex("0A050002")
BENCH_VERSION_B7 = 0x07
DEFAULT_HOST_ID = 0xFD

# el05_protocol.md §8.0: 実機の出荷値(プロファイルの既定値=マニュアル値より優先する)
HW_FACTORY_VALUES: dict[str, int | float] = {
    "limit_torque": 6.0,
    "cur_kp": 0.125,
    "cur_ki": 0.0158,
    "cur_filt_gain": 0.1,
    "limit_spd": 50.0,
    "limit_cur": 11.0,
    "loc_kp": 30.0,
    "spd_kp": 6.0,
    "spd_ki": 0.02,
    "spd_filt_gain": 0.5,
    "acc_rad": 10.0,
    "vel_max": 10.0,
    "acc_set": 10.0,
    "EPScan_time": 0,
    "VBUS": 24.49,
}
HW_INITIAL_TEMPERATURE = 29.0

# 実機で type 17 が失敗する(§8.0)プロファイル内の index(書込は受け付ける)
UNREADABLE_INDICES = frozenset({0x702C, 0x702D, 0x702E})
# プロファイルに無いが type 17 で読める index(§8.0): raw B4–B7
HIDDEN_READABLE: dict[int, bytes] = {0x701D: bytes((3, 0, 0, 0))}
# 範囲チェックを緩めるパラメータ(実機は 0 が出荷値。プロファイルの min=1 より優先)
_RANGE_EXEMPT = frozenset({"EPScan_time"})

# type 21 fault ビット → type 2 の 6bit 故障フィールドのビット(faults.py の対応を用いた推定)
FEEDBACK_BIT_FOR_FAULT21: dict[int, int] = {
    0: 2,  # モータ過温 → 過温
    1: 1,  # ドライバ IC → 三相電流
    2: 0,  # 低電圧
    4: 1,
    5: 1,
    16: 1,  # 各相過電流 → 三相電流
    7: 5,  # エンコーダ未校正 → 未校正
    14: 4,  # ストール過負荷
}

RUN_MODES = frozenset(int(m) for m in RunMode)


@dataclass(slots=True)
class SimPhysics:
    """シミュレータ内部の物理定数(モータの仕様ではなくモデルの都合の値)。"""

    inertia: float = 0.0007  # J [kg·m²]
    damping: float = 0.002  # b [N·m·s/rad]
    # クーロン摩擦 [N·m]。既定 0(従来の挙動)。実機は ≈ 0.03(hw_bench.md HW-4)。
    # tanh(v / 0.01) で滑らかにする

    coulomb: float = 0.0
    kt: float = 0.55  # トルク定数 [N·m/A]
    ambient: float = HW_INITIAL_TEMPERATURE  # [℃]
    heat_coeff: float = 0.02  # 発熱 [℃/s per A²]
    thermal_tau: float = 120.0  # 放熱時定数 [s]
    substep: float = 1e-4  # 積分の最大刻み [s](Kd=5 でも安定な値)


@dataclass(slots=True)
class _MitCommand:
    position: float = 0.0
    velocity: float = 0.0
    kp: float = 0.0
    kd: float = 0.0
    torque: float = 0.0


def is_persistent_index(index: int) -> bool:
    """type 22 で flash に残るパラメータか(§2.10: 0x20xx と 0x702A / 0x702B のみ)。"""
    return (index & 0xFF00) == 0x2000 or index in (0x702A, 0x702B)


@dataclass(eq=False)
class SimMotor:
    """1 台の EL05(CAN ID `motor_id`)。

    公開状態: `mode`, `run_mode`(params["run_mode"]), `position`(多回転 rad), `velocity`,
    `torque`, `iq`, `temperature`, `faults`(type 21 の fault)、`feedback_faults`(type 2 の 6bit)。
    """

    motor_id: int
    profile: MotorProfile
    uid: bytes = BENCH_UID
    version: bytes = BENCH_VERSION
    version_b7: int = BENCH_VERSION_B7
    host_id: int = DEFAULT_HOST_ID
    report_comm_type: int = CommType.ACTIVE_REPORT  # 主動上報の通信タイプ(2 か 24)
    physics: SimPhysics = field(default_factory=SimPhysics)
    initial_position: float = 0.0  # 多回転 [rad]。±4π の外でもよい(起動時にずれた多回転の再現)
    # MIT の p_set を何と比べるか(実機は未確認 = open_questions Q-P12)。
    # "multi" = 多回転位置(mechPos と同じ。既定。8π の引き戻しが起きる悲観側)
    # "wrapped" = 折り返し位置
    mit_frame: str = "multi"
    initial_temperature: float = HW_INITIAL_TEMPERATURE

    # --- 状態 ---
    mode: MotorMode = field(init=False, default=MotorMode.RESET)
    params: dict[int, int | float] = field(init=False, default_factory=dict)
    saved_params: dict[int, int | float] = field(init=False, default_factory=dict)
    position: float = field(init=False, default=0.0)
    velocity: float = field(init=False, default=0.0)
    torque: float = field(init=False, default=0.0)
    iq: float = field(init=False, default=0.0)
    temperature: float = field(init=False, default=HW_INITIAL_TEMPERATURE)
    ext_torque: float = field(init=False, default=0.0)  # 外力(手で回す等)[N·m]
    faults: int = field(init=False, default=0)
    warnings: int = field(init=False, default=0)
    feedback_faults: int = field(init=False, default=0)
    active_report: bool = field(init=False, default=False)
    baud_code: int = field(init=False, default=1)  # type 23 の設定値(再投入後有効、未適用)
    protocol: int = field(init=False, default=0)  # type 25 の設定値(再投入後有効、未適用)
    active_baud_code: int = field(init=False, default=1)  # 現在動作中(power_cycle で baud_code へ)
    active_protocol: int = field(init=False, default=0)  # 現在動作中(0 独自 / 1 CANopen / 2 MIT)
    t: float = field(init=False, default=0.0)
    last_rx_t: float = field(init=False, default=0.0)
    _mit: _MitCommand = field(init=False, default_factory=_MitCommand)
    _integ: float = field(init=False, default=0.0)
    _v_ramp: float = field(init=False, default=0.0)
    _pp_pos: float = field(init=False, default=0.0)
    _pp_vel: float = field(init=False, default=0.0)
    _loc_track: bool = field(init=False, default=True)
    _next_report: float = field(init=False, default=0.0)
    _outbox: list[CanFrame] = field(init=False, default_factory=list)
    _lock: threading.RLock = field(init=False, default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        if len(self.uid) != 8:
            raise ValueError("UID は 8 バイト")
        if len(self.version) != 4:
            raise ValueError("version は 4 バイト(B3–B6)")
        if self.report_comm_type not in (CommType.FEEDBACK, CommType.ACTIVE_REPORT):
            raise ValueError("report_comm_type は 2 または 24")
        if self.mit_frame not in ("multi", "wrapped"):
            raise ValueError("mit_frame は 'multi' または 'wrapped'")
        self._by_key = {p.key: p for p in self.profile.params}
        for p in self.profile.params:
            self.params[p.index] = HW_FACTORY_VALUES.get(p.key, p.default)
        # 工場出荷値。type 22 で更新されるのは永続対象(0x20xx / 0x702A / 0x702B)だけ。
        # 0x70xx の運転パラメータは電源再投入で工場値へ戻る(実測 HW-6)
        self.saved_params = dict(self.params)
        self.position = self.initial_position
        self.temperature = self.initial_temperature
        self._sync_loc_ref()

    # ------------------------------------------------------------------
    # パラメータ・状態の取得
    # ------------------------------------------------------------------
    def _idx(self, key_or_index: int | str) -> int:
        return self.profile.param(key_or_index).index

    def get_param(self, key_or_index: int | str) -> int | float:
        """内部値(動的な読出専用パラメータは現在値)。"""
        with self._lock:
            idx = self._idx(key_or_index)
            return self._read_value(idx)

    def set_param(self, key_or_index: int | str, value: int | float) -> None:
        """検証なしで内部値を直接書く(テスト・GUI 用。読出専用も可)。"""
        with self._lock:
            idx = self._idx(key_or_index)
            dtype = self.profile.param(idx).dtype
            self.params[idx] = int(value) if dtype.is_integer else float(value)
            self._after_param_write(idx)

    @property
    def run_mode(self) -> int:
        return int(self.params[0x7005])

    @property
    def enabled(self) -> bool:
        return self.mode is MotorMode.MOTOR

    @property
    def vbus(self) -> float:
        return float(self.params[0x701C])

    @property
    def report_period(self) -> float:
        """主動上報の周期 [s] = 10 ms + N·5 ms(N = EPScan_time。実機測定 §8.0)。"""
        n = max(0, int(self.params[0x7026]))
        return (10.0 + n * 5.0) / 1000.0

    def _read_value(self, idx: int) -> int | float:
        # 動的な読出専用パラメータ(§5.2)
        if idx == 0x7019:  # mechPos
            return self.position
        if idx == 0x701A:  # iqf
            return self.iq
        if idx == 0x701B:  # mechVel
            return self.velocity
        return self.params[idx]

    def _limit(self, key: str) -> float:
        return float(self.params[self._by_key[key].index])

    def _sync_loc_ref(self) -> None:
        # 停止中は loc_ref が現在位置に追従する(実機 §8.0)
        self.params[0x7016] = self.position

    # ------------------------------------------------------------------
    # 故障
    # ------------------------------------------------------------------
    def inject_fault(
        self,
        bit: int = 0,
        *,
        feedback_bit: int | None = None,
        warning: int | None = None,
    ) -> None:
        """type 21 の fault ビット `bit` を立てる(出典 §2.11)。

        type 2 の故障フィールドは `FEEDBACK_BIT_FOR_FAULT21` の対応(または `feedback_bit`)で立てる。
        故障でモータは停止(Reset)し、type 21 フレームが次の `step()` の戻り値に載る。
        `warning` 省略時は過温(bit 0)のみ warning bit 0 も立てる。
        """
        with self._lock:
            self.faults |= 1 << bit
            fb = feedback_bit if feedback_bit is not None else FEEDBACK_BIT_FOR_FAULT21.get(bit)
            if fb is not None:
                self.feedback_faults |= 1 << fb
            if warning is None:
                warning = 1 if bit == 0 else 0
            self.warnings |= warning
            self._set_mode(MotorMode.RESET)
            self._outbox.append(self._fault_frame())

    def clear_faults(self) -> None:
        """故障・警告をすべて消す(type 4 の B0=1 と同じ)。"""
        with self._lock:
            self.faults = 0
            self.warnings = 0
            self.feedback_faults = 0

    # ------------------------------------------------------------------
    # フレーム生成(モータ → ホスト)
    # ------------------------------------------------------------------
    def _frame(self, comm_type: int, data2: int, dest: int, data: bytes) -> CanFrame:
        return CanFrame(make_id(comm_type, data2, dest), bytes(data))

    def _wrap_position(self) -> float:
        """type 2 の位置: 実機と同じく ±4π(profile.position.wrap_half)で折り返す(§8.0)。
        mechPos(0x7019)は折り返さない多回転値(`position`)を返す。"""
        h = self.profile.position.wrap_half
        return (self.position + h) % (2.0 * h) - h

    def _feedback_data(self) -> bytes:
        r = self.profile.ranges
        temp10 = max(-0x8000, min(0x7FFF, round(self.temperature * 10)))
        return struct.pack(
            ">HHHh",
            float_to_uint(self._wrap_position(), *r.position, 16),
            float_to_uint(self.velocity, *r.velocity, 16),
            float_to_uint(self.torque, *r.torque, 16),
            temp10,
        )

    def _feedback_id2(self) -> int:
        # ID bit23..22 = モード、bit21..16 = 故障、bit15..8 = モータ ID(§2.3)
        return (int(self.mode) << 14) | ((self.feedback_faults & 0x3F) << 8) | self.motor_id

    def feedback_frame(
        self, host: int | None = None, comm_type: int = CommType.FEEDBACK
    ) -> CanFrame:
        """type 2(または 24)のフィードバックフレーム。"""
        with self._lock:
            h = self.host_id if host is None else host
            return self._frame(comm_type, self._feedback_id2(), h, self._feedback_data())

    def _version_frame(self, host: int) -> CanFrame:
        # type 2 の ID + `00 C4 56 | version(4) | B7`(§2.6、実機 §8.0)
        data = bytes((0x00, 0xC4, 0x56)) + self.version + bytes((self.version_b7,))
        return self._frame(CommType.FEEDBACK, self._feedback_id2(), host, data)

    def _uid_frame(self) -> CanFrame:
        # type 0 応答: ID = 0x00 <モータ ID> 0xFE、データ = UID(§2.1)
        return self._frame(CommType.GET_ID, self.motor_id, BROADCAST_ID, self.uid)

    def _fault_frame(self) -> CanFrame:
        # type 21: ID = 0x15 00 <モータ ID> <ホスト>、B0–B3 fault / B4–B7 warning、LE(§2.11)
        data = struct.pack("<II", self.faults, self.warnings)
        return self._frame(CommType.FAULT, self.motor_id, self.host_id, data)

    def _param_frame(self, host: int, index: int, ok: bool, raw: bytes) -> CanFrame:
        # type 17 応答: bit23..16 = 0 成功 / 1 失敗(§2.9)
        data = struct.pack("<HH", index, 0) + raw
        return self._frame(CommType.PARAM_READ, ((0 if ok else 1) << 8) | self.motor_id, host, data)

    # ------------------------------------------------------------------
    # 要求の処理(ホスト → モータ)
    # ------------------------------------------------------------------
    def handle(self, frame: CanFrame) -> list[CanFrame]:
        """要求フレームを処理して応答フレーム(0 個以上)を返す。宛先が自分でなければ空。

        動作中のプロトコルが MIT / CANopen の間は私有プロトコルのフレームを無視し、
        独自プロトコルへ戻す切替フレームにだけ反応する(`_on_foreign_protocol`)。
        """
        if len(frame.data) != 8:
            return []
        if self.active_protocol != 0:
            with self._lock:
                return self._on_foreign_protocol(frame)
        if not frame.extended:
            return []
        f = parse_id(frame.can_id)
        if f.dest != self.motor_id:
            return []
        data = bytes(frame.data)
        ct = f.comm_type
        with self._lock:
            self.last_rx_t = self.t
            if ct == CommType.MIT_CTRL:
                return self._on_mit(f.data2, data)
            host = f.data2 & 0xFF  # bit15..8 = ホスト ID(§1.2)
            self.host_id = host
            if ct == CommType.GET_ID:
                return [self._uid_frame()]
            if ct == CommType.ENABLE:
                return self._on_enable(host)
            if ct == CommType.STOP:
                return self._on_stop(host, data)
            if ct == CommType.SET_ZERO:
                return self._on_set_zero(host, data)
            if ct == CommType.SET_CAN_ID:
                return self._on_set_can_id(f.data2)
            if ct == CommType.PARAM_READ:
                return self._on_param_read(host, data)
            if ct == CommType.PARAM_WRITE:
                return self._on_param_write(host, data)
            if ct == CommType.SAVE:
                # 実機: type 22 は type 0(UID、宛先 0xFE)で応答し、0x70xx は保存しない(HW-6)
                for idx in self.params:
                    if is_persistent_index(idx):
                        self.saved_params[idx] = self.params[idx]
                return [self._uid_frame()]
            if ct == CommType.BAUD:
                # 1..4 のみ有効(§2.13)。再投入までは動作中のボーレートのまま
                if 1 <= data[6] <= 4:
                    self.baud_code = data[6]
                return [self._uid_frame()]
            if ct == CommType.ACTIVE_REPORT:
                return self._on_active_report(host, data)
            if ct == CommType.PROTOCOL:
                # 0..2 のみ有効(§2.15)。再投入までは動作中のプロトコルのまま
                if data[6] in (0, 1, 2):
                    self.protocol = data[6]
                return [self._uid_frame()]
            if ct == CommType.VERSION_ALT:
                # 実機の EL05 は type 26 で版数を返さず通常のフィードバックが返る(§8.0)
                return [self.feedback_frame(host)]
        return []  # 未定義の通信タイプには応答しない(推定)

    def _std_uid_frame(self) -> CanFrame:
        # MIT / CANopen の復帰応答: 標準フレーム、ID = モータ ID、データ = UID(§4.3, §6.4)
        return CanFrame(self.motor_id, bytes(self.uid), False)

    def _on_foreign_protocol(self, frame: CanFrame) -> list[CanFrame]:
        """MIT(2)/ CANopen(1)で動作中のモータ: 復帰フレーム以外は無視する(私有フレームも)。

        - MIT: 標準フレーム ID = モータ ID、データ `FF×6 00 FD`(指令 8、F_CMD = 0)(§4.11)
        - CANopen: 拡張 ID 0xFFF、データ `01..06 F_CMD 00`(§6.4)
        どちらも設定値 `protocol` を F_CMD にして UID の標準フレームを返す(再投入後有効)。
        """
        data = bytes(frame.data)
        if self.active_protocol == 2:
            if (
                not frame.extended
                and frame.can_id == self.motor_id
                and data[:6] == bytes([0xFF]) * 6
                and data[7] == 0xFD
                and data[6] in (0, 1, 2)
            ):
                self.protocol = data[6]
                return [self._std_uid_frame()]
            return []
        if (
            frame.extended
            and frame.can_id == 0xFFF
            and data[:6] == bytes(range(1, 7))
            and data[6] in (0, 1, 2)
        ):
            self.protocol = data[6]
            return [self._std_uid_frame()]
        return []

    def power_cycle(self) -> None:
        """電源再投入(テスト・GUI 用): 保留中のボーレート・プロトコルを適用し、
        パラメータ(type 18 の書込。type 22 の後でも永続対象外の 0x70xx)を失い、
        停止(Reset)・故障なし・主動上報 OFF から始める。

        CAN ID は type 7 で変えたものを保持する(flash へ自動保存されるかは未確認 §8.3 の
        推定)。位置・温度などの物理状態は保つ。
        """
        with self._lock:
            self.active_protocol = self.protocol
            self.active_baud_code = self.baud_code
            self.params = dict(self.saved_params)
            self.active_report = False
            self.faults = 0
            self.warnings = 0
            self.feedback_faults = 0
            self._outbox.clear()
            self.mode = MotorMode.RESET
            self._reset_controller()
            self.torque = 0.0
            self.iq = 0.0
            self.velocity = 0.0
            self._mit = _MitCommand()
            self._loc_track = True
            self._sync_loc_ref()

    def _on_mit(self, t_ff_u16: int, data: bytes) -> list[CanFrame]:
        # type 1: t_ff は ID bit23..8、他は BE u16(§2.2)。ホスト ID は ID に無い → 直近の値
        r = self.profile.ranges
        p, v, kp, kd = struct.unpack(">HHHH", data)
        self._mit = _MitCommand(
            uint_to_float(p, *r.position, 16),
            uint_to_float(v, *r.velocity, 16),
            uint_to_float(kp, *r.kp, 16),
            uint_to_float(kd, *r.kd, 16),
            uint_to_float(t_ff_u16, *r.torque, 16),
        )
        return [self.feedback_frame()]

    def _on_enable(self, host: int) -> list[CanFrame]:
        # 故障が残っている間は有効化されない(推定)。応答の故障ビットで分かる
        if self.mode is MotorMode.RESET and self.faults == 0:
            self._set_mode(MotorMode.MOTOR)
        return [self.feedback_frame(host)]

    def _on_stop(self, host: int, data: bytes) -> list[CanFrame]:
        if data[0] == 0x00 and data[1] == 0xC4:
            # バージョン読出(§2.6)。停止はしない(推定)。応答は type 2 の ID
            return [self._version_frame(host)]
        self._set_mode(MotorMode.RESET)
        if data[0] == 1:  # 故障クリア(§2.5)
            self.clear_faults()
        return [self.feedback_frame(host)]

    def _on_set_zero(self, host: int, data: bytes) -> list[CanFrame]:
        # type 6: CSP と運控でのみ有効、PP では無視(§2.7)。新ファーム動作(動かない)
        if data[0] == 1 and self.run_mode in (RunMode.OPERATION, RunMode.CSP):
            self.position = 0.0
            self.params[0x7016] = 0.0
            self._pp_pos = 0.0
        return [self.feedback_frame(host)]

    def _on_set_can_id(self, data2: int) -> list[CanFrame]:
        # data2 = 新 ID(bit23..16) | ホスト(bit15..8)(§2.8)。応答は新 ID の type 0
        new_id = (data2 >> 8) & 0xFF
        if 0 <= new_id <= 127:
            self.motor_id = new_id
        return [self._uid_frame()]

    def _on_param_read(self, host: int, data: bytes) -> list[CanFrame]:
        index = struct.unpack_from("<H", data)[0]
        zero = bytes(4)
        if index in HIDDEN_READABLE:
            return [self._param_frame(host, index, True, HIDDEN_READABLE[index])]
        if index not in self.params or index in UNREADABLE_INDICES:
            return [self._param_frame(host, index, False, zero)]
        dtype = self.profile.param(index).dtype
        return [self._param_frame(host, index, True, pack_value(dtype, self._read_value(index)))]

    def _on_param_write(self, host: int, data: bytes) -> list[CanFrame]:
        # 応答は常に type 2。不正な書込(未定義/読出専用/範囲外/走行中の run_mode)は無視(推定)
        index = struct.unpack_from("<H", data)[0]
        if index in self.params:
            pd = self.profile.param(index)
            if pd.writable:
                value = unpack_value(pd.dtype, data[4:8])
                if self._write_ok(pd.index, pd.key, pd.min, pd.max, value):
                    self.params[index] = value
                    self._after_param_write(index)
        return [self.feedback_frame(host)]

    def _write_ok(
        self, index: int, key: str, lo: float | None, hi: float | None, value: int | float
    ) -> bool:
        if isinstance(value, float) and not math.isfinite(value):
            return False
        if key not in _RANGE_EXEMPT:
            if lo is not None and value < lo:
                return False
            if hi is not None and value > hi:
                return False
        if index == 0x7005:  # run_mode: 4 は未定義、運転中は変更不可(§3.1)
            return int(value) in RUN_MODES and self.mode is MotorMode.RESET
        return True

    def _after_param_write(self, index: int) -> None:
        if index == 0x7005:
            self._reset_controller()
        elif index == 0x7016:
            self._loc_track = False  # 書込後は追従をやめる(停止中の保持は推定)
        elif index == 0x7026 and self.active_report:
            self._next_report = self.t + self.report_period

    def _on_active_report(self, host: int, data: bytes) -> list[CanFrame]:
        on = data[6] == 1
        if on and not self.active_report:
            self._next_report = self.t + self.report_period
        self.active_report = on
        if on:
            return []  # ON は上報フレームの開始そのものが応答(ON 直後の個別応答は未確認)
        return [self.feedback_frame(host)]  # OFF には type 2 が 1 回返る(§8.0)

    # ------------------------------------------------------------------
    # モード遷移・制御器
    # ------------------------------------------------------------------
    def _set_mode(self, mode: MotorMode) -> None:
        if mode is self.mode:
            return
        self.mode = mode
        self._reset_controller()
        if mode is MotorMode.RESET:
            self.torque = 0.0
            self.iq = 0.0
            self._mit = _MitCommand()
            self._loc_track = True
            self._sync_loc_ref()

    def _reset_controller(self) -> None:
        self._integ = 0.0
        self._v_ramp = self.velocity
        self._pp_pos = self.position
        self._pp_vel = self.velocity

    def _vel_loop(self, v_cmd: float, h: float, lim_i: float) -> float:
        """速度 PI(出力 iq [A])。積分は 1 ms あたり ki·err。

        出力が ±lim_i で飽和する向きには積分しない(アンチワインドアップ。戻る向きは許す)。
        """
        err = v_cmd - self.velocity
        p = self._limit("spd_kp") * err
        new_i = self._integ + self._limit("spd_ki") * err * h * 1000.0
        if abs(p + new_i) <= lim_i or abs(new_i) < abs(self._integ):
            self._integ = max(-lim_i, min(lim_i, new_i))
        return max(-lim_i, min(lim_i, p + self._integ))

    def _control(self, h: float) -> float:
        """モータ発生トルク [N·m]。無効時は 0。"""
        if self.mode is not MotorMode.MOTOR:
            return 0.0
        ph = self.physics
        lim = self.profile.limits
        tau_max = min(self._limit("limit_torque"), lim.torque)
        lim_i = min(self._limit("limit_cur"), lim.current)
        rm = self.run_mode
        iq: float | None = None
        tau = 0.0
        if rm == RunMode.OPERATION:
            c = self._mit
            p_ref = self._wrap_position() if self.mit_frame == "wrapped" else self.position
            tau = c.kp * (c.position - p_ref) + c.kd * (c.velocity - self.velocity) + c.torque
        elif rm == RunMode.CURRENT:
            iq = max(-lim.current, min(lim.current, float(self.params[0x7006])))
        elif rm == RunMode.VELOCITY:
            target = max(-lim.speed, min(lim.speed, float(self.params[0x700A])))
            step = self._limit("acc_rad") * h
            self._v_ramp += max(-step, min(step, target - self._v_ramp))
            iq = self._vel_loop(self._v_ramp, h, lim_i)
        elif rm == RunMode.CSP:
            limit_spd = min(self._limit("limit_spd"), lim.speed)
            v_cmd = self._limit("loc_kp") * (float(self.params[0x7016]) - self.position)
            iq = self._vel_loop(max(-limit_spd, min(limit_spd, v_cmd)), h, lim_i)
        elif rm == RunMode.PP:
            self._plan_pp(h)
            v_cmd = self._pp_vel + self._limit("loc_kp") * (self._pp_pos - self.position)
            iq = self._vel_loop(max(-lim.speed, min(lim.speed, v_cmd)), h, lim_i)
        if iq is not None:
            tau = ph.kt * iq
        tau = max(-tau_max, min(tau_max, tau))
        self.iq = tau / ph.kt
        return tau

    def _plan_pp(self, h: float) -> None:
        """PP の台形速度計画(vel_max / acc_set。減速も acc_set、dcc_set は使わない)。"""
        target = float(self.params[0x7016])
        vmax = max(0.0, self._limit("vel_max"))
        acc = max(1e-6, self._limit("acc_set"))
        rem = target - self._pp_pos
        if abs(rem) < 1e-6 and abs(self._pp_vel) <= acc * h * 2:
            self._pp_pos, self._pp_vel = target, 0.0
            return
        v_des = math.copysign(min(vmax, math.sqrt(2.0 * acc * abs(rem))), rem)
        dv = acc * h
        self._pp_vel += max(-dv, min(dv, v_des - self._pp_vel))
        self._pp_pos += self._pp_vel * h

    # ------------------------------------------------------------------
    # 時間発展
    # ------------------------------------------------------------------
    def step(self, dt: float) -> list[CanFrame]:
        """`dt` 秒進める。主動上報フレームと(注入された)type 21 フレームを返す。"""
        out: list[CanFrame] = []
        if dt < 0:
            raise ValueError("dt < 0")
        with self._lock:
            ph = self.physics
            lim = self.profile.limits
            n = max(1, math.ceil(dt / ph.substep)) if dt > 0 else 0
            h = dt / n if n else 0.0
            for _ in range(n):
                tau_m = self._control(h)
                self.torque = tau_m
                net = tau_m + self.ext_torque - ph.damping * self.velocity
                if ph.coulomb:
                    net -= ph.coulomb * math.tanh(self.velocity / 0.01)
                self.velocity += net / ph.inertia * h
                self.velocity = max(-lim.speed, min(lim.speed, self.velocity))
                self.position += self.velocity * h
            self.t += dt
            if self.mode is MotorMode.RESET and self._loc_track:
                self._sync_loc_ref()
            self._thermal(dt)
            self._check_can_timeout()
            out.extend(self._outbox)
            self._outbox.clear()
            guard = 0
            while self.active_report and self.t + 1e-9 >= self._next_report and guard < 1000:
                out.append(self.feedback_frame(comm_type=self.report_comm_type))
                self._next_report += self.report_period
                guard += 1
        return out

    def _thermal(self, dt: float) -> None:
        ph = self.physics
        d = ph.heat_coeff * self.iq**2 - (self.temperature - ph.ambient) / ph.thermal_tau
        self.temperature += d * dt
        limit = self.profile.limits.temp_fault
        if self.temperature >= limit and not self.faults & 1:
            self.inject_fault(0)

    def _check_can_timeout(self) -> None:
        # canTimeout: 20000 = 1 s(50 µs/count)、0 = 無効(§5.2)。受信途絶で停止
        count = int(self.params[0x7028])
        if count > 0 and self.mode is MotorMode.MOTOR:
            if self.t - self.last_rx_t > count * 50e-6:
                self._set_mode(MotorMode.RESET)
