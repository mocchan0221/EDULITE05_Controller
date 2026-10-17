"""パラメータページ(ui_design.md §5.2)のテスト(offscreen)。

DemoBridge(モータ 2 台、どちらも `disabled`)で、表示・絞り込み・編集・書込のロック・書込・
Flash 保存・書き出し/読み込み・既定値を確認する。
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import Qt

import edulite05.app.pages.params.page as page_mod
from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.i18n import catalog, tr
from edulite05.app.pages.params import ParamsPage
from edulite05.app.pages.params.edit import ROLE_DEF, ROLE_PROBLEM
from edulite05.app.pages.params.groups import (
    check_value,
    decimals_of,
    fmt_cell,
    fmt_param,
    group_of,
    range_cell,
    range_text,
    same_value,
    title_of,
)
from edulite05.app.ui.dialogs import FlashItem
from edulite05.app.ui.style import theme_manager
from edulite05.core.control.params import VelocityParams

SPD_KP = 0x701F  # 速度ループ Kp(既定 6、範囲の定義なし)
LIMIT_TORQUE = 0x700B  # トルク制限(0 〜 6 N·m。運転系なので表では編集できない)
CUR_FILT = 0x7014  # 電流フィルタ(0 〜 1。編集のテストに使う範囲つきの書込可能項目)
ZERO_STA = 0x7029  # uint8 0/1
CAN_TIMEOUT = 0x7028  # uint32、下限 0 のみ
VBUS = 0x701C  # 読み取り専用


class FakeShell:
    def __init__(self) -> None:
        self.toasts: list[tuple[str, str]] = []
        self.connection_opened = 0

    def navigate(self, page_id: str) -> None:  # pragma: no cover - 使わない
        pass

    def toast(self, text: str, kind: str = "success") -> None:
        self.toasts.append((text, kind))

    def open_connection(self) -> None:
        self.connection_opened += 1


@pytest.fixture
def env(qtbot):
    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0)
    shell = FakeShell()
    page = ParamsPage(bridge, shell)
    qtbot.addWidget(page)
    page.resize(1280, 800)
    page.show()
    bridge.start()
    page.activated()
    qtbot.waitUntil(lambda: page.motor_value(SPD_KP) is not None, timeout=3000)
    yield page, bridge, shell
    bridge.shutdown()


def type_value(page, index: int, text: str):
    """入力欄を開いて、ユーザーが入力したのと同じ経路で文字列を入れる。"""
    ed = page.begin_edit(index)
    assert ed is not None
    ed.type_text(text)
    return ed


def cell_center(page, index: int):
    """「新しい値」セルの中心(ビューポート座標)。"""
    r = page._rows[index].row
    page.table.scrollToItem(page.table.item(r, page_mod.C_NEW))
    return page.table.visualRect(page.table.model().index(r, page_mod.C_NEW)).center()


# ---------------------------------------------------------------- 純粋なロジック
def test_groups_and_names() -> None:
    defs = {d.index: d for d in DemoBridge(demo_motion=False).param_definitions()}
    assert group_of(defs[SPD_KP]) == "gains"
    assert group_of(defs[LIMIT_TORQUE]) == "limits"
    assert group_of(defs[0x7016]) == "command"
    assert group_of(defs[0x7026]) == "settings"
    # 読み取り専用はすべて「状態」
    ro = [d for d in defs.values() if "W" not in d.rw]
    assert ro and all(group_of(d) == "status" for d in ro)
    # 日本語名がカタログにある(全 index)
    assert title_of(defs[SPD_KP]) == "速度ループ Kp"
    assert all(f"param_page.name.{d.index:04X}" in catalog() for d in defs.values())


def test_check_value_and_format() -> None:
    d = {x.index: x for x in DemoBridge(demo_motion=False).param_definitions()}
    assert check_value(d[SPD_KP], "") == (None, "")  # 空は編集なし
    assert check_value(d[SPD_KP], "8.5") == (8.5, "")
    assert check_value(d[SPD_KP], "abc")[1] == tr("form.not_a_number")
    assert check_value(d[LIMIT_TORQUE], "7")[1] == tr("form.out_of_range", lo="0", hi="6")
    assert check_value(d[ZERO_STA], "0.5")[1] == tr("param_page.need_integer")
    assert check_value(d[CAN_TIMEOUT], "-1")[1] == tr("param_page.too_small", lo="0")
    assert check_value(d[CAN_TIMEOUT], "20000") == (20000.0, "")
    assert fmt_param(6.0, d[SPD_KP]) == "6"
    assert fmt_param(0.012, d[0x7011]) == "0.012"
    assert fmt_param(3, d[ZERO_STA]) == "3"
    assert fmt_param(None) == "—"
    assert range_text(d[LIMIT_TORQUE]) == "0 – 6"
    # 長い説明つきの範囲は「最小 – 最大」に縮める(全文はツールチップ)
    assert range_cell(d[0x7026]) == "0 – 65535" and range_cell(d[LIMIT_TORQUE]) == "0 – 6"
    assert same_value(6, 6.0000001) and not same_value(6, 6.1) and not same_value(None, 1)


def test_cell_format_has_fixed_decimals_per_parameter() -> None:
    d = {x.index: x for x in DemoBridge(demo_motion=False).param_definitions()}
    assert decimals_of(d[ZERO_STA]) == 0 and fmt_cell(1, d[ZERO_STA]) == "1"
    assert decimals_of(d[SPD_KP]) == 2 and fmt_cell(6, d[SPD_KP]) == "6.00"
    assert fmt_cell(8.5, d[SPD_KP]) == "8.50"  # 値によらず桁がそろう
    assert decimals_of(d[0x7011]) == 4 and fmt_cell(0.012, d[0x7011]) == "0.0120"
    assert decimals_of(d[0x7010]) == 3 and fmt_cell(0.17, d[0x7010]) == "0.170"
    assert fmt_cell(24, d[VBUS]) == "24.000"  # 読み取り専用(計測値)は最低 3 桁
    assert fmt_cell(None, d[SPD_KP]) == "—"


# ---------------------------------------------------------------- 表示
def test_table_structure_and_readonly_group(env) -> None:
    page, _b, _s = env
    # 28 行 + グループ見出し 5 行
    assert len(page._rows) == 28 and page.table.rowCount() == 33
    assert [page.table.horizontalHeaderItem(c).text() for c in range(7)] == [
        tr(f"param_page.col_{k}")
        for k in ("state", "name", "value", "new", "unit", "range", "default")
    ]
    # 読み取り専用は編集できず(手がかりも出さない)、値は読めている
    assert page.begin_edit(VBUS) is None and page.edit_widget(VBUS) is None
    ro = page.table.item(page._rows[VBUS].row, page_mod.C_NEW)
    assert ro.data(ROLE_DEF) is None and not ro.flags() & Qt.ItemFlag.ItemIsEditable
    assert page.motor_value(VBUS) == pytest.approx(24.0)
    assert page.table.item(page._rows[VBUS].row, page_mod.C_VALUE).text() == "24.000"
    # 書込可能な行の「新しい値」は平文で空(入力欄は開くまで存在しない)= 空の枠が並ばない
    assert page.cell_text(SPD_KP) == "" and page.edit_widget(SPD_KP) is None
    assert not page._open_editors()
    wr = page.table.item(page._rows[SPD_KP].row, page_mod.C_NEW)
    assert wr.flags() & Qt.ItemFlag.ItemIsEditable and wr.data(ROLE_DEF) is not None
    # 列の幅: 状態 40 / 名称 240 / モータの値 110 / 新しい値 120 / 単位 72 / 範囲 124 / 既定 90
    widths = [page.table.columnWidth(c) for c in range(7)]
    assert widths == [40, 240, 110, 120, 72, 124, 90]
    # 数値は固定桁(パラメータごと)
    assert page.table.item(page._rows[SPD_KP].row, page_mod.C_VALUE).text() == "6.00"
    assert page.table.item(page._rows[SPD_KP].row, page_mod.C_DEFAULT).text() == "6.00"
    # 値の列は「状態」グループの行も通常色(淡色にしない)
    it = page.table.item(page._rows[VBUS].row, page_mod.C_VALUE)
    assert it.foreground().color().name().upper() == "#E9EBEE"
    # 初期状態: 未書込 0 · Flash 未保存 0
    assert page.counter_label.text() == tr("param_page.counter", pending=0, dirty=0)


def test_group_and_search_filter(env) -> None:
    page, _b, _s = env
    assert len(page.visible_indices()) == 28
    page.seg.set_current("status")
    assert sorted(page.visible_indices()) == [0x7019, 0x701A, 0x701B, 0x701C]
    page.seg.set_current("gains")
    assert SPD_KP in page.visible_indices() and VBUS not in page.visible_indices()
    page.seg.set_current("all")
    page.search.setText("速度ループ")
    assert sorted(page.visible_indices()) == [SPD_KP, 0x7020]
    page.search.setText("0x7028")
    assert page.visible_indices() == [CAN_TIMEOUT]
    page.search.setText("spd_ref")
    assert page.visible_indices() == [0x700A]
    page.search.setText("")
    assert len(page.visible_indices()) == 28
    # グループ見出しは「すべて」のときだけ
    assert not page.table.isRowHidden(page._header_rows["gains"])
    page.seg.set_current("limits")
    assert page.table.isRowHidden(page._header_rows["gains"])


# ---------------------------------------------------------------- 編集と書込
def test_edit_marks_pending_and_accent_only_when_pending(env) -> None:
    page, bridge, _s = env
    assert page.write_btn.variant == "standard" and not page.write_btn.isEnabled()
    assert page.write_btn.toolTip() == tr("param_page.reason_nothing")
    type_value(page, SPD_KP, "8")  # ユーザー入力と同じ経路
    assert page.pending() == {SPD_KP: 8.0}
    assert page.counter_label.text() == tr("param_page.counter", pending=1, dirty=0)
    assert page.write_btn.variant == "accent" and page.write_btn.isEnabled()
    assert not page.table.item(page._rows[SPD_KP].row, page_mod.C_STATE).icon().isNull()
    # モータの値と同じ値に戻すと編集ではなくなる
    type_value(page, SPD_KP, "6")
    assert page.pending() == {}
    assert page.table.item(page._rows[SPD_KP].row, page_mod.C_STATE).icon().isNull()
    # 空欄 = 編集なし
    type_value(page, SPD_KP, "9")
    type_value(page, SPD_KP, "")
    assert page.pending() == {} and page.write_btn.variant == "standard"
    # 書いただけではモータは変わらない
    assert bridge._params[1][SPD_KP] == 6.0


def test_motion_params_are_read_only_with_reason(env, qtbot) -> None:
    """運転系(MotorService が書込を拒否するキー)は値を見せ、編集欄は出さず、理由を添える。"""
    from edulite05.core.control.service import MOTION_PARAM_KEYS

    page, bridge, _s = env
    locked = [i for i, r in page._rows.items() if r.d.name in MOTION_PARAM_KEYS]
    assert {page._rows[i].d.name for i in locked} == set(MOTION_PARAM_KEYS)
    reason = tr("param_page.motion_locked")
    assert reason == "運転画面・出力段階で設定します"
    for i in locked:
        row = page._rows[i]
        assert row.motion_locked and not row.editable
        # 値は見える(読み取り専用と同じ通常表示)
        assert page.table.item(row.row, page_mod.C_VALUE).text() not in ("", "—") or (
            page.motor_value(i) is None
        )
        # 編集欄は出ない(キー・クリック・begin_edit のどれでも)
        assert page.begin_edit(i) is None and page.edit_widget(i) is None
        new = page.table.item(row.row, page_mod.C_NEW)
        assert new.data(ROLE_DEF) is None and not new.flags() & Qt.ItemFlag.ItemIsEditable
        # 理由: 右端の空き列に薄く + 「新しい値」のツールチップ
        assert page.table.item(row.row, page_mod.C_FILL).text() == reason
        assert new.toolTip() == tr("param_page.motion_locked_tip")
        # 変更を積めない(ファイル取り込み・既定値に戻すの対象外でもある)
        page.set_pending(i, 1.0)
        assert i not in page.pending()
    assert not page._open_editors()
    # 書込可能な通常の項目には理由を出さない
    assert page.table.item(page._rows[SPD_KP].row, page_mod.C_FILL).text() == ""
    # 「既定値に戻す」でも運転系は書込対象にならない
    assert not {d.name for d, _o, _n in page.diff_to_defaults()} & set(MOTION_PARAM_KEYS)
    # 書込は一切 bridge に届かない(ログのエラーに頼らない)
    page.set_pending(SPD_KP, 8.0)
    assert set(page.pending()) == {SPD_KP}


def test_motion_param_value_is_shown(env) -> None:
    page, _b, _s = env
    row = page._rows[LIMIT_TORQUE]
    assert page.motor_value(LIMIT_TORQUE) is not None
    assert page.table.item(row.row, page_mod.C_VALUE).text() == fmt_cell(
        page.motor_value(LIMIT_TORQUE), row.d
    )


def test_out_of_range_shows_reason_and_blocks_write(env) -> None:
    page, _b, _s = env
    reason = tr("form.out_of_range", lo="0", hi="1")
    edit = type_value(page, CUR_FILT, "9")
    assert edit.has_problem() and edit.property("invalid") is True  # 赤枠
    row = page._rows[CUR_FILT]
    assert page.problem_of(CUR_FILT) == reason
    assert page.table.item(row.row, page_mod.C_FILL).text() == reason  # 理由は同じ行の右端
    assert page.table.rowHeight(row.row) == page_mod.ROW_H  # 行の高さは動かない
    assert page.pending() == {}  # 不正な値は未書込にしない
    # 入力欄を閉じても、不正な入力は赤印つきで残る(消えない)
    page.end_edit()
    assert page.edit_widget(CUR_FILT) is None
    cell = page.table.item(row.row, page_mod.C_NEW)
    assert cell.text() == "9" and cell.data(ROLE_PROBLEM) == reason
    # 別の有効な編集があっても、不正入力が残る間は書込めない
    page.set_pending(SPD_KP, 8.0)
    assert not page.write_btn.isEnabled()
    assert page.write_btn.toolTip() == tr("param_page.reason_problem")
    assert page.write_pending() == 0
    # 直すと解消
    edit = type_value(page, CUR_FILT, "0.5")
    assert edit.text() == "0.5" and not edit.has_problem()
    assert page.problem_of(CUR_FILT) == ""
    assert page.table.item(row.row, page_mod.C_FILL).text() == ""
    assert page.pending() == {SPD_KP: 8.0, CUR_FILT: 0.5}
    assert page.write_btn.isEnabled()


# ---------------------------------------------------------------- デリゲートでの編集
def test_edit_starts_by_click_double_click_enter_f2_and_typing(env, qtbot) -> None:
    page, _b, _s = env
    vp = page.table.viewport()
    # クリック
    qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=cell_center(page, SPD_KP))
    qtbot.waitUntil(lambda: page.edit_widget(SPD_KP) is not None, timeout=2000)
    page.end_edit()
    assert page.edit_widget(SPD_KP) is None
    # ダブルクリック(別の列でも、その行の入力欄が開く)
    r = page._rows[SPD_KP].row
    page.table.cellDoubleClicked.emit(r, page_mod.C_NAME)  # (QTest の DClick はこの信号を出さない)
    qtbot.waitUntil(lambda: page.edit_widget(SPD_KP) is not None, timeout=2000)
    page.end_edit()
    # Enter / F2(カーソルのあるセル)
    for key in (Qt.Key.Key_Return, Qt.Key.Key_F2):
        page.table.setCurrentCell(page._rows[0x7020].row, page_mod.C_NEW)
        page.table.setFocus()
        qtbot.keyClick(page.table, key)
        qtbot.waitUntil(lambda: page.edit_widget(0x7020) is not None, timeout=2000)
        page.end_edit()
    # 文字キーを打つと、その文字から入力が始まる
    page.table.setCurrentCell(page._rows[SPD_KP].row, page_mod.C_NEW)
    page.table.setFocus()
    qtbot.keyClick(page.table, Qt.Key.Key_9)
    qtbot.waitUntil(lambda: page.edit_widget(SPD_KP) is not None, timeout=2000)
    assert page.edit_widget(SPD_KP).text() == "9" and page.pending() == {SPD_KP: 9.0}


def test_readonly_row_cannot_be_edited_by_click(env, qtbot) -> None:
    page, _b, _s = env
    qtbot.mouseClick(
        page.table.viewport(), Qt.MouseButton.LeftButton, pos=cell_center(page, VBUS)
    )
    page.table.cellDoubleClicked.emit(page._rows[VBUS].row, page_mod.C_NAME)
    qtbot.wait(50)
    assert page.edit_widget(VBUS) is None and not page._open_editors()


def test_delegate_commit_valid_value_becomes_pending(env, qtbot) -> None:
    page, _b, _s = env
    ed = page.begin_edit(SPD_KP)
    ed.type_text("8")
    qtbot.keyClick(ed, Qt.Key.Key_Return)  # Enter で確定して閉じる
    qtbot.waitUntil(lambda: page.edit_widget(SPD_KP) is None, timeout=2000)
    assert page.pending() == {SPD_KP: 8.0} and not page.has_problems()
    # 平文のセルに固定桁で出る(入力欄ではない)
    assert page.cell_text(SPD_KP) == "8.00"
    assert not page._open_editors()
    # もう一度開くと、入力のままの値から始まる。モータの値と同じに戻せば編集なし
    ed = page.begin_edit(SPD_KP)
    assert ed.text() == "8"
    ed.type_text("6")
    qtbot.keyClick(ed, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: page.edit_widget(SPD_KP) is None, timeout=2000)
    assert page.pending() == {} and page.cell_text(SPD_KP) == ""


def test_delegate_invalid_value_is_flagged_and_not_committed(env, qtbot) -> None:
    page, _b, _s = env
    reason = tr("form.out_of_range", lo="0", hi="1")
    ed = page.begin_edit(CUR_FILT)
    ed.type_text("9")
    assert ed.property("invalid") is True and ed.problem_text() == reason
    qtbot.keyClick(ed, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: page.edit_widget(CUR_FILT) is None, timeout=2000)
    # 未書込にならず、書込できない。セルは入力のまま + 問題を持ち続ける
    assert page.pending() == {} and page.has_problems()
    assert page.problem_of(CUR_FILT) == reason and page.cell_text(CUR_FILT) == "9"
    assert not page.write_btn.isEnabled() and page.write_btn.toolTip() == tr(
        "param_page.reason_problem"
    )
    # 開き直すと、赤枠のまま入力が戻る
    ed = page.begin_edit(CUR_FILT)
    assert ed.text() == "9" and ed.property("invalid") is True
    # 数値でない入力も同様
    ed.type_text("abc")
    assert page.problem_of(CUR_FILT) == tr("form.not_a_number") and page.pending() == {}


def test_delegate_escape_cancels_edit(env, qtbot) -> None:
    page, _b, _s = env
    # 新規入力の取り消し: 編集なしに戻る
    ed = page.begin_edit(SPD_KP)
    ed.type_text("8")
    assert page.pending() == {SPD_KP: 8.0}
    qtbot.keyClick(ed, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: page.edit_widget(SPD_KP) is None, timeout=2000)
    assert page.pending() == {} and page.cell_text(SPD_KP) == ""
    # 既に未書込の値がある項目: 開いた時点の値に戻る
    page.set_pending(SPD_KP, 8.0)
    ed = page.begin_edit(SPD_KP)
    ed.type_text("9.5")
    assert page.pending() == {SPD_KP: 9.5}
    qtbot.keyClick(ed, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: page.edit_widget(SPD_KP) is None, timeout=2000)
    assert page.pending() == {SPD_KP: 8.0} and page.cell_text(SPD_KP) == "8.00"
    # 不正な入力の途中で取り消すと問題も消える
    ed = page.begin_edit(CUR_FILT)
    ed.type_text("9")
    assert page.has_problems()
    qtbot.keyClick(ed, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: page.edit_widget(CUR_FILT) is None, timeout=2000)
    assert not page.has_problems() and page.cell_text(CUR_FILT) == ""


def test_edit_closed_when_motor_changes(env, qtbot) -> None:
    page, bridge, _s = env
    ed = page.begin_edit(SPD_KP)
    ed.type_text("8")
    bridge.select_motor(2)
    qtbot.waitUntil(lambda: page.edit_widget(SPD_KP) is None, timeout=2000)
    assert page.motor_id() == 2 and page.pending() == {} and page.cell_text(SPD_KP) == ""
    bridge.select_motor(1)
    assert page.pending() == {SPD_KP: 8.0}  # 確定した編集は元のモータに残る


# ---------------------------------------------------------------- ツールバー
def test_toolbar_arrangement_and_wrapping(env, qtbot) -> None:
    page, _b, _s = env
    page.resize(1800, 800)
    qtbot.waitUntil(lambda: not page.toolbar.wrapped(), timeout=2000)
    tb = page.toolbar

    def x_of(w) -> int:
        return w.mapTo(tb, w.rect().topLeft()).x()

    def y_of(w) -> int:
        return w.mapTo(tb, w.rect().topLeft()).y()

    # 1 行: 左に検索 → グループ、右に件数 → ボタンの順。件数はボタンのすぐ左
    assert x_of(page.search) < x_of(page.seg) < x_of(page.counter_label) < x_of(page.read_btn)
    assert x_of(page.read_btn) < x_of(page.write_btn) < x_of(page.save_btn) < x_of(page.more_btn)
    gap = x_of(page.read_btn) - (x_of(page.counter_label) + page.counter_label.width())
    assert 0 <= gap <= 24
    # 狭いと右側(件数 + ボタン)が 2 行目へ回り、右寄せのまま
    page.resize(1000, 800)
    qtbot.waitUntil(lambda: page.toolbar.wrapped(), timeout=2000)
    assert y_of(page.read_btn) > y_of(page.search)
    assert x_of(page.more_btn) + page.more_btn.width() >= tb.width() - 2
    page.resize(1800, 800)
    qtbot.waitUntil(lambda: not page.toolbar.wrapped(), timeout=2000)


def test_write_locked_unless_disabled(env) -> None:
    page, bridge, _s = env
    page.set_pending(SPD_KP, 8.0)
    assert page.write_btn.isEnabled() and not page.lock_note.isVisibleTo(page)
    # 有効化 → 書込・Flash 保存とも不可。理由はツールチップと常設の一行
    bridge.enable([1], 2)
    assert page.lock_reason() == tr("param_page.lock_enabled")
    assert not page.write_btn.isEnabled()
    assert page.write_btn.toolTip() == tr("param_page.lock_enabled")
    assert not page.save_btn.isEnabled()
    assert page.save_btn.toolTip() == tr("param_page.lock_enabled")
    assert page.lock_note.isVisibleTo(page) and page.lock_note.text() == tr(
        "param_page.lock_enabled"
    )
    assert page.write_btn.variant == "standard"  # 書けないときは強調しない
    assert page.write_pending() == 0 and bridge._params[1][SPD_KP] == 6.0
    # 編集欄はロック中も使える(値を用意しておける)。読込も可
    assert page.begin_edit(SPD_KP).isEnabled() and page.read_btn.isEnabled()
    # 運転中も不可、無効化で戻る
    bridge.start_controller("velocity", {"motor": 1}, VelocityParams(velocity=1.0))
    assert page.lock_reason() == tr("param_page.lock_running")
    bridge.disable([1])
    assert page.lock_reason() == "" and page.write_btn.isEnabled()
    assert page.write_btn.variant == "accent"
    # 故障中も不可
    bridge.inject_fault(1, "overtemp")
    assert page.lock_reason() == tr("param_page.lock_fault") and not page.write_btn.isEnabled()


def test_write_then_changed_then_save_to_motor(env, monkeypatch) -> None:
    page, bridge, shell = env
    page.set_pending(SPD_KP, 8.0)
    page.set_pending(0x7020, 0.05)
    assert page.write_pending() == 2
    assert bridge._params[1][SPD_KP] == 8.0 and bridge._params[1][0x7020] == 0.05
    # 書込後: 編集は消え、モータの値が更新され、「変更済み」が 2 件
    assert page.pending() == {} and page.cell_text(SPD_KP) == ""
    assert page.motor_value(SPD_KP) == 8.0
    assert page.counter_label.text() == tr("param_page.counter", pending=0, dirty=2)
    assert page.dirty_indices() == [SPD_KP, 0x7020]
    assert shell.toasts[-1][0] == tr("param_page.written", n=2)
    state_icon = page.table.item(page._rows[SPD_KP].row, page_mod.C_STATE)
    assert not state_icon.icon().isNull() and state_icon.toolTip() == tr("param_page.state_unsaved")
    assert page.save_btn.isEnabled() and page.save_btn.variant == "standard"
    assert not page.write_btn.isEnabled()

    # Flash 保存: アプリ共通の Flash 保存ダイアログに保存対象の一覧を渡す。キャンセルなら保存しない
    asked: list[tuple[int, list[FlashItem]]] = []

    def fake_confirm(parent, motor_id, items=()):
        asked.append((motor_id, list(items)))
        return len(asked) > 1  # 1 回目はキャンセル、2 回目は承認

    monkeypatch.setattr(page_mod, "confirm_flash_save", fake_confirm)
    assert page.save_flash() is False and page.dirty_indices() == [SPD_KP, 0x7020]
    assert asked[0] == (
        1, [FlashItem("速度ループ Kp", "8.00"), FlashItem("速度ループ Ki", "0.0500")]
    )
    assert page.save_flash() is True
    # 0x70xx は type 22 でも電源断で戻る: 保存しても「変更済み」は消えず、保存ボタンも使える
    assert page.dirty_indices() == [SPD_KP, 0x7020]
    assert page.counter_label.text() == tr("param_page.counter", pending=0, dirty=2)
    assert page.save_btn.isEnabled()


def test_wording_does_not_promise_persistence(env) -> None:
    page, _bridge, _s = env
    assert page.runtime_hint.text() == tr("param_page.runtime_hint")
    assert "0x70xx" in page.runtime_hint.text()
    assert page.save_btn.text() == tr("param_page.save_flash")
    for key in ("param_page.save_flash", "param_page.state_unsaved", "param_page.saved",
                "param_page.written", "params.save_confirm_detail", "flash.purpose"):
        txt = tr(key)
        assert "Flash" not in txt and "未保存" not in txt, key
        assert "電源を切っても" not in txt and "再起動後も" not in txt, key
    assert "type 22" in tr("param_page.save_flash")


def test_per_motor_pending_and_values(env, qtbot) -> None:
    page, bridge, _s = env
    page.set_pending(SPD_KP, 8.0)
    bridge.select_motor(2)
    assert page.motor_id() == 2 and page.pending() == {}
    assert page.cell_text(SPD_KP) == ""
    qtbot.waitUntil(lambda: page.motor_value(SPD_KP) is not None, timeout=3000)  # 初回は自動で読む
    page.set_pending(SPD_KP, 9.0)
    bridge.select_motor(1)
    assert page.pending() == {SPD_KP: 8.0} and page.cell_text(SPD_KP) == "8.00"


def test_read_button_reads_and_drops_matching_edits(env, qtbot) -> None:
    page, bridge, shell = env
    page.set_pending(SPD_KP, 12.0)
    bridge._params[1][SPD_KP] = 12.0  # モータ側が既に同じ値になった
    page.read_btn.click()
    qtbot.waitUntil(
        lambda: bool(shell.toasts) and shell.toasts[-1][0].startswith("28"), timeout=3000
    )
    assert page.pending() == {} and page.motor_value(SPD_KP) == 12.0


# ---------------------------------------------------------------- 書き出し・読み込み・既定値
def test_export_import_with_diff_preview(env, tmp_path, monkeypatch) -> None:
    page, bridge, _s = env
    path = tmp_path / "p.json"
    n = page.export_json(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert n == 28 and data["motor_id"] == 1 and data["params"]["0x701F"] == 6.0
    # ファイルを書き換えて取り込む: 差分だけが「新しい値」になる(指令・状態は除外)
    data["params"]["0x701F"] = 7.5
    data["params"]["0x7011"] = 0.012  # 同じ値 → 差分なし
    data["params"]["0x700A"] = 3.0  # 指令 → 対象外
    data["params"]["0x701C"] = 30.0  # 状態 → 対象外
    path.write_text(json.dumps(data), encoding="utf-8")
    diff = page.diff_from_file(path)
    assert [(d.index, new) for d, _old, new in diff] == [(SPD_KP, 7.5)]

    seen: dict = {}

    def fake_ask(cls, parent, **kw):
        seen.update(kw)
        return True

    monkeypatch.setattr(page_mod.ConfirmDialog, "ask", classmethod(fake_ask))
    assert page.import_json(path) == 1
    assert "速度ループ Kp  6 → 7.5" in seen["body"]
    assert page.pending() == {SPD_KP: 7.5} and page.cell_text(SPD_KP) == "7.50"
    assert bridge._params[1][SPD_KP] == 6.0  # 書込むまでモータは変わらない
    # 差分が無ければ何もしない
    page.set_pending(SPD_KP, None)
    path.write_text(json.dumps({"motor_id": 1, "params": {"0x701F": 6.0}}), encoding="utf-8")
    assert page.import_json(path, confirm=False) == 0


def test_reset_to_defaults_stages_only_settings(env, monkeypatch) -> None:
    page, bridge, _s = env
    bridge.write_param(1, SPD_KP, 9.0)
    bridge.write_param(1, 0x7006, 2.0)  # 指令(iq_ref): 既定値リセットの対象外
    monkeypatch.setattr(page_mod.ConfirmDialog, "ask", classmethod(lambda cls, parent, **kw: True))
    assert page.reset_to_defaults() == 1
    assert page.pending() == {SPD_KP: 6.0}
    assert page.reset_to_defaults() == 1  # 既に編集値でも、モータの値と違えば対象のまま


def test_empty_state_when_offline(qtbot) -> None:
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0, start_offline=True)
    shell = FakeShell()
    page = ParamsPage(bridge, shell)
    qtbot.addWidget(page)
    page.show()
    bridge.start()
    assert page.stack.currentWidget() is page.empty
    assert page.empty.title_label.text() == tr("param_page.empty_title")
    page.empty.action_clicked.emit()
    assert shell.connection_opened == 1
    bridge.connect_adapter({})
    bridge.scan()
    qtbot.waitUntil(lambda: page.stack.currentWidget() is page.content, timeout=5000)
    bridge.shutdown()


def test_theme_switch_keeps_page_consistent(env) -> None:
    page, _b, _s = env
    page.set_pending(SPD_KP, 8.0)
    theme_manager().apply("light")
    try:
        it = page.table.item(page._rows[VBUS].row, page_mod.C_VALUE)
        assert it.foreground().color().name().upper() == "#1B1D21"
        assert page.pending() == {SPD_KP: 8.0}
    finally:
        theme_manager().apply("dark")
