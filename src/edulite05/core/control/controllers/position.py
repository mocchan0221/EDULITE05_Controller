"""Csp / Pp: 位置モード(run_mode=5 / 1)。

CSP は loc_ref を逐次送信し limit_spd で速度制限、PP は loc_ref + vel_max + acc_set
(el05_protocol.md §3.3)。停止は現在位置保持(HoldCmd)。
"""

from __future__ import annotations

from typing import ClassVar

from edulite05.core.control.commands import Command, HoldCmd, SetpointCmd
from edulite05.core.control.controller import Controller
from edulite05.core.control.params import CspParams, PpParams
from edulite05.core.device.state import MotorState
from edulite05.core.protocol.types import RunMode


class Csp(Controller):
    name: ClassVar[str] = "csp"
    label: ClassVar[str] = "controller.csp"
    run_mode: ClassVar[RunMode] = RunMode.CSP
    Params: ClassVar[type] = CspParams

    def step(self, t: float, dt: float, states: dict[str, MotorState]) -> dict[str, Command]:
        p: CspParams = self.params
        return {"motor": SetpointCmd("position", p.position, speed_limit=p.speed_limit)}

    def on_stop(self, states: dict[str, MotorState]) -> dict[str, Command]:
        return {"motor": HoldCmd()}


class Pp(Controller):
    name: ClassVar[str] = "pp"
    label: ClassVar[str] = "controller.pp"
    run_mode: ClassVar[RunMode] = RunMode.PP
    Params: ClassVar[type] = PpParams

    def step(self, t: float, dt: float, states: dict[str, MotorState]) -> dict[str, Command]:
        p: PpParams = self.params
        # SetpointCmd.speed_limit は PP では vel_max(0x7024)、accel は acc_set(0x7025)へ書かれる
        return {
            "motor": SetpointCmd("position", p.position, speed_limit=p.velocity, accel=p.accel)
        }

    def on_stop(self, states: dict[str, MotorState]) -> dict[str, Command]:
        return {"motor": HoldCmd()}
