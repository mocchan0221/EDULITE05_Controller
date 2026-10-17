"""保守ページ(ui_design.md §5.4)のテスト(offscreen)。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.i18n import tr
from edulite05.app.main_window import MainWindow
from edulite05.app.pages.maintenance import (
    GROUP_CAUTION,
    GROUP_COMMON,
    MaintenancePage,
    builtin_items,
)
from edulite05.app.ui.dialogs import SetZeroDialog
from edulite05.app.ui.style import theme_manager

COMMON = ["set_zero", "active_report", "can_timeout"]
CAUTION = ["set_can_id", "set_baud", "switch_protocol", "restore_protocol"]


@pytest.fixture
def window(qtbot):
    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0)
    win = MainWindow(bridge, settings_path=None, enable_3d=False)
    qtbot.addWidget(win)
    win.show()
    bridge.start()
    qtbot.waitExposed(win)
    win.navigate("maintenance")
    yield win
    win.close()


@pytest.fixture
def page(window) -> MaintenancePage:
    p = window.page("maintenance")
    assert isinstance(p, MaintenancePage)
    return p


@pytest.fixture
def calls(window, monkeypatch) -> list[tuple]:
    out: list[tuple] = []
    monkeypatch.setattr(window.bridge, "run_tool", lambda t, m, a: out.append((t, m, a)))
    return out


def drive_modal(fn) -> None:
    def tick() -> None:
        dlg = QApplication.activeModalWidget()
        if dlg is None:
            QTimer.singleShot(20, tick)
        else:
            fn(dlg)

    QTimer.singleShot(20, tick)


# ------------------------------------------------------------------ 構成
def test_structure_two_groups_and_rows(page) -> None:
    items = builtin_items()
    assert [i.key for i in items if i.group == GROUP_COMMON] == COMMON
    assert [i.key for i in items if i.group == GROUP_CAUTION] == CAUTION
    assert list(page.rows) == COMMON + CAUTION
    assert page._groups[GROUP_COMMON].rows == [page.rows[k] for k in COMMON]
    assert page._groups[GROUP_CAUTION].rows == [page.rows[k] for k in CAUTION]
    # 行 = タイトル + 1 行説明 + 影響タグ + [設定…]
    for key, row in page.rows.items():
        assert row.title_label.text() and row.description_label.text(), key
        assert row.button.text() == tr("maint.configure") == "設定…"
        assert row.button.variant == "standard"
        assert row.tags, key
    titles = {k: r.title_label.text() for k, r in page.rows.items()}
    assert titles["set_zero"] == "ゼロ点を設定"
    assert titles["set_can_id"] == "CAN ID を変更"
    assert titles["restore_protocol"] == "CANopen・MIT プロトコルから戻す"
    # 影響タグ(電源の再投入・通信不能)は注意グループにある
    assert "電源の再投入が必要" in page.rows["set_baud"].tag_texts()
    assert "通信できなくなる場合あり" in page.rows["switch_protocol"].tag_texts()
    # 強調色(accent)のボタンはない(保守は静かなページ)
    assert all(r.button.variant != "accent" for r in page.rows.values())


def test_group_headers_and_heading(page) -> None:
    from edulite05.app.pages.boxed_list import GroupHeader

    heads = {h.label.text(): h for h in page.findChildren(GroupHeader)}
    assert set(heads) == {tr("maint.group_common"), tr("maint.group_caution")}
    assert heads[tr("maint.group_caution")]._icon_name == "triangle-alert"  # グループ見出しに ⚠
    assert heads[tr("maint.group_common")]._icon_name is None
    assert page.heading.text() == tr("nav.maintenance")


# ------------------------------------------------------------------ 実行可否(原則 6)
def test_enabled_only_when_disabled_state(window, page) -> None:
    b = window.bridge
    assert b.states()[1].ui_state == "disabled"
    assert page.target().id == 1 and page.lock_reason() == ""
    assert all(r.button.isEnabled() for r in page.rows.values())
    assert not page.lock_bar.isVisibleTo(page)
    assert page.target_label.text().startswith("ID1")

    b.enable([1], 2)  # 有効 → 保守は実行できない(押せず、理由を言う)
    assert b.states()[1].ui_state == "enabled"
    reason = tr("maint.reason_enabled", id=1)
    assert page.lock_reason() == reason
    for key, r in page.rows.items():
        assert not r.button.isEnabled()
        # 「プロトコルから戻す」は選択中のモータではなく、有効なモータの有無で理由を言う
        want = tr("maint.reason_any_enabled") if key == "restore_protocol" else reason
        assert r.button.toolTip() == want
    assert page.lock_bar.isVisibleTo(page) and page.lock_bar.text() == reason

    b.disable([1])
    assert all(r.button.isEnabled() for r in page.rows.values())
    assert not page.lock_bar.isVisibleTo(page)


def test_locked_while_running_fault_and_estop(window, page) -> None:
    from edulite05.core.control.params import VelocityParams

    b = window.bridge
    b.enable([1], 2)
    b.start_controller("velocity", {"motor": 1}, VelocityParams(velocity=1.0))
    assert b.states()[1].ui_state == "running"
    assert page.lock_reason() == tr("maint.reason_running", id=1)
    assert not page.rows["set_zero"].button.isEnabled()
    b.disable([1])
    b.inject_fault(1, "overtemp")
    assert b.states()[1].ui_state == "fault"
    assert page.lock_reason() == tr("maint.reason_fault", id=1)
    b.clear_fault([1])
    assert page.lock_reason() == ""
    b.estop()
    assert page.lock_reason() == tr("maint.reason_estop")
    assert not page.rows["set_can_id"].button.isEnabled()
    b.release_estop()
    assert page.rows["set_can_id"].button.isEnabled()


def test_target_follows_selected_motor(window, page) -> None:
    b = window.bridge
    b.enable([1], 2)
    b.select_motor(2)  # ID2 は停止中 → 実行できる
    assert page.target().id == 2
    assert page.target_label.text().startswith("ID2")
    # モータを指定する行は押せる。「プロトコルから戻す」は有効なモータが 1 台でもあれば押せない
    assert all(r.button.isEnabled() for k, r in page.rows.items() if k != "restore_protocol")
    assert not page.rows["restore_protocol"].button.isEnabled()
    b.select_motor(1)
    assert not page.rows["set_zero"].button.isEnabled()


def test_offline_has_no_target(qtbot) -> None:
    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0, start_offline=True)
    win = MainWindow(bridge, settings_path=None, enable_3d=False)
    qtbot.addWidget(win)
    win.show()
    bridge.start()
    page = win.page("maintenance")
    assert page.target() is None
    assert page.lock_reason() == tr("maint.reason_no_motor")
    assert not any(r.button.isEnabled() for r in page.rows.values())
    win.close()


# ------------------------------------------------------------------ 実行
def test_run_item_calls_bridge_after_accept(window, page, calls, monkeypatch) -> None:
    toasts: list[tuple] = []
    monkeypatch.setattr(window, "toast", lambda t, k="success": toasts.append((t, k)))
    monkeypatch.setattr(page, "_exec", lambda dlg: isinstance(dlg, SetZeroDialog))
    assert page.run_item("set_zero") is True
    assert calls == [("set_zero", 1, {})]
    assert toasts and toasts[-1][1] == "success"
    # キャンセルなら送らない
    monkeypatch.setattr(page, "_exec", lambda dlg: False)
    assert page.run_item("set_zero") is False and len(calls) == 1


def test_reboot_required_toast_is_caution(window, page, calls, monkeypatch) -> None:
    toasts: list[tuple] = []
    monkeypatch.setattr(window, "toast", lambda t, k="success": toasts.append((t, k)))

    def accept(dlg) -> bool:
        dlg.combo.setCurrentIndex(dlg.combo.findData(2))
        return True

    monkeypatch.setattr(page, "_exec", accept)
    assert page.run_item("set_baud")
    assert calls[0][0] == "set_baud" and calls[0][2]["baud"] == 2
    assert toasts[-1][1] == "caution" and "電源" in toasts[-1][0]


def test_state_change_during_dialog_cancels_run(window, page, calls, monkeypatch) -> None:
    def exec_and_enable(dlg) -> bool:
        window.bridge.enable([1], 2)  # ダイアログを開いている間に有効化された
        return True

    monkeypatch.setattr(page, "_exec", exec_and_enable)
    assert page.run_item("set_zero") is False
    assert calls == []


def test_locked_row_cannot_run(window, page, calls, monkeypatch) -> None:
    window.bridge.enable([1], 2)
    monkeypatch.setattr(page, "_exec", lambda dlg: True)
    assert page.run_item("set_zero") is False and calls == []


def test_can_id_flow_through_real_modal(window, page, calls) -> None:
    """ボタン → 実際のモーダル → 対象 ID を打って確定 → run_tool(CAN ID は新しい ID)。"""
    seen: dict[str, object] = {}

    def drive(dlg) -> None:
        seen["before"] = dlg.confirm_button.isEnabled()
        for cb in dlg.checkboxes:
            cb.setChecked(True)
        seen["checks_only"] = dlg.confirm_button.isEnabled()
        dlg.spin.setValue(5)
        dlg.text_edit.setText("1")
        seen["verb"] = dlg.confirm_button.text()
        dlg.confirm_button.click()

    drive_modal(drive)
    page.rows["set_can_id"].button.click()
    assert seen == {"before": False, "checks_only": False, "verb": "ID を 5 に変更"}
    assert calls == [("set_can_id", 1, {"new_id": 5})]


def test_estop_closes_maintenance_dialog_without_running(window, page, calls) -> None:
    drive_modal(lambda dlg: window.estop())
    page.rows["set_zero"].button.click()
    assert calls == [] and window.bridge.estop_latched()


def test_add_item_extension_point(page, calls, monkeypatch) -> None:
    """ui_design.md §10 の add_maintenance_item 相当。"""
    from edulite05.app.pages.maintenance import MaintenanceItem

    item = MaintenanceItem(
        "extra", GROUP_COMMON, "追加項目", "説明", ("タグ",), lambda parent, motor: SetZeroDialog(
            parent, motor
        ),
    )
    row = page.add_item(item)
    assert row.title_label.text() == "追加項目" and row.button.isEnabled()
    assert page._groups[GROUP_COMMON].rows[-1] is row
    monkeypatch.setattr(page, "_exec", lambda dlg: True)
    assert page.run_item("extra") and calls[0][0] == "set_zero"


# --------------------------------------------------------- プロトコルから戻す(モータ選択不要)
def test_restore_protocol_does_not_need_selected_motor(qtbot, monkeypatch) -> None:
    """CANopen・MIT のモータはスキャンで見つからない: 接続中なら、モータが 0 台でも押せる。"""
    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0, start_offline=True)
    win = MainWindow(bridge, settings_path=None, enable_3d=False)
    qtbot.addWidget(win)
    win.show()
    bridge.start()
    page = win.page("maintenance")
    row = page.rows["restore_protocol"]
    assert not row.button.isEnabled()  # 未接続
    assert row.button.toolTip() == tr("maint.reason_not_connected")

    bridge.connect_adapter({"adapter": "simulator"})
    qtbot.waitUntil(lambda: bridge.connection_info().connected, timeout=3000)
    assert bridge.motors() == []  # 接続直後(スキャン結果が出る前): モータは 0 台
    assert page.target() is None and page.lock_reason() == tr("maint.reason_no_motor")
    assert row.button.isEnabled()
    assert not page.rows["set_zero"].button.isEnabled()  # ほかの行は選択中のモータが必要

    calls: list[tuple] = []
    monkeypatch.setattr(bridge, "run_tool", lambda t, m, a: calls.append((t, m, a)))

    def accept(dlg) -> bool:
        for cb in dlg.checkboxes:
            cb.setChecked(True)
        dlg.id_spin.setValue(9)
        dlg.combo.setCurrentIndex(dlg.combo.findData("mit"))
        return True

    monkeypatch.setattr(page, "_exec", accept)
    assert page.run_item("restore_protocol") is True
    assert calls == [("restore_protocol", 9, {"from_protocol": "mit"})]
    win.close()


def test_restore_protocol_locked_while_enabled_or_estop(window, page) -> None:
    b = window.bridge
    row = page.rows["restore_protocol"]
    assert row.button.isEnabled()
    b.enable([2], 2)  # 選択中でないモータが有効でも不可
    assert b.selected_motor() == 1 and not row.button.isEnabled()
    assert row.button.toolTip() == tr("maint.reason_any_enabled")
    b.disable([2])
    assert row.button.isEnabled()
    b.estop()
    assert not row.button.isEnabled() and row.button.toolTip() == tr("maint.reason_estop")
