"""ボタン類(ui_design.md §6): Button / HoldButton / EStopButton。

押せるものだけがボタンの形(面・枠・ホバー)を持つ。強調色(accent)は 1 区画に 1 つまで。
無効にするときは `set_available(False, "理由")` で理由をツールチップに出す(原則 6)。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QFocusEvent, QFontMetrics, QGuiApplication, QIcon, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from edulite05.app.ui.icon import icon, pixmap
from edulite05.app.ui.style import make_font, set_prop, theme_manager, tokens
from edulite05.app.ui.tokens import (
    FS_BODY,
    FS_CAPTION,
    H_CONTROL,
    H_ESTOP,
    H_JOG,
    H_PRIMARY,
    ICON_M,
    ICON_S,
    SP_ICON,
    SP_PAD,
    SP_RELATED,
    W_ESTOP_MIN,
)

VARIANTS = ("accent", "standard", "subtle", "danger", "caution", "inverse")

# variant ごとのアイコン色(トークン属性名)
_ICON_COLOR = {
    "accent": "accent_text_on",
    "standard": "text_primary",
    "subtle": "text_secondary",
    "danger": "estop_text",
    "caution": "caution_text_on",
    "inverse": "estop",
    "hold": "text_primary",
}


class Button(QPushButton):
    """variant: accent(主・塗り)/ standard(面+枠)/ subtle(枠なし)/ danger(非常停止専用)/
    caution(HIGH 確定専用)/ inverse(濃い面のバナー上)。高さ 32(large=40)。

    `Button("有効化", "accent", icon="power", large=True)`。アイコンだけのボタンは
    `Button(icon="ellipsis", variant="subtle", tooltip="その他")`(32×32)。
    """

    def __init__(
        self,
        text: str = "",
        variant: str = "standard",
        *,
        icon: str | None = None,
        large: bool = False,
        tooltip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._variant = variant
        self._icon_name = icon
        self._large = large
        self._tip = tooltip
        self._reason: str | None = None
        self.setProperty("variant", variant)
        self.setProperty("large", large)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # フォーカスリングはキーボード操作時だけ
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(H_PRIMARY if large else H_CONTROL)
        if icon is not None and not text:
            self.setProperty("iconOnly", True)
            self.setFixedSize(H_CONTROL, H_CONTROL)
        if tooltip:
            self.setToolTip(tooltip)
        self._refresh_icon()
        theme_manager().changed.connect(self._refresh_icon)

    # ---- 見た目 ----
    @property
    def variant(self) -> str:
        return self._variant

    def set_variant(self, variant: str) -> None:
        self._variant = variant
        set_prop(self, "variant", variant)
        self._refresh_icon()

    def set_icon_name(self, name: str | None) -> None:
        self._icon_name = name
        self._refresh_icon()

    def _icon_size(self) -> int:
        return ICON_M if self._large else ICON_S

    def _icon_color(self) -> str:
        return _ICON_COLOR.get(self._variant, "text_primary")

    def _refresh_icon(self, _name: str = "") -> None:
        if self._icon_name is None:
            self.setIcon(QIcon())
            return
        size = self._icon_size()
        self.setIconSize(QSize(size, size))
        self.setIcon(icon(self._icon_name, self._icon_color(), size))

    # ---- 無効化と理由(原則 6) ----
    def set_available(self, available: bool, reason: str = "") -> None:
        """押せるか。押せないときは `reason` をツールチップに出す。"""
        self.setEnabled(available)
        self._reason = None if available else (reason or None)
        self.setToolTip(self._reason if self._reason else self._tip)

    def set_base_tooltip(self, text: str) -> None:
        self._tip = text
        if self._reason is None:
            self.setToolTip(text)


class HoldButton(Button):
    """押下保持専用(JOG)。押している間だけ `hold_started` → `hold_ended(reason)`。

    離す(release)・ポインタが外れる(leave)・フォーカスを失う(focus_out)・ウィンドウが非アクティブ
    (window_inactive)・無効化/非表示(disabled)のいずれでも即座に `hold_ended` を発行する。
    キーボードは Space / Enter の押下保持。枠付き・押下中は塗り(ui_design.md §5.1)。
    """

    hold_started = Signal()
    hold_ended = Signal(str)  # reason

    def __init__(
        self,
        text: str = "",
        *,
        icon: str | None = "hand",
        parent: QWidget | None = None,
    ) -> None:
        self._holding = False  # 基底クラスの初期化(アイコン着色)が参照するため先に設定
        super().__init__(text, "hold", icon=icon, parent=parent)
        self.setFixedHeight(H_JOG)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoRepeat(False)
        app = QGuiApplication.instance()
        if app is not None:
            app.focusWindowChanged.connect(self._on_focus_window)  # type: ignore[attr-defined]

    def is_holding(self) -> bool:
        return self._holding

    def _icon_color(self) -> str:
        return "accent_text_on" if self._holding else "text_primary"

    def press(self) -> None:
        """押下保持を開始する(キーボード・テスト用の公開 API。マウス押下と同じ)。"""
        if self._holding or not self.isEnabled():
            return
        self._holding = True
        set_prop(self, "holding", True)
        self._refresh_icon()
        self.hold_started.emit()

    def release(self, reason: str = "release") -> None:
        """押下保持を終える。`hold_ended(reason)` を発行する(保持中でなければ何もしない)。"""
        if not self._holding:
            return
        self._holding = False
        set_prop(self, "holding", False)
        self._refresh_icon()
        self.hold_ended.emit(reason)

    def _on_focus_window(self, win: object) -> None:
        if not self._holding:
            return
        mine = self.window().windowHandle() if self.window() is not None else None
        if win is None or win is not mine:
            self.release("window_inactive")

    # ---- 入力イベント ----
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.press()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._holding and not self.rect().contains(event.position().toPoint()):
            self.release("leave")
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.release("release")
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self.release("leave")
        super().leaveEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.isAutoRepeat():
                self.press()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.isAutoRepeat():
                self.release("release")
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        self.release("focus_out")
        super().focusOutEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self.release("disabled")
        super().changeEvent(event)

    def hideEvent(self, event: object) -> None:  # noqa: N802
        self.release("disabled")
        super().hideEvent(event)  # type: ignore[arg-type]


class EStopButton(Button):
    """非常停止(ヘッダー右)。赤地白文字・高さ 40・幅 ≥ 140。`[⊗ 非常停止  Pause / Esc(運転中)]`。

    1 クリックで即座に効く(長押しにしない)。画面内で唯一の赤い塗り。
    """

    def __init__(self, text: str, hint: str = "Pause", *, parent: QWidget | None = None) -> None:
        super().__init__("", "danger", parent=parent)
        self.setFixedHeight(H_ESTOP)
        self.setMinimumWidth(W_ESTOP_MIN)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP_PAD, 0, SP_PAD, 0)
        lay.setSpacing(SP_RELATED)
        lay.addStretch(1)
        self._icon_label = QLabel()
        self._icon_label.setFixedSize(ICON_M, ICON_M)
        self._text_label = QLabel(text)
        self._text_label.setProperty("role", "heading")
        # 太字の幅で確保する(QSS 適用前の寸法で欠けないように)
        fm = QFontMetrics(make_font(FS_BODY, semibold=True))
        self._text_label.setMinimumWidth(fm.horizontalAdvance(text) + 4)
        self._hint_label = QLabel(hint)
        self._hint_label.setProperty("role", "caption")
        lay.addWidget(self._icon_label)
        lay.addWidget(self._text_label)
        lay.addSpacing(SP_ICON)
        lay.addWidget(self._hint_label)
        lay.addStretch(1)
        # ヒントも文字幅で確保し、ボタンの最小幅を中身に合わせる(欠け・重なり防止)
        hint_fm = QFontMetrics(make_font(FS_CAPTION))
        hint_w = max((hint_fm.horizontalAdvance(t) for t in hint.split("\n")), default=0) + 4
        self._hint_label.setMinimumWidth(hint_w)
        content = lay.totalMinimumSize().width()
        self.setMinimumWidth(max(W_ESTOP_MIN, content))
        for w in (self._icon_label, self._text_label, self._hint_label):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._icon_label.setPixmap(pixmap("octagon-x", tokens().estop_text, ICON_M))
        theme_manager().changed.connect(self._retint)

    def _retint(self, _name: str = "") -> None:
        self._icon_label.setPixmap(pixmap("octagon-x", tokens().estop_text, ICON_M))
