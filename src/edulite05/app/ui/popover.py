"""ポップオーバー(フライアウト)と Menu。角丸 8 + 影(影はポップオーバーとダイアログだけに使う)。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from edulite05.app.ui.style import tokens
from edulite05.app.ui.tokens import SP_PAD

_SHADOW_MARGIN = 20


class Popover(QWidget):
    """アンカーの下に出る Popup。内容は `popover.body`(QVBoxLayout)に追加する。

    外側クリック / Esc で閉じる(Qt.Popup)。`closed` シグナル。
    """

    closed = Signal()

    def __init__(self, parent: QWidget | None = None, *, width: int = 320) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(_SHADOW_MARGIN, _SHADOW_MARGIN, _SHADOW_MARGIN, _SHADOW_MARGIN)
        self._frame = QFrame()
        self._frame.setObjectName("PopoverBody")
        self._frame.setFixedWidth(width)
        outer.addWidget(self._frame)
        self.body = QVBoxLayout(self._frame)
        self.body.setContentsMargins(SP_PAD, SP_PAD, SP_PAD, SP_PAD)
        self.body.setSpacing(SP_PAD - 4)
        fx = QGraphicsDropShadowEffect(self._frame)
        fx.setBlurRadius(24)
        fx.setOffset(0, 4)
        c = QColor(tokens().shadow)
        c.setAlpha(tokens().shadow_alpha)
        fx.setColor(c)
        self._frame.setGraphicsEffect(fx)

    def show_below(self, anchor: QWidget, *, align: str = "left") -> None:
        """`anchor` の直下に表示する(画面の端ではみ出さないよう補正)。"""
        self.adjustSize()
        gp = anchor.mapToGlobal(QPoint(0, anchor.height()))
        x = gp.x() - _SHADOW_MARGIN if align == "left" else (
            gp.x() + anchor.width() - self.width() + _SHADOW_MARGIN
        )
        y = gp.y() + 4 - _SHADOW_MARGIN
        screen = anchor.screen() or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            x = min(max(x, avail.left()), avail.right() - self.width())
            y = min(y, avail.bottom() - self.height())
        self.move(x, y)
        self.show()

    def hideEvent(self, event) -> None:  # noqa: ANN001, N802
        super().hideEvent(event)
        self.closed.emit()


class Menu(QMenu):
    """QSS の角丸が効く(背景透過 + 枠なし)メニュー。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
