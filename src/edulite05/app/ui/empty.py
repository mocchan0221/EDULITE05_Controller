"""空状態。アイコン 32 + 見出し 20 + 説明 14 + 主ボタン(次に押すべきものを 1 つだけ)。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from edulite05.app.ui.button import Button
from edulite05.app.ui.icon import pixmap
from edulite05.app.ui.label import Label
from edulite05.app.ui.style import theme_manager
from edulite05.app.ui.tokens import ICON_XL, SP_GROUP, SP_RELATED, SP_SECTION


class EmptyState(QWidget):
    """`EmptyState("plug", "アダプタが接続されていません", "…", action="接続")`。

    - `action_clicked` … 主ボタン(強調色)
    - `extra_layout` … 説明と主ボタンの間に追加する部品(ポート選択など)用
    """

    action_clicked = Signal()

    def __init__(
        self,
        icon: str,
        title: str,
        description: str = "",
        *,
        action: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP_SECTION, SP_SECTION, SP_SECTION, SP_SECTION)
        lay.setSpacing(SP_RELATED)
        lay.addStretch(1)
        self._icon = QLabel()
        self._icon.setFixedSize(ICON_XL, ICON_XL)
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(SP_RELATED)
        self.title_label = Label(title, "title", wrap=True)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.title_label)
        self.description_label = Label(description, "secondary", wrap=True)
        self.description_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self.description_label.setMaximumWidth(460)
        self.description_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self.description_label.setVisible(bool(description))
        desc_row = QHBoxLayout()  # 左右の伸びで中央に置く(折返し付きラベルは幅が縮むため)
        desc_row.setContentsMargins(0, 0, 0, 0)
        desc_row.addStretch(1)
        desc_row.addWidget(self.description_label, 100)
        desc_row.addStretch(1)
        lay.addLayout(desc_row)
        self.extra_layout = QVBoxLayout()
        self.extra_layout.setContentsMargins(0, SP_GROUP, 0, 0)
        self.extra_layout.setSpacing(SP_RELATED)
        lay.addLayout(self.extra_layout)
        self.action_button: Button | None = None
        if action:
            self.action_button = Button(action, "accent", large=True)
            self.action_button.clicked.connect(self.action_clicked)
            lay.addSpacing(SP_RELATED)
            lay.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(2)
        self._retint()
        theme_manager().changed.connect(self._retint)

    def set_texts(self, title: str, description: str = "") -> None:
        self.title_label.setText(title)
        self.description_label.setText(description)
        self.description_label.setVisible(bool(description))

    def set_icon_name(self, name: str) -> None:
        self._icon_name = name
        self._retint()

    def _retint(self, _name: str = "") -> None:
        self._icon.setPixmap(pixmap(self._icon_name, "text_tertiary", ICON_XL))
