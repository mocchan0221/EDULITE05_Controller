"""Controller 基底クラス(phase2.md §1.3, architecture.md §4.5)。

Controller は「モータ指令を作る」だけで、SafetyGuard・エンコード・送信は `ControlLoop` が行う。
`step()` は ControlLoop のスレッドから呼ばれる(UI スレッドからは呼ばない)。

- ``states`` / 戻り値のキーは **スロット名**(`slots`。単一モータは ``"motor"``)。
- ``t`` は **attach からの経過秒**(最初の step が 0)、``dt`` は前回の step からの実経過秒
  (最初の step は公称周期)。
- `update_params` は別スレッド(UI)から呼んでよい: Params を複製して参照を 1 回で差し替える。
  `step` は先頭で ``p = self.params`` と 1 回だけ読むこと(周期の途中で値が変わらないように)。
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from edulite05.core.control.commands import Command
from edulite05.core.device.state import MotorState
from edulite05.core.protocol.types import RunMode


class Controller(ABC):
    name: ClassVar[str]
    label: ClassVar[str]  # tr キー
    # 必要な run_mode(SineTest / StepTest は対象に応じてインスタンス側で決まる)
    run_mode: ClassVar[RunMode]
    slots: ClassVar[tuple[str, ...]] = ("motor",)
    Params: ClassVar[type]  # core/control/params.py の dataclass

    def __init__(self, params: Any = None) -> None:
        self._params: Any = self._copy(params if params is not None else self.Params())

    def _copy(self, params: Any) -> Any:
        if not isinstance(params, self.Params):
            raise TypeError(f"{type(self).__name__} の Params は {self.Params.__name__} です")
        return dataclasses.replace(params)

    @property
    def params(self) -> Any:
        """現在の Params(読み取り専用の参照。書き換えず `update_params` を使う)。"""
        return self._params

    def update_params(self, params: Any) -> None:
        """運転中の目標変更。スレッド安全(複製して参照を差し替える)。"""
        self._params = self._copy(params)

    def on_start(self, states: dict[str, MotorState]) -> None:  # noqa: B027 - 任意の拡張点
        """attach 時に 1 回(最初の step の前)。"""

    @abstractmethod
    def step(
        self, t: float, dt: float, states: dict[str, MotorState]
    ) -> dict[str, Command]:
        """1 周期分の指令をスロットごとに返す(返さないスロットは何も送らない)。"""

    @abstractmethod
    def on_stop(self, states: dict[str, MotorState]) -> dict[str, Command]:
        """運転停止(detach)時に 1 回送る指令(速度 0 / 現在位置保持 など)。"""
