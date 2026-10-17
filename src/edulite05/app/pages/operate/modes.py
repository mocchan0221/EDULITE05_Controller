"""インスペクタの制御モード(ui_design.md §5.1)とコントローラ・run_mode の対応。Qt を使わない。

モード(UI の言葉)→ コントローラ(`PARAMS_BY_CONTROLLER` のキー)→ run_mode(0x7005)。
  JOG → jog(2) / 速度 → velocity(2) / 位置 → csp(5) か pp(1) / MIT → mit(0) /
  電流 → current(3) / 試験信号 → sine か step(対象の量で 5・2・3・0) / 2台連動 → mit(0)
run_mode の値は core の `Controller.run_mode` が正(UI に別の表を持たない)。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from edulite05.app.viewmodels import RUN_MODE_KEYS
from edulite05.builtin.operate_modes import builtin_mode_specs
from edulite05.core.control.controllers import CONTROLLERS
from edulite05.core.control.controllers.tests import TARGET_RUN_MODE
from edulite05.core.control.params import PARAMS_BY_CONTROLLER
from edulite05.plugin_api import GROUP_MAIN, ModeSpec

# ---- モードの一覧と各コントローラのフォーム ----
# 内容は組込みプラグイン(`edulite05.builtin.operate_modes`)と、他のプラグインが
# `UiRegistry.add_mode` で登録したもの。`install()` が登録簿から作り直す(起動時にシェルが呼ぶ)。
# 登録簿に触れない単体利用(テスト・ギャラリー)では、import 時に組込みだけで初期化する。
MAIN_MODES: tuple[str, ...] = ()  # 本体のセグメント(JOG / 速度 / 位置 / MIT)
OTHER_MODES: tuple[str, ...] = ()  # 「その他 ▾」のメニュー(組込み + プラグイン)
ALL_MODES: tuple[str, ...] = ()
MODE_SPECS: dict[str, ModeSpec] = {}

# 目標として大きく出す項目 / 「制限と加速度」に畳む項目(Params dataclass のフィールド名)
TARGET_FIELDS: dict[str, tuple[str, ...]] = {}
LIMIT_FIELDS: dict[str, tuple[str, ...]] = {}

# ラベルを言い換える項目: (コントローラ, フィールド) → tr キー
LABEL_OVERRIDES: dict[tuple[str, str], str] = {}


def install(specs: Sequence[ModeSpec]) -> None:
    """登録簿のモード一覧で、モード・フォームの対応表を作り直す(辞書は同じ物を更新する)。"""
    global MAIN_MODES, OTHER_MODES, ALL_MODES
    MODE_SPECS.clear()
    TARGET_FIELDS.clear()
    LIMIT_FIELDS.clear()
    LABEL_OVERRIDES.clear()
    for spec in specs:
        MODE_SPECS[spec.id] = spec
        for form in spec.controllers:
            TARGET_FIELDS[form.name] = tuple(form.target_fields)
            LIMIT_FIELDS[form.name] = tuple(form.limit_fields)
            for fname, key in form.label_overrides.items():
                LABEL_OVERRIDES[(form.name, fname)] = key
    MAIN_MODES = tuple(s.id for s in specs if s.group == GROUP_MAIN)
    OTHER_MODES = tuple(s.id for s in specs if s.group != GROUP_MAIN)
    ALL_MODES = (*MAIN_MODES, *OTHER_MODES)


def other_menu(motor_count: int) -> list[tuple[str, list[tuple[str, str]]]]:
    """「その他 ▾」メニューの内容: [(グループ名, [(モード ID, 名前の tr キー/文字列)])]。

    グループは登録順。台数が足りないモード(2台連動)は含めない。
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    for mid in OTHER_MODES:
        spec = MODE_SPECS[mid]
        if motor_count >= spec.min_motors:
            groups.setdefault(spec.group, []).append((mid, spec.title))
    return list(groups.items())


def short_title(mode: str) -> str:
    """選択中に「その他 ▾」の位置へ出す名前(tr キー/文字列)。"""
    spec = MODE_SPECS.get(mode)
    if spec is None:
        return mode
    return spec.short_title or spec.title


def mode_of_controller(controller: str) -> str | None:
    """コントローラ名(実行中のもの)を持つモード ID。"""
    for spec in MODE_SPECS.values():
        if any(f.name == controller for f in spec.controllers):
            return spec.id
    return None


# 送信周期(既定 5 ms = 200 Hz。architecture.md §4.5)
PERIODS_MS: tuple[int, ...] = (1, 2, 5, 10, 20)
DEFAULT_PERIOD_MS = 5

# run_mode は core のコントローラ(`Controller.run_mode`)を正とする(UI に別の表を持たない)。
# 試験信号は対象の量で決まる(torque は運控 = run_mode 0。core の `TARGET_RUN_MODE`)。
_TEST_RUN_MODE: dict[str, int] = {t: int(m) for t, m in TARGET_RUN_MODE.items()}
RUN_MODE_OF: dict[str, int] = {
    name: int(CONTROLLERS[name].run_mode)
    for name in ("mit", "pp", "velocity", "jog", "current", "csp")
}


@dataclass(slots=True)
class MotorModel:
    """モータごとに覚えておく入力(モード・各コントローラの目標・周期)。"""

    mode: str = "velocity"
    pos_style: str = "csp"  # 位置の動き方: csp(追従)/ pp(台形)
    wave: str = "sine"  # 試験信号の波形: sine / step
    period_ms: int = DEFAULT_PERIOD_MS
    continuous: bool = True  # MIT の連続/単発
    follower: int | None = None  # 2台連動で追従させるモータ
    link_kp: float = 8.0
    # 位置モードの目標(「1 回転内(最短)」の基準のときの角度 θ [rad]。-π〜π)。
    # 多回転の目標は params["csp"/"pp"].position が持つ
    target_theta: float = 0.0
    engaged: bool = False  # 自分が有効化した(無効化するまでモードを固定する)
    engaged_at: float = 0.0
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, cls in PARAMS_BY_CONTROLLER.items():
            self.params.setdefault(name, cls())


def controller_of(model: MotorModel) -> str:
    """いまのモードで運転開始に使うコントローラ名。"""
    m = model.mode
    if m == "position":
        return model.pos_style
    if m == "test":
        return model.wave
    if m == "link":
        return "mit"
    if m in ("jog", "velocity", "mit", "current"):
        return m
    spec = MODE_SPECS.get(m)
    if spec is not None and spec.controllers and spec.owner not in BUILTIN_OWNERS:
        return spec.controllers[0].name  # プラグインのモード: 先頭のコントローラ
    return "velocity"


def run_mode_for(model: MotorModel) -> int:
    """有効化時に設定する run_mode(0x7005)。"""
    ctrl = controller_of(model)
    if ctrl in ("sine", "step"):
        target = str(getattr(model.params[ctrl], "target", "velocity"))
        return _TEST_RUN_MODE.get(target, 2)
    if ctrl in RUN_MODE_OF:
        return RUN_MODE_OF[ctrl]
    # プラグインのコントローラ: run_mode はインスタンス(Params の target 等)で決まることがある
    return int(CONTROLLERS[ctrl](copy_params(model, ctrl)).run_mode)


def mode_desc_key(model: MotorModel) -> str:
    """モードの 1 行説明の tr キー(位置は動き方で変わる)。"""
    if model.mode == "position":
        return f"operate.mode_desc.position_{model.pos_style}"
    spec = MODE_SPECS.get(model.mode)
    if spec is not None and spec.description:
        return spec.description  # プラグインのモードは文字列そのまま(tr が素通しにする)
    return f"operate.mode_desc.{model.mode}"


def mode_info_key(model: MotorModel) -> str:
    if model.mode == "position":
        return f"operate.mode_info.position_{model.pos_style}"
    spec = MODE_SPECS.get(model.mode)
    if spec is not None and spec.owner not in BUILTIN_OWNERS:
        return spec.info or spec.description
    return f"operate.mode_info.{model.mode}"


def apply_derived(model: MotorModel, controller: str | None, run_mode: int) -> None:
    """外部で有効化・運転されていたモータの状態から、表示するモードを決める。

    運転中は実行中のコントローラ名、待機中(保持)は run_mode から推定する。
    run_mode 2 は速度/JOG の区別がつかないため速度とする。
    """
    if controller == "jog":
        model.mode = "jog"
    elif controller == "velocity":
        model.mode = "velocity"
    elif controller in ("csp", "pp"):
        model.mode, model.pos_style = "position", controller
    elif controller == "mit":
        model.mode = "mit"
    elif controller == "current":
        model.mode = "current"
    elif controller in ("sine", "step"):
        model.mode, model.wave = "test", controller
    elif controller is not None and (plug := mode_of_controller(controller)) is not None:
        model.mode = plug  # プラグインのコントローラで運転中
    elif controller is None:
        key = RUN_MODE_KEYS.get(run_mode)
        if key == "mit":
            model.mode = "mit"
        elif key == "pp":
            model.mode, model.pos_style = "position", "pp"
        elif key == "csp":
            model.mode, model.pos_style = "position", "csp"
        elif key == "velocity":
            model.mode = "velocity"
        elif key == "current":
            model.mode = "current"


def copy_params(model: MotorModel, name: str) -> Any:
    return dataclasses.replace(model.params[name])


# 組込みの所有者 id(`edulite05.builtin.*`)。プラグインのモードと区別する
BUILTIN_OWNERS = frozenset({"edulite05.builtin.operate_modes", ""})

install(builtin_mode_specs())  # 単体利用の既定(シェルは起動時に登録簿の内容で install し直す)
