"""排他選択のセグメント(ui_design.md §6)。選択中は面の色 + Semibold(強調色の塗りにしない)。"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QKeyEvent
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton, QWidget

from edulite05.app.ui.icon import icon
from edulite05.app.ui.style import make_font, set_prop, theme_manager
from edulite05.app.ui.tokens import FS_BODY, H_CONTROL, ICON_S, SP_GROUP

SegItem = tuple[str, str] | tuple[str, str, str | None]


class _SegButton(QPushButton):
    def __init__(self, key: str, text: str, icon_name: str | None) -> None:
        super().__init__(text)
        self.key = key
        self.icon_name = icon_name
        self.setCheckable(True)
        self.setProperty("seg", True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_icon()

    def refresh_icon(self) -> None:
        if self.icon_name:
            self.setIconSize(QSize(ICON_S, ICON_S))
            color = "text_primary" if self.isChecked() else "text_secondary"
            self.setIcon(icon(self.icon_name, color))


class Segmented(QFrame):
    """`Segmented([("jog", "JOG"), ("vel", "速度")])`。

    - `selected(key)`: ユーザー操作で選択が変わった(プログラムからの変更では出ない)
    - `current_changed(key)`: 選択が変わった(どちらの経路でも)
    - `set_item_enabled(key, False, "理由")`: 選べなくして理由をツールチップに出す
    - `set_item_tone(key, "caution")`: その項目が選択中のときの意味色(例: 高出力の琥珀)
    各セグメントの幅は最大の項目にそろえる。高さ 32。
    """

    selected = Signal(str)
    current_changed = Signal(str)

    def __init__(
        self, items: Sequence[SegItem] = (), *, current: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.setFixedHeight(H_CONTROL)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(2, 2, 2, 2)
        self._lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, _SegButton] = {}
        self._reasons: dict[str, str] = {}
        self._current: str | None = None
        self.set_items(items, current=current)
        theme_manager().changed.connect(self._retint)

    # ---- 構成 ----
    def set_items(self, items: Sequence[SegItem], *, current: str | None = None) -> None:
        for b in list(self._buttons.values()):
            self._group.removeButton(b)
            self._lay.removeWidget(b)
            b.deleteLater()
        self._buttons.clear()
        self._reasons.clear()
        specs = [(it[0], it[1], it[2] if len(it) > 2 else None) for it in items]  # type: ignore[misc]
        fm = QFontMetrics(make_font(FS_BODY, semibold=True))
        widest = max((fm.horizontalAdvance(t) for _k, t, _i in specs), default=0)
        for key, text, icon_name in specs:
            b = _SegButton(key, text, icon_name)
            extra = (ICON_S + 6) if icon_name else 0
            b.setMinimumWidth(widest + extra + 2 * SP_GROUP + 4)
            self._group.addButton(b)
            self._lay.addWidget(b, 1)
            b.clicked.connect(lambda _c=False, k=key: self._on_clicked(k))
            self._buttons[key] = b
        self._current = None
        if specs:
            first = current if current in self._buttons else specs[0][0]
            self.set_current(first)

    def keys(self) -> list[str]:
        return list(self._buttons)

    # ---- 選択 ----
    def current(self) -> str | None:
        return self._current

    def set_current(self, key: str) -> None:
        """プログラムから選択を変える(`selected` は出さない)。"""
        if key not in self._buttons:
            return
        for k, b in self._buttons.items():
            b.setChecked(k == key)
            b.refresh_icon()
        if key != self._current:
            self._current = key
            self.current_changed.emit(key)

    def _on_clicked(self, key: str) -> None:
        b = self._buttons[key]
        if not b.isEnabled():
            return
        changed = key != self._current
        self.set_current(key)
        if changed:
            self.selected.emit(key)

    # ---- 無効化と理由 ----
    def set_item_enabled(self, key: str, enabled: bool, reason: str = "") -> None:
        b = self._buttons.get(key)
        if b is None:
            return
        b.setEnabled(enabled)
        b.setToolTip("" if enabled else reason)
        self._reasons[key] = "" if enabled else reason

    def set_all_enabled(self, enabled: bool, reason: str = "") -> None:
        for k in self._buttons:
            self.set_item_enabled(k, enabled, reason)

    def item_enabled(self, key: str) -> bool:
        return self._buttons[key].isEnabled()

    def item_tooltip(self, key: str) -> str:
        return self._buttons[key].toolTip()

    def set_item_tone(self, key: str, tone: str | None) -> None:
        b = self._buttons.get(key)
        if b is not None:
            set_prop(b, "tone", tone or "")

    def set_item_text(self, key: str, text: str) -> None:
        b = self._buttons.get(key)
        if b is not None:
            b.setText(text)

    def button(self, key: str) -> QPushButton:
        return self._buttons[key]

    # ---- キーボード: ← → で隣の有効な項目へ ----
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self._current is not None:
            keys = self.keys()
            step = -1 if event.key() == Qt.Key.Key_Left else 1
            i = keys.index(self._current)
            while 0 <= i + step < len(keys):
                i += step
                if self._buttons[keys[i]].isEnabled():
                    self._on_clicked(keys[i])
                    break
            event.accept()
            return
        super().keyPressEvent(event)

    def _retint(self, _name: str = "") -> None:
        for b in self._buttons.values():
            b.refresh_icon()
