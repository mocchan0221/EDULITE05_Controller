"""Flash 保存の確認ダイアログ(ui_design.md §5.2)。保存対象の一覧を見せる。

Flash への保存は恒久化であって破壊的ではないので、実行ボタンは赤にしない(accent)。
パラメータページ(P2)は `confirm_flash_save(parent, motor_label, items)` を呼ぶ。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from edulite05.app.i18n import tr
from edulite05.app.ui.dialogs.base import ActionDialog
from edulite05.app.ui.label import Label
from edulite05.app.ui.tokens import H_ROW

MAX_VISIBLE_ROWS = 8


@dataclass(frozen=True, slots=True)
class FlashItem:
    """保存対象 1 件。`value_text` は単位込みの表示(例: `8.000 rad/s`)。"""

    name: str
    value_text: str


class FlashSaveDialog(ActionDialog):
    """`FlashSaveDialog(parent, motor_id=1, items=[FlashItem("速度ループ Kp", "8.000")])`。"""

    def __init__(
        self, parent: QWidget | None, *, motor_id: int, items: Sequence[FlashItem] = ()
    ) -> None:
        super().__init__(
            parent,
            title=tr("flash.title", id=motor_id),
            purpose=tr("flash.purpose"),
            icon="save",
            impacts=[tr("flash.impact1")],
            confirm_text=tr("flash.confirm"),
            confirm_variant="accent",
        )
        self.items = list(items)
        if self.items:
            self.table = self._build_table()
            self.form.addWidget(self.table)
            self.form.insertWidget(0, self._heading())
        else:
            self.table = None
            self.form.addWidget(self._note())

    def _heading(self) -> QWidget:
        return Label(tr("flash.items_title", n=len(self.items)), "heading")

    def _note(self) -> QWidget:
        return Label(tr("flash.no_items"), "secondary", wrap=True)

    def _build_table(self) -> QTableWidget:
        t = QTableWidget(len(self.items), 2)
        t.setHorizontalHeaderLabels(["", ""])
        t.horizontalHeader().setVisible(False)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        t.setShowGrid(False)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for i, it in enumerate(self.items):
            t.setRowHeight(i, H_ROW)
            t.setItem(i, 0, QTableWidgetItem(it.name))
            val = QTableWidgetItem(it.value_text)
            val.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.setItem(i, 1, val)
        rows = min(len(self.items), MAX_VISIBLE_ROWS)
        t.setFixedHeight(rows * H_ROW + 2)
        return t


def confirm_flash_save(
    parent: QWidget | None, motor_id: int, items: Sequence[FlashItem] = ()
) -> bool:
    """Flash 保存の確認。承認されたら True。"""
    dlg = FlashSaveDialog(parent, motor_id=motor_id, items=items)
    return dlg.exec() == FlashSaveDialog.DialogCode.Accepted
