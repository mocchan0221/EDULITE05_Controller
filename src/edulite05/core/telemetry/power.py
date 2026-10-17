"""消費電力の概算(Qt 非依存の純関数。profiles の `[power_estimate]` の係数を使う)。

バス電流は測っていないので、フィードバックのトルク τ と速度 ω から次のように見積もる。
**係数は未較正**(profiles/el05.toml の注記、open_questions.md Q-P7)。目安として使う:

    P_mech = τ·ω                     出力側の機械出力(符号つき。ブレーキ中は負)
    P_cu   = 1.5·R_ph·(|τ|/Kt)²      巻線の銅損(3 相ピーク表記: 1.5·R·I_pk²)
    P_base = 定数                    制御回路などの待機消費
    P_in   = P_mech + P_cu + P_base  入力電力。負なら回生(電源へ戻る/抵抗で消費)

`LowPass` は表示のちらつきを抑える 1 次ローパス(約 0.3 s)。時刻は呼び出し側の秒。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from edulite05.core.device.profile import PowerEstimateSpec

SMOOTH_TAU_S = 0.3  # 表示用ローパスの時定数 [s]


@dataclass(frozen=True, slots=True)
class PowerEstimate:
    """1 時点の電力の見積り [W]。"""

    p_mech: float  # τ·ω(符号つき)
    p_cu: float  # 銅損(0 以上)
    p_base: float  # 待機消費(定数)
    p_in: float  # 合計(負 = 回生)

    @property
    def regenerating(self) -> bool:
        return self.p_in < 0.0


def estimate_power(torque: float, velocity: float, spec: PowerEstimateSpec) -> PowerEstimate:
    """トルク [N·m]・速度 [rad/s] から電力を見積もる。非有限値は 0 として扱う。"""
    tau = torque if math.isfinite(torque) else 0.0
    w = velocity if math.isfinite(velocity) else 0.0
    p_mech = tau * w
    i_pk = abs(tau) / spec.kt
    p_cu = 1.5 * spec.r_phase * i_pk * i_pk
    return PowerEstimate(p_mech, p_cu, spec.p_base, p_mech + p_cu + spec.p_base)


class LowPass:
    """1 次ローパス `y += (x - y)·(1 - exp(-dt/τ))`。最初の値はそのまま採る。"""

    __slots__ = ("tau", "_y", "_t")

    def __init__(self, tau: float = SMOOTH_TAU_S) -> None:
        if tau <= 0.0:
            raise ValueError("tau は正である必要があります")
        self.tau = tau
        self._y: float | None = None
        self._t: float | None = None

    @property
    def value(self) -> float | None:
        return self._y

    def reset(self) -> None:
        self._y = None
        self._t = None

    def update(self, t: float, x: float) -> float:
        if self._y is None or self._t is None:
            self._y = x
        else:
            dt = max(0.0, t - self._t)
            self._y += (x - self._y) * (1.0 - math.exp(-dt / self.tau))
        self._t = t
        return self._y


def smooth_estimate(
    filters: tuple[LowPass, LowPass, LowPass], t: float, est: PowerEstimate
) -> PowerEstimate:
    """内訳(機械・銅損・待機)ごとにローパスをかけ、合計は平滑後の内訳の和にする
    (表示の内訳と合計が食い違わない)。"""
    pm = filters[0].update(t, est.p_mech)
    pc = filters[1].update(t, est.p_cu)
    pb = filters[2].update(t, est.p_base)
    return PowerEstimate(pm, pc, pb, pm + pc + pb)
