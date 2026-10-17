"""Velocity: 速度モード(run_mode=2)。spd_ref / limit_cur / acc_rad。"""

from __future__ import annotations

from typing import ClassVar

from edulite05.core.control.commands import Command, SetpointCmd
from edulite05.core.control.controller import Controller
from edulite05.core.control.params import VelocityParams
from edulite05.core.device.state import MotorState
from edulite05.core.protocol.types import RunMode


class Velocity(Controller):
    name: ClassVar[str] = "velocity"
    label: ClassVar[str] = "controller.velocity"
    run_mode: ClassVar[RunMode] = RunMode.VELOCITY
    Params: ClassVar[type] = VelocityParams

    @staticmethod
    def _cmd(velocity: float, p: VelocityParams) -> SetpointCmd:
        return SetpointCmd("velocity", velocity, current_limit=p.current_limit, accel=p.accel)

    def step(self, t: float, dt: float, states: dict[str, MotorState]) -> dict[str, Command]:
        p: VelocityParams = self.params
        return {"motor": self._cmd(p.velocity, p)}

    def on_stop(self, states: dict[str, MotorState]) -> dict[str, Command]:
        return {"motor": self._cmd(0.0, self.params)}
