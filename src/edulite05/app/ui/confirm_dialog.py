"""確認ダイアログ(ui_design.md §6)。タイトル = 質問形、本文 = 影響、前提チェック、ボタンは動詞。

`QDialogButtonBox` の役割(Accept / Reject)で OS の並び順に従う。破壊的ボタンを既定にしない
(既定ボタンはキャンセル)。前提チェックがすべて入るまで実行ボタンは押せず、理由をツールチップに出す。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from edulite05.app.i18n import tr
from edulite05.app.ui.button import Button
from edulite05.app.ui.icon import pixmap
from edulite05.app.ui.label import Label
from edulite05.app.ui.tokens import ICON_XL, SP_GROUP, SP_PAD, SP_RELATED, SP_SECTION

_TONE_ICON = {
    "neutral": ("circle-help", "text_secondary"),
    "caution": ("triangle-alert", "caution"),
    "critical": ("circle-alert", "critical"),
}


class ConfirmDialog(QDialog):
    """`ConfirmDialog(parent, title="ID 1 の CAN ID を 5 に変更しますか?", body="…",
    checks=["モータを固定した"], confirm_text="ID を 5 に変更", tone="caution")`。

    - `confirm_variant`: 実行ボタンの種類(standard / accent / caution)。赤(danger)は使わない
    - `require_text`: 指定すると、その文字列(例: 対象モータ ID)の入力が必要
    - 実行すると `accept()`。`ConfirmDialog.ask(...)` は承認されたら True
    """

    def __init__(
        self,
        parent: QWidget | None,
        *,
        title: str,
        body: str = "",
        checks: Sequence[str] = (),
        confirm_text: str = "",
        cancel_text: str | None = None,
        tone: str = "neutral",
        confirm_variant: str = "standard",
        require_text: str | None = None,
        require_hint: str = "",
        icon: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._require_text = require_text
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP_SECTION, SP_SECTION, SP_SECTION, SP_PAD)
        lay.setSpacing(SP_PAD)

        head = QHBoxLayout()
        head.setSpacing(SP_GROUP)
        icon_name, icon_color = _TONE_ICON.get(tone, _TONE_ICON["neutral"])
        self._icon = QLabel()
        self._icon.setFixedSize(ICON_XL, ICON_XL)
        self._icon.setPixmap(pixmap(icon or icon_name, icon_color, ICON_XL))
        head.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self.title_label = Label(title, "title", wrap=True)
        head.addWidget(self.title_label, 1)
        lay.addLayout(head)

        self.body_label = Label(body, "secondary", wrap=True)
        self.body_label.setVisible(bool(body))
        lay.addWidget(self.body_label)

        self.checkboxes: list[QCheckBox] = []
        if checks:
            box = QVBoxLayout()
            box.setSpacing(SP_RELATED)
            for text in checks:
                cb = QCheckBox(text)
                cb.toggled.connect(self._update_confirm)
                self.checkboxes.append(cb)
                box.addWidget(cb)
            lay.addLayout(box)

        self.text_edit: QLineEdit | None = None
        if require_text is not None:
            hint = require_hint or tr("dialog.require_text", text=require_text)
            lay.addWidget(Label(hint, "caption"))
            self.text_edit = QLineEdit()
            self.text_edit.setPlaceholderText(require_text)
            self.text_edit.textChanged.connect(self._update_confirm)
            lay.addWidget(self.text_edit)

        self.buttons = QDialogButtonBox()
        self.confirm_button = Button(confirm_text or tr("dialog.execute"), confirm_variant)
        self.cancel_button = Button(cancel_text or tr("dialog.cancel"), "standard")
        self.buttons.addButton(self.confirm_button, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(self.cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        self.confirm_button.setAutoDefault(False)
        self.cancel_button.setDefault(True)  # 既定ボタンはキャンセル(破壊側にしない)
        self.cancel_button.setFocus()
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._update_confirm()

    def confirm_enabled(self) -> bool:
        return all(cb.isChecked() for cb in self.checkboxes) and (
            self._require_text is None
            or (self.text_edit is not None and self.text_edit.text().strip() == self._require_text)
        )

    def _update_confirm(self, *_args: object) -> None:
        ok = self.confirm_enabled()
        reason = ""
        if not ok:
            reason = tr("dialog.need_checks") if self.checkboxes else tr("dialog.need_text")
            if self._require_text is not None and all(cb.isChecked() for cb in self.checkboxes):
                reason = tr("dialog.need_text")
        self.confirm_button.set_available(ok, reason)

    @classmethod
    def ask(cls, parent: QWidget | None, **kwargs: object) -> bool:
        dlg = cls(parent, **kwargs)  # type: ignore[arg-type]
        return dlg.exec() == QDialog.DialogCode.Accepted
