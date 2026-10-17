"""運転ページの組込み制御モードを `UiRegistry.add_mode_spec` で登録する(ui_design.md §5.1)。

以前 `app/pages/operate/modes.py` に書いていた `MAIN_MODES` / `OTHER_MODES` /
`TARGET_FIELDS` / `LIMIT_FIELDS` / `LABEL_OVERRIDES` と同じ内容。
モード → コントローラ(`ControllerForm`)の対応:
  JOG → jog / 速度 → velocity / 位置 → csp・pp / MIT → mit / 電流 → current /
  試験信号 → sine・step / 2台連動 → (フォーム無し。MIT 2 台を `LinkSession` が動かす)
"""

from __future__ import annotations

from typing import ClassVar

from edulite05.core.control.params import (
    CspParams,
    CurrentParams,
    JogParams,
    MitParams,
    PpParams,
    SineParams,
    StepParams,
    VelocityParams,
)
from edulite05.plugin_api import (
    GROUP_MAIN,
    GROUP_OTHER,
    ControllerForm,
    ModeSpec,
    Plugin,
    PluginContext,
)


def _mode(
    mid: str, group: str, forms: tuple[ControllerForm, ...], *, title: str | None = None,
    short: str = "", min_motors: int = 1,
) -> ModeSpec:
    return ModeSpec(
        id=mid, title=title or f"operate.mode.{mid}", group=group, controllers=forms,
        short_title=short, description=f"operate.mode_desc.{mid}",
        info=f"operate.mode_info.{mid}", min_motors=min_motors,
    )


def builtin_mode_specs() -> list[ModeSpec]:
    """組込みモードの一覧(並びは画面の並び: 本体 4 つ → 「その他 ▾」3 つ)。"""
    return [
        _mode("jog", GROUP_MAIN, (
            ControllerForm("jog", JogParams, ("speed",), ("current_limit", "accel")),
        )),
        _mode("velocity", GROUP_MAIN, (
            ControllerForm("velocity", VelocityParams, ("velocity",), ("current_limit", "accel")),
        )),
        _mode("position", GROUP_MAIN, (
            ControllerForm("csp", CspParams, ("position",), ("speed_limit",)),
            ControllerForm("pp", PpParams, ("position",), ("velocity", "accel")),
        )),
        _mode("mit", GROUP_MAIN, (
            ControllerForm(
                "mit", MitParams, ("position", "kp", "kd"),
                ("velocity", "torque_ff", "friction_comp"),
                {"velocity": "operate.target.mit_velocity"},
            ),
        )),
        _mode("current", GROUP_OTHER, (
            ControllerForm("current", CurrentParams, ("iq",), ()),
        ), short="operate.mode.current_short"),
        _mode("test", GROUP_OTHER, (
            ControllerForm("sine", SineParams, ("target", "amplitude", "frequency", "offset")),
            ControllerForm("step", StepParams, ("target", "low", "high", "period")),
        )),
        _mode("link", GROUP_OTHER, (), min_motors=2),  # 2 台以上のときだけ選べる
    ]


class BuiltinOperateModes(Plugin):
    id: ClassVar[str] = "edulite05.builtin.operate_modes"
    name: ClassVar[str] = "組込みの運転モード"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "JOG・速度・位置・MIT・電流・試験信号・2台連動"

    def activate(self, ctx: PluginContext) -> None:
        if ctx.ui is None:
            return
        for spec in builtin_mode_specs():
            ctx.ui.add_mode_spec(spec)
