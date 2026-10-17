"""MitDirect: 運控(MIT)モードの 5 値を直接指令する(run_mode=0)。"""

from __future__ import annotations

from typing import ClassVar

from edulite05.core.control.commands import Command, HoldCmd, MitCmd
from edulite05.core.control.controller import Controller
from edulite05.core.control.params import MitParams
from edulite05.core.device.state import MotorState
from edulite05.core.protocol.types import RunMode


class MitDirect(Controller):
    name: ClassVar[str] = "mit"
    label: ClassVar[str] = "controller.mit"
    run_mode: ClassVar[RunMode] = RunMode.OPERATION
    Params: ClassVar[type] = MitParams

    def step(self, t: float, dt: float, states: dict[str, MotorState]) -> dict[str, Command]:
        p: MitParams = self.params
        return {"motor": MitCmd(p.position, p.velocity, p.kp, p.kd, p.torque_ff, p.friction_comp)}

    def on_stop(self, states: dict[str, MotorState]) -> dict[str, Command]:
        return {"motor": HoldCmd()}
