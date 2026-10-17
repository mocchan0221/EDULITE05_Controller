"""イベント行(ui_design.md §5.1): モニタ下端の 1 行。最新の 1 件。クリックで診断ページへ。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from edulite05.app.i18n import tr
from edulite05.app.ui.icon import pixmap
from edulite05.app.ui.style import make_font, theme_manager, tokens
from edulite05.app.ui.tokens import (
    FS_BODY,
    FS_CAPTION,
    H_CONTROL,
    ICON_S,
    R_CONTROL,
    SP_GROUP,
    SP_ICON,
    SP_RELATED,
)
from edulite05.app.viewmodels import LogEntry

_LEVEL_ICON = {"warn": ("triangle-alert", "caution"), "error": ("circle-alert", "critical")}


class EventLine(QAbstractButton):
    """`▸ イベント  12:03:41  ID 1: 速度モードで運転を開始しました`。押せる(ホバーで面)。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entry: LogEntry | None = None
        self._hover = False
        self.setFixedHeight(H_CONTROL)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip(tr("operate.event.tip"))
        theme_manager().changed.connect(self._on_theme)

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    def set_entry(self, entry: LogEntry | None) -> None:
        self._entry = entry
        self.update()

    def entry(self) -> LogEntry | None:
        return self._entry

    def text_value(self) -> str:
        """表示中の本文(テスト用)。"""
        return self._entry.text if self._entry is not None else tr("operate.event.none")

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, H_CONTROL)

    def enterEvent(self, e) -> None:  # noqa: ANN001, N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: ANN001, N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.overlay_hover))
            p.drawRoundedRect(r, R_CONTROL, R_CONTROL)
        if self.hasFocus():
            p.setPen(QPen(QColor(t.accent), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), R_CONTROL, R_CONTROL)
        x = SP_RELATED
        cy = r.height() / 2
        chev = pixmap("chevron-right", "text_secondary", ICON_S)
        p.drawPixmap(int(x), int(cy - ICON_S / 2), chev)
        x += ICON_S + SP_ICON
        f_title = make_font(FS_CAPTION, semibold=True)
        p.setFont(f_title)
        p.setPen(QColor(t.text_secondary))
        title = tr("operate.event.title")
        p.drawText(QRectF(x, 0, 200, r.height()), Qt.AlignmentFlag.AlignVCenter, title)
        x += QFontMetrics(f_title).horizontalAdvance(title) + SP_GROUP
        e = self._entry
        if e is None:
            p.setFont(make_font(FS_BODY))
            p.setPen(QColor(t.text_tertiary))
            p.drawText(
                QRectF(x, 0, r.width() - x, r.height()), Qt.AlignmentFlag.AlignVCenter,
                tr("operate.event.none"),
            )
            p.end()
            return
        f_mono = make_font(FS_CAPTION, mono=True)
        p.setFont(f_mono)
        p.setPen(QColor(t.text_secondary))
        stamp = datetime.fromtimestamp(e.t).strftime("%H:%M:%S")
        p.drawText(QRectF(x, 0, 120, r.height()), Qt.AlignmentFlag.AlignVCenter, stamp)
        x += QFontMetrics(f_mono).horizontalAdvance(stamp) + SP_GROUP
        icon_spec = _LEVEL_ICON.get(e.level)
        if icon_spec is not None:
            p.drawPixmap(int(x), int(cy - ICON_S / 2), pixmap(icon_spec[0], icon_spec[1], ICON_S))
            x += ICON_S + SP_RELATED
        f_body = make_font(FS_BODY)
        p.setFont(f_body)
        p.setPen(QColor(t.text_primary))
        text = QFontMetrics(f_body).elidedText(
            e.text, Qt.TextElideMode.ElideRight, max(int(r.width() - x - SP_RELATED), 20)
        )
        p.drawText(
            QRectF(x, 0, r.width() - x, r.height()), Qt.AlignmentFlag.AlignVCenter, text
        )
        p.end()

