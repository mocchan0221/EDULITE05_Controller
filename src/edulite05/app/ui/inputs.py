"""入力部品: Combo(QSS の項目スタイルが効くコンボ)/ Switch(即時反映の二値設定)/ ValueSlider。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QAbstractButton, QComboBox, QListView, QSizePolicy, QWidget

from edulite05.app.ui.style import theme_manager, tokens
from edulite05.app.ui.tokens import H_CONTROL


class Combo(QComboBox):
    """ホイールで値が変わらない(ページのスクロールで誤操作しない)コンボ。高さ 32。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setView(QListView())  # QSS の `::item` スタイルを効かせる
        self.setFixedHeight(H_CONTROL)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()


class Switch(QAbstractButton):
    """スイッチ(オンで即時反映する二値設定。例: 連続送信)。`toggled(bool)`。"""

    _W, _H = 36, 20

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(self._W + 4, self._H + 4)
        theme_manager().changed.connect(self._on_theme)

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._W + 4, self._H + 4)

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(2, 2, self._W, self._H)
        on = self.isChecked()
        en = self.isEnabled()
        if on:
            track, knob, border = QColor(t.accent), QColor(t.accent_text_on), QColor(t.accent)
        else:
            track, knob = QColor(t.bg_control), QColor(t.text_secondary)
            border = QColor(t.text_tertiary)
        if not en:
            track, knob = QColor(t.fill_disabled), QColor(t.text_disabled)
            border = QColor(t.fill_disabled)
        p.setPen(QPen(border, 1))
        p.setBrush(track)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), self._H / 2, self._H / 2)
        d = 12
        x = r.right() - 4 - d if on else r.left() + 4
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(knob)
        p.drawEllipse(QRectF(x, r.center().y() - d / 2, d, d))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(t.accent), 2))
            p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), self._H / 2 + 1, self._H / 2 + 1)
        p.end()


class ValueSlider(QWidget):
    """数値スライダー(トラック 4・つまみ 18)。実測位置の小さな縦線(marker)付き。

    ホイールでは動かさない(ページのスクロールで値が変わる事故を防ぐ)。
    キー: ←→↑↓ で 1 ステップ(Shift で 10 倍)、PageUp/Down で 10%、Home/End。
    `value_changed(float)` はユーザー操作でのみ発行。
    """

    value_changed = Signal(float)

    _HANDLE = 18

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lo, self._hi, self._step = 0.0, 1.0, 0.01
        self._value = 0.0
        self._marker: float | None = None
        self._zero_tick = False
        self._drag = False
        self._hover = False
        self.setFixedHeight(24)
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        theme_manager().changed.connect(self._on_theme)

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    # ---- モデル ----
    def set_range(self, lo: float, hi: float, step: float | None = None) -> None:
        self._lo, self._hi = lo, max(hi, lo)
        if step is not None:
            self._step = step
        self._value = min(max(self._value, self._lo), self._hi)
        self.update()

    def set_value(self, v: float) -> None:
        self._value = min(max(v, self._lo), self._hi)
        self.update()

    def value(self) -> float:
        return self._value

    def set_marker(self, v: float | None) -> None:
        self._marker = v
        self.update()

    def set_zero_tick(self, on: bool) -> None:
        """範囲が負にまたがるとき、0 の位置に目盛りを出す(MIT の kd など、0 が節目の項目)。"""
        self._zero_tick = on
        self.update()

    def zero_tick(self) -> bool:
        return self._zero_tick

    def _frac(self, v: float) -> float:
        span = self._hi - self._lo
        return 0.0 if span <= 0 else min(max((v - self._lo) / span, 0.0), 1.0)

    def _track(self) -> tuple[float, float]:
        pad = self._HANDLE / 2 + 1
        return pad, self.width() - pad

    def _x(self, v: float) -> float:
        a, b = self._track()
        return a + (b - a) * self._frac(v)

    def _from_x(self, x: float) -> float:
        a, b = self._track()
        f = 0.0 if b <= a else min(max((x - a) / (b - a), 0.0), 1.0)
        v = self._lo + (self._hi - self._lo) * f
        if self._step > 0:
            v = self._lo + round((v - self._lo) / self._step) * self._step
        return min(max(v, self._lo), self._hi)

    def _set_user(self, v: float) -> None:
        if v != self._value:
            self._value = v
            self.update()
            self.value_changed.emit(v)

    # ---- 入力 ----
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._drag = True
            self._set_user(self._from_x(event.position().x()))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag:
            self._set_user(self._from_x(event.position().x()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag = False

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()

    def enterEvent(self, event) -> None:  # noqa: ANN001, N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001, N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        k = event.key()
        mult = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        step = self._step if self._step > 0 else (self._hi - self._lo) / 100
        span = self._hi - self._lo
        if k in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self._set_user(min(self._value + step * mult, self._hi))
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self._set_user(max(self._value - step * mult, self._lo))
        elif k == Qt.Key.Key_PageUp:
            self._set_user(min(self._value + span * 0.1, self._hi))
        elif k == Qt.Key.Key_PageDown:
            self._set_user(max(self._value - span * 0.1, self._lo))
        elif k == Qt.Key.Key_Home:
            self._set_user(self._lo)
        elif k == Qt.Key.Key_End:
            self._set_user(self._hi)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    # ---- 描画 ----
    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        t = tokens()
        en = self.isEnabled()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = self.height() / 2
        a, b = self._track()
        track_h = 4
        # 溝
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.stroke_control if en else t.stroke_divider))
        p.drawRoundedRect(QRectF(a, cy - track_h / 2, b - a, track_h), 2, 2)
        # 塗り(最小値から現在値)
        x = self._x(self._value)
        p.setBrush(QColor(t.accent if en else t.text_disabled))
        p.drawRoundedRect(QRectF(a, cy - track_h / 2, max(x - a, 0), track_h), 2, 2)
        # 0 の目盛り
        if self._zero_tick and self._lo < 0.0 < self._hi:
            zx = self._x(0.0)
            p.setBrush(QColor(t.text_tertiary))
            p.drawRoundedRect(QRectF(zx - 0.5, cy - 6, 1, 12), 0.5, 0.5)
        # 実測位置の縦線
        if self._marker is not None:
            mx = self._x(self._marker)
            p.setBrush(QColor(t.text_secondary))
            p.drawRoundedRect(QRectF(mx - 1, cy - 7, 2, 14), 1, 1)
        # つまみ
        d = self._HANDLE
        p.setPen(QPen(QColor(t.text_tertiary if en else t.stroke_divider), 1))
        p.setBrush(QColor(t.bg_control_hover if self._hover and en else t.bg_control))
        p.drawEllipse(QPointF(x, cy), d / 2 - 0.5, d / 2 - 0.5)
        inner = 5 if not (self._hover or self._drag) else 6
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.accent if en else t.text_disabled))
        p.drawEllipse(QPointF(x, cy), inner, inner)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(t.accent), 2))
            p.drawEllipse(QPointF(x, cy), d / 2 + 2, d / 2 + 2)
        p.end()
