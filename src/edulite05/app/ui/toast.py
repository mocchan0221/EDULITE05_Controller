"""トースト。一時的な結果通知(3 s)。故障はトーストにしない(バナー・診断ページへ)。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QWidget

from edulite05.app.ui.icon import pixmap
from edulite05.app.ui.label import Label
from edulite05.app.ui.style import theme_manager, tokens
from edulite05.app.ui.tokens import ICON_S, SP_GROUP, SP_PAD, SP_RELATED

TOAST_MS = 3000
_BOTTOM_MARGIN = 24
_KINDS = {"info": ("info", "text_secondary"), "success": ("circle-check", "success"),
          "caution": ("triangle-alert", "caution")}


class Toast(QFrame):
    """ウィンドウの子として下部中央に出る角丸 8 + 影の通知。`ToastHost` から使う。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP_PAD, SP_GROUP, SP_PAD, SP_GROUP)
        lay.setSpacing(SP_RELATED)
        self._icon = QLabel()
        self._icon.setFixedSize(ICON_S, ICON_S)
        self._label = Label("", "body")
        lay.addWidget(self._icon)
        lay.addWidget(self._label)
        fx = QGraphicsDropShadowEffect(self)
        fx.setBlurRadius(24)
        fx.setOffset(0, 4)
        self._fx = fx
        self.setGraphicsEffect(fx)
        self._kind = "info"
        self.hide()
        theme_manager().changed.connect(self._retint)

    def set_message(self, text: str, kind: str) -> None:
        self._kind = kind if kind in _KINDS else "info"
        self._label.setText(text)
        self._retint()
        self.adjustSize()

    def message(self) -> str:
        return self._label.text()

    def _retint(self, _name: str = "") -> None:
        icon_name, color = _KINDS[self._kind]
        self._icon.setPixmap(pixmap(icon_name, color, ICON_S))
        qc = QColor(tokens().shadow)
        qc.setAlpha(tokens().shadow_alpha)
        self._fx.setColor(qc)


class ToastHost(QObject):
    """ウィンドウに 1 つ付けて使う。`ToastHost(window).show_message("保存しました")`。"""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        self._toast = Toast(window)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._toast.hide)
        window.installEventFilter(self)

    @property
    def toast(self) -> Toast:
        return self._toast

    def show_message(self, text: str, kind: str = "success", ms: int = TOAST_MS) -> None:
        self._toast.set_message(text, kind)
        self._place()
        self._toast.show()
        self._toast.raise_()
        self._timer.start(ms)

    def _place(self) -> None:
        w = self._window
        self._toast.adjustSize()
        x = (w.width() - self._toast.width()) // 2
        y = w.height() - self._toast.height() - _BOTTOM_MARGIN - 24  # ステータスバーの上
        self._toast.move(max(0, x), max(0, y))

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._window and event.type() == QEvent.Type.Resize and self._toast.isVisible():
            self._place()
        return False
