"""保守ページ・設定ページ共通の「boxed list」(GNOME HIG のグループ化リスト。ui_design.md §5.4)。

    group = BoxedList()
    group.add_row(ListRow("ゼロ点を設定", "いまの位置を…", tags=["位置が変わる"], trailing=btn))

- `BoxedList` … カード面(bg.card)に行を縦に並べ、行の間に 1px の区切り線を入れる
- `ListRow` … タイトル(14 Semibold)+ 影響タグ(無彩色の小さなバッジ)+ 1 行説明(12 二次色)+ 右端の部品
- `GroupHeader` … グループ見出し(14 Semibold)。アイコン付き(例: 「注意が必要な操作」の ⚠)にできる
- `centered_scroll()` … 読みやすい幅(既定 760)で中央に置くスクロール領域

色・寸法はすべてトークン経由。ページは部品とこれだけで組む。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from edulite05.app.ui.badge import StatusBadge
from edulite05.app.ui.icon import pixmap
from edulite05.app.ui.label import Label
from edulite05.app.ui.style import theme_manager
from edulite05.app.ui.tokens import ICON_S, SP_GROUP, SP_ICON, SP_PAD, SP_RELATED, SP_SECTION
from edulite05.app.ui.util import divider

COLUMN_WIDTH = 760


class GroupHeader(QWidget):
    """グループ見出し。`icon` と `tone`(caution など)を付けられる(色は文字とアイコンだけ)。"""

    def __init__(
        self, text: str, *, icon: str | None = None, tone: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon
        self._tone = tone
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP_RELATED)
        self._icon = QLabel()
        self._icon.setFixedSize(ICON_S, ICON_S)
        self._icon.setVisible(icon is not None)
        lay.addWidget(self._icon)
        self.label = Label(text, "heading", tone=tone)
        lay.addWidget(self.label)
        lay.addStretch(1)
        self._refresh()
        theme_manager().changed.connect(self._refresh)

    def _refresh(self, _name: str = "") -> None:
        if self._icon_name is not None:
            color = "caution" if self._tone == "caution" else "text_secondary"
            self._icon.setPixmap(pixmap(self._icon_name, color, ICON_S))


class ListRow(QWidget):
    """boxed list の 1 行。`trailing` は右端(ボタン・セグメント・コンボなど)で縦中央に置く。"""

    def __init__(
        self,
        title: str,
        description: str = "",
        *,
        tags: Sequence[str] = (),
        trailing: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(SP_PAD, SP_GROUP, SP_PAD, SP_GROUP)
        outer.setSpacing(SP_PAD)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(SP_ICON)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(SP_RELATED)
        self.title_label = Label(title, "heading")
        title_row.addWidget(self.title_label)
        self.tags: list[StatusBadge] = []
        for t in tags:  # 影響タグ(無彩色の小さなバッジ)はタイトルの右に並べる
            badge = StatusBadge(t, "neutral")
            self.tags.append(badge)
            title_row.addWidget(badge)
        title_row.addStretch(1)
        text.addLayout(title_row)
        self.description_label = Label(description, "caption", wrap=True)
        self.description_label.setVisible(bool(description))
        text.addWidget(self.description_label)
        outer.addLayout(text, 1)
        self.trailing = trailing
        if trailing is not None:
            outer.addWidget(trailing, 0, Qt.AlignmentFlag.AlignVCenter)

    def tag_texts(self) -> list[str]:
        return [b.text() for b in self.tags]


class BoxedList(QFrame):
    """行を囲むカード。行の間に区切り線。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self.rows: list[QWidget] = []

    def add_row(self, row: QWidget) -> None:
        if self.rows:
            self._lay.addWidget(divider())
        self._lay.addWidget(row)
        self.rows.append(row)


def centered_scroll(max_width: int = COLUMN_WIDTH) -> tuple[QScrollArea, QVBoxLayout]:
    """中身を `max_width` までの幅で中央に置くスクロール領域。返す layout にグループを足す。"""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    host = QWidget()
    host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    host.setProperty("surface", "layer")
    outer = QHBoxLayout(host)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addStretch(1)
    column = QWidget()
    column.setMaximumWidth(max_width)
    column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    lay = QVBoxLayout(column)
    lay.setContentsMargins(0, 0, 0, SP_SECTION)
    lay.setSpacing(SP_RELATED)
    outer.addWidget(column, 100)
    outer.addStretch(1)
    area.setWidget(host)
    return area, lay
