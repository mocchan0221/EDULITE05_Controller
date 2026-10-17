"""UI 部品(app/ui/)の基本動作。押下保持・セグメントの無効化と理由・バッジ・ParamRow など。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from edulite05.app.gallery import GalleryWindow
from edulite05.app.i18n import tr
from edulite05.app.ui import (
    Button,
    ConfirmDialog,
    EmptyState,
    Expander,
    HoldButton,
    InfoBar,
    MotorChip,
    NavRail,
    ParamRow,
    ReadoutCell,
    ReadoutDualCell,
    Section,
    Segmented,
    StatusBadge,
    Switch,
    ToastHost,
    ValueSlider,
    theme_manager,
)
from edulite05.app.ui.icon import available_icons, icon, pixmap
from edulite05.app.ui.readout import MINUS, fmt_signed
from edulite05.app.ui.style import tokens


@pytest.fixture(autouse=True)
def _theme():
    theme_manager().apply("dark")
    yield
    theme_manager().apply("dark")


def mouse_event(kind: QEvent.Type, pos: QPoint, button=Qt.MouseButton.LeftButton) -> QMouseEvent:
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
    p = QPointF(pos)
    return QMouseEvent(kind, p, p, button, buttons, Qt.KeyboardModifier.NoModifier)


# ------------------------------------------------------------------ HoldButton
class TestHoldButton:
    @pytest.fixture
    def hb(self, qtbot):
        w = HoldButton("◀ 押している間 逆転")
        qtbot.addWidget(w)
        w.resize(200, 48)
        w.show()
        events: list[str] = []
        w.hold_started.connect(lambda: events.append("start"))
        w.hold_ended.connect(lambda reason: events.append(f"end:{reason}"))
        return w, events

    def test_press_and_release(self, hb, qtbot) -> None:
        w, events = hb
        qtbot.mousePress(w, Qt.MouseButton.LeftButton)
        assert w.is_holding() and events == ["start"]
        assert w.property("holding") is True  # 押下中は塗り(QSS)
        qtbot.mouseRelease(w, Qt.MouseButton.LeftButton)
        assert not w.is_holding() and events == ["start", "end:release"]
        assert w.property("holding") is False

    def test_leaving_the_button_while_pressed_stops(self, hb, qtbot) -> None:
        w, events = hb
        qtbot.mousePress(w, Qt.MouseButton.LeftButton)
        QApplication.sendEvent(w, mouse_event(QEvent.Type.MouseMove, QPoint(-40, -40)))
        assert events == ["start", "end:leave"] and not w.is_holding()
        # 外へ出た後に離しても二重に終了しない
        qtbot.mouseRelease(w, Qt.MouseButton.LeftButton)
        assert events == ["start", "end:leave"]

    def test_focus_out_stops(self, hb, qtbot) -> None:
        w, events = hb
        qtbot.mousePress(w, Qt.MouseButton.LeftButton)
        QApplication.sendEvent(w, QFocusEvent(QEvent.Type.FocusOut))
        assert events == ["start", "end:focus_out"]

    def test_disable_and_hide_stop(self, hb, qtbot) -> None:
        w, events = hb
        qtbot.mousePress(w, Qt.MouseButton.LeftButton)
        w.setEnabled(False)  # 通信断などで無効化されたら即停止
        assert events[0] == "start" and len(events) == 2
        assert events[1] in ("end:disabled", "end:focus_out") and not w.is_holding()
        events.clear()
        w.setEnabled(True)
        qtbot.mousePress(w, Qt.MouseButton.LeftButton)
        w.hide()
        assert events[-1].startswith("end:") and not w.is_holding()

    def test_window_deactivation_stops(self, hb, qtbot) -> None:
        w, events = hb
        qtbot.mousePress(w, Qt.MouseButton.LeftButton)
        w._on_focus_window(None)  # フォーカスウィンドウが無くなった(別アプリへ切替)
        assert events == ["start", "end:window_inactive"]

    def test_keyboard_hold(self, hb, qtbot) -> None:
        w, events = hb
        w.setFocus()
        qtbot.keyPress(w, Qt.Key.Key_Space)
        assert events == ["start"]
        qtbot.keyRelease(w, Qt.Key.Key_Space)
        assert events == ["start", "end:release"]

    def test_disabled_button_does_not_start(self, hb, qtbot) -> None:
        w, events = hb
        w.setEnabled(False)
        qtbot.mousePress(w, Qt.MouseButton.LeftButton)
        assert events == [] and not w.is_holding()

    def test_public_press_and_release(self, hb) -> None:
        w, events = hb
        w.press()
        w.press()  # 二重に始めない
        assert w.is_holding() and events == ["start"]
        w.release("focus_out")
        w.release()  # 保持中でなければ何もしない
        assert not w.is_holding() and events == ["start", "end:focus_out"]
        w.press()
        w.release()
        assert events[-1] == "end:release"

    def test_height_is_jog_height_despite_qss_min_height(self, hb) -> None:
        from edulite05.app.ui.tokens import H_JOG

        w, _events = hb
        w.ensurePolished()
        assert w.height() == H_JOG and w.minimumSizeHint().height() >= H_JOG - 2
        assert w.sizeHint().height() >= H_JOG - 2


# ------------------------------------------------------------------ Segmented
class TestSegmented:
    def test_exclusive_selection_and_signals(self, qtbot) -> None:
        seg = Segmented([("a", "JOG"), ("b", "速度"), ("c", "位置")], current="b")
        qtbot.addWidget(seg)
        user: list[str] = []
        any_change: list[str] = []
        seg.selected.connect(user.append)
        seg.current_changed.connect(any_change.append)
        assert seg.current() == "b" and seg.button("b").isChecked()
        qtbot.mouseClick(seg.button("c"), Qt.MouseButton.LeftButton)
        assert seg.current() == "c" and user == ["c"] and any_change == ["c"]
        assert not seg.button("b").isChecked()
        seg.set_current("a")  # プログラムからは selected を出さない
        assert user == ["c"] and any_change == ["c", "a"]

    def test_disabled_item_has_reason_and_cannot_be_selected(self, qtbot) -> None:
        seg = Segmented([("low", "低出力"), ("high", "高出力")], current="low")
        qtbot.addWidget(seg)
        user: list[str] = []
        seg.selected.connect(user.append)
        seg.set_item_enabled("high", False, "全モータを無効化すると切り替えられます")
        assert not seg.item_enabled("high")
        assert seg.item_tooltip("high") == "全モータを無効化すると切り替えられます"
        qtbot.mouseClick(seg.button("high"), Qt.MouseButton.LeftButton)
        assert user == [] and seg.current() == "low"
        seg.set_item_enabled("high", True)
        assert seg.item_enabled("high") and seg.item_tooltip("high") == ""

    def test_set_all_enabled_and_keyboard(self, qtbot) -> None:
        seg = Segmented([("a", "A"), ("b", "B"), ("c", "C")], current="a")
        qtbot.addWidget(seg)
        seg.show()
        seg.set_item_enabled("b", False, "理由")
        seg.setFocus()
        qtbot.keyClick(seg, Qt.Key.Key_Right)  # 無効な b を飛ばして c へ
        assert seg.current() == "c"
        qtbot.keyClick(seg, Qt.Key.Key_Left)
        assert seg.current() == "a"
        seg.set_all_enabled(False, "未接続")
        assert all(not seg.item_enabled(k) for k in seg.keys())

    def test_equal_segment_widths(self, qtbot) -> None:
        seg = Segmented([("a", "A"), ("b", "とても長い項目")], current="a")
        qtbot.addWidget(seg)
        seg.show()
        assert seg.button("a").width() == seg.button("b").width()
        assert seg.height() == 32


# ------------------------------------------------------------------ Button / Badge / Chip
def test_button_variants_height_and_reason(qtbot) -> None:
    for variant in ("accent", "standard", "subtle", "danger", "caution"):
        b = Button("テスト", variant)
        qtbot.addWidget(b)
        assert b.height() == 32 and b.property("variant") == variant
    big = Button("運転開始", "accent", large=True)
    qtbot.addWidget(big)
    assert big.height() == 40
    icon_only = Button(icon="ellipsis", variant="subtle", tooltip="その他")
    qtbot.addWidget(icon_only)
    assert (icon_only.width(), icon_only.height()) == (32, 32)
    # 無効化と理由(原則 6)
    b = Button("運転開始", "accent", tooltip="運転を開始します")
    qtbot.addWidget(b)
    b.set_available(False, "有効化すると運転を開始できます")
    assert not b.isEnabled() and b.toolTip() == "有効化すると運転を開始できます"
    b.set_available(True)
    assert b.isEnabled() and b.toolTip() == "運転を開始します"


def test_status_badge_is_not_interactive(qtbot) -> None:
    """状態バッジは押せない・ホバーしない(ui_design.md 原則 2)。"""
    states = ("offline", "disabled", "enabled", "running", "fault")
    expected = {"offline": "neutral", "disabled": "neutral", "enabled": "accent",
                "running": "success", "fault": "critical"}
    for s in states:
        b = StatusBadge.for_ui_state(s)
        qtbot.addWidget(b)
        assert b.tone == expected[s] and b.text() == tr(f"motor_state.{s}")
        assert b.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert not hasattr(b, "clicked")  # クリックのシグナルを持たない
        assert b.height() == 24
        assert not b.testAttribute(Qt.WidgetAttribute.WA_Hover)
    # QSS にもバッジのホバー規則がない
    from edulite05.app.ui.style import build_qss

    qss = build_qss(tokens())
    assert "QFrame#Badge:hover" not in qss and "Badge:hover" not in qss


def test_motor_chip_state_selection_and_color(qtbot) -> None:
    chip = MotorChip(2)
    qtbot.addWidget(chip)
    got: list[int] = []
    chip.chip_clicked.connect(got.append)
    assert chip.ui_state == "offline" and not chip.selected
    chip.set_state("running")
    chip.set_selected(True)
    assert chip.ui_state == "running" and chip.selected
    assert chip.label_text() == "ID2" and chip.state_text() == tr("motor_state.running")
    chip.show()
    qtbot.mouseClick(chip, Qt.MouseButton.LeftButton)
    assert got == [2]
    # 状態が変わっても幅が動かない(ヘッダーの隣が揺れない)
    w0 = chip.width()
    chip.set_state("fault")
    assert chip.width() == w0
    chip.grab()  # 描画で例外が出ない


# ------------------------------------------------------------------ InfoBar / Toast
def test_infobar_severity_action_and_close(qtbot) -> None:
    bar = InfoBar("caution", "高出力モード", "固定して使用してください", action="低出力に戻す")
    qtbot.addWidget(bar)
    bar.show()
    clicks: list[int] = []
    bar.action_clicked.connect(lambda: clicks.append(1))
    assert bar.action_button is not None
    assert bar.close_button is None  # 条件が解消するまで閉じられない
    bar.action_button.click()
    assert clicks == [1]
    assert bar.severity == "caution" and bar.title() == "高出力モード"
    bar.set_severity("critical")
    assert bar.property("severity") == "critical"
    closable = InfoBar("info", "t", "x", closable=True)
    qtbot.addWidget(closable)
    seen: list[int] = []
    closable.closed.connect(lambda: seen.append(1))
    closable.close_button.click()
    assert seen == [1]
    estop = InfoBar("estop", "非常停止中", "x", action="解除")
    qtbot.addWidget(estop)
    assert estop.action_button.property("variant") == "inverse"


def test_toast_auto_hides(qtbot) -> None:
    host_widget = QWidget()
    qtbot.addWidget(host_widget)
    host_widget.resize(600, 400)
    host_widget.show()
    host = ToastHost(host_widget)
    host.show_message("保存しました", "success", ms=150)
    assert host.toast.isVisible() and host.toast.message() == "保存しました"
    qtbot.waitUntil(lambda: not host.toast.isVisible(), timeout=2000)


# ------------------------------------------------------------------ ParamRow / ValueSlider
class TestParamRow:
    def make(self, qtbot, **kw) -> ParamRow:
        row = ParamRow("速度", unit="rad/s", minimum=-5, maximum=5, step=0.01, decimals=3,
                       default=0.0, value=1.0, **kw)
        qtbot.addWidget(row)
        row.show()
        return row

    def test_edit_commit_slider_and_reset(self, qtbot) -> None:
        row = self.make(qtbot)
        got: list[float] = []
        row.value_changed.connect(got.append)
        row.edit.setText("2.5")
        row.edit.editingFinished.emit()
        assert row.value() == 2.5 and got == [2.5]
        assert row.slider.value() == 2.5
        row.slider.value_changed.emit(-1.0)  # スライダー操作
        assert row.value() == -1.0 and row.edit.text() == "-1.000"
        row.reset_btn.click()  # ↺ = 既定に戻す
        assert row.value() == 0.0 and got[-1] == 0.0
        assert row.min_label.text() == "-5" and row.max_label.text() == "5"

    def test_out_of_range_input_is_rejected_with_reason(self, qtbot) -> None:
        row = self.make(qtbot)
        got: list[float] = []
        row.value_changed.connect(got.append)
        row.edit.setText("20")
        row.edit.textEdited.emit("20")
        assert row.edit.property("invalid") is True
        assert row.note_label.isVisible() and "範囲外" in row.note_label.text()
        row.edit.editingFinished.emit()
        assert got == [] and row.value() == 1.0  # 確定されない
        row.edit.setText("3")
        row.edit.textEdited.emit("3")
        assert row.edit.property("invalid") is False and not row.note_label.isVisible()
        row.edit.editingFinished.emit()
        assert got == [3.0]

    def test_not_a_number(self, qtbot) -> None:
        row = self.make(qtbot)
        row.edit.setText("abc")
        row.edit.textEdited.emit("abc")
        assert row.note_label.isVisible() and row.note_label.text() == tr("form.not_a_number")

    def test_range_update_clamps_and_note(self, qtbot) -> None:
        row = self.make(qtbot)
        assert row.set_range(-0.5, 0.5) is True  # 値が切られた
        assert row.value() == 0.5
        row.set_note("低出力上限で制限中")
        assert row.note_label.isVisible() and row.note_label.property("tone") == "caution"
        row.set_note(None)
        assert not row.note_label.isVisible()

    def test_actual_marker_and_external_error(self, qtbot) -> None:
        row = self.make(qtbot)
        row.set_actual(0.92)
        assert row.slider._marker == 0.92
        row.set_error("目標が範囲外です")
        assert row.note_label.isVisible() and row.note_label.property("tone") == "critical"
        row.set_error(None)
        assert not row.note_label.isVisible()


def test_value_slider_keyboard_and_wheel(qtbot) -> None:
    s = ValueSlider()
    qtbot.addWidget(s)
    s.resize(200, 24)
    s.show()
    s.set_range(0, 10, 1)
    s.set_value(5)
    got: list[float] = []
    s.value_changed.connect(got.append)
    s.setFocus()
    qtbot.keyClick(s, Qt.Key.Key_Right)
    qtbot.keyClick(s, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)  # 10 倍
    assert s.value() == 10 and got == [6, 10]
    qtbot.keyClick(s, Qt.Key.Key_Home)
    assert s.value() == 0
    qtbot.keyClick(s, Qt.Key.Key_PageUp)  # 10%
    assert s.value() == 1
    # ホイールでは動かさない(ページのスクロールで値が変わる事故を防ぐ)
    before = s.value()
    from PySide6.QtGui import QWheelEvent

    ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 120), QPoint(0, 120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(s, ev)
    assert s.value() == before


def test_switch_toggles(qtbot) -> None:
    sw = Switch(False)
    qtbot.addWidget(sw)
    got: list[bool] = []
    sw.toggled.connect(got.append)
    qtbot.mouseClick(sw, Qt.MouseButton.LeftButton)
    assert sw.isChecked() and got == [True]
    sw.grab()


# ------------------------------------------------------------------ ReadoutCell
def test_readout_cell_values_and_formatting(qtbot) -> None:
    assert fmt_signed(0.736, 3) == "+0.736" and fmt_signed(-2.07, 2) == f"{MINUS}2.07"
    assert fmt_signed(-0.0001, 3) == "+0.000"  # 符号つきの -0.000 を出さない
    cell = ReadoutCell("位置", "rad", decimals=3, minimum=-12.57, maximum=12.57)
    qtbot.addWidget(cell)
    assert cell.value_text() == "—"
    cell.set_value(0.736)
    cell.set_command(1.0)
    cell.set_allowed(-5, 5)
    cell.set_meta("多回転 +3.45 rad")
    assert cell.value_text() == "+0.736"
    assert cell.command_label.text() == tr("ui.command", value="+1.000")
    assert cell.meta_label.text() == "多回転 +3.45 rad"
    cell.set_note("低出力上限で制限中")  # 注記は meta より優先して琥珀で出す
    assert cell.meta_label.text() == "低出力上限で制限中"
    assert cell.meta_label.property("tone") == "caution"
    cell.set_note(None)
    assert cell.meta_label.text() == "多回転 +3.45 rad"
    cell.set_command(None)
    assert cell.command_label.text() == ""
    cell.grab()
    dual = ReadoutDualCell(("温度", "℃", 1), ("電圧", "V", 1))
    qtbot.addWidget(dual)
    dual.set_values(28.74, 24.0)
    assert dual.value_texts() == ("28.7", "24.0")


# ------------------------------------------------------ Section / Expander / EmptyState / NavRail
def test_expander_toggles_content(qtbot) -> None:
    exp = Expander("制限と加速度")
    qtbot.addWidget(exp)
    label = QWidget()
    exp.set_content(label)
    exp.show()
    seen: list[bool] = []
    exp.toggled.connect(seen.append)
    assert not exp.expanded() and not label.isVisible()  # 既定は閉
    exp.header.click()
    assert exp.expanded() and label.isVisible() and seen == [True]
    exp.header.click()
    assert not label.isVisible() and seen == [True, False]


def test_section_and_empty_state(qtbot) -> None:
    sec = Section("見出し", card=True)
    qtbot.addWidget(sec)
    assert sec.property("card") is True and sec.title_label.text() == "見出し"
    sec.add(QWidget())
    es = EmptyState("plug", "アダプタが接続されていません", "説明", action="接続")
    qtbot.addWidget(es)
    clicks: list[int] = []
    es.action_clicked.connect(lambda: clicks.append(1))
    assert es.action_button.property("variant") == "accent"
    es.action_button.click()
    assert clicks == [1]
    assert EmptyState("plug", "t").action_button is None


def test_nav_rail_selection_badge_and_bottom(qtbot) -> None:
    rail = NavRail()
    qtbot.addWidget(rail)
    for key in ("operate", "params", "settings"):
        rail.add_item(key, key, "gauge", bottom=(key == "settings"))
    rail.show()
    seen: list[str] = []
    rail.current_changed.connect(seen.append)
    assert rail.current() == "operate" and rail.width() == 72
    qtbot.mouseClick(rail.item("params"), Qt.MouseButton.LeftButton)
    assert seen == ["params"] and rail.item("params").isChecked()
    assert not rail.item("operate").isChecked()
    rail.set_badge("params", True)
    assert rail.item("params").badge()
    assert rail.item("settings").y() > rail.item("params").y() + 56
    rail.grab()


# ------------------------------------------------------------------ ConfirmDialog
def test_confirm_dialog_gates_on_checks_and_text(qtbot) -> None:
    dlg = ConfirmDialog(None, title="ID 1 の CAN ID を 5 に変更しますか?", body="影響",
                        checks=["モータを固定した", "周囲に人がいない"],
                        confirm_text="ID を 5 に変更",
                        require_text="1", tone="caution")
    qtbot.addWidget(dlg)
    assert not dlg.confirm_button.isEnabled()
    assert dlg.confirm_button.toolTip()  # 押せない理由が出る
    assert dlg.cancel_button.isDefault()  # 既定はキャンセル(破壊側にしない)
    dlg.checkboxes[0].setChecked(True)
    dlg.checkboxes[1].setChecked(True)
    assert not dlg.confirm_button.isEnabled()  # 対象 ID の入力がまだ
    dlg.text_edit.setText("2")
    assert not dlg.confirm_button.isEnabled()
    dlg.text_edit.setText("1")
    assert dlg.confirm_button.isEnabled()
    assert dlg.confirm_button.text() == "ID を 5 に変更"
    dlg.confirm_button.click()
    assert dlg.result() == dlg.DialogCode.Accepted


def test_confirm_dialog_cancel_rejects(qtbot) -> None:
    dlg = ConfirmDialog(None, title="質問?", checks=["確認"], confirm_text="実行")
    qtbot.addWidget(dlg)
    dlg.cancel_button.click()
    assert dlg.result() == dlg.DialogCode.Rejected


# ------------------------------------------------------------------ Icon / Gallery
def test_icons_exist_and_render(qtbot) -> None:
    names = available_icons()
    for needed in ("gauge", "sliders-horizontal", "stethoscope", "wrench", "settings", "circle",
                   "circle-dot", "play", "triangle-alert", "octagon-x", "power", "square",
                   "rotate-ccw", "chevron-down", "chevron-right", "search", "ellipsis", "plug",
                   "refresh-cw", "pause", "hand", "info", "lock"):
        assert needed in names, needed
    pm = pixmap("play", "success", 16)
    assert not pm.isNull() and pm.width() >= 16
    assert not icon("play", "#ff0000").isNull()
    with pytest.raises(FileNotFoundError):
        pixmap("no-such-icon", "text_primary")


def test_gallery_builds_in_both_themes(qtbot) -> None:
    gal = GalleryWindow()
    qtbot.addWidget(gal)
    gal.show()
    assert gal.windowTitle()
    for name in ("light", "dark"):
        gal.theme_seg.set_current(name)
        theme_manager().apply(name)
        assert not gal.grab().isNull()
