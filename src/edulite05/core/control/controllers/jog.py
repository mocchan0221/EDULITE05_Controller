"""Jog: 押している間だけ回転する(速度モード、run_mode=2)。

`set_direction(-1 | 0 | +1)` で ±speed / 0。方向の変更は UI スレッドから呼んでよい
(int の代入のみ)。加速度・電流制限は Params(公式は 1 rad/s 固定)。
"""

from __future__ import annotations

from typing import ClassVar

from edulite05.core.control.commands import Command, SetpointCmd
from edulite05.core.control.controller import Controller
from edulite05.core.control.params import JogParams
from edulite05.core.device.state import MotorState
from edulite05.core.protocol.types import RunMode


class Jog(Controller):
    name: ClassVar[str] = "jog"
    label: ClassVar[str] = "controller.jog"
    run_mode: ClassVar[RunMode] = RunMode.VELOCITY
    Params: ClassVar[type] = JogParams

    def __init__(self, params: JogParams | None = None) -> None:
        super().__init__(params)
        self._direction = 0

    @property
    def direction(self) -> int:
        return self._direction

    def set_direction(self, direction: int) -> None:
        """-1(逆)/ 0(停止)/ +1(正)。それ以外の値は符号に丸める。"""
        self._direction = (direction > 0) - (direction < 0)

    def _cmd(self, velocity: float, p: JogParams) -> SetpointCmd:
        return SetpointCmd(
            "velocity", velocity, current_limit=p.current_limit, accel=p.accel
        )

    def step(self, t: float, dt: float, states: dict[str, MotorState]) -> dict[str, Command]:
        p: JogParams = self.params
        return {"motor": self._cmd(self._direction * p.speed, p)}

    def on_stop(self, states: dict[str, MotorState]) -> dict[str, Command]:
        self._direction = 0
        return {"motor": self._cmd(0.0, self.params)}
