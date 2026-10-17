"""モータチップ(ヘッダー用)。モータ色の四角 + `ID1` + 状態文字。押せる(ホバーで面)。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from edulite05.app.i18n import tr
from edulite05.app.motor_colors import fallback_index
from edulite05.app.ui.badge import ui_state_style
from edulite05.app.ui.colors import qcolor
from edulite05.app.ui.icon import pixmap
from edulite05.app.ui.style import make_font, theme_manager, tokens
from edulite05.app.ui.tokens import (
    FS_BODY,
    FS_CAPTION,
    H_PRIMARY,
    ICON_S,
    R_CONTROL,
    SP_GROUP,
    SP_ICON,
    SP_RELATED,
)
from edulite05.app.viewmodels import UI_STATES

_SWATCH = 12


class MotorChip(QAbstractButton):
    """`MotorChip(1)` … `set_state("running")` / `set_selected(True)`。

    色の枠 `color_index` は Bridge の割り当て(初登録順で固定。`MotorInfo.color_index`)を渡す。
    省略時は ID 順(ギャラリー用)。グラフ・3D・診断と共通(ui_design.md §7.2)。
    クリックで `chip_clicked(motor_id)`。選択中は背景 + 下線(アクセント)。
    """

    chip_clicked = Signal(int)

    def __init__(
        self, motor_id: int, *, color_index: int | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.motor_id = motor_id
        self._color_index = fallback_index(motor_id) if color_index is None else color_index
        self._ui_state = "offline"
        self._selected = False
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.clicked.connect(lambda: self.chip_clicked.emit(self.motor_id))
        self.setFixedHeight(H_PRIMARY)
        self._update_geometry()
        theme_manager().changed.connect(self._on_theme)

    # ---- 状態 ----
    def set_state(self, ui_state: str) -> None:
        if ui_state != self._ui_state:
            self._ui_state = ui_state
            self._update_geometry()
            self.update()

    @property
    def ui_state(self) -> str:
        return self._ui_state

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self.update()

    @property
    def selected(self) -> bool:
        return self._selected

    def state_text(self) -> str:
        return ui_state_style(self._ui_state)[2]

    def label_text(self) -> str:
        return tr("ui.motor_id", id=self.motor_id)

    # ---- 寸法 ----
    def _fonts(self):  # noqa: ANN202
        return make_font(FS_BODY, semibold=True), make_font(FS_CAPTION, semibold=True)

    def _update_geometry(self) -> None:
        # 状態が変わっても幅が動かないよう、最も長い状態文字にそろえる(ヘッダーの隣が揺れない)
        f_id, f_state = self._fonts()
        widest = max(
            QFontMetrics(f_state).horizontalAdvance(ui_state_style(s)[2]) for s in UI_STATES
        )
        w = (
            SP_GROUP + _SWATCH + SP_RELATED
            + QFontMetrics(f_id).horizontalAdvance(self.label_text())
            + SP_RELATED + ICON_S + SP_ICON + widest + SP_GROUP
        )
        self.setFixedWidth(w)
        self.updateGeometry()

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.width(), self.height())

    def enterEvent(self, event) -> None:  # noqa: ANN001, N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001, N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    # ---- 描画 ----
    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        t = tokens()
        tone, icon_name, text = ui_state_style(self._ui_state)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self._selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.bg_control))
            p.drawRoundedRect(r, R_CONTROL, R_CONTROL)
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(t.overlay_hover))
            p.drawRoundedRect(r, R_CONTROL, R_CONTROL)
        if self._selected:  # 下線
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.accent))
            bar = QRectF(SP_RELATED, r.bottom() - 3, r.width() - 2 * SP_RELATED, 2)
            p.drawRoundedRect(bar, 1, 1)
        if self.hasFocus():  # キーボードのフォーカスリング
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(t.accent), 2))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), R_CONTROL, R_CONTROL)
        f_id, f_state = self._fonts()
        x = SP_GROUP
        cy = r.height() / 2
        # モータ色の四角
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.motor_color(self._color_index)))
        p.drawRoundedRect(QRectF(x, cy - _SWATCH / 2, _SWATCH, _SWATCH), 3, 3)
        x += _SWATCH + SP_RELATED
        # ID
        p.setFont(f_id)
        p.setPen(QColor(t.text_primary))
        fm = QFontMetrics(f_id)
        w_id = fm.horizontalAdvance(self.label_text())
        id_rect = QRectF(x, 0, w_id + 2, r.height())
        p.drawText(id_rect, Qt.AlignmentFlag.AlignVCenter, self.label_text())
        x += w_id + SP_RELATED
        # 状態アイコン + 文字(状態色)
        color = t.tone_color(tone)
        p.drawPixmap(int(x), int(cy - ICON_S / 2), pixmap(icon_name, color, ICON_S))
        x += ICON_S + SP_ICON
        p.setFont(f_state)
        p.setPen(QColor(color))
        p.drawText(QRectF(x, 0, r.width() - x, r.height()), Qt.AlignmentFlag.AlignVCenter, text)
        p.end()
