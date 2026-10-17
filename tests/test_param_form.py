"""ParamForm: limit 付きフィールドが出力段階の上限でクランプされる。"""

from __future__ import annotations

import dataclasses

import pytest

from edulite05.app.bridge import Bridge
from edulite05.app.ui.param_row import ParamForm, effective_range
from edulite05.app.viewmodels import PowerLimits
from edulite05.core.control.params import (
    PARAMS_BY_CONTROLLER,
    CurrentParams,
    JogParams,
    MitParams,
    SineParams,
    VelocityParams,
)

LOW = PowerLimits("low", torque=0.5, current=1.0, speed=5.0, kp=20.0, kd=1.0)
HIGH = PowerLimits("high", torque=6.0, current=11.0, speed=50.0, kp=500.0, kd=5.0)


class StubBridge(Bridge):
    """Bridge のうち ParamForm が使う部分だけのスタブ。"""

    def __init__(self, tier: str = "low") -> None:
        super().__init__()
        self._tier = tier

    def power_limits(self) -> PowerLimits:
        return LOW if self._tier == "low" else HIGH

    def power_tier(self) -> str:
        return self._tier

    def switch(self, tier: str) -> None:
        self._tier = tier
        self.power_tier_changed.emit(tier)


def test_effective_range_uses_limit() -> None:
    meta = dict(dataclasses.fields(MitParams)[4].metadata)  # torque_ff
    assert effective_range(meta, None) == (-6.0, 6.0)
    assert effective_range(meta, LOW) == (-0.5, 0.5)
    assert effective_range(meta, HIGH) == (-6.0, 6.0)


def test_limited_fields_clamped_to_low_tier(qtbot) -> None:
    bridge = StubBridge("low")
    form = ParamForm(MitParams, bridge)
    qtbot.addWidget(form)
    assert form.field_range("kp") == (0.0, 20.0)
    assert form.field_range("kd") == (0.0, 1.0)
    assert form.field_range("velocity") == (-5.0, 5.0)
    assert form.field_range("torque_ff") == (-0.5, 0.5)
    assert form.field_range("position") == pytest.approx((-12.57, 12.57))  # limit なし
    # LOW の上限を超える値は設定できずクランプされる
    form.set_value("kp", 300.0)
    assert form.value("kp") == 20.0
    form.set_value("torque_ff", -4.0)
    assert form.value("torque_ff") == -0.5


def test_tier_change_updates_range_and_clamps_value(qtbot) -> None:
    bridge = StubBridge("high")
    form = ParamForm(MitParams, bridge)
    qtbot.addWidget(form)
    assert form.field_range("kp") == (0.0, 500.0)
    form.set_value("kp", 300.0)
    form.set_value("velocity", -30.0)
    assert form.value("kp") == 300.0
    changes: list[str] = []
    form.changed.connect(lambda name, _v: changes.append(name))
    bridge.switch("low")  # 段階を下げると即座に範囲が狭まり、値がクランプされる
    assert form.field_range("kp") == (0.0, 20.0)
    assert form.value("kp") == 20.0
    assert form.value("velocity") == -5.0
    assert {"kp", "velocity"} <= set(changes)
    assert form.params().kp == 20.0
    bridge.switch("high")  # 上げても値は勝手に増えないが範囲は広がる
    assert form.field_range("kp") == (0.0, 500.0)
    assert form.value("kp") == 20.0


def test_reset_button_restores_default(qtbot) -> None:
    """↺ は「既定に戻す」(0 ではなく dataclass の既定値)。"""
    form = ParamForm(JogParams, StubBridge("high"))
    qtbot.addWidget(form)
    default = JogParams().speed
    form.set_value("speed", 4.0)
    assert form.value("speed") == 4.0
    form._rows["speed"].reset_btn.click()
    assert form.value("speed") == default


def test_clamp_by_tier_shows_inline_note(qtbot) -> None:
    """出力段階の上限で値が切られたら、行内に注記が出る(ui_design.md §5.1・§6)。"""
    bridge = StubBridge("high")
    form = ParamForm(VelocityParams(velocity=30.0), bridge)
    qtbot.addWidget(form)
    form.show()
    row = form._rows["velocity"]
    assert not row.note_label.isVisible()
    bridge.switch("low")
    assert form.value("velocity") == 5.0
    assert row.note_label.isVisible() and "5" in row.note_label.text()


def test_signals_and_choice_fields(qtbot) -> None:
    form = ParamForm(SineParams, StubBridge())
    qtbot.addWidget(form)
    assert form.value("target") == "velocity"
    got: list[object] = []
    form.params_changed.connect(got.append)
    form.set_value("amplitude", 2.0)
    assert got and got[-1].amplitude == 2.0
    combo = form._rows["target"]._combo
    combo.setCurrentIndex(combo.findData("position"))
    assert form.params().target == "position"


@pytest.mark.parametrize("name", sorted(PARAMS_BY_CONTROLLER))
def test_defaults_are_within_low_tier(name: str) -> None:
    """既定値は出力段階 LOW の上限以下(公式の危険な既定値 27 A 等を採らない)。"""
    p = PARAMS_BY_CONTROLLER[name]()
    for f in dataclasses.fields(p):
        limit = f.metadata.get("limit")
        if limit:
            assert abs(getattr(p, f.name)) <= getattr(LOW, limit), (name, f.name)


def test_velocity_current_limit_default_not_27a() -> None:
    assert VelocityParams().current_limit <= LOW.current
    assert CurrentParams().iq == 0.0
    assert JogParams().speed <= LOW.speed
