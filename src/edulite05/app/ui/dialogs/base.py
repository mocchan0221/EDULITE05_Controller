"""ダイアログの共通基底 `ActionDialog`(ui_design.md §5.4・§5.7・§6 ConfirmDialog)。

構成(上から): ⚠ などのアイコン + 質問形のタイトル / 目的 / 対象モータ / 入力フォーム /
「実行すると」(影響の箇条書き)/ 「実行前の確認」(前提チェック)/ 対象 ID の入力 / ボタン。

- 実行ボタンは動詞(「ID を 2 に変更」)。`QDialogButtonBox` の役割で OS の並び順に従う
- 既定ボタンはキャンセル。実行ボタン(破壊的になり得る側)を既定にしない
- 前提チェック・入力の確認・追加の検証(`set_blocker`)がそろうまで実行ボタンは押せず、
  理由をツールチップと注記に出す(原則 6)
- 赤(danger)は非常停止専用なので使わない。実行ボタンは standard / accent / caution
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
from edulite05.app.ui.badge import StatusBadge
from edulite05.app.ui.button import Button
from edulite05.app.ui.icon import pixmap
from edulite05.app.ui.label import Label
from edulite05.app.ui.section import Section
from edulite05.app.ui.style import theme_manager
from edulite05.app.ui.tokens import ICON_XL, SP_GROUP, SP_PAD, SP_RELATED, SP_SECTION

DIALOG_WIDTH = 560
_BULLET_W = 12


class _Bullet(QWidget):
    """「・」+ 折り返し本文(2 行目以降を本文にそろえる)。"""

    def __init__(self, text: str) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP_RELATED)
        dot = Label("•", "secondary")
        dot.setFixedWidth(_BULLET_W)
        dot.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.label = Label(text, "secondary", wrap=True)
        lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.label, 1)


class ActionDialog(QDialog):
    """`ActionDialog(parent, title=…, purpose=…, impacts=[…], checks=[…], confirm_text=…)`。

    サブクラスは `self.form`(QVBoxLayout)に入力部品を足し、値が変わるたびに
    `set_impacts` / `set_confirm_text` / `set_blocker` / `set_check_text` を呼ぶ。
    """

    def __init__(
        self,
        parent: QWidget | None,
        *,
        title: str,
        purpose: str = "",
        icon: str = "circle-help",
        icon_color: str = "text_secondary",
        target: tuple[str, str] | None = None,
        impacts: Sequence[str] = (),
        impact_title: str | None = None,
        checks: Sequence[str] = (),
        checks_title: str | None = None,
        confirm_text: str = "",
        confirm_variant: str = "standard",
        require_text: str | None = None,
        width: int = DIALOG_WIDTH,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._require_text = require_text
        self._blocker = ""
        self._icon_name = icon
        self._icon_color = icon_color

        root = QVBoxLayout(self)
        root.setContentsMargins(SP_SECTION, SP_SECTION, SP_SECTION, SP_PAD)
        root.setSpacing(SP_PAD)

        # ---- アイコン + タイトル(質問形) ----
        head = QHBoxLayout()
        head.setSpacing(SP_GROUP)
        self._icon = QLabel()
        self._icon.setFixedSize(ICON_XL, ICON_XL)
        head.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = Label(title, "title", wrap=True)
        head.addWidget(self.title_label, 1)
        root.addLayout(head)

        self.purpose_label = Label(purpose, "secondary", wrap=True)
        self.purpose_label.setVisible(bool(purpose))
        root.addWidget(self.purpose_label)

        # ---- 対象モータ ----
        self.target_badge: StatusBadge | None = None
        if target is not None:
            row = QHBoxLayout()
            row.setSpacing(SP_RELATED)
            row.addWidget(Label(tr("dlg.target"), "caption"))
            row.addWidget(Label(target[0], "heading"))
            self.target_badge = StatusBadge.for_ui_state(target[1])
            row.addWidget(self.target_badge)
            row.addStretch(1)
            root.addLayout(row)

        # ---- 入力フォーム(サブクラスが使う)と注記 ----
        self.form = QVBoxLayout()
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setSpacing(SP_RELATED)
        root.addLayout(self.form)
        self.note = Label("", "caption", tone="critical", wrap=True)
        self.note.setVisible(False)
        root.addWidget(self.note)

        # ---- 影響 ----
        self.impact_section = Section(impact_title or tr("dlg.impact_title"), card=True)
        root.addWidget(self.impact_section)
        self._bullets: list[_Bullet] = []
        self.set_impacts(impacts)

        # ---- 前提チェック ----
        self.checkboxes: list[QCheckBox] = []
        self.checks_title = Label(checks_title or tr("dlg.checks_title"), "heading")
        self._checks_box = QVBoxLayout()
        self._checks_box.setSpacing(SP_RELATED)
        if checks:
            root.addWidget(self.checks_title)
        for text in checks:
            cb = QCheckBox(text)
            cb.toggled.connect(self._update_confirm)
            self.checkboxes.append(cb)
            self._checks_box.addWidget(cb)
        root.addLayout(self._checks_box)

        # ---- 対象 ID の入力(注意グループ) ----
        self.text_edit: QLineEdit | None = None
        if require_text is not None:
            root.addWidget(Label(tr("dlg.require_hint", text=require_text), "secondary", wrap=True))
            self.text_edit = QLineEdit()
            self.text_edit.setProperty("mono", True)
            self.text_edit.textChanged.connect(self._update_confirm)
            root.addWidget(self.text_edit)

        # ---- ボタン(動詞 + キャンセル) ----
        self.buttons = QDialogButtonBox()
        self.confirm_button = Button(confirm_text or tr("dialog.execute"), confirm_variant)
        self.cancel_button = Button(tr("dialog.cancel"), "standard")
        self.buttons.addButton(self.confirm_button, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(self.cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.setDefault(False)
        self.cancel_button.setAutoDefault(True)
        self.cancel_button.setDefault(True)  # 既定はキャンセル(実行側にしない)
        self.cancel_button.setFocus()
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self._refresh_icon()
        theme_manager().changed.connect(self._refresh_icon)
        self._update_confirm()

    # ---- 見た目 ----
    def _refresh_icon(self, _name: str = "") -> None:
        self._icon.setPixmap(pixmap(self._icon_name, self._icon_color, ICON_XL))

    def set_impacts(self, impacts: Sequence[str]) -> None:
        """「実行すると」の箇条書きを差し替える。"""
        for b in self._bullets:
            self.impact_section.body.removeWidget(b)
            b.hide()  # deleteLater は次のイベントループまで効かない。それまで左上に残らないよう外す
            b.setParent(None)
            b.deleteLater()
        self._bullets = []
        for text in impacts:
            b = _Bullet(text)
            self._bullets.append(b)
            self.impact_section.add(b)
        self.impact_section.setVisible(bool(impacts))

    def impacts(self) -> list[str]:
        return [b.label.text() for b in self._bullets]

    def set_confirm_text(self, text: str) -> None:
        self.confirm_button.setText(text)

    def set_check_text(self, index: int, text: str) -> None:
        self.checkboxes[index].setText(text)

    # ---- 実行可否 ----
    def set_blocker(self, reason: str, *, show_note: bool = True) -> None:
        """入力が不正などで実行できないとき理由を渡す(空文字で解除)。ツールチップに出し、
        `show_note` なら赤い注記も出す(「選択してください」のような案内では出さない)。"""
        self._blocker = reason
        self.note.setText(reason if show_note else "")
        self.note.setVisible(bool(reason) and show_note)
        self._update_confirm()

    def confirm_enabled(self) -> bool:
        return self._reason() == ""

    def _reason(self) -> str:
        if self._blocker:
            return self._blocker
        if not all(cb.isChecked() for cb in self.checkboxes):
            return tr("dialog.need_checks")
        if self._require_text is not None and not (
            self.text_edit is not None and self.text_edit.text().strip() == self._require_text
        ):
            return tr("dialog.need_text")
        return ""

    def _update_confirm(self, *_args: object) -> None:
        reason = self._reason()
        self.confirm_button.set_available(reason == "", reason)

    @classmethod
    def ask(cls, parent: QWidget | None, **kwargs: object) -> bool:
        dlg = cls(parent, **kwargs)  # type: ignore[arg-type]
        return dlg.exec() == QDialog.DialogCode.Accepted
