"""モータプロファイル(`profiles/*.toml`)の読込(phase1.md §1.5)。

モータ固有の数値(レンジ・上限・出力段階・パラメータ定義)はここ経由でのみ得る。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from edulite05.core.errors import ProfileError, ProtocolError
from edulite05.core.protocol.types import DType, Ranges

PROFILES_ENV = "EDULITE05_PROFILES_DIR"
GROUPS: tuple[str, ...] = ("制御ゲイン", "制限", "指令", "状態", "設定")
ACCESS: tuple[str, ...] = ("rw", "r")


@dataclass(frozen=True, slots=True)
class ParamDef:
    index: int
    key: str
    label: str
    group: str
    dtype: DType
    unit: str
    min: float | None
    max: float | None
    default: int | float
    access: str  # "rw" / "r"
    description: str

    @property
    def writable(self) -> bool:
        return self.access == "rw"


@dataclass(frozen=True, slots=True)
class TierLimits:
    torque: float
    current: float
    speed: float
    kp: float
    kd: float
    # MIT のホスト側項の上限(SafetyGuard。無い段階/プロファイルは 0 = 負の kd・摩擦補償なし)
    kd_negative_max: float = 0.0  # kd の負側の上限(大きさ)
    friction_comp_max: float = 0.0  # 摩擦補償 [N·m]


@dataclass(frozen=True, slots=True)
class MitHostSpec:
    """MIT のホスト側項(負の kd・摩擦補償)と暴走ガードの定数(`[mit_host]`)。

    - `friction_v_eps` [rad/s]: 摩擦補償 `t_ff += c·tanh(v / v_eps)` の幅
    - `runaway_on` / `runaway_off`: 段階の速度上限に対する比。|v| > on で暴走ガード開始、
      |v| < off で解除(ヒステリシス)
    - `guard_kd`: ガード中にフレームの kd を最低でもこの値にする
    """

    friction_v_eps: float = 0.05
    runaway_on: float = 0.8
    runaway_off: float = 0.4
    guard_kd: float = 0.1


@dataclass(frozen=True, slots=True)
class Limits:
    """物理上限。"""

    current: float
    speed: float
    torque: float
    temp_fault: float


@dataclass(frozen=True, slots=True)
class PowerEstimateSpec:
    """消費電力の概算の係数(`[power_estimate]`。**未較正**。core/telemetry/power.py)。"""

    kt: float = 0.65  # トルク定数 [N·m/A](実測。profiles/el05.toml)
    r_phase: float = 1.36  # 相抵抗 [Ω]
    p_base: float = 1.0  # 待機消費 [W]


MIT_FRAMES: tuple[str, ...] = ("unknown", "wrapped", "multi")


@dataclass(frozen=True, slots=True)
class PositionSpec:
    """位置の折り返しと MIT の p_set の座標系(el05_protocol.md §8.0、open_questions Q-P12)。

    - `wrap_half`: type 2 の位置が折り返す半幅 [rad](= 4π。周期 = 2·wrap_half)。
      mechPos(0x7019)は折り返さない多回転値。
    - `wrap_margin`: 多回転位置が `wrap_half - wrap_margin` を超える領域では、位置目標が
      表現できない / 曖昧なので MIT の kp を 0 にする(SafetyGuard)。
    - `mit_frame`: MIT の p_set をモータ内部で何と比べるか。
      "unknown"(未確認。両方の解釈で最悪を見積もる)/ "wrapped"(折り返し位置)/ "multi"(多回転)。
    """

    wrap_half: float
    wrap_margin: float
    mit_frame: str

    @property
    def period(self) -> float:
        return 2.0 * self.wrap_half

    @property
    def wrap_limit(self) -> float:
        """多回転位置の絶対値がこれを超えたら位置目標を使わない(kp = 0)領域。"""
        return self.wrap_half - self.wrap_margin


@dataclass(frozen=True, slots=True)
class MotorProfile:
    model: str
    name: str
    ranges: Ranges
    limits: Limits
    power_tiers: dict[str, TierLimits]
    params: tuple[ParamDef, ...]
    position: PositionSpec
    mit_host: MitHostSpec = field(default_factory=MitHostSpec)
    power_estimate: PowerEstimateSpec = field(default_factory=PowerEstimateSpec)
    _by_index: dict[int, ParamDef] = field(init=False, repr=False, compare=False)
    _by_key: dict[str, ParamDef] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_index", {p.index: p for p in self.params})
        object.__setattr__(self, "_by_key", {p.key: p for p in self.params})

    # -- 読込 -------------------------------------------------------------
    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> MotorProfile:
        p = Path(path)
        try:
            with p.open("rb") as f:
                raw = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise ProfileError(f"プロファイルを読めません: {p}: {e}") from e
        try:
            return _build(raw)
        except (KeyError, TypeError, ValueError, ProtocolError) as e:
            raise ProfileError(f"プロファイルが不正です: {p}: {e!r}") from e

    @classmethod
    def load_builtin(cls, name: str = "el05") -> MotorProfile:
        return cls.load(find_profile_path(name))

    # -- 参照 -------------------------------------------------------------
    def param(self, index_or_key: int | str) -> ParamDef:
        """index(int)または key(str)で検索。無ければ `KeyError`。"""
        table: dict[Any, ParamDef] = (
            self._by_index if isinstance(index_or_key, int) else self._by_key
        )
        try:
            return table[index_or_key]
        except KeyError:
            raise KeyError(f"未定義のパラメータ: {index_or_key!r}") from None

    def param_dtypes(self) -> dict[int, DType]:
        return {p.index: p.dtype for p in self.params}

    def tier(self, name: str) -> TierLimits:
        """出力段階("low" / "high")の上限。無ければ `KeyError`。"""
        try:
            return self.power_tiers[name]
        except KeyError:
            raise KeyError(f"未定義の出力段階: {name!r}") from None


def _pair(v: Any) -> tuple[float, float]:
    lo, hi = v
    if not float(lo) < float(hi):
        raise ValueError(f"範囲が不正: {v!r}")
    return float(lo), float(hi)


def _build_param(row: dict[str, Any]) -> ParamDef:
    dtype = DType.from_name(row["dtype"])
    group = row["group"]
    if group not in GROUPS:
        raise ValueError(f"未知のグループ: {group!r}")
    access = row["access"]
    if access not in ACCESS:
        raise ValueError(f"未知の access: {access!r}")

    def conv(v: Any) -> Any:
        if v is None:
            return None
        return float(v) if dtype is DType.FLOAT else int(v)

    lo, hi = conv(row.get("min")), conv(row.get("max"))
    default = conv(row["default"])
    if lo is not None and hi is not None and lo > hi:
        raise ValueError(f"min > max: {row['key']}")
    return ParamDef(
        index=int(row["index"]),
        key=str(row["key"]),
        label=str(row["label"]),
        group=group,
        dtype=dtype,
        unit=str(row.get("unit", "")),
        min=lo,
        max=hi,
        default=default,
        access=access,
        description=str(row.get("description", "")),
    )


def _build(raw: dict[str, Any]) -> MotorProfile:
    motor = raw["motor"]
    r = raw["ranges"]
    ranges = Ranges(
        position=_pair(r["position"]),
        velocity=_pair(r["velocity"]),
        torque=_pair(r["torque"]),
        kp=_pair(r["kp"]),
        kd=_pair(r["kd"]),
    )
    lim = raw["limits"]
    limits = Limits(
        current=float(lim["current"]),
        speed=float(lim["speed"]),
        torque=float(lim["torque"]),
        temp_fault=float(lim["temp_fault"]),
    )
    tiers = {
        name: TierLimits(
            **{k: float(t[k]) for k in ("torque", "current", "speed", "kp", "kd")},
            **{k: float(t[k]) for k in ("kd_negative_max", "friction_comp_max") if k in t},
        )
        for name, t in raw["power_tiers"].items()
    }
    for name, t in tiers.items():
        if t.kd_negative_max < 0.0 or t.friction_comp_max < 0.0:
            raise ValueError(f"power_tiers.{name}: 負の kd・摩擦補償の上限は 0 以上です")
    mh = raw.get("mit_host", {})
    mit_host = MitHostSpec(**{k: float(mh[k]) for k in MitHostSpec.__dataclass_fields__ if k in mh})
    if not (mit_host.friction_v_eps > 0.0 and 0.0 < mit_host.runaway_off < mit_host.runaway_on):
        raise ValueError("mit_host: friction_v_eps > 0 かつ 0 < runaway_off < runaway_on です")
    if mit_host.guard_kd < 0.0:
        raise ValueError("mit_host.guard_kd は 0 以上です")
    pos = raw["position"]
    position = PositionSpec(
        wrap_half=float(pos["wrap_half"]),
        wrap_margin=float(pos["wrap_margin"]),
        mit_frame=str(pos["mit_position_frame"]),
    )
    if not position.wrap_half > 0.0:
        raise ValueError("position.wrap_half は正です")
    if not 0.0 <= position.wrap_margin < position.wrap_half:
        raise ValueError("position.wrap_margin は 0 以上 wrap_half 未満です")
    if position.mit_frame not in MIT_FRAMES:
        raise ValueError(
            f"position.mit_position_frame は {MIT_FRAMES} のいずれか: {position.mit_frame!r}"
        )
    pe = raw.get("power_estimate", {})
    power_estimate = PowerEstimateSpec(
        **{k: float(pe[k]) for k in PowerEstimateSpec.__dataclass_fields__ if k in pe}
    )
    pe_s = power_estimate
    if not (pe_s.kt > 0.0 and pe_s.r_phase >= 0.0 and pe_s.p_base >= 0.0):
        raise ValueError("power_estimate: kt > 0、r_phase・p_base は 0 以上です")
    params = tuple(_build_param(row) for row in raw.get("params", []))
    if len({p.index for p in params}) != len(params):
        raise ValueError("index が重複しています")
    if len({p.key for p in params}) != len(params):
        raise ValueError("key が重複しています")
    return MotorProfile(
        model=str(motor["model"]),
        name=str(motor["name"]),
        ranges=ranges,
        limits=limits,
        power_tiers=tiers,
        params=params,
        position=position,
        mit_host=mit_host,
        power_estimate=power_estimate,
    )


_EXTRA_PROFILE_DIRS: list[Path] = []


def add_profile_dir(path: str | os.PathLike[str]) -> None:
    """プラグイン用: `<name>.toml` を探す場所を足す(組込みの探索より後。同名は組込みが優先)。"""
    p = Path(path)
    if p not in _EXTRA_PROFILE_DIRS:
        _EXTRA_PROFILE_DIRS.append(p)


def remove_profile_dir(path: str | os.PathLike[str]) -> None:
    p = Path(path)
    if p in _EXTRA_PROFILE_DIRS:
        _EXTRA_PROFILE_DIRS.remove(p)


def find_profile_path(name: str) -> Path:
    """`profiles/<name>.toml` を探す。

    探索順: 環境変数 EDULITE05_PROFILES_DIR → このファイルから親方向(リポジトリルート)→
    カレントディレクトリから親方向。
    """
    fname = f"{name}.toml"
    roots: list[Path] = []
    env = os.environ.get(PROFILES_ENV)
    if env:
        roots.append(Path(env))
    for start in (Path(__file__).resolve().parent, Path.cwd().resolve()):
        roots.extend(d / "profiles" for d in (start, *start.parents))
    roots.extend(_EXTRA_PROFILE_DIRS)
    for d in roots:
        cand = d / fname
        if cand.is_file():
            return cand
    raise ProfileError(f"プロファイルが見つかりません: {fname}")
