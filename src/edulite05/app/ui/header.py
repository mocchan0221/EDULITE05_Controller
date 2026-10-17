"""ヘッダー(ui_design.md §4)。全ページ共通 = ISA-101 のレベル 1(全体の状況が常に見える)。

`[アプリ名] [● COM7 接続中 ▾]  [■ID1 運転中] [■ID2 停止中] [+]  …  出力 [低出力|高出力]
 [⊗ 非常停止 Pause / Esc(運転中)]  [⋯]`

純粋な表示部品(Bridge を知らない)。配線は `MainWindow` が行う。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QResizeEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from edulite05.app.i18n import tr
from edulite05.app.ui.button import Button, EStopButton
from edulite05.app.ui.chip import MotorChip
from edulite05.app.ui.icon import pixmap
from edulite05.app.ui.label import Label
from edulite05.app.ui.segmented import Segmented
from edulite05.app.ui.style import theme_manager, tokens
from edulite05.app.ui.tokens import (
    H_CONTROL,
    H_HEADER,
    ICON_S,
    SP_GROUP,
    SP_ICON,
    SP_PAD,
    SP_RELATED,
)

_DOT = 8
_COMPACT_WIDTH = 1400  # これ未満では「出力」の見出しを省く


class ConnectionButton(QPushButton):
    """`● COM7 接続中 ▾` / `○ 未接続 ▾`。フラット(subtle)。押すと接続ポップオーバー。

    点は接続中だけ success(緑)。通信断は critical。未接続は中抜きのグレー。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("variant", "subtle")
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(H_CONTROL)
        self._state = "offline"  # connected / offline / lost
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP_GROUP, 0, SP_RELATED, 0)
        lay.setSpacing(SP_RELATED)
        self._dot = QLabel()
        self._dot.setFixedSize(_DOT + 2, _DOT + 2)
        self._text = Label("", "body")
        self._chev = QLabel()
        self._chev.setFixedSize(ICON_S, ICON_S)
        lay.addWidget(self._dot)
        lay.addWidget(self._text)
        lay.addWidget(self._chev)
        for w in (self._dot, self._text, self._chev):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.set_connection("", "offline")
        theme_manager().changed.connect(self._retint)

    def set_connection(self, text: str, state: str) -> None:
        self._state = state
        self._text.setText(text)
        self._retint()
        self.setFixedWidth(self.sizeHint().width())

    def text_value(self) -> str:
        return self._text.text()

    @property
    def state(self) -> str:
        return self._state

    def sizeHint(self):  # noqa: ANN201, N802
        lay = self.layout()
        h = lay.sizeHint() if lay is not None else super().sizeHint()
        return h

    def _retint(self, _name: str = "") -> None:
        t = tokens()
        d = _DOT + 2
        pm = QPixmap(d * 2, d * 2)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(d, d)
        if self._state == "connected":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.success))
            p.drawEllipse(c, _DOT, _DOT)
        elif self._state == "lost":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.critical))
            p.drawEllipse(c, _DOT, _DOT)
        else:
            p.setPen(QPen(QColor(t.text_tertiary), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, _DOT - 1.5, _DOT - 1.5)
        p.end()
        pm = pm.scaled(
            d, d, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self._dot.setPixmap(pm)
        self._chev.setPixmap(pixmap("chevron-down", "text_secondary", ICON_S))


class Header(QFrame):
    """ヘッダー全体。信号は「ユーザーが押した」ことだけを通知する。"""

    connection_clicked = Signal()
    chip_selected = Signal(int)
    scan_clicked = Signal()
    tier_selected = Signal(str)  # "low" / "high"
    estop_clicked = Signal()
    more_clicked = Signal()

    def __init__(self, app_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Header")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(H_HEADER)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP_PAD, 0, SP_PAD, 0)
        lay.setSpacing(SP_GROUP)

        self.app_name = Label(app_name, "heading")
        lay.addWidget(self.app_name)
        lay.addSpacing(SP_RELATED)
        self.connection_btn = ConnectionButton()
        self.connection_btn.setToolTip(tr("header.connect_tip"))
        self.connection_btn.clicked.connect(self.connection_clicked)
        lay.addWidget(self.connection_btn)

        # モータチップ(1 台 1 個)+ 再スキャン
        self._chips_host = QWidget()
        self._chips_lay = QHBoxLayout(self._chips_host)
        self._chips_lay.setContentsMargins(0, 0, 0, 0)
        self._chips_lay.setSpacing(SP_ICON)
        lay.addWidget(self._chips_host)
        self.no_motors_label = Label(tr("header.no_motors"), "caption")
        self.no_motors_label.setVisible(False)
        lay.addWidget(self.no_motors_label)
        self.scan_btn = Button(variant="subtle", icon="plus", tooltip=tr("header.scan_tip"))
        self.scan_btn.clicked.connect(self.scan_clicked)
        lay.addWidget(self.scan_btn)
        lay.addStretch(1)

        tier_box = QHBoxLayout()
        tier_box.setContentsMargins(0, 0, 0, 0)
        tier_box.setSpacing(SP_RELATED)
        self.output_label = Label(tr("header.output"), "caption")
        tier_box.addWidget(self.output_label)
        self.tier = Segmented(
            [("low", tr("header.tier_low")), ("high", tr("header.tier_high"))], current="low"
        )
        self.tier.set_item_tone("high", "caution")
        self.tier.selected.connect(self.tier_selected)
        tier_box.addWidget(self.tier)
        lay.addLayout(tier_box)
        lay.addSpacing(SP_RELATED)
        self.estop_btn = EStopButton(tr("header.estop"), tr("header.estop_hint"))
        self.estop_btn.setToolTip(tr("header.estop_tip"))
        self.estop_btn.clicked.connect(self.estop_clicked)
        lay.addWidget(self.estop_btn)
        self.more_btn = Button(variant="subtle", icon="ellipsis", tooltip=tr("header.more"))
        self.more_btn.clicked.connect(self.more_clicked)
        lay.addWidget(self.more_btn)

        self._chips: dict[int, MotorChip] = {}

    # ---- 接続 ----
    def set_connection(self, text: str, state: str) -> None:
        self.connection_btn.set_connection(text, state)

    # ---- モータチップ ----
    def set_motors(
        self, motors: Sequence[tuple[int, str] | tuple[int, str, int]], selected: int | None = None
    ) -> None:
        """`(id, 名前)` または `(id, 名前, 色の枠)`。色の枠は `MotorInfo.color_index`。"""
        for chip in self._chips.values():
            self._chips_lay.removeWidget(chip)
            chip.deleteLater()
        self._chips.clear()
        for item in motors:
            mid = item[0]
            chip = MotorChip(mid, color_index=item[2] if len(item) > 2 else None)
            chip.setToolTip(tr("header.motor_tip", id=mid))
            chip.chip_clicked.connect(self.chip_selected)
            self._chips[mid] = chip
            self._chips_lay.addWidget(chip)
        if selected is not None:
            self.set_selected(selected)

    def chips(self) -> dict[int, MotorChip]:
        return dict(self._chips)

    def set_motor_state(self, motor_id: int, ui_state: str) -> None:
        chip = self._chips.get(motor_id)
        if chip is not None:
            chip.set_state(ui_state)

    def set_selected(self, motor_id: int) -> None:
        for mid, chip in self._chips.items():
            chip.set_selected(mid == motor_id)

    def set_scan_enabled(self, enabled: bool, reason: str = "") -> None:
        self.scan_btn.set_available(enabled, reason)
        self.scan_btn.set_base_tooltip(tr("header.scan_tip"))

    def set_no_motors_hint(self, visible: bool) -> None:
        self.no_motors_label.setVisible(visible)

    # ---- 出力段階 ----
    def set_tier(self, tier: str) -> None:
        self.tier.set_current(tier)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.output_label.setVisible(event.size().width() >= _COMPACT_WIDTH)
