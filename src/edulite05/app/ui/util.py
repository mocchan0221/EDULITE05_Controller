"""部品を組むときの小さな補助(レイアウト・区切り線・テーマ追従)。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLayout, QVBoxLayout, QWidget

from edulite05.app.ui.tokens import SP_PAD, SP_RELATED


def vbox(
    parent: QWidget | None = None,
    *,
    margins: int | tuple[int, int, int, int] = 0,
    spacing: int = SP_RELATED,
) -> QVBoxLayout:
    lay = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    _setup(lay, margins, spacing)
    return lay


def hbox(
    parent: QWidget | None = None,
    *,
    margins: int | tuple[int, int, int, int] = 0,
    spacing: int = SP_RELATED,
) -> QHBoxLayout:
    lay = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    _setup(lay, margins, spacing)
    return lay


def _setup(lay: QLayout, margins: int | tuple[int, int, int, int], spacing: int) -> None:
    if isinstance(margins, int):
        lay.setContentsMargins(margins, margins, margins, margins)
    else:
        lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)


def divider(orientation: Qt.Orientation = Qt.Orientation.Horizontal) -> QFrame:
    """1px の区切り線(`stroke.divider`)。"""
    f = QFrame()
    f.setProperty("divider", "h" if orientation == Qt.Orientation.Horizontal else "v")
    f.setFrameShape(QFrame.Shape.NoFrame)
    return f


def styled_background(widget: QWidget, surface: str) -> QWidget:
    """プレーンな QWidget に QSS の背景(`surface`: layer / base / sunken)を効かせる。"""
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    widget.setProperty("surface", surface)
    return widget


__all__ = [
    "SP_PAD",
    "divider",
    "hbox",
    "styled_background",
    "vbox",
]
