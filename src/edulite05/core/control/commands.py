"""モータ指令型(phase2.md §1.2)。

Controller.step() の戻り値であり、ControlLoop が SafetyGuard を通してフレームへ変換する。
すべて frozen dataclass。単位は SI(rad, rad/s, N·m, A)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SetpointKind = Literal["velocity", "current", "position"]

# 現在位置保持(HoldCmd)の既定ゲイン。実際の送出値は SafetyGuard が段階の上限でクランプする。
DEFAULT_HOLD_KP = 5.0
DEFAULT_HOLD_KD = 0.3


@dataclass(frozen=True, slots=True)
class MitCmd:
    """運控(MIT)モード指令 = type 1 の 5 値。"""

    # p_set [rad]。**多回転座標**(MotorState.position_multi / mechPos と同じ)。type 2 の折り返し位置
    # ではない。MIT フレームの座標系への変換は SafetyGuard.clamp_mit が行う(safety.py の説明)
    position: float = 0.0
    velocity: float = 0.0  # v_set [rad/s]
    kp: float = 0.0
    kd: float = 0.0
    torque: float = 0.0  # t_ff [N·m]
    # PC 側の項(SafetyGuard が t_ff へ畳み込み、フレームには載らない。safety.py の説明):
    #   kd < 0 は kd_fw = 0 と t_ff += kd·(v_set - v)、friction_comp は t_ff += c·tanh(v / v_eps)
    friction_comp: float = 0.0  # 摩擦補償 [N·m](0 以上)


@dataclass(frozen=True, slots=True)
class SetpointCmd:
    """速度 / 電流 / 位置(CSP・PP)モードの目標値 = type 18 の spd_ref / iq_ref / loc_ref。

    `speed_limit` / `current_limit` / `accel` は None なら書き換えない(変化したときだけ書く)。
    """

    kind: SetpointKind
    value: float
    speed_limit: float | None = None  # [rad/s]
    current_limit: float | None = None  # [A]
    accel: float | None = None  # [rad/s²]


@dataclass(frozen=True, slots=True)
class HoldCmd:
    """現在位置保持。MIT モードなら p_set=p(多回転座標)・v_set=0・t_ff=0、位置モードなら loc_ref=p。

    kp / kd が None なら既定値(`DEFAULT_HOLD_KP` / `DEFAULT_HOLD_KD`)。
    """

    kp: float | None = None
    kd: float | None = None

    def to_mit(self, position: float) -> MitCmd:
        """MIT モード用の指令へ展開する(position は多回転座標の現在位置)。"""
        return MitCmd(
            position=position,
            velocity=0.0,
            kp=DEFAULT_HOLD_KP if self.kp is None else self.kp,
            kd=DEFAULT_HOLD_KD if self.kd is None else self.kd,
            torque=0.0,
        )

    def to_setpoint(self, position: float) -> SetpointCmd:
        """位置モード(CSP/PP)用の指令へ展開する。"""
        return SetpointCmd("position", position)


Command = MitCmd | SetpointCmd | HoldCmd
