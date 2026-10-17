"""運転ページのインスペクタ(ui_design.md §3・§5.1)のテスト。主ボタンの状態表・JOG・MIT・モード。"""

from __future__ import annotations

import pytest
from operate_helpers import make_page, teardown
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent

from edulite05.app.i18n import tr
from edulite05.app.pages.operate import modes as md
from edulite05.app.ui.style import theme_manager


@pytest.fixture(autouse=True)
def _theme():
    theme_manager().apply("dark")
    yield
    theme_manager().apply("dark")


@pytest.fixture
def env(qtbot, tmp_path):
    page, bridge, shell = make_page(qtbot, tmp_path)
    yield page, bridge, shell
    teardown(page, bridge)


def ctrl_of(bridge, mid: int):
    return bridge._motors[mid].ctrl  # noqa: SLF001  (DemoBridge の内部状態を確認する)


# ------------------------------------------------------------------ 主ボタンの状態表(§3)
def test_main_button_follows_state_table(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    # disabled: 有効化(強調色)、無効化は出ない、モード選択は可
    assert insp.heading.text() == "ID1 EL05 #1"
    assert insp.ui_state() == "disabled" and insp.primary_kind == "enable"
    assert insp.primary.text() == tr("operate.action.enable")
    assert insp.primary.property("variant") == "accent"
    assert insp.primary.height() == 40
    assert not insp.secondary.isVisible()
    assert all(insp.mode_seg.item_enabled(k) for k in insp.mode_seg.keys())
    # enabled: 運転開始(強調色)+ 無効化。モード選択は不可で理由が出る
    insp.primary.click()
    assert insp.ui_state() == "enabled" and insp.primary_kind == "start"
    assert insp.primary.text() == tr("operate.action.start")
    assert insp.primary.property("variant") == "accent"
    assert insp.secondary.isVisible() and insp.secondary.text() == tr("operate.action.disable")
    for k in insp.mode_seg.keys():
        assert not insp.mode_seg.item_enabled(k)
        assert insp.mode_seg.item_tooltip(k) == tr("operate.mode.locked")
    # running: 運転停止(通常ボタン。強調色にしない)
    insp.primary.click()
    assert insp.ui_state() == "running" and insp.primary_kind == "stop"
    assert insp.primary.text() == tr("operate.action.stop")
    assert insp.primary.property("variant") == "standard"
    assert tr("operate.action.sending", hz=200) in insp.hint_label.text()
    assert insp.badge.tone == "success"
    # 運転停止 → enabled(有効のまま)
    insp.primary.click()
    assert insp.ui_state() == "enabled" and insp.primary_kind == "start"
    # 無効化 → disabled
    insp.secondary.click()
    assert insp.ui_state() == "disabled" and insp.primary_kind == "enable"
    assert insp.mode_seg.item_enabled("mit")


def test_fault_state_shows_cause_and_reset(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    bridge.inject_fault(1, "overtemp")
    assert insp.ui_state() == "fault" and insp.primary_kind == "reset"
    assert insp.primary.text() == tr("operate.action.reset_fault")
    assert insp.fault_block.isVisible()
    assert tr("fault.overtemp") in insp.fault_title.text()
    assert insp.fault_remedy.text().startswith(tr("operate.action.fault_remedy", remedy="")[:3])
    assert not insp.mode_seg.item_enabled("velocity")
    assert insp.mode_seg.item_tooltip("velocity") == tr("operate.mode.locked_fault")
    insp.primary.click()
    assert insp.ui_state() == "disabled" and not insp.fault_block.isVisible()


def test_estop_latched_blocks_everything(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    bridge.estop()
    assert not insp.primary.isEnabled()
    assert insp.primary.toolTip() == tr("operate.action.estop_latched")
    assert not insp.mode_seg.item_enabled("jog")
    assert insp.mode_seg.item_tooltip("jog") == tr("operate.mode.locked_estop")
    bridge.release_estop()
    assert insp.primary.isEnabled() and insp.mode_seg.item_enabled("jog")


def test_offline_shows_no_actions(qtbot, tmp_path) -> None:
    page, bridge, _shell = make_page(qtbot, tmp_path, start_offline=True)
    insp = page.inspector
    assert insp.heading.text() == tr("operate.heading.none")
    assert not insp.primary.isVisible() and not insp.secondary.isVisible()
    assert not insp.badge.isVisible()
    # 空状態: 見出し・主ボタン領域・ヒントは出さず、メッセージだけを縦中央に置く
    assert insp.empty.isVisible() and not insp.head.isVisible() and not insp.foot.isVisible()
    assert not insp.hint_label.isVisible()
    assert insp.empty.title_label.text() == tr("operate.heading.empty")
    assert not insp.mode_seg.item_enabled("velocity")
    teardown(page, bridge)


# ------------------------------------------------------------------ モード
def test_mode_to_controller_and_run_mode(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    table = [
        ("jog", None, "jog", 2),
        ("velocity", None, "velocity", 2),
        ("position", "csp", "csp", 5),
        ("position", "pp", "pp", 1),
        ("mit", None, "mit", 0),
    ]
    for mode, style, ctrl, run_mode in table:
        insp.mode_seg.button(mode).click()
        if style:
            insp.style_seg.button(style).click()
        assert insp.controller_name() == ctrl
        insp.primary.click()  # 有効化
        assert bridge.states()[1].run_mode == run_mode
        insp.secondary.click()  # 無効化
    insp.pick_other("current")
    assert insp.controller_name() == "current" and md.run_mode_for(insp.model()) == 3
    insp.pick_other("test")
    insp.forms["sine"].set_value("target", "position")
    assert md.run_mode_for(insp.model()) == 5


def test_mode_description_is_plain_and_info_tooltip_has_technical_text(env) -> None:
    page, _bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("mit").click()
    assert insp.desc_label.text() == tr("operate.mode_desc.mit")
    assert "run_mode" not in insp.desc_label.text() and "Kp" not in insp.desc_label.text()
    assert "run_mode 0" in insp.info_btn.toolTip()
    insp.mode_seg.button("velocity").click()
    assert "run_mode" not in insp.desc_label.text()
    assert "run_mode 2" in insp.info_btn.toolTip()


def test_other_menu_selects_mode(env, qtbot) -> None:
    page, _bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("other").click()
    menu = insp._other_menu  # noqa: SLF001
    texts = [a.text() for a in menu.actions()]
    assert texts == [tr("operate.mode.current"), tr("operate.mode.test"), tr("operate.mode.link")]
    menu.actions()[1].trigger()
    menu.hide()
    qtbot.waitUntil(lambda: insp.mode() == "test", timeout=2000)
    assert insp.mode_seg.current() == "other"
    assert tr("operate.mode.test") in insp.mode_seg.button("other").text()
    # メニューを選ばずに閉じたら、元のモードの選択に戻る
    insp.mode_seg.button("velocity").click()
    insp.mode_seg.button("other").click()
    insp._other_menu.hide()  # noqa: SLF001
    qtbot.waitUntil(lambda: insp.mode_seg.current() == "velocity", timeout=2000)


def test_position_style_and_wave_selectors_only_in_their_modes(env) -> None:
    page, _bridge, _shell = env
    insp = page.inspector
    assert not insp.style_box.isVisible() and not insp.preset_box.isVisible()
    insp.mode_seg.button("position").click()
    assert insp.style_box.isVisible() and not insp.preset_box.isVisible()
    insp.mode_seg.button("mit").click()
    assert insp.preset_box.isVisible() and not insp.style_box.isVisible()
    insp.pick_other("test")
    assert insp.wave_box.isVisible()
    insp.wave_seg.button("step").click()
    assert insp.controller_name() == "step"


def test_limits_expander_is_collapsed_by_default(env) -> None:
    page, _bridge, _shell = env
    insp = page.inspector
    assert not insp.limits.expanded()
    assert not insp._limit_boxes["velocity"].isVisible()  # noqa: SLF001
    insp.limits.set_expanded(True)
    assert insp._limit_boxes["velocity"].isVisible()  # noqa: SLF001
    assert insp.period_combo.isVisible()
    assert not insp.continuous_row.isVisible()  # 連続/単発は MIT だけ
    insp.mode_seg.button("mit").click()
    assert insp.continuous_row.isVisible()


# ------------------------------------------------------------------ 目標
def test_targets_clamp_to_tier_with_caution_note(env) -> None:
    page, _bridge, _shell = env
    insp = page.inspector
    form = insp.forms["velocity"]
    row = form.row("velocity")
    row.edit.setText("10")  # 低出力の上限(5 rad/s)を超える入力
    row.edit.editingFinished.emit()
    assert form.value("velocity") == pytest.approx(5.0)
    assert row.note_label.isVisible()
    assert "5" in row.note_label.text() and tr("tier.low.short") in row.note_label.text()
    assert row.note_label.property("tone") == "caution"
    # 物理レンジを超える入力は、赤いエラーで拒否する
    row.edit.setText("80")
    row.edit.editingFinished.emit()
    assert form.value("velocity") == pytest.approx(5.0)
    assert row.edit.has_problem()


def test_changes_apply_live_while_running(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.primary.click()
    insp.primary.click()
    assert insp.ui_state() == "running"
    insp.forms["velocity"].set_value("velocity", 3.0)
    assert ctrl_of(bridge, 1).params.velocity == pytest.approx(3.0)


def test_mit_presets(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("mit").click()
    assert list(insp.preset_buttons) == ["free", "damper", "zero", "spring", "hold"]
    insp.preset_buttons["spring"].click()
    f = insp.forms["mit"]
    assert (f.value("kp"), f.value("kd")) == (2.0, 0.1)
    insp.preset_buttons["damper"].click()
    assert (f.value("kp"), f.value("kd")) == (0.0, 0.15)
    insp.preset_buttons["free"].click()
    assert (f.value("kp"), f.value("kd")) == (0.0, 0.0)
    # 抵抗ゼロ: kp 0・kd 0・摩擦補償 0.03。ほかのプリセットは摩擦補償を 0 に戻す
    insp.preset_buttons["zero"].click()
    assert (f.value("kp"), f.value("kd"), f.value("friction_comp")) == (0.0, 0.0, 0.03)
    insp.preset_buttons["damper"].click()
    assert f.value("friction_comp") == 0.0
    # その場で保持: 今の位置を目標にする
    bridge._motors[1].theta = 1.25  # noqa: SLF001
    bridge._publish_states()  # noqa: SLF001
    insp.preset_buttons["hold"].click()
    assert f.value("position") == pytest.approx(1.25, abs=0.01)
    assert f.value("kp") > 0
    # 出力段階の上限(低出力: Kp ≤ 20)で切られる
    f.set_value("kp", 100.0)
    assert f.value("kp") == pytest.approx(20.0)


def test_enable_sets_position_target_to_current(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("position").click()
    bridge._motors[1].theta = 2.0  # noqa: SLF001
    bridge._publish_states()  # noqa: SLF001
    insp.primary.click()  # 有効化: 急に動かないよう、今の位置が目標になる
    assert insp.forms["csp"].value("position") == pytest.approx(2.0, abs=0.01)


# ------------------------------------------------------------------ JOG
def test_jog_uses_two_hold_buttons_when_enabled(env, qtbot) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("jog").click()
    assert not insp.jog_row.isVisible()  # 有効化するまでは主ボタン
    insp.primary.click()
    assert insp.jog_row.isVisible() and not insp.primary.isVisible()
    page.show()
    assert insp.jog_rev.minimumHeight() >= 46 and insp.jog_fwd.minimumHeight() >= 46
    assert insp.secondary.isVisible()
    # 押している間だけ回る
    qtbot.mousePress(insp.jog_fwd, Qt.MouseButton.LeftButton)
    assert insp.jog_active()
    assert bridge.states()[1].controller == "jog" and bridge.states()[1].ui_state == "running"
    assert ctrl_of(bridge, 1).params.speed > 0
    qtbot.mouseRelease(insp.jog_fwd, Qt.MouseButton.LeftButton)
    assert not insp.jog_active()
    assert bridge.states()[1].controller is None and bridge.states()[1].ui_state == "enabled"
    # 逆転
    qtbot.mousePress(insp.jog_rev, Qt.MouseButton.LeftButton)
    assert ctrl_of(bridge, 1).params.speed < 0
    qtbot.mouseRelease(insp.jog_rev, Qt.MouseButton.LeftButton)


def test_jog_stops_on_leave_focus_out_and_page_hidden(env, qtbot) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("jog").click()
    insp.primary.click()
    # ポインタが外れたら止まる
    qtbot.mousePress(insp.jog_fwd, Qt.MouseButton.LeftButton)
    assert insp.jog_active()
    insp.jog_fwd.leaveEvent(QEvent(QEvent.Type.Leave))
    assert not insp.jog_active() and bridge.states()[1].ui_state == "enabled"
    # フォーカスを失ったら止まる
    qtbot.mousePress(insp.jog_rev, Qt.MouseButton.LeftButton)
    assert insp.jog_active()
    insp.jog_rev.clearFocus()
    insp.jog_rev.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert not insp.jog_active()
    # ページが隠れたら止まる
    qtbot.mousePress(insp.jog_fwd, Qt.MouseButton.LeftButton)
    assert insp.jog_active()
    page.deactivated()
    assert not insp.jog_active() and bridge.states()[1].ui_state == "enabled"
    # 無効化・通信断でも止まる
    qtbot.mousePress(insp.jog_fwd, Qt.MouseButton.LeftButton)
    bridge.inject_comm_loss()
    assert not insp.jog_active()


def test_jog_with_arrow_keys(env, qtbot) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("jog").click()
    insp.primary.click()
    qtbot.keyPress(insp.hint_label, Qt.Key.Key_Left)
    assert insp.jog_active() and insp.jog_rev.is_holding()
    assert ctrl_of(bridge, 1).params.speed < 0
    qtbot.keyRelease(insp.hint_label, Qt.Key.Key_Left)
    assert not insp.jog_active() and bridge.states()[1].ui_state == "enabled"
    qtbot.keyPress(insp.hint_label, Qt.Key.Key_Right)
    assert insp.jog_fwd.is_holding() and ctrl_of(bridge, 1).params.speed > 0
    # インスペクタの外へフォーカスが移ったら止まる
    insp._on_focus_changed(None, None)  # noqa: SLF001
    assert not insp.jog_active()
    # JOG 以外のモードでは矢印キーで回らない
    insp.secondary.click()
    insp.mode_seg.button("velocity").click()
    insp.primary.click()
    qtbot.keyPress(insp.hint_label, Qt.Key.Key_Left)
    assert not insp.jog_active()


# ------------------------------------------------------------------ モータごとの記憶・外部の状態
def test_inputs_are_remembered_per_motor(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.mode_seg.button("jog").click()
    insp.forms["jog"].set_value("speed", 2.5)
    bridge.select_motor(2)
    assert insp.motor_id() == 2 and insp.heading.text() == "ID2 EL05 #2"
    assert insp.mode() == "velocity"
    assert insp.forms["jog"].value("speed") == pytest.approx(1.0)
    bridge.select_motor(1)
    assert insp.mode() == "jog" and insp.forms["jog"].value("speed") == pytest.approx(2.5)


def test_mode_follows_motor_enabled_from_outside(env) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    bridge.select_motor(2)
    bridge.enable([2], 1)  # 外部(別画面・デモ)で PP として有効化された
    assert insp.mode() == "position" and insp.model().pos_style == "pp"
    assert insp.mode_seg.current() == "position" and not insp.mode_seg.item_enabled("mit")


def test_two_motor_link_follows_leader(env, qtbot) -> None:
    page, bridge, _shell = env
    insp = page.inspector
    insp.pick_other("link")
    assert insp.link_box.isVisible() and insp.follower_combo.currentData() == 2
    insp.primary.click()  # 有効化(2 台とも)
    assert bridge.states()[1].ui_state == "enabled" and bridge.states()[2].ui_state == "enabled"
    insp.primary.click()  # 運転開始
    assert bridge.states()[1].ui_state == "running" and bridge.states()[2].ui_state == "running"
    assert ctrl_of(bridge, 1).name == "bilateral" and ctrl_of(bridge, 2).role == "follower"
    bridge._motors[1].theta = 2.0  # noqa: SLF001  (手で動かしたことにする)
    # core の Bilateral と同じ式の代役: 追従側が相手の位置へ寄る(位置差は開始時の 0 を保つ)
    qtbot.waitUntil(lambda: bridge._motors[2].theta > 0.3, timeout=5000)  # noqa: SLF001
    insp.link_box.form.set_value("stiffness", 3.0)  # 運転中の設定変更はそのまま反映される
    assert ctrl_of(bridge, 1).params.stiffness == 3.0 and ctrl_of(bridge, 2).params.stiffness == 3.0
    insp.primary.click()  # 運転停止(2 台とも)
    assert bridge.states()[1].ui_state == "enabled" and bridge.states()[2].ui_state == "enabled"
    insp.secondary.click()  # 無効化(2 台とも)
    assert bridge.states()[1].ui_state == "disabled" and bridge.states()[2].ui_state == "disabled"


def test_link_mode_needs_two_motors(qtbot, tmp_path) -> None:
    page, bridge, _shell = make_page(qtbot, tmp_path)
    insp = page.inspector
    bridge._motors.pop(2)  # noqa: SLF001
    bridge._motor_infos = [i for i in bridge._motor_infos if i.id == 1]  # noqa: SLF001
    bridge.motors_changed.emit(bridge.motors())
    insp.mode_seg.button("other").click()
    texts = [a.text() for a in insp._other_menu.actions()]  # noqa: SLF001
    assert tr("operate.mode.link") not in texts
    insp._other_menu.hide()  # noqa: SLF001
    teardown(page, bridge)


# ------------------------------------------------------------------ キーボード(F5 / F6)
def test_f5_f6_trigger_main_actions(env) -> None:
    page, _bridge, _shell = env
    insp = page.inspector
    assert insp.trigger_primary()  # F5: 有効化
    assert insp.ui_state() == "enabled"
    assert insp.trigger_primary()  # F5: 運転開始
    assert insp.ui_state() == "running"
    assert not insp.trigger_primary()  # 運転中の F5 は何もしない
    assert insp.trigger_stop()  # F6: 運転停止
    assert insp.ui_state() == "enabled"
    assert not insp.trigger_stop()


def test_f5_f6_shortcuts_are_registered_on_the_page(env) -> None:
    page, _bridge, _shell = env
    keys = sorted(sc.key().toString() for sc in page._shortcuts)  # noqa: SLF001
    assert keys == ["Ctrl+R", "F5", "F6"]
    page._shortcuts[0].activated.emit()  # noqa: SLF001  (F5)
    assert page.inspector.ui_state() == "enabled"
    page._shortcuts[1].activated.emit()  # noqa: SLF001  (F6: 運転中でないので何も起きない)
    assert page.inspector.ui_state() == "enabled"


# ------------------------------------------------------------------ modes.py
def test_modes_module_mapping() -> None:
    m = md.MotorModel()
    assert m.mode == "velocity" and md.controller_of(m) == "velocity"
    assert set(m.params) == set(md.PARAMS_BY_CONTROLLER)
    m.mode, m.pos_style = "position", "pp"
    assert md.controller_of(m) == "pp" and md.run_mode_for(m) == 1
    assert md.mode_desc_key(m) == "operate.mode_desc.position_pp"
    md.apply_derived(m, None, 0)
    assert m.mode == "mit"
    md.apply_derived(m, "sine", 2)
    assert (m.mode, m.wave) == ("test", "sine")
    md.apply_derived(m, None, 5)
    assert (m.mode, m.pos_style) == ("position", "csp")
    assert set(md.TARGET_FIELDS) == set(md.LIMIT_FIELDS) == set(md.PARAMS_BY_CONTROLLER)
