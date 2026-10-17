"""試験信号: SineTest / StepTest(対象量に波形を出す)。

対象(`target`)と run_mode:
  position → CSP(5) / velocity → 速度(2) / current → 電流(3) / torque → 運控(0, kp=kd=0 の t_ff)

- **position の波形は on_start 時点の位置からの相対**(base + offset + 波形)。絶対位置へ
  急に飛ばないため。座標は多回転角(`MotorState.position_multi`)。
- target は運転中(on_start 後)は変更できない(run_mode が変わるため)。ValueError。
- run_mode はインスタンスごとに決まる(ClassVar の値は既定の velocity 側)。
"""

from __future__ import annotations

import math
from typing import ClassVar

from edulite05.core.control.commands import Command, HoldCmd, MitCmd, SetpointCmd
from edulite05.core.control.controller import Controller
from edulite05.core.control.params import SineParams, StepParams
from edulite05.core.device.state import MotorState
from edulite05.core.protocol.types import RunMode

TARGET_RUN_MODE: dict[str, RunMode] = {
    "position": RunMode.CSP,
    "velocity": RunMode.VELOCITY,
    "current": RunMode.CURRENT,
    "torque": RunMode.OPERATION,
}


class _SignalController(Controller):
    """波形を作る Controller の共通部(対象 → run_mode / 指令の組立)。"""

    def __init__(self, params: SineParams | StepParams | None = None) -> None:
        super().__init__(params)
        self.run_mode = self._mode_of(self._params.target)  # type: ignore[misc]
        self._base = 0.0
        self._started = False

    @staticmethod
    def _mode_of(target: str) -> RunMode:
        try:
            return TARGET_RUN_MODE[target]
        except KeyError:
            raise ValueError(f"未対応の target です: {target!r}") from None

    def update_params(self, params: SineParams | StepParams) -> None:
        mode = self._mode_of(params.target)
        if self._started and params.target != self._params.target:
            raise ValueError("target は運転中に変更できません")
        super().update_params(params)
        if not self._started:
            self.run_mode = mode  # type: ignore[misc]

    def on_start(self, states: dict[str, MotorState]) -> None:
        self._base = states["motor"].position_multi
        self._started = True

    def _speed_limit(self, p: SineParams | StepParams) -> float:
        raise NotImplementedError

    def _command(self, value: float, p: SineParams | StepParams, state: MotorState) -> Command:
        target = p.target
        if target == "position":
            return SetpointCmd(
                "position", self._base + value, speed_limit=self._speed_limit(p)
            )
        if target == "velocity":
            return SetpointCmd("velocity", value)
        if target == "current":
            return SetpointCmd("current", value)
        return MitCmd(position=state.position_multi, velocity=0.0, kp=0.0, kd=0.0, torque=value)

    def on_stop(self, states: dict[str, MotorState]) -> dict[str, Command]:
        self._started = False
        target = self._params.target
        if target == "velocity":
            return {"motor": SetpointCmd("velocity", 0.0)}
        if target == "current":
            return {"motor": SetpointCmd("current", 0.0)}
        return {"motor": HoldCmd()}


# プラグインが波形 Controller を作るときの基底(対象 → run_mode・位置の相対化・指令の組立・停止時の
# 保持をまとめて引き受ける)。`plugins/example_triangle/` が使い方の例(plugin_guide.md)。
SignalController = _SignalController


class SineTest(_SignalController):
    """offset + amplitude · sin(2π · frequency · t)。"""

    name: ClassVar[str] = "sine"
    label: ClassVar[str] = "controller.sine"
    run_mode: ClassVar[RunMode] = RunMode.VELOCITY
    Params: ClassVar[type] = SineParams

    @staticmethod
    def value_at(p: SineParams, t: float) -> float:
        return p.offset + p.amplitude * math.sin(2.0 * math.pi * p.frequency * t)

    def _speed_limit(self, p: SineParams) -> float:  # type: ignore[override]
        # 最大速度 2π f A の 1.5 倍(下限 0.5 rad/s)。段階の上限は SafetyGuard が掛ける
        return min(50.0, max(0.5, 1.5 * 2.0 * math.pi * p.frequency * abs(p.amplitude)))

    def step(self, t: float, dt: float, states: dict[str, MotorState]) -> dict[str, Command]:
        p: SineParams = self.params
        return {"motor": self._command(self.value_at(p, t), p, states["motor"])}


class StepTest(_SignalController):
    """low と high を交互に出す。1 周期 = `period` 秒(前半 low、後半 high、t=0 は low)。"""

    name: ClassVar[str] = "step"
    label: ClassVar[str] = "controller.step"
    run_mode: ClassVar[RunMode] = RunMode.VELOCITY
    Params: ClassVar[type] = StepParams

    @staticmethod
    def value_at(p: StepParams, t: float) -> float:
        phase = (t / p.period) % 1.0 if p.period > 0 else 0.0
        return p.high if phase >= 0.5 else p.low

    def _speed_limit(self, p: StepParams) -> float:  # type: ignore[override]
        # ステップ幅を半周期の 1/4 で移動できる速さ(下限 1 rad/s)
        return min(50.0, max(1.0, 8.0 * abs(p.high - p.low) / max(p.period, 1e-3)))

    def step(self, t: float, dt: float, states: dict[str, MotorState]) -> dict[str, Command]:
        p: StepParams = self.params
        return {"motor": self._command(self.value_at(p, t), p, states["motor"])}
