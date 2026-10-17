"""状態バッジ(ui_design.md §6)。押せない・ホバーしない。アイコン + 文字。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from edulite05.app.i18n import tr
from edulite05.app.ui.icon import pixmap
from edulite05.app.ui.style import set_prop, theme_manager, tokens
from edulite05.app.ui.tokens import H_BADGE, ICON_S, SP_ICON, SP_RELATED

# ui_state → (tone, アイコン, tr キー)  ui_design.md §3
UI_STATE_STYLE: dict[str, tuple[str, str, str]] = {
    "offline": ("neutral", "circle", "motor_state.offline"),
    "disabled": ("neutral", "circle", "motor_state.disabled"),
    "enabled": ("accent", "circle-dot", "motor_state.enabled"),
    "running": ("success", "play", "motor_state.running"),
    "fault": ("critical", "triangle-alert", "motor_state.fault"),
}


def ui_state_style(ui_state: str) -> tuple[str, str, str]:
    """`MotorViewState.ui_state` から (tone, アイコン名, 表示文字)。"""
    tone, icon_name, key = UI_STATE_STYLE.get(ui_state, UI_STATE_STYLE["offline"])
    return tone, icon_name, tr(key)


class StatusBadge(QFrame):
    """`StatusBadge("運転中", "success", icon="play")` / `StatusBadge.for_ui_state("running")`。

    tone: neutral(グレー)/ accent(青系の文字のみ)/ success / caution / critical。
    背景は状態色 10〜12% の薄い面 + 状態色の文字。ホバー・クリック・フォーカスなし。
    """

    def __init__(
        self,
        text: str = "",
        tone: str = "neutral",
        *,
        icon: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Badge")
        self.setFixedHeight(H_BADGE)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tone = tone
        self._icon_name = icon
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP_RELATED, 0, SP_RELATED + 2, 0)
        lay.setSpacing(SP_ICON)
        self._icon = QLabel()
        self._icon.setFixedSize(ICON_S, ICON_S)
        self._text = QLabel(text)
        self._text.setProperty("role", "caption-strong")  # 12 Semibold
        lay.addWidget(self._icon)
        lay.addWidget(self._text)
        self.setProperty("tone", tone)
        self._refresh()
        theme_manager().changed.connect(self._refresh)

    @classmethod
    def for_ui_state(cls, ui_state: str, parent: QWidget | None = None) -> StatusBadge:
        b = cls(parent=parent)
        b.set_ui_state(ui_state)
        return b

    def set_ui_state(self, ui_state: str) -> None:
        tone, icon_name, text = ui_state_style(ui_state)
        self.set_state(text, tone, icon=icon_name)

    def set_state(self, text: str, tone: str, *, icon: str | None = None) -> None:
        self._text.setText(text)
        self._icon_name = icon
        self._tone = tone
        set_prop(self, "tone", tone)
        self._refresh()

    @property
    def tone(self) -> str:
        return self._tone

    def text(self) -> str:
        return self._text.text()

    def _refresh(self, _name: str = "") -> None:
        self._icon.setVisible(self._icon_name is not None)
        if self._icon_name is not None:
            color = tokens().tone_color(self._tone)
            self._icon.setPixmap(pixmap(self._icon_name, color, ICON_S))
