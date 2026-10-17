"""位置目標の基準(1 回転内 / 多回転)の変換(純関数。Qt 非依存)。

位置モード(CSP/PP)・MIT の位置目標は多回転座標(`MotorState.position_multi`)で送る。
JOG などで多回転位置が大きくなる(例 400 rad)と、目標を多回転値で入力するのは使いにくい。
そこで目標を「1 回転内の角度 θ ∈ [-π, π)」で指定し、**現在位置から最短で到達できる多回転側の
等価角**へ変換する: `p_target = p_multi + wrap(θ - p_multi)`。コントローラ・SafetyGuard には
従来どおり多回転の目標を渡す。
"""

from __future__ import annotations

import math

TWO_PI = 2.0 * math.pi

# 目標の基準(UI の選択肢と永続化キー)
REF_WITHIN_TURN = "turn"  # 1 回転内(最短)。既定
REF_MULTI_TURN = "multi"  # 多回転
TARGET_REFS: tuple[str, ...] = (REF_WITHIN_TURN, REF_MULTI_TURN)
DEFAULT_TARGET_REF = REF_WITHIN_TURN


def wrap_to_pi(x: float) -> float:
    """角度を [-π, π) へ折り返す。"""
    return (x + math.pi) % TWO_PI - math.pi


def nearest_target(theta: float, p_multi: float) -> float:
    """1 回転内の角度 `theta` [rad] に対する、現在の多回転位置 `p_multi` から最短の多回転目標。

    `theta` 自体は何回転でもよい(内部で θ - p を折り返す)。結果は `p_multi` から
    [-π, π) 以内の距離にあり、`wrap_to_pi(結果) == wrap_to_pi(theta)`。
    """
    return p_multi + wrap_to_pi(theta - p_multi)


def resolve_target(value: float, p_multi: float, ref: str) -> float:
    """目標の基準 `ref` に従って入力値を多回転目標へ変換する。

    `REF_WITHIN_TURN`: `value` は 1 回転内の角度 [rad]。`REF_MULTI_TURN`: そのまま。
    """
    if ref == REF_WITHIN_TURN:
        return nearest_target(value, p_multi)
    if ref == REF_MULTI_TURN:
        return value
    raise ValueError(f"未知の目標の基準: {ref!r}")
