"""QColor へ変換する補助。トークンの `overlay_*` は QSS 用の `rgba(r, g, b, a)` 文字列で、
`QColor("rgba(...)")` は解釈できず黒になるため、自前描画(paintEvent)ではここを通す。"""

from __future__ import annotations

import re

from PySide6.QtGui import QColor

_RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(\d+(?:\.\d+)?)\s*)?\)")


def qcolor(value: str) -> QColor:
    """`#rrggbb` / 色名 / `rgba(r, g, b, a)`(a は 0〜255。`ui.tokens.rgba` の形式)から QColor。"""
    m = _RGBA.fullmatch(value.strip())
    if m is None:
        return QColor(value)
    r, g, b = (int(m.group(i)) for i in (1, 2, 3))
    a = 255 if m.group(4) is None else round(float(m.group(4)))
    return QColor(r, g, b, a)
