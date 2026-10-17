"""ZeroReturn: CSP で 0 rad へ戻る(run_mode=5)。

loc_ref = 0 を送り続ける。速度は limit_spd(Params.speed_limit)で制限されるので
緩やかに戻る(公式 A 版の Zero Position 相当)。停止は現在位置保持。
"""

from __future__ import annotations

from typing import ClassVar

from edulite05.core.control.commands import Command, HoldCmd, SetpointCmd
from edulite05.core.control.controller import Controller
from edulite05.core.control.params import ZeroReturnParams
from edulite05.core.device.state import MotorState
from edulite05.core.protocol.types import RunMode


class ZeroReturn(Controller):
    name: ClassVar[str] = "zero_return"
    label: ClassVar[str] = "controller.zero_return"
    run_mode: ClassVar[RunMode] = RunMode.CSP
    Params: ClassVar[type] = ZeroReturnParams

    def step(self, t: float, dt: float, states: dict[str, MotorState]) -> dict[str, Command]:
        p: ZeroReturnParams = self.params
        return {"motor": SetpointCmd("position", 0.0, speed_limit=p.speed_limit)}

    def on_stop(self, states: dict[str, MotorState]) -> dict[str, Command]:
        return {"motor": HoldCmd()}
