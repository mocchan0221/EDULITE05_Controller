"""phase4-fb-X の画面テスト: 位置の目標の基準・原点に設定・MIT の負の Kd と摩擦補償。

DemoBridge(実時間で動く)で、運転ページ(インスペクタ・読み値カード)を操作して確認する。
目標の変換そのものは tests/core/test_targets.py、SafetyGuard は tests/core/test_safety_mit_host.py。
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from operate_helpers import FakeShell, teardown
from PySide6.QtCore import QSettings

from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.i18n import tr
from edulite05.app.pages.operate import OperatePage
from edulite05.app.pages.operate.inspector import TARGET_REF_KEY
from edulite05.app.ui.readout import fmt_signed
from edulite05.app.ui.style import theme_manager
from edulite05.core.control.targets import nearest_target, wrap_to_pi

WAIT = 20000


class SettingsShell(FakeShell):
    """UI 状態の保存先(ini)を持つ疑似シェル。"""

    def __init__(self, ini) -> None:
        super().__init__()
        self._ini = str(ini)

    def settings(self) -> QSettings:
        return QSettings(self._ini, QSettings.Format.IniFormat)


def build(qtbot, tmp_path, ini=None, *, start_offline: bool = False):
    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0, start_offline=start_offline)
    shell = SettingsShell(ini or tmp_path / "ui_state.ini")
    page = OperatePage(bridge, shell, record_dir=tmp_path)
    qtbot.addWidget(page)
    page.resize(1464, 800)
    page.show()
    bridge.start()
    qtbot.waitExposed(page)
    qtbot.waitUntil(lambda: len(bridge.motors()) == 2, timeout=WAIT)
    return page, bridge, shell


@pytest.fixture(autouse=True)
def _theme():
    theme_manager().apply("dark")
    yield
    theme_manager().apply("dark")


@pytest.fixture
def env(qtbot, tmp_path):
    page, bridge, shell = build(qtbot, tmp_path)
    yield page, bridge, shell
    teardown(page, bridge)


def ui(bridge, mid: int = 1) -> str:
    s = bridge.states().get(mid)
    return s.ui_state if s else "none"


def ctrl_of(bridge, mid: int = 1):
    return bridge._motors[mid].ctrl  # noqa: SLF001


def set_theta(bridge, theta: float, mid: int = 1) -> None:
    bridge._motors[mid].theta = theta  # noqa: SLF001
    bridge._publish_states()  # noqa: SLF001


# ============================================================ 目標の基準(位置モード)
def test_target_ref_defaults_to_nearest_turn_and_shows_angle_row(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("position").click()
    assert insp.target_ref() == "turn"
    assert insp.ref_box.isVisible() and insp.turn_box.isVisible()
    assert not insp._target_boxes["csp"].isVisible()  # noqa: SLF001  多回転の位置欄は隠れる
    lo, hi = insp.turn_row.range()
    assert (lo, hi) == (pytest.approx(-math.pi), pytest.approx(math.pi))
    assert insp.ref_seg.current() == "turn"
    # 多回転にすると従来の位置欄(±4π)に戻る
    insp.set_target_ref("multi")
    assert not insp.turn_box.isVisible() and insp._target_boxes["csp"].isVisible()  # noqa: SLF001
    assert insp.forms["csp"].field_range("position") == (
        pytest.approx(-4 * math.pi, abs=0.01), pytest.approx(4 * math.pi, abs=0.01),
    )


def test_hint_shows_absolute_target_and_distance(env, qtbot) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("position").click()
    set_theta(bridge, 400.0)
    qtbot.waitUntil(lambda: bridge.states()[1].position_multi > 399.0, timeout=WAIT)
    insp.turn_row.set_value(1.0, emit=True)
    target = nearest_target(1.0, 400.0)
    expect = tr(
        "operate.target.turn_hint",
        target=fmt_signed(target, 2), delta=fmt_signed(target - 400.0, 2), unit="rad",
    )
    assert insp.target_hint_text() == expect
    assert expect.startswith("→ +") and abs(target - 400.0) <= math.pi
    # 動き出す前でも、位置が変われば結果の絶対位置も最短のまま追従する
    set_theta(bridge, 0.0)
    target0 = nearest_target(1.0, 0.0)
    assert target0 == pytest.approx(1.0)
    qtbot.waitUntil(lambda: fmt_signed(target0, 2) in insp.target_hint_text(), timeout=WAIT)


def test_start_sends_multi_turn_target_nearest_to_current_position(env, qtbot) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("position").click()
    set_theta(bridge, 400.0)
    insp.primary.click()  # 有効化(今の角度が目標になる)
    qtbot.waitUntil(lambda: ui(bridge) == "enabled", timeout=WAIT)
    insp.turn_row.set_value(1.0, emit=True)
    insp.primary.click()  # 運転開始
    qtbot.waitUntil(lambda: ui(bridge) == "running", timeout=WAIT)
    sent = ctrl_of(bridge).params.position
    assert abs(sent - 400.0) <= math.pi + 1e-6  # 多回転座標のまま、最短の等価角
    assert wrap_to_pi(sent) == pytest.approx(wrap_to_pi(1.0), abs=1e-6)
    assert sent == pytest.approx(nearest_target(1.0, 400.0), abs=1e-6)


def test_angle_edit_updates_running_target_live(env, qtbot) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("position").click()
    set_theta(bridge, 100.0)
    insp.primary.click()
    qtbot.waitUntil(lambda: ui(bridge) == "enabled", timeout=WAIT)
    insp.primary.click()
    qtbot.waitUntil(lambda: ui(bridge) == "running", timeout=WAIT)
    insp.turn_row.set_value(-2.0, emit=True)
    p = bridge.states()[1].position_multi
    sent = ctrl_of(bridge).params.position
    assert wrap_to_pi(sent) == pytest.approx(wrap_to_pi(-2.0), abs=1e-6)
    assert abs(sent - p) <= math.pi + 0.5
    assert fmt_signed(sent, 2) in insp.target_hint_text()  # 運転中は送っている目標を示す
    # 速度制限など別の欄を変えても、目標は動かない
    insp.forms["csp"].set_value("speed_limit", 1.5)
    assert ctrl_of(bridge).params.position == pytest.approx(sent)
    # 運転中は基準を切り替えられない
    assert not insp.ref_seg.item_enabled("multi")


def test_multi_turn_reference_keeps_current_behavior(env, qtbot) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("position").click()
    insp.set_target_ref("multi")
    insp.forms["csp"].set_value("position", 5.0)
    insp.primary.click()
    qtbot.waitUntil(lambda: ui(bridge) == "enabled", timeout=WAIT)
    insp.forms["csp"].set_value("position", 5.0)
    insp.primary.click()
    qtbot.waitUntil(lambda: ui(bridge) == "running", timeout=WAIT)
    assert ctrl_of(bridge).params.position == pytest.approx(5.0)
    assert insp.target_hint_text() == ""


def test_switching_reference_preserves_the_physical_target(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("position").click()
    set_theta(bridge, 7.0)
    insp.turn_row.set_value(0.5, emit=True)
    insp.set_target_ref("multi")  # 7.0 の近くの 0.5 の等価角が多回転の欄に入る
    assert insp.forms["csp"].value("position") == pytest.approx(nearest_target(0.5, 7.0), abs=0.01)
    insp.forms["csp"].set_value("position", -8.0)
    insp.set_target_ref("turn")
    assert insp.model().target_theta == pytest.approx(wrap_to_pi(-8.0), abs=1e-6)


def test_reference_is_persisted_and_restored(qtbot, tmp_path) -> None:
    ini = tmp_path / "ui_state.ini"
    page, bridge, _shell = build(qtbot, tmp_path, ini)
    try:
        assert page.inspector.target_ref() == "turn"
        page.inspector.mode_seg.button("position").click()
        page.inspector.ref_seg.button("multi").click()  # 画面の操作で切り替える
        assert page.inspector.target_ref() == "multi"
        assert QSettings(str(ini), QSettings.Format.IniFormat).value(
            TARGET_REF_KEY.format(mode="position")
        ) == "multi"
    finally:
        teardown(page, bridge)
    page2, bridge2, _ = build(qtbot, tmp_path, ini)
    try:
        assert page2.inspector.target_ref() == "multi"
        page2.inspector.mode_seg.button("position").click()
        assert not page2.inspector.turn_box.isVisible()
    finally:
        teardown(page2, bridge2)


def test_angle_row_follows_readout_unit_toggle(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("position").click()
    set_theta(bridge, 0.0)
    insp.turn_row.set_value(math.pi / 2, emit=True)
    page.strip.unit_toggle.set_unit("deg")
    assert insp.angle_unit() == "deg"
    assert insp.turn_row.range() == (-180.0, 180.0)
    assert insp.turn_row.value() == pytest.approx(90.0)
    assert insp.turn_row.unit_label.text() == "deg"
    assert insp.target_hint_text().endswith("deg)") and "deg へ" in insp.target_hint_text()
    insp.turn_row.set_value(-45.0, emit=True)
    assert insp.model().target_theta == pytest.approx(math.radians(-45.0))
    page.strip.unit_toggle.set_unit("rad")
    assert insp.turn_row.value() == pytest.approx(math.radians(-45.0))


# ============================================================ 原点に設定
def test_zero_button_state_follows_connection_and_estop(env, qtbot) -> None:
    page, bridge, _shell = env
    btn = page.strip.zero_btn
    assert btn.isEnabled()
    bridge.estop()
    qtbot.waitUntil(lambda: not btn.isEnabled(), timeout=WAIT)
    assert btn.toolTip() == tr("operate.zero.unavailable_estop")
    bridge.release_estop()
    qtbot.waitUntil(btn.isEnabled, timeout=WAIT)
    bridge.disconnect_adapter()
    qtbot.waitUntil(lambda: not btn.isEnabled(), timeout=WAIT)
    assert btn.toolTip() == tr("operate.zero.unavailable_offline")


def test_zero_on_stopped_motor_confirms_then_sets_zero(env, qtbot) -> None:
    page, bridge, shell = env
    set_theta(bridge, 400.0)
    qtbot.waitUntil(lambda: bridge.states()[1].position_multi > 399, timeout=WAIT)
    page.strip.zero_btn.clicked.emit()
    pop = page.zero_popover
    assert pop.isVisible() and not pop.warn_label.isVisible()  # 停止中: 無効化の予告は出さない
    assert tr("operate.zero.body") == "現在位置を 0 にします"
    pop.confirm_btn.click()
    qtbot.waitUntil(lambda: bool(shell.toasts), timeout=WAIT)
    text, kind = shell.toasts[-1]
    assert (text, kind) == (tr("operate.zero.done", id=1), "success")
    qtbot.waitUntil(lambda: abs(bridge.states()[1].position_multi) < 0.05, timeout=WAIT)
    assert not pop.isVisible()


def test_zero_while_running_stops_and_disables_first(env, qtbot) -> None:
    page, bridge, shell = env
    insp = page.inspector
    insp.mode_seg.button("velocity").click()
    insp.primary.click()
    qtbot.waitUntil(lambda: ui(bridge) == "enabled", timeout=WAIT)
    insp.primary.click()
    qtbot.waitUntil(lambda: ui(bridge) == "running", timeout=WAIT)
    insp.forms["velocity"].set_value("velocity", 2.0)
    qtbot.wait(300)
    page.strip.zero_btn.clicked.emit()
    pop = page.zero_popover
    assert pop.isVisible() and pop.warn_label.isVisible()  # 先に停止して無効化することを知らせる
    pop.confirm_btn.click()
    qtbot.waitUntil(lambda: bool(shell.toasts), timeout=WAIT)
    assert shell.toasts[-1] == (tr("operate.zero.done", id=1), "success")
    assert ui(bridge) == "disabled"
    assert abs(bridge.states()[1].position_multi) < 2.0  # 0 にした後、惰性で少し進むだけ


def test_zero_does_not_leave_a_jump_line_in_the_scope(env, qtbot) -> None:
    page, bridge, shell = env
    set_theta(bridge, 50.0)
    qtbot.wait(200)
    page.strip.zero_btn.clicked.emit()
    page.zero_popover.confirm_btn.click()
    qtbot.waitUntil(lambda: bool(shell.toasts), timeout=WAIT)
    qtbot.wait(300)
    _t, v = bridge.scope_data(["m1.pos"], 3.0)["m1.pos"]
    assert np.isnan(v).any(), "0 への跳びを結ぶ線を切る NaN が入っていない"
    finite = v[np.isfinite(v)]
    k = int(np.flatnonzero(~np.isfinite(v))[0])
    assert abs(v[k - 1]) > 40 and abs(finite[-1]) < 1.0  # 前は旧い座標、後は新しい座標


def test_zero_is_refused_when_estop_latched(env, qtbot) -> None:
    page, bridge, shell = env
    bridge.estop()
    page.strip.zero_btn.clicked.emit()  # 無効のボタンは押せないが、直接呼ばれても実行しない
    assert not page.zero_popover.isVisible()
    assert page.zero_flow.start(1) is False
    assert shell.toasts and shell.toasts[-1][1] == "caution"


# ============================================================ MIT: 負の Kd と摩擦補償
def test_mit_kd_allows_negative_range_with_zero_tick(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("mit").click()
    f = insp.forms["mit"]
    lo, hi = f.field_range("kd")
    assert lo == pytest.approx(-0.05) and hi == pytest.approx(1.0)  # LOW
    kd_row = f.row("kd")
    assert kd_row.slider.zero_tick()
    f.set_value("kd", -0.03)
    assert f.value("kd") == pytest.approx(-0.03)
    assert kd_row.note_label.isVisible()
    assert kd_row.note_label.text() == tr("operate.target.host_note")
    assert tr("param.host_side_tip") in kd_row.toolTip()
    # 下限を超える入力は LOW の上限に切られて注記が出る
    insp._on_field("mit", "kd", -0.5)  # noqa: SLF001
    assert f.value("kd") == pytest.approx(-0.05)
    assert bridge.set_power_tier("high")
    assert f.field_range("kd")[0] == pytest.approx(-0.2)
    bridge.set_power_tier("low")


def test_friction_comp_lives_in_the_limits_expander_with_help(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("mit").click()
    f = insp.forms["mit"]
    row = f.row("friction_comp")
    assert insp._limit_boxes["mit"].isAncestorOf(row)  # noqa: SLF001  制限と加速度の展開部
    assert row.label.text() == tr("param.friction_comp") == "摩擦補償"
    assert f.field_range("friction_comp") == (0.0, pytest.approx(0.1))
    assert tr("param.host_side_tip") in row.toolTip()
    f.set_value("friction_comp", 0.03)
    assert row.note_label.text() == tr("operate.target.host_note")


def test_preset_grid_is_tidy_and_zero_resistance_works(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("mit").click()
    page.show()
    names = list(insp.preset_buttons)
    assert names == ["free", "damper", "zero", "spring", "hold"]
    assert insp.preset_buttons["zero"].text() == "抵抗ゼロ"
    geo = {k: b.geometry() for k, b in insp.preset_buttons.items()}
    # 上段 3 つが同じ行、下段(ばね・保持)がその下
    assert geo["free"].y() == geo["damper"].y() == geo["zero"].y()
    assert geo["spring"].y() == geo["hold"].y() > geo["zero"].y()
    assert geo["hold"].right() == pytest.approx(geo["zero"].right(), abs=1)  # 右端がそろう
    insp.preset_buttons["zero"].click()
    f = insp.forms["mit"]
    assert (f.value("kp"), f.value("kd"), f.value("friction_comp")) == (0.0, 0.0, 0.03)


def test_demo_mit_negative_kd_runaway_guard_brakes(env, qtbot) -> None:
    """デモの代役ガードでも、負の Kd + 摩擦補償は暴走せず上限の 8 割付近で制動される。"""
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("mit").click()
    insp.primary.click()
    qtbot.waitUntil(lambda: ui(bridge) == "enabled", timeout=WAIT)
    f = insp.forms["mit"]
    f.set_value("kp", 0.0)
    f.set_value("kd", -0.05)
    f.set_value("friction_comp", 0.1)
    insp.primary.click()
    qtbot.waitUntil(lambda: ui(bridge) == "running", timeout=WAIT)
    bridge._motors[1].omega = 1.0  # noqa: SLF001  軽く押す
    qtbot.wait(1500)
    peak = 0.0
    for _ in range(40):
        qtbot.wait(25)
        peak = max(peak, abs(bridge._motors[1].omega))  # noqa: SLF001
    assert peak < 1.3 * bridge.power_limits().speed



# ============================================================ グラフの枠(プロットの高さ)
def test_plot_height_is_stable_across_normal_operations(env, qtbot) -> None:
    """トリガの待機・成立、Δ カーソル、運転の開始・停止、ステップ結果の有無で、
    プロット領域の高さ(と幅)が変わらない。帯のオフ・FFT への切替は明示的な選択なので対象外。"""
    from edulite05.app.pages.operate import analysis as an

    page, bridge, _shell = env
    g = page.monitor.graph
    mon = page.monitor
    qtbot.wait(200)
    h0, w0 = g.glw.height(), g.glw.width()
    bar_h = mon.view_seg.parentWidget().height()

    def same(what: str) -> None:
        qtbot.wait(120)
        assert (g.glw.height(), g.glw.width()) == (h0, w0), what

    # 運転の開始 → ステップ結果が出る → 停止
    page.inspector.mode_seg.button("velocity").click()
    page.inspector.primary.click()
    qtbot.waitUntil(lambda: ui(bridge) == "enabled", timeout=WAIT)
    page.inspector.primary.click()
    qtbot.waitUntil(lambda: ui(bridge) == "running", timeout=WAIT)
    page.inspector.forms["velocity"].set_value("velocity", 2.0)
    qtbot.waitUntil(lambda: g.step_result() is not None, timeout=WAIT)
    same("ステップ結果が出た")
    # トリガの待機と状態の文言
    g.arm_trigger(an.TriggerSpec("m1.vel", 1.0, True, 1.0))
    same("トリガ待機")
    g.disarm_trigger()
    same("トリガ解除")
    # 一時停止中の Δ カーソル
    g.set_paused(True)
    g.add_mark(-1.0)
    g.add_mark(-2.0)
    assert len(g.marks()) == 2 and g.legend.status.text()
    same("Δ カーソル")
    g.set_paused(False)
    g.disarm_trigger()
    same("再開")
    page.inspector.primary.click()  # 運転停止
    qtbot.waitUntil(lambda: ui(bridge) == "enabled", timeout=WAIT)
    same("運転停止")
    g.set_window(2)
    qtbot.waitUntil(lambda: g.step_result() is None, timeout=WAIT)
    same("ステップ結果が消えた")
    # FFT の「対象」の出入りでツールバーの高さは変わらない(プロットの数は表示の切替なので別)
    mon.preset_combo.setCurrentIndex(mon.preset_combo.findData("fft"))
    qtbot.wait(120)
    assert mon.view_seg.parentWidget().height() == bar_h
    mon.preset_combo.setCurrentIndex(mon.preset_combo.findData("standard"))
    same("標準へ戻した")


# ====================================================== 消費電力の概算(温度・電圧カードの 3 行目)
def test_power_row_shows_rough_estimate_with_tag_and_breakdown_tooltip(env, qtbot) -> None:
    import dataclasses

    from edulite05.core.telemetry.power import PowerEstimate

    page, bridge, _shell = env
    strip = page.strip
    qtbot.wait(300)
    name, value = strip.env_cell.extra_texts()
    assert name == tr("operate.readout.power") == "消費電力"
    assert value.startswith("≈ ") and float(value[2:]) == pytest.approx(1.0, abs=0.2)  # 待機のみ
    assert tr("operate.readout.power_tag") == "推定"
    tip = strip.env_cell.toolTip()
    assert "機械出力" in tip and "銅損" in tip and "基本消費" in tip and "未較正" in tip
    # 運転中は機械出力ぶん増える
    page.inspector.mode_seg.button("velocity").click()
    page.inspector.primary.click()
    qtbot.waitUntil(lambda: ui(bridge) == "enabled", timeout=WAIT)
    page.inspector.primary.click()
    qtbot.waitUntil(lambda: ui(bridge) == "running", timeout=WAIT)
    page.inspector.forms["velocity"].set_value("velocity", 3.0)
    qtbot.wait(1500)
    assert float(strip.env_cell.extra_texts()[1][2:]) > 1.0
    # 回生(負): 名前が「回生」になり、値は大きさ
    st = bridge.states()[1]
    strip.set_state(dataclasses.replace(st, id=99, power=PowerEstimate(-9.0, 1.0, 1.0, -7.0)),
                    bridge.power_limits())
    assert strip.env_cell.extra_texts() == (tr("operate.readout.power_regen"), "≈ 7.0")
    strip.set_state(None, None)
    assert strip.env_cell.extra_texts()[1] == "—"


def test_power_row_does_not_change_card_sizes(env, qtbot) -> None:
    page, _bridge, _shell = env
    strip = page.strip
    qtbot.wait(300)
    heights = {c.height() for c in strip._cells}  # noqa: SLF001
    assert len(heights) == 1  # 4 枚とも同じ高さ(3 行目を足しても、他のカードより高くならない)
    assert strip.env_cell.sizeHint().height() <= strip.pos_cell.height()
