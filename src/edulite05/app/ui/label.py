"""役割(role)付きのラベル。文字サイズ・太さ・色は QSS(トークン)が決める。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from edulite05.app.ui.style import set_prop

ROLES = (
    "body",  # 14 / 本文色
    "secondary",  # 14 / 二次色
    "caption",  # 12 / 二次色(補足・単位・タイムスタンプ)
    "caption-strong",  # 12 Semibold / 二次色(バッジ・ナビの文字)
    "caption-tertiary",  # 12 / 三次色(最小・最大・プレースホルダ)
    "heading",  # 14 Semibold(見出し)
    "inspector",  # 16 Semibold(インスペクタ見出しのみ)
    "title",  # 20 Semibold(ページ見出し・空状態)
    "readout",  # 28 Semibold 等幅(読み値)
    "readout-s",  # 20 等幅(副次の読み値)
    "body-mono",  # 14 等幅
)


class Label(QLabel):
    """`Label("速度", role="caption", tone="caution")`。

    role は文字の役割(上記)、tone は意味色(neutral/accent/success/caution/critical)。
    """

    def __init__(
        self,
        text: str = "",
        role: str = "body",
        *,
        tone: str | None = None,
        wrap: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setProperty("role", role)
        if tone:
            self.setProperty("tone", tone)
        self.setWordWrap(wrap)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def set_role(self, role: str) -> None:
        set_prop(self, "role", role)

    def set_tone(self, tone: str | None) -> None:
        set_prop(self, "tone", tone or "")
