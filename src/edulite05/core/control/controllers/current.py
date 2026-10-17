"""Current: 電流モード(run_mode=3)。iq_ref。"""

from __future__ import annotations

from typing import ClassVar

from edulite05.core.control.commands import Command, SetpointCmd
from edulite05.core.control.controller import Controller
from edulite05.core.control.params import CurrentParams
from edulite05.core.device.state import MotorState
from edulite05.core.protocol.types import RunMode


class Current(Controller):
    name: ClassVar[str] = "current"
    label: ClassVar[str] = "controller.current"
    run_mode: ClassVar[RunMode] = RunMode.CURRENT
    Params: ClassVar[type] = CurrentParams

    def step(self, t: float, dt: float, states: dict[str, MotorState]) -> dict[str, Command]:
        p: CurrentParams = self.params
        return {"motor": SetpointCmd("current", p.iq)}

    def on_stop(self, states: dict[str, MotorState]) -> dict[str, Command]:
        return {"motor": SetpointCmd("current", 0.0)}
