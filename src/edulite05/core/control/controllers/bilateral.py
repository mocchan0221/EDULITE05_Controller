"""Bilateral: 2 台の運控(MIT)モータを結合するバイラテラル制御(phase4_5.md §O、run_mode=0)。

slots = ("leader", "follower")。位置差 d = follower - leader(多回転角)。

- symmetric: 各モータへ MIT 指令 p_set = 相手の位置 ± d、v_set = 相手の速度、kp = K、kd = D。
  - follower 目標 = leader + d、leader 目標 = follower - d
- master_slave: follower だけが上の指令で leader に追従。leader は kp = 0・軽い減衰のみ
  (t_ff = 0)で、手で動かせる。
- 力順送(`force_ff` ≠ 0): t_ff = 係数 × 相手のトルク推定(master_slave では follower のみ)。
- 位置差の扱い(`offset_mode`): keep = 開始時の差 d0(+ offset)を保持(急に引き寄せない)、
  zero = d を offset へ `converge_speed` 以下の速さで近づける。目標差の変更も同じ速度制限を受ける。

位置の差・目標はすべて多回転角(`position_multi`、mechPos と同じ座標)で計算する。折り返した
`state.position` は使わない(折り返しをまたいでも 8π の跳びが出ない)。`MitCmd.position` は多回転座標
で、MIT フレームの座標系(折り返し / 多回転)への変換と、±4π 付近で kp = 0 にする規則は
SafetyGuard(`clamp_mit`)が通常のクランプ(段階の kp/kd/トルク上限と最悪トルク制限)と一緒に行う。
"""

from __future__ import annotations

from typing import ClassVar

from edulite05.core.control.commands import Command, HoldCmd, MitCmd
from edulite05.core.control.controller import Controller
from edulite05.core.control.params import BilateralParams
from edulite05.core.device.state import MotorState
from edulite05.core.protocol.types import RunMode

_MODES = ("symmetric", "master_slave")
_OFFSET_MODES = ("keep", "zero")


class Bilateral(Controller):
    name: ClassVar[str] = "bilateral"
    label: ClassVar[str] = "controller.bilateral"
    run_mode: ClassVar[RunMode] = RunMode.OPERATION
    slots: ClassVar[tuple[str, ...]] = ("leader", "follower")
    Params: ClassVar[type] = BilateralParams

    def __init__(self, params: BilateralParams | None = None) -> None:
        super().__init__(params)
        self._check(self._params)
        self._d0 = 0.0  # 開始時の位置差(follower - leader)
        self._d = 0.0  # 現在の目標位置差
        self._ready = False

    @staticmethod
    def _check(p: BilateralParams) -> None:
        if p.mode not in _MODES:
            raise ValueError(f"未対応の mode です: {p.mode!r}")
        if p.offset_mode not in _OFFSET_MODES:
            raise ValueError(f"未対応の offset_mode です: {p.offset_mode!r}")

    def update_params(self, params: BilateralParams) -> None:
        self._check(self._copy(params))  # 型検査(TypeError)→ 値検査(ValueError)
        super().update_params(params)

    @property
    def offset_now(self) -> float:
        """現在の目標位置差(follower - leader)[rad]。"""
        return self._d

    @staticmethod
    def _fed(states: dict[str, MotorState]) -> bool:
        return all(s.last_rx is not None for s in states.values())

    def _latch(self, states: dict[str, MotorState]) -> None:
        self._d0 = states["follower"].position_multi - states["leader"].position_multi
        self._d = self._d0
        self._ready = True

    def on_start(self, states: dict[str, MotorState]) -> None:
        self._ready = False
        if self._fed(states):  # フィードバック未受信なら最初の有効な step で記録する
            self._latch(states)

    def _target_offset(self, p: BilateralParams) -> float:
        return self._d0 + p.offset if p.offset_mode == "keep" else p.offset

    def step(self, t: float, dt: float, states: dict[str, MotorState]) -> dict[str, Command]:
        p: BilateralParams = self.params
        if not self._fed(states):
            return {}  # 相手の位置が不明な間は何も送らない(0 へ引き寄せない)
        if not self._ready:
            self._latch(states)
        lead, fol = states["leader"], states["follower"]

        # 目標位置差を速度制限付きで更新
        err = self._target_offset(p) - self._d
        lim = max(0.0, p.converge_speed) * max(0.0, dt)
        self._d += max(-lim, min(lim, err))
        d = self._d

        ff_f = p.force_ff * lead.torque
        follower = MitCmd(
            position=lead.position_multi + d,
            velocity=lead.velocity,
            kp=p.stiffness,
            kd=p.damping,
            torque=ff_f,
        )
        if p.mode == "master_slave":
            leader = MitCmd(
                position=lead.position_multi, velocity=0.0, kp=0.0, kd=p.leader_damping, torque=0.0
            )
        else:
            leader = MitCmd(
                position=fol.position_multi - d,
                velocity=fol.velocity,
                kp=p.stiffness,
                kd=p.damping,
                torque=p.force_ff * fol.torque,
            )
        return {"leader": leader, "follower": follower}

    def on_stop(self, states: dict[str, MotorState]) -> dict[str, Command]:
        self._ready = False
        return {"leader": HoldCmd(), "follower": HoldCmd()}
