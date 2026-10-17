"""P3 のダイアログ(app/ui/dialogs)のテスト(offscreen)。

目的・影響・前提チェック・動詞ボタン・対象 ID の入力・既定ボタン・ボタンの役割を確認する。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from edulite05.app.i18n import tr
from edulite05.app.ui.dialogs import (
    ActionDialog,
    ActiveReportDialog,
    CanTimeoutDialog,
    EStopReleaseDialog,
    FlashItem,
    FlashSaveDialog,
    HighTierDialog,
    MotorTarget,
    RestoreProtocolDialog,
    SetBaudDialog,
    SetCanIdDialog,
    SetZeroDialog,
    SwitchProtocolDialog,
    confirm_flash_save,
    confirm_high_tier,
    confirm_release_estop,
)
from edulite05.app.ui.style import theme_manager

MOTOR = MotorTarget(1, "EL05 #1", "disabled")


@pytest.fixture(autouse=True)
def _theme():
    theme_manager().apply("dark")


def check_all(dlg: ActionDialog) -> None:
    for cb in dlg.checkboxes:
        cb.setChecked(True)


def drive_modal(fn) -> None:
    """モーダルが開いたら `fn(dialog)` を呼ぶ(exec() を抜けさせるのは fn の役目)。"""

    def tick() -> None:
        dlg = QApplication.activeModalWidget()
        if dlg is None:
            QTimer.singleShot(20, tick)
        else:
            fn(dlg)

    QTimer.singleShot(20, tick)


# ------------------------------------------------------------------ 共通の作法
def test_base_dialog_rules(qtbot) -> None:
    dlg = ActionDialog(
        None, title="質問?", purpose="目的", checks=["a", "b"], confirm_text="実行する",
        impacts=["影響 1"], require_text="3",
    )
    qtbot.addWidget(dlg)
    # 実行ボタンは動詞。キャンセルが既定で、実行ボタンは既定にならない
    assert dlg.confirm_button.text() == "実行する"
    assert dlg.cancel_button.isDefault() and not dlg.confirm_button.isDefault()
    # OS の並び順は QDialogButtonBox の役割に任せる
    assert dlg.buttons.buttonRole(dlg.confirm_button) == QDialogButtonBox.ButtonRole.AcceptRole
    assert dlg.buttons.buttonRole(dlg.cancel_button) == QDialogButtonBox.ButtonRole.RejectRole
    # チェック → 入力の順にそろえる。押せない間は理由をツールチップに出す
    assert not dlg.confirm_button.isEnabled()
    assert dlg.confirm_button.toolTip() == tr("dialog.need_checks")
    check_all(dlg)
    assert not dlg.confirm_button.isEnabled()
    assert dlg.confirm_button.toolTip() == tr("dialog.need_text")
    dlg.text_edit.setText("4")
    assert not dlg.confirm_button.isEnabled()
    dlg.text_edit.setText("3")
    assert dlg.confirm_button.isEnabled() and dlg.confirm_button.toolTip() == ""
    # 追加の検証(blocker)でも押せなくなり、理由を注記に出す
    dlg.set_blocker("入力が不正です")
    assert not dlg.confirm_button.isEnabled() and dlg.note.text() == "入力が不正です"
    dlg.set_blocker("")
    assert dlg.confirm_button.isEnabled() and not dlg.note.isVisibleTo(dlg)


def test_confirm_button_is_never_danger(qtbot) -> None:
    """赤(danger)は非常停止専用。どのダイアログの実行ボタンも danger にしない。"""
    for dlg in (
        HighTierDialog(), EStopReleaseDialog(), SetZeroDialog(None, MOTOR),
        SetCanIdDialog(None, MOTOR), SetBaudDialog(None, MOTOR),
        SwitchProtocolDialog(None, MOTOR), RestoreProtocolDialog(None, MOTOR),
    ):
        qtbot.addWidget(dlg)
        assert dlg.confirm_button.variant != "danger"
        assert not dlg.confirm_button.isDefault()


# ------------------------------------------------------------------ HIGH 切替(§5.7)
def test_high_tier_dialog(qtbot) -> None:
    dlg = HighTierDialog()
    qtbot.addWidget(dlg)
    # ⚠ アイコン + 見出し(大きな HIGH バッジは置かない)。実行ボタンは琥珀(caution)
    assert dlg._icon_name == "triangle-alert" and dlg._icon_color == "caution"
    assert dlg.title_label.text() == tr("confirm.high_tier.title")
    assert dlg.confirm_button.variant == "caution"
    assert dlg.confirm_button.text() == tr("confirm.high_tier.ok")
    # 前提チェック 3 つがすべて必須
    assert [cb.text() for cb in dlg.checkboxes] == [
        tr("confirm.high_tier.check_fixed"),
        tr("confirm.high_tier.check_clear"),
        tr("confirm.high_tier.check_power"),
    ]
    assert not dlg.confirm_button.isEnabled()
    for cb in dlg.checkboxes[:-1]:
        cb.setChecked(True)
    assert not dlg.confirm_button.isEnabled()
    dlg.checkboxes[-1].setChecked(True)
    assert dlg.confirm_button.isEnabled()
    assert dlg.cancel_button.isDefault()


def test_confirm_high_tier_accept_and_cancel(qtbot) -> None:
    def accept(dlg) -> None:
        check_all(dlg)
        dlg.confirm_button.click()

    drive_modal(accept)
    assert confirm_high_tier(None) is True
    drive_modal(lambda dlg: dlg.cancel_button.click())
    assert confirm_high_tier(None) is False


# ------------------------------------------------------------------ 非常停止の解除
def test_estop_release_dialog(qtbot) -> None:
    dlg = EStopReleaseDialog()
    qtbot.addWidget(dlg)
    assert [cb.text() for cb in dlg.checkboxes] == [tr("confirm.estop_release.check")]
    assert dlg.checkboxes[0].text() == "安全を確認しました"
    assert not dlg.confirm_button.isEnabled()
    # 解除後もモータは停止中のまま、自動では再開しない
    text = " ".join(dlg.impacts())
    assert "停止中" in text and "再開しません" in text
    dlg.checkboxes[0].setChecked(True)
    assert dlg.confirm_button.isEnabled()


def test_confirm_release_estop_flow(qtbot) -> None:
    drive_modal(lambda dlg: dlg.cancel_button.click())
    assert confirm_release_estop(None) is False

    def accept(dlg) -> None:
        dlg.checkboxes[0].setChecked(True)
        dlg.confirm_button.click()

    drive_modal(accept)
    assert confirm_release_estop(None) is True


# ------------------------------------------------------------------ よく使う設定
def test_set_zero_dialog(qtbot) -> None:
    dlg = SetZeroDialog(None, MOTOR)
    qtbot.addWidget(dlg)
    assert dlg.tool == "set_zero" and dlg.tool_args() == {}
    assert "ID1" in dlg.title_label.text()
    assert dlg.target_badge is not None
    assert len(dlg.impacts()) == 3 and len(dlg.checkboxes) == 2
    assert dlg.text_edit is None  # 「よく使う設定」は ID 入力を求めない
    assert not dlg.confirm_button.isEnabled()
    check_all(dlg)
    assert dlg.confirm_button.isEnabled()


def test_active_report_dialog(qtbot) -> None:
    dlg = ActiveReportDialog(None, MOTOR)
    qtbot.addWidget(dlg)
    assert dlg.tool_args() == {"on": True, "period_n": 0}
    assert "10 ms" in dlg.confirm_button.text()
    dlg.period.setCurrentIndex(dlg.period.findData(3))  # 10 + 3 × 5 = 25 ms
    assert dlg.tool_args()["period_n"] == 3 and "25 ms" in dlg.confirm_button.text()
    assert "40" in " ".join(dlg.impacts())  # 1000 / 25 = 40 フレーム/s
    dlg.mode.set_current("off")
    assert dlg.tool_args()["on"] is False and not dlg.period.isEnabled()
    assert dlg.confirm_button.text() == tr("dlg.report.confirm_off")


def test_can_timeout_dialog(qtbot) -> None:
    dlg = CanTimeoutDialog(None, MOTOR)
    qtbot.addWidget(dlg)
    assert dlg.tool_args() == {"seconds": 0.5, "seconds_text": "0.5 s"}
    assert "0.5 s" in dlg.confirm_button.text()
    dlg.spin.setValue(0.0)
    assert dlg.tool_args()["seconds"] == 0.0
    assert dlg.confirm_button.text() == tr("dlg.timeout.confirm_off")
    assert "無効" in dlg.impacts()[0]


# ------------------------------------------------------------------ 注意が必要な操作
def test_set_can_id_dialog(qtbot) -> None:
    dlg = SetCanIdDialog(None, MOTOR)
    qtbot.addWidget(dlg)
    assert dlg.tool == "set_can_id"
    assert dlg.confirm_button.variant == "standard"
    assert dlg.new_id() == 2 and dlg.confirm_button.text() == "ID を 2 に変更"
    assert "2" in dlg.checkboxes[0].text()
    # 対象モータ ID の入力が必要(チェックだけでは押せない)
    check_all(dlg)
    assert not dlg.confirm_button.isEnabled()
    dlg.text_edit.setText("2")  # 新しい ID ではなく、対象(現在)の ID を打つ
    assert not dlg.confirm_button.isEnabled()
    dlg.text_edit.setText("1")
    assert dlg.confirm_button.isEnabled()
    assert dlg.tool_args() == {"new_id": 2}
    # 現在と同じ ID は不可(理由つき)
    dlg.spin.setValue(1)
    assert not dlg.confirm_button.isEnabled()
    assert dlg.note.text() == tr("dlg.set_id.same")
    dlg.spin.setValue(127)
    assert dlg.confirm_button.isEnabled() and dlg.confirm_button.text() == "ID を 127 に変更"
    assert dlg.spin.maximum() == 127  # ID 範囲 0〜127(§1.3)


def test_set_baud_dialog(qtbot) -> None:
    dlg = SetBaudDialog(None, MOTOR)
    qtbot.addWidget(dlg)
    assert dlg.reboot_required
    # 選ぶまで実行できない(危険な値を既定にしない)
    check_all(dlg)
    dlg.text_edit.setText("1")
    assert not dlg.confirm_button.isEnabled()
    assert dlg.confirm_button.toolTip() == tr("dlg.choose")
    dlg.combo.setCurrentIndex(dlg.combo.findData(2))
    assert dlg.confirm_button.isEnabled()
    assert dlg.confirm_button.text() == "ボーレートを 500 kbps に変更"
    assert dlg.tool_args() == {"baud": 2, "baud_text": "500 kbps"}
    assert any("電源" in line for line in dlg.impacts())


def test_switch_protocol_dialog(qtbot) -> None:
    dlg = SwitchProtocolDialog(None, MOTOR)
    qtbot.addWidget(dlg)
    assert dlg.reboot_required and dlg.choice() is None
    check_all(dlg)
    dlg.text_edit.setText("1")
    assert not dlg.confirm_button.isEnabled()  # 切り替え先を選ぶまで
    dlg.combo.setCurrentIndex(dlg.combo.findData(2))
    assert dlg.choice() == "mit" and dlg.confirm_button.isEnabled()
    assert dlg.confirm_button.text() == "プロトコルを MIT に切り替え"
    assert dlg.tool_args()["protocol"] == 2
    text = " ".join(dlg.impacts())
    assert "通信できなくなります" in text and "戻す" in text
    dlg.combo.setCurrentIndex(dlg.combo.findData(0))
    assert dlg.choice() == "private" and len(dlg.impacts()) == 1


def test_restore_protocol_dialog(qtbot) -> None:
    # 選択中のモータが無くても開ける。対象はダイアログで入力する(ID 0〜127 + いまのプロトコル)
    dlg = RestoreProtocolDialog(None, None)
    qtbot.addWidget(dlg)
    assert dlg.target_badge is None and dlg.text_edit is None
    assert (dlg.id_spin.minimum(), dlg.id_spin.maximum()) == (0, 127)
    check_all(dlg)
    assert not dlg.confirm_button.isEnabled()  # プロトコルを選ぶまで
    dlg.id_spin.setValue(5)
    dlg.combo.setCurrentIndex(dlg.combo.findData("canopen"))
    assert dlg.confirm_button.isEnabled() and dlg.target_id() == 5
    assert "すべてのモータ" in dlg.impacts()[0]  # CANopen の戻しフレームはモータ ID を含まない
    assert dlg.tool_args() == {"from_protocol": "canopen"}
    dlg.combo.setCurrentIndex(dlg.combo.findData("mit"))
    assert "すべて" not in dlg.impacts()[0] and "ID5" in dlg.impacts()[0]
    assert "ID5" in dlg.confirm_button.text()
    # 選択中のモータがあれば入力欄の初期値にする
    assert RestoreProtocolDialog(None, MOTOR).target_id() == 1


# ------------------------------------------------------------------ Flash 保存
def test_flash_save_dialog(qtbot) -> None:
    items = [FlashItem(f"パラメータ {i}", f"{i}.000 rad/s") for i in range(12)]
    dlg = FlashSaveDialog(None, motor_id=1, items=items)
    qtbot.addWidget(dlg)
    assert dlg.confirm_button.variant == "accent"  # 破壊的ではなく恒久化 → 赤くしない
    assert dlg.confirm_button.isEnabled() and dlg.cancel_button.isDefault()
    assert dlg.table is not None and dlg.table.rowCount() == 12
    assert dlg.table.height() < 12 * 28  # 長い一覧はスクロール(行数の上限あり)
    empty = FlashSaveDialog(None, motor_id=1)
    qtbot.addWidget(empty)
    assert empty.table is None


def test_confirm_flash_save(qtbot) -> None:
    drive_modal(lambda dlg: dlg.confirm_button.click())
    assert confirm_flash_save(None, 1, [FlashItem("Kp", "8")]) is True
    drive_modal(lambda dlg: dlg.cancel_button.click())
    assert confirm_flash_save(None, 1) is False
