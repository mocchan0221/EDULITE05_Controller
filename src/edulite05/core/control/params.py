"""各制御モードのパラメータ定義(ロジック無しの dataclass のみ)。

Phase 2 の Controller 実装と、GUI の自動フォーム(`app/ui/param_row.py` の `ParamForm`)が共有する。
フィールドの metadata(フォーム生成用):

- ``label``   : 表示名の tr キー
- ``unit``    : 単位文字列
- ``min`` / ``max`` : 範囲(SI 単位)
- ``step``    : スピンボックスの刻み
- ``decimals``: 小数桁数
- ``limit``   : 出力段階の上限キー("torque" / "current" / "speed" / "kp" / "kd")。
                指定があると、現在の出力段階の上限で範囲が動的に制限される
- ``neg_limit``: 負側の下限を出力段階の上限キー("kd_negative_max")の符号反転で動的に制限する
                (min が負のフィールド用。指定が無ければ min のまま)
- ``hint``    : フォームのツールチップに足す補足の tr キー
- ``choices`` : 文字列フィールドの選択肢 ``((値, tr キー), ...)``

既定値・範囲の出典:
- 範囲: el05_protocol.md §1.5 / §3(P ±12.57 rad、V ±50 rad/s、T ±6 N·m、Kp 0–500、Kd 0–5、I ±11 A)
- MIT の既定値は公式 MotorStudio(motorstudio_features.md §3)に合わせ全て 0
- 電流制限・速度の既定値は公式の危険な既定値(速度モード 27 A 等)を採らず、
  出力段階 LOW の上限(architecture.md §4.3.1: 1.0 A / 5 rad/s)以下とする
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# el05_protocol.md §1.5 のレンジ(Phase 1 でプロファイル TOML に移す)
_P = 12.57
_V = 50.0
_T = 6.0
_I = 11.0


def _f(
    default: Any,
    label: str,
    unit: str = "",
    *,
    min: float | None = None,
    max: float | None = None,
    step: float = 0.01,
    decimals: int = 3,
    limit: str | None = None,
    neg_limit: str | None = None,
    hint: str | None = None,
    choices: tuple[tuple[str, str], ...] | None = None,
) -> Any:
    """フォーム生成用 metadata 付きの dataclass フィールド。"""
    meta: dict[str, Any] = {"label": label, "unit": unit, "step": step, "decimals": decimals}
    if min is not None:
        meta["min"] = min
    if max is not None:
        meta["max"] = max
    if limit is not None:
        meta["limit"] = limit
    if neg_limit is not None:
        meta["neg_limit"] = neg_limit
    if hint is not None:
        meta["hint"] = hint
    if choices is not None:
        meta["choices"] = choices
    return field(default=default, metadata=meta)


param_field = _f  # プラグインが Params を作るときの公開名(plugin_guide.md)

TARGET_CHOICES: tuple[tuple[str, str], ...] = (
    ("position", "target.position"),
    ("velocity", "target.velocity"),
    ("current", "target.current"),
    ("torque", "target.torque"),
)


@dataclass(slots=True)
class MitParams:
    """運控(MIT)モード: t_ref = Kp(p_set-p) + Kd(v_set-v) + t_ff(el05_protocol.md §2.2)。"""

    position: float = _f(0.0, "param.position", "rad", min=-_P, max=_P)
    velocity: float = _f(0.0, "param.velocity", "rad/s", min=-_V, max=_V, limit="speed")
    kp: float = _f(0.0, "param.kp", "", min=0.0, max=500.0, step=0.1, decimals=2, limit="kp")
    # kd の負側(PC 側で計算する負の減衰)の下限は出力段階の `kd_negative_max`(neg_limit)
    kd: float = _f(
        0.0, "param.kd", "", min=-5.0, max=5.0, step=0.01, decimals=3, limit="kd",
        neg_limit="kd_negative_max", hint="param.host_side_tip",
    )
    torque_ff: float = _f(0.0, "param.torque_ff", "N·m", min=-_T, max=_T, limit="torque")
    # 摩擦補償(PC 側): t_ff += friction_comp·tanh(v / v_eps)。上限は出力段階の `friction_comp_max`
    friction_comp: float = _f(
        0.0, "param.friction_comp", "N·m", min=0.0, max=_T, step=0.005, decimals=3,
        limit="friction_comp_max", hint="param.host_side_tip",
    )


@dataclass(slots=True)
class VelocityParams:
    """速度モード(run_mode=2): spd_ref / limit_cur / acc_rad(el05_protocol.md §3.3)。"""

    velocity: float = _f(0.0, "param.velocity", "rad/s", min=-_V, max=_V, limit="speed")
    # 公式既定 27 A は採らない。LOW 段階の上限(1.0 A)以下
    current_limit: float = _f(
        1.0, "param.current_limit", "A", min=0.0, max=_I, step=0.05, decimals=2, limit="current"
    )
    accel: float = _f(20.0, "param.accel", "rad/s²", min=0.0, max=150.0, step=1.0, decimals=1)


@dataclass(slots=True)
class CurrentParams:
    """電流モード(run_mode=3): iq_ref(el05_protocol.md §3.3)。"""

    iq: float = _f(0.0, "param.iq", "A", min=-_I, max=_I, step=0.05, decimals=3, limit="current")


@dataclass(slots=True)
class CspParams:
    """位置モード CSP(run_mode=5): limit_spd / loc_ref。"""

    position: float = _f(0.0, "param.position", "rad", min=-_P, max=_P)
    speed_limit: float = _f(
        2.0, "param.speed_limit", "rad/s", min=0.0, max=_V, step=0.1, decimals=2, limit="speed"
    )


@dataclass(slots=True)
class PpParams:
    """位置モード PP(run_mode=1): loc_ref / vel_max / acc_set。"""

    position: float = _f(0.0, "param.position", "rad", min=-_P, max=_P)
    velocity: float = _f(
        2.0, "param.velocity_max", "rad/s", min=0.0, max=_V, step=0.1, decimals=2, limit="speed"
    )
    accel: float = _f(10.0, "param.accel", "rad/s²", min=0.0, max=100.0, step=1.0, decimals=1)


@dataclass(slots=True)
class JogParams:
    """JOG(押している間だけ回転)。公式は 1 rad/s 固定だが速度・加速度・電流制限を設定可。"""

    speed: float = _f(
        1.0, "param.jog_speed", "rad/s", min=0.0, max=_V, step=0.1, decimals=2, limit="speed"
    )
    accel: float = _f(20.0, "param.accel", "rad/s²", min=0.0, max=150.0, step=1.0, decimals=1)
    current_limit: float = _f(
        1.0, "param.current_limit", "A", min=0.0, max=_I, step=0.05, decimals=2, limit="current"
    )


@dataclass(slots=True)
class SineParams:
    """試験信号: 正弦波(対象量に重畳)。"""

    target: str = _f("velocity", "param.target", choices=TARGET_CHOICES)
    amplitude: float = _f(0.5, "param.amplitude", "", min=0.0, max=_P, step=0.05, decimals=3)
    frequency: float = _f(1.0, "param.frequency", "Hz", min=0.01, max=20.0, step=0.1, decimals=2)
    offset: float = _f(0.0, "param.offset", "", min=-_P, max=_P, step=0.05, decimals=3)


@dataclass(slots=True)
class StepParams:
    """試験信号: ステップ(low と high を交互に出力)。"""

    target: str = _f("velocity", "param.target", choices=TARGET_CHOICES)
    low: float = _f(0.0, "param.step_low", "", min=-_P, max=_P, step=0.05, decimals=3)
    high: float = _f(0.5, "param.step_high", "", min=-_P, max=_P, step=0.05, decimals=3)
    period: float = _f(2.0, "param.step_period", "s", min=0.1, max=60.0, step=0.1, decimals=2)


@dataclass(slots=True)
class ZeroReturnParams:
    """0 rad へ戻る(CSP)。UI のモード一覧には出さないため PARAMS_BY_CONTROLLER には入れない。"""

    speed_limit: float = _f(
        1.0, "param.speed_limit", "rad/s", min=0.0, max=_V, step=0.1, decimals=2, limit="speed"
    )


BILATERAL_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    ("symmetric", "bilateral.symmetric"),
    ("master_slave", "bilateral.master_slave"),
)
BILATERAL_OFFSET_CHOICES: tuple[tuple[str, str], ...] = (
    ("keep", "bilateral.offset_keep"),
    ("zero", "bilateral.offset_zero"),
)


@dataclass(slots=True)
class BilateralParams:
    """バイラテラル制御(位置対称 / マスタスレーブ)。2 台(leader / follower)を MIT で結合する。

    各モータの MIT 指令: p_set = 相手の位置 ± 位置差、v_set = 相手の速度、kp = K、kd = D。
    位置差 d = (follower - leader)。`offset_mode`:
      - keep: 開始時の位置差 d0 を保持する(+ `offset`)。急に引き寄せない
      - zero: d を `offset`(既定 0)へ `converge_speed` 以下の速さで徐々に近づける
    master_slave では follower だけが leader に追従し、leader は kp = 0・軽い減衰
    (`leader_damping`)のみで手で動かせる。
    """

    mode: str = _f("symmetric", "param.bilateral_mode", choices=BILATERAL_MODE_CHOICES)
    stiffness: float = _f(
        5.0, "param.stiffness", "N·m/rad", min=0.0, max=500.0, step=0.1, decimals=2, limit="kp"
    )
    damping: float = _f(0.2, "param.damping", "N·m·s/rad", min=0.0, max=5.0, limit="kd")
    offset_mode: str = _f("keep", "param.offset_mode", choices=BILATERAL_OFFSET_CHOICES)
    # keep: 開始時の位置差への加算、zero: 収束先の位置差
    offset: float = _f(0.0, "param.offset", "rad", min=-_P, max=_P, step=0.05, decimals=3)
    converge_speed: float = _f(
        0.5, "param.converge_speed", "rad/s", min=0.0, max=_V, step=0.1, decimals=2, limit="speed"
    )
    # 力順送: t_ff = 係数 × 相手のトルク推定(0 = 無効)
    force_ff: float = _f(0.0, "param.force_ff", "", min=-2.0, max=2.0, step=0.05, decimals=2)
    leader_damping: float = _f(
        0.05, "param.leader_damping", "N·m·s/rad", min=0.0, max=5.0, limit="kd"
    )


# コントローラ名 → Params クラス(GUI のタブ生成・Bridge の start_controller で共通利用)
PARAMS_BY_CONTROLLER: dict[str, type] = {
    "mit": MitParams,
    "velocity": VelocityParams,
    "current": CurrentParams,
    "csp": CspParams,
    "pp": PpParams,
    "jog": JogParams,
    "sine": SineParams,
    "step": StepParams,
}


def register_params(name: str, cls: type) -> None:
    """プラグイン用: コントローラ名 → Params を `PARAMS_BY_CONTROLLER` に足す(自動フォームの元)。"""
    old = PARAMS_BY_CONTROLLER.get(name)
    if old is not None and old is not cls:
        raise ValueError(f"Params {name!r} は登録済みです({old.__name__})")
    PARAMS_BY_CONTROLLER[name] = cls


def unregister_params(name: str) -> None:
    """`register_params` で足したものを外す(組込みの 8 種は外せない)。"""
    if name in _BUILTIN_PARAMS:
        raise ValueError(f"組込みの Params は外せません: {name!r}")
    PARAMS_BY_CONTROLLER.pop(name, None)


_BUILTIN_PARAMS: frozenset[str] = frozenset(PARAMS_BY_CONTROLLER)
