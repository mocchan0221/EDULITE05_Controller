"""非常停止の解除ダイアログ(ui_design.md §3)。2 段階: バナーの [非常停止を解除…] → この確認。

「安全を確認しました」のチェックが必須。解除しても全モータは停止中(無効)のままで、自動では再開しない。
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from edulite05.app.i18n import tr
from edulite05.app.ui.dialogs.base import ActionDialog


class EStopReleaseDialog(ActionDialog):
    """`EStopReleaseDialog(parent).exec()`。承認されたら Accepted。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            title=tr("confirm.estop_release.title"),
            purpose=tr("dlg.estop.hint"),
            icon="octagon-x",
            icon_color="critical",
            impacts=[tr("dlg.estop.impact1"), tr("dlg.estop.impact2"), tr("dlg.estop.impact3")],
            checks=[tr("confirm.estop_release.check")],
            checks_title=tr("dlg.estop.check_title"),
            confirm_text=tr("confirm.estop_release.ok"),
            confirm_variant="standard",
        )


def confirm_release_estop(parent: QWidget | None = None) -> bool:
    """非常停止の解除確認。承認されたら True。"""
    return EStopReleaseDialog(parent).exec() == EStopReleaseDialog.DialogCode.Accepted
