"""InfoBar(バナー)。重大度 info / caution / critical(+ 非常停止ラッチ専用の estop)。

左に 4px の重大度帯、アイコン + 本文 + 操作ボタン(任意)。閉じるボタンは条件が解消するまで出さない
(`closable=True` は info のみで使う)。ページの上に重ねず、レイアウトを押し下げて出す。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from edulite05.app.ui.button import Button
from edulite05.app.ui.icon import pixmap
from edulite05.app.ui.label import Label
from edulite05.app.ui.style import set_prop, theme_manager
from edulite05.app.ui.tokens import ICON_M, SP_GROUP, SP_PAD, SP_RELATED

SEVERITIES = ("info", "caution", "critical", "estop")
_ICONS = {
    "info": "info",
    "caution": "triangle-alert",
    "critical": "circle-alert",
    "estop": "octagon-x",
}
_ICON_COLOR = {
    "info": "text_secondary",
    "caution": "caution",
    "critical": "critical",
    "estop": "estop_text",
}
_BAND_W = 4
_HEIGHT = 48
_HEIGHT_INFO = 40


class InfoBar(QFrame):
    """`InfoBar("caution", "高出力モード", "モータを固定して使用", action="低出力に戻す")`。

    `action_clicked` が操作ボタンのクリック、`closed` が閉じるボタン(closable のとき)。
    """

    action_clicked = Signal()
    closed = Signal()

    def __init__(
        self,
        severity: str = "info",
        title: str = "",
        text: str = "",
        *,
        action: str | None = None,
        closable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("InfoBar")
        self.setProperty("severity", severity)
        self.setMinimumHeight(_HEIGHT_INFO if severity == "info" else _HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._severity = severity
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._band = QFrame()
        self._band.setObjectName("InfoBarBand")
        self._band.setProperty("severity", severity)
        self._band.setFixedWidth(_BAND_W)
        self._band.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        outer.addWidget(self._band)
        body = QHBoxLayout()
        body.setContentsMargins(SP_PAD - 4, SP_RELATED, SP_PAD, SP_RELATED)
        body.setSpacing(SP_GROUP)
        outer.addLayout(body, 1)
        self._icon = QLabel()
        self._icon.setFixedSize(ICON_M, ICON_M)
        body.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._title = Label(title, "heading")
        self._text = Label(text, "secondary")
        body.addWidget(self._title)
        body.addWidget(self._text, 1)
        self._action: Button | None = None
        if action:
            variant = "inverse" if severity == "estop" else "standard"
            self._action = Button(action, variant)
            self._action.clicked.connect(self.action_clicked)
            body.addWidget(self._action)
        self._close: Button | None = None
        if closable:
            self._close = Button(variant="subtle", icon="x")
            self._close.clicked.connect(self.closed)
            body.addWidget(self._close)
        self._retint()
        theme_manager().changed.connect(self._retint)

    @property
    def severity(self) -> str:
        return self._severity

    def set_severity(self, severity: str) -> None:
        self._severity = severity
        self.setMinimumHeight(_HEIGHT_INFO if severity == "info" else _HEIGHT)
        set_prop(self, "severity", severity)
        set_prop(self._band, "severity", severity)
        self._retint()

    def set_texts(self, title: str, text: str = "") -> None:
        self._title.setText(title)
        self._text.setText(text)

    def title(self) -> str:
        return self._title.text()

    def text(self) -> str:
        return self._text.text()

    @property
    def action_button(self) -> Button | None:
        return self._action

    @property
    def close_button(self) -> Button | None:
        return self._close

    def _retint(self, _name: str = "") -> None:
        self._icon.setPixmap(
            pixmap(_ICONS[self._severity], _ICON_COLOR[self._severity], ICON_M)
        )
        # estop は濃い赤地。本文も白(二次色にしない)
        self._text.set_role("body" if self._severity == "estop" else "secondary")


class BannerStack(QWidget):
    """バナー領域。優先順位順(追加順 = 上から)に積み、条件が成立している分だけ表示する。

    ui_design.md §4: 非常停止 > 故障 > 通信断 > HIGH > モック/シミュレータ。
    表示中のバナーは重ねず、レイアウトを押し下げる。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._bars: dict[str, InfoBar] = {}

    def add(self, key: str, bar: InfoBar) -> InfoBar:
        """優先度の高い順に呼ぶ。最初は非表示。"""
        bar.setVisible(False)
        self._bars[key] = bar
        self._lay.addWidget(bar)
        return bar

    def bar(self, key: str) -> InfoBar:
        return self._bars[key]

    def set_active(self, key: str, active: bool) -> None:
        self._bars[key].setVisible(active)

    def active_keys(self) -> list[str]:
        """表示中のバナー(優先順)。"""
        return [k for k, b in self._bars.items() if not b.isHidden()]
