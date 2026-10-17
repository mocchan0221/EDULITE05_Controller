"""出力段階 HIGH への切替ダイアログ(ui_design.md §5.7)。

v1 の構成(前提チェック + 動詞ボタン + OS の並び順)は維持し、大きな HIGH バッジをやめて
⚠ アイコン + 見出しにした。実行ボタンは琥珀系(caution。赤にしない)。前提 3 つはすべて必須。
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from edulite05.app.i18n import tr
from edulite05.app.ui.dialogs.base import ActionDialog


class HighTierDialog(ActionDialog):
    """`HighTierDialog(parent).exec()`。承認されたら Accepted。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            title=tr("confirm.high_tier.title"),
            purpose=tr("confirm.high_tier.body"),
            icon="triangle-alert",
            icon_color="caution",
            impacts=[tr("dlg.high.impact1"), tr("dlg.high.impact2"), tr("dlg.high.impact3")],
            checks=[
                tr("confirm.high_tier.check_fixed"),
                tr("confirm.high_tier.check_clear"),
                tr("confirm.high_tier.check_power"),
            ],
            confirm_text=tr("confirm.high_tier.ok"),
            confirm_variant="caution",
        )


def confirm_high_tier(parent: QWidget | None = None) -> bool:
    """高出力への切替確認。承認されたら True。"""
    return HighTierDialog(parent).exec() == HighTierDialog.DialogCode.Accepted
