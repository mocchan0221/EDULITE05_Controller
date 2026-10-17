"""Section(見出し + 内容)と Expander(▸ 見出しで折りたたみ)。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QLayout, QVBoxLayout, QWidget

from edulite05.app.ui.button import Button
from edulite05.app.ui.label import Label
from edulite05.app.ui.tokens import SP_GROUP, SP_PAD, SP_RELATED


class Section(QFrame):
    """見出し(14 Semibold)+ 内容。`card=True` で面(カード)にする。

    カード面は「ひとまとまりのグループ」にだけ使い、入れ子は 1 段まで。
    内容は `section.body`(QVBoxLayout)に追加する: `section.add(widget)`。
    """

    def __init__(
        self, title: str = "", *, card: bool = False, description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("card", card)
        root = QVBoxLayout(self)
        pad = SP_PAD if card else 0
        root.setContentsMargins(pad, pad, pad, pad)
        root.setSpacing(SP_GROUP)
        self.title_label = Label(title, "heading")
        self.title_label.setVisible(bool(title))
        root.addWidget(self.title_label)
        self.description_label = Label(description, "caption", wrap=True)
        self.description_label.setVisible(bool(description))
        root.addWidget(self.description_label)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(SP_RELATED)
        root.addLayout(self.body)

    def add(self, widget: QWidget, stretch: int = 0) -> None:
        self.body.addWidget(widget, stretch)

    def add_layout(self, layout: QLayout) -> None:
        self.body.addLayout(layout)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)
        self.title_label.setVisible(bool(title))


class Expander(QWidget):
    """`Expander("制限と加速度")` … 見出しをクリックで開閉(既定は閉)。

    内容は `expander.content_layout` に追加するか `set_content(widget)`。`toggled(bool)`。
    """

    toggled = Signal(bool)

    def __init__(
        self, title: str, *, expanded: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._expanded = expanded
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SP_RELATED)
        self.header = Button(title, "subtle", icon="chevron-right")
        self.header.setProperty("align", "left")
        self.header.setCheckable(False)
        self.header.clicked.connect(self.toggle)
        root.addWidget(self.header)
        self._content = QWidget()
        self.content_layout = QVBoxLayout(self._content)
        self.content_layout.setContentsMargins(SP_RELATED, 0, 0, 0)
        self.content_layout.setSpacing(SP_PAD)
        root.addWidget(self._content)
        self._apply()

    def set_content(self, widget: QWidget) -> None:
        self.content_layout.addWidget(widget)

    def expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, on: bool) -> None:
        if on != self._expanded:
            self._expanded = on
            self._apply()
            self.toggled.emit(on)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def _apply(self) -> None:
        self._content.setVisible(self._expanded)
        self.header.set_icon_name("chevron-down" if self._expanded else "chevron-right")
