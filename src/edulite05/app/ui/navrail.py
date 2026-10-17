"""ナビゲーションレール(左 72px、アイコン 20 + 文字 12 の縦並び)。ui_design.md §4。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QAbstractButton, QFrame, QSizePolicy, QVBoxLayout, QWidget

from edulite05.app.ui.icon import pixmap
from edulite05.app.ui.style import make_font, theme_manager, tokens
from edulite05.app.ui.tokens import FS_CAPTION, ICON_M, R_CONTROL, SP_ICON, SP_RELATED, W_NAV

_ITEM_H = 56
_BAR_W = 3
_DOT = 8


class NavItem(QAbstractButton):
    """レールの 1 項目。選択中は面 + 左の 3px アクセントバー + Semibold。"""

    def __init__(self, item_id: str, label: str, icon_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.item_id = item_id
        self._label = label
        self._icon_name = icon_name
        self._badge = False
        self._hover = False
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(_ITEM_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_badge(self, on: bool) -> None:
        if on != self._badge:
            self._badge = on
            self.update()

    def badge(self) -> bool:
        return self._badge

    def label_text(self) -> str:
        return self._label

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(W_NAV - 2 * SP_RELATED, _ITEM_H)

    def enterEvent(self, event) -> None:  # noqa: ANN001, N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001, N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        t = tokens()
        sel = self.isChecked()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        if sel:
            p.setBrush(QColor(t.bg_card))
            p.drawRoundedRect(r, R_CONTROL, R_CONTROL)
        if self._hover and not sel:
            p.setBrush(QColor(t.overlay_hover))
            p.drawRoundedRect(r, R_CONTROL, R_CONTROL)
        if sel:  # 左のアクセントバー
            p.setBrush(QColor(t.accent))
            p.drawRoundedRect(QRectF(0, 12, _BAR_W, r.height() - 24), 1.5, 1.5)
        color = t.text_primary if sel else t.text_secondary
        px = pixmap(self._icon_name, color, ICON_M)
        ix = (r.width() - ICON_M) / 2
        p.drawPixmap(int(ix), 9, px)
        font = make_font(FS_CAPTION, semibold=sel)
        p.setFont(font)
        p.setPen(QColor(color))
        fm = QFontMetrics(font)
        ty = 9 + ICON_M + SP_ICON
        label_rect = QRectF(0, ty, r.width(), fm.height() + 2)
        p.drawText(label_rect, Qt.AlignmentFlag.AlignHCenter, self._label)
        if self._badge:  # 未確認の故障(赤い点)
            p.setPen(QColor(t.bg_base))
            p.setBrush(QColor(t.critical))
            p.drawEllipse(QRectF(ix + ICON_M - 5, 5, _DOT, _DOT))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor(t.accent))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), R_CONTROL, R_CONTROL)
        p.end()


class NavRail(QFrame):
    """`rail.add_item("operate", "運転", "gauge")` … `current_changed(id)`。

    `bottom=True` の項目は下端に寄せる(設定)。`set_badge(id, True)` で赤い点(診断の未確認故障)。
    """

    current_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NavRail")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(W_NAV)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(SP_RELATED, SP_RELATED, SP_RELATED, SP_RELATED)
        self._lay.setSpacing(SP_ICON)
        self._stretch_added = False
        self._items: dict[str, NavItem] = {}
        self._current: str | None = None
        theme_manager().changed.connect(self._on_theme)

    def _on_theme(self, _name: str = "") -> None:
        for it in self._items.values():
            it.update()

    def add_item(
        self, item_id: str, label: str, icon_name: str, *, bottom: bool = False, tooltip: str = ""
    ) -> NavItem:
        item = NavItem(item_id, label, icon_name)
        if tooltip:
            item.setToolTip(tooltip)
        if bottom and not self._stretch_added:
            self._lay.addStretch(1)
            self._stretch_added = True
        self._lay.addWidget(item)
        item.clicked.connect(lambda _c=False, i=item_id: self.set_current(i))
        self._items[item_id] = item
        if self._current is None:
            self.set_current(item_id)
        return item

    def item_ids(self) -> list[str]:
        return list(self._items)

    def item(self, item_id: str) -> NavItem:
        return self._items[item_id]

    def current(self) -> str | None:
        return self._current

    def set_current(self, item_id: str) -> None:
        if item_id not in self._items:
            return
        for i, it in self._items.items():
            it.setChecked(i == item_id)
        if item_id != self._current:
            self._current = item_id
            self.current_changed.emit(item_id)

    def set_badge(self, item_id: str, on: bool) -> None:
        it = self._items.get(item_id)
        if it is not None:
            it.set_badge(on)
