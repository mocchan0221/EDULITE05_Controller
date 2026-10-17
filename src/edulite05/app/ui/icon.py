"""Lucide(ISC)アイコン。`assets/icons/*.svg` を読み、テーマの文字色などで着色して描く。

- `icon("play", "text_primary")` … QIcon(通常 + 無効色)。色はトークン属性名か `#rrggbb`
- `pixmap(name, color, size)` … QPixmap(画面の倍率に合わせた解像度)
- `icon_file(name, color)` … QSS の `url()` に渡す PNG のパス(コンボの矢印・チェックなど用)

ライセンス: `assets/icons/LICENSE`(Lucide, ISC)。
"""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from edulite05.app.ui import style
from edulite05.app.ui.tokens import ICON_S, Tokens

ICON_DIR = Path(__file__).resolve().parents[4] / "assets" / "icons"
_CACHE_DIR = Path(tempfile.gettempdir()) / "edulite05_icons"


def resolve_color(color: str, tk: Tokens | None = None) -> str:
    """トークン属性名(`text_secondary` など)か色文字列を色文字列にする。"""
    if color.startswith("#") or color.startswith("rgb") or color == "transparent":
        return color
    return str(getattr(tk or style.tokens(), color))


@lru_cache(maxsize=256)
def _svg_text(name: str) -> str:
    path = ICON_DIR / f"{name}.svg"
    if not path.exists():
        raise FileNotFoundError(f"アイコンがありません: {path}")
    return path.read_text(encoding="utf-8")


def available_icons() -> list[str]:
    return sorted(p.stem for p in ICON_DIR.glob("*.svg"))


def _device_ratio() -> float:
    scr = QGuiApplication.primaryScreen()
    return float(scr.devicePixelRatio()) if scr is not None else 1.0


@lru_cache(maxsize=1024)
def _render(name: str, color: str, size: int, dpr: float) -> QPixmap:
    svg = _svg_text(name).replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    px = max(1, round(size * dpr))
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p)
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def pixmap(name: str, color: str = "text_secondary", size: int = ICON_S) -> QPixmap:
    return _render(name, resolve_color(color), size, _device_ratio())


def icon(
    name: str,
    color: str = "text_secondary",
    size: int = ICON_S,
    *,
    disabled_color: str | None = None,
    active_color: str | None = None,
) -> QIcon:
    """通常色 + 無効色(既定は `text_disabled`)の QIcon。"""
    ic = QIcon()
    normal = pixmap(name, color, size)
    ic.addPixmap(normal, QIcon.Mode.Normal)
    ic.addPixmap(pixmap(name, active_color or color, size), QIcon.Mode.Active)
    ic.addPixmap(pixmap(name, disabled_color or "text_disabled", size), QIcon.Mode.Disabled)
    return ic


def icon_file(name: str, color: str, size: int = ICON_S) -> str:
    """着色した PNG を一時フォルダに書き出してパスを返す(QSS の `url()` 用。`/` 区切り)。"""
    col = resolve_color(color)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"{name}_{col.lstrip('#')}_{size}.png"
    if not path.exists():
        pm = _render(name, col, size, 1.0)
        pm.save(str(path), "PNG")
    return path.as_posix()


def blank_icon(size: int = ICON_S) -> QIcon:
    return QIcon(QPixmap(QSize(size, size)))


def tinted(pm: QPixmap, color: str) -> QPixmap:
    """既存のピクスマップを 1 色で塗り直す(アルファは保つ)。"""
    out = QPixmap(pm)
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(out.rect(), QColor(resolve_color(color)))
    p.end()
    return out
