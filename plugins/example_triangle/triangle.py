"""三角波の試験信号 Controller(サンプル)。コア側だけで完結し、Qt を使わない。

組込みの `SineTest` と同じく、対象の量(位置・速度・電流・トルク)に波形を出す。
波形の組立・対象 → run_mode・位置の相対化・停止時の保持は `SignalController`(基底)が行い、
このクラスは「時刻 t から値を作る」部分(`value_at`)と Params だけを書く。

指令は `SetpointCmd` / `MitCmd` を返すだけで、送信前に必ず `ControlLoop` が `SafetyGuard` を通す
(出力段階 LOW/HIGH の上限で切られる)。ここから CAN フレームを直接送ってはいけない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

from edulite05.core.control.commands import Command
from edulite05.core.control.controllers.tests import SignalController
from edulite05.core.control.params import TARGET_CHOICES, param_field
from edulite05.core.device.state import MotorState
from edulite05.core.protocol.types import RunMode

_P = 12.57  # 位置の範囲 [rad](el05_protocol.md §1.5)。段階の上限は SafetyGuard が掛ける


@dataclass(slots=True)
class TriangleParams:
    """三角波: offset + amplitude × 三角波(周波数 frequency)。

    フィールドの metadata(`param_field`)から、運転ページの入力フォームが自動で作られる。
    ラベルは `tr` のキーか、そのままの文字列。
    """

    target: str = param_field("velocity", "param.target", choices=TARGET_CHOICES)
    amplitude: float = param_field(
        0.5, "param.amplitude", "", min=0.0, max=_P, step=0.05, decimals=3
    )
    frequency: float = param_field(
        0.5, "param.frequency", "Hz", min=0.01, max=20.0, step=0.1, decimals=2
    )
    offset: float = param_field(0.0, "param.offset", "", min=-_P, max=_P, step=0.05, decimals=3)


class TriangleTest(SignalController):
    """offset + amplitude · tri(2π · frequency · t)。t = 0 は offset から上り始める。"""

    name: ClassVar[str] = "triangle"
    label: ClassVar[str] = "三角波"  # tr キー or そのままの文字列
    run_mode: ClassVar[RunMode] = RunMode.VELOCITY  # 既定。実際は target で決まる
    Params: ClassVar[type] = TriangleParams

    @staticmethod
    def value_at(p: TriangleParams, t: float) -> float:
        # asin(sin x) は振幅 π/2 の三角波。2/π を掛けて ±1 に揃える
        tri = (2.0 / math.pi) * math.asin(math.sin(2.0 * math.pi * p.frequency * t))
        return p.offset + p.amplitude * tri

    def _speed_limit(self, p: TriangleParams) -> float:  # type: ignore[override]
        # 位置が目標のときの CSP 速度上限: 最大傾き 4 f A の 1.5 倍(下限 0.5 rad/s)
        return min(50.0, max(0.5, 1.5 * 4.0 * p.frequency * abs(p.amplitude)))

    def step(self, t: float, dt: float, states: dict[str, MotorState]) -> dict[str, Command]:
        p: TriangleParams = self.params  # step の先頭で 1 回だけ読む(周期の途中で値が変わらない)
        return {"motor": self._command(self.value_at(p, t), p, states["motor"])}
