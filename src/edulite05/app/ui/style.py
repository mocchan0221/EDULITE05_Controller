"""テーマ管理と QSS 生成。トークン(`tokens.py`)から全体のスタイルシートを作る。

部品は動的プロパティで見た目を選ぶ(QSS の `[prop="value"]` セレクタ):

- `variant`  : Button の種類(accent / standard / subtle / danger / caution / inverse)
- `role`     : Label の役割(secondary / caption / caption-tertiary / heading / inspector / title /
               readout / readout-s / body-mono)
- `tone`     : 意味色(neutral / accent / success / caution / critical)
- `card`     : "true" でカード面
- `severity` : InfoBar の重大度(info / caution / critical / estop)

使い方:

    from edulite05.app.ui.style import theme_manager, tokens
    theme_manager().apply("dark")      # アプリ全体に適用(QSS・パレット・フォント)
    tokens().bg_layer                  # 現在のテーマの色
    theme_manager().changed.connect(slot)   # テーマ切替の通知(自前描画の部品が再描画する)
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from edulite05.app.ui import tokens as tk
from edulite05.app.ui.tokens import THEMES, Tokens

_FONT_CACHE: dict[str, list[str]] = {}


def _available(candidates: tuple[str, ...]) -> list[str]:
    families = set(QFontDatabase.families())
    present = [c for c in candidates if c in families]
    return present or list(candidates)


def ui_families() -> list[str]:
    """和文 UI フォントの優先順(インストール済みのものだけ。無ければ候補をそのまま)。"""
    if "ui" not in _FONT_CACHE:
        _FONT_CACHE["ui"] = _available(tk.UI_FONT_FAMILIES)
    return _FONT_CACHE["ui"]


def mono_family() -> str:
    """等幅フォントの最初に見つかったもの。"""
    if "mono" not in _FONT_CACHE:
        _FONT_CACHE["mono"] = _available(tk.MONO_FONT_FAMILIES)
    return _FONT_CACHE["mono"][0]


def make_font(size: int = tk.FS_BODY, *, semibold: bool = False, mono: bool = False) -> QFont:
    """自前描画(paintEvent)用のフォント。サイズは px(トークンの FS_*)。"""
    f = QFont()
    f.setFamilies([mono_family()] if mono else ui_families())
    f.setPixelSize(size)
    f.setWeight(QFont.Weight.DemiBold if semibold else QFont.Weight.Normal)
    if mono:
        f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def _palette(t: Tokens) -> QPalette:
    pal = QPalette()
    c = QColor
    R = QPalette.ColorRole
    pal.setColor(R.Window, c(t.bg_layer))
    pal.setColor(R.WindowText, c(t.text_primary))
    pal.setColor(R.Base, c(t.bg_control))
    pal.setColor(R.AlternateBase, c(t.bg_card))
    pal.setColor(R.Text, c(t.text_primary))
    pal.setColor(R.Button, c(t.bg_control))
    pal.setColor(R.ButtonText, c(t.text_primary))
    pal.setColor(R.BrightText, c(t.estop_text))
    pal.setColor(R.ToolTipBase, c(t.bg_card))
    pal.setColor(R.ToolTipText, c(t.text_primary))
    pal.setColor(R.PlaceholderText, c(t.text_tertiary))
    pal.setColor(R.Highlight, c(t.accent))
    pal.setColor(R.HighlightedText, c(t.accent_text_on))
    pal.setColor(R.Link, c(t.accent))
    pal.setColor(R.Mid, c(t.stroke_control))
    pal.setColor(R.Dark, c(t.stroke_divider))
    pal.setColor(R.Light, c(t.bg_card))
    pal.setColor(R.Shadow, c("#000000"))
    for role in (R.Text, R.ButtonText, R.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, c(t.text_disabled))
    return pal


def build_qss(t: Tokens) -> str:
    """トークンからアプリ全体の QSS を生成する。値はすべて `t` と tokens.py の定数から来る。"""
    from edulite05.app.ui.icon import icon_file  # 循環 import を避けるため遅延

    mono = mono_family()
    chev_down = icon_file("chevron-down", t.text_secondary)
    check_on = icon_file("check", t.accent_text_on)
    b = t.bg_control
    return f"""
/* ================= 基本 ================= */
QMainWindow {{ background: {t.bg_base}; }}
QDialog {{ background: {t.bg_layer}; }}
QToolTip {{
    background: {t.bg_card}; color: {t.text_primary}; border: 1px solid {t.stroke_control};
    padding: 4px 8px; font-size: {tk.FS_CAPTION}px;
}}
QWidget[surface="layer"] {{ background: {t.bg_layer}; }}
QWidget[surface="base"] {{ background: {t.bg_base}; }}
QWidget[surface="sunken"] {{ background: {t.bg_sunken}; }}
QFrame[card="true"] {{
    background: {t.bg_card}; border: 1px solid {t.card_border}; border-radius: {tk.R_CARD}px;
}}
QFrame[divider="h"] {{ background: {t.stroke_divider}; border: none; min-height: 1px; max-height: 1px; }}
QFrame[divider="v"] {{ background: {t.stroke_divider}; border: none; min-width: 1px; max-width: 1px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ================= ラベル ================= */
QLabel {{ background: transparent; color: {t.text_primary}; }}
QLabel[role="secondary"] {{ color: {t.text_secondary}; }}
QLabel[role="caption"] {{ color: {t.text_secondary}; font-size: {tk.FS_CAPTION}px; }}
QLabel[role="caption-strong"] {{ color: {t.text_secondary}; font-size: {tk.FS_CAPTION}px; font-weight: {tk.W_SEMIBOLD}; }}
QLabel[role="caption-tertiary"] {{ color: {t.text_tertiary}; font-size: {tk.FS_CAPTION}px; }}
QLabel[role="heading"] {{ font-weight: {tk.W_SEMIBOLD}; }}
QLabel[role="inspector"] {{ font-size: {tk.FS_INSPECTOR}px; font-weight: {tk.W_SEMIBOLD}; }}
QLabel[role="title"] {{ font-size: {tk.FS_TITLE}px; font-weight: {tk.W_SEMIBOLD}; }}
QLabel[role="readout"] {{ font-family: "{mono}"; font-size: {tk.FS_READOUT}px; font-weight: {tk.W_SEMIBOLD}; }}
QLabel[role="readout-s"] {{ font-family: "{mono}"; font-size: {tk.FS_TITLE}px; }}
QLabel[role="body-mono"] {{ font-family: "{mono}"; }}
QLabel[tone="accent"] {{ color: {t.accent}; }}
QLabel[tone="success"] {{ color: {t.success}; }}
QLabel[tone="caution"] {{ color: {t.caution}; }}
QLabel[tone="critical"] {{ color: {t.critical}; }}

/* ================= ボタン(variant) ================= */
QPushButton {{
    background: {b}; color: {t.text_primary}; border: 1px solid {t.stroke_control};
    border-radius: {tk.R_CONTROL}px; padding: 0 {tk.SP_PAD}px; min-height: {tk.H_CONTROL - 2}px;
    font-size: {tk.FS_BODY}px;
}}
QPushButton:hover {{ background: {t.bg_control_hover}; }}
QPushButton:pressed {{ background: {t.bg_control_pressed}; }}
QPushButton:focus {{ border: {tk.FOCUS_RING_PX}px solid {t.accent}; padding: 0 {tk.SP_PAD - 1}px; }}
QPushButton:disabled {{ background: transparent; color: {t.text_disabled}; border-color: {t.stroke_divider}; }}

QPushButton[variant="accent"] {{
    background: {t.accent}; color: {t.accent_text_on}; border: 1px solid {t.accent};
    font-weight: {tk.W_SEMIBOLD};
}}
QPushButton[variant="accent"]:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton[variant="accent"]:pressed {{ background: {t.accent_pressed}; border-color: {t.accent_pressed}; }}
QPushButton[variant="accent"]:focus {{ border: {tk.FOCUS_RING_PX}px solid {t.text_primary}; }}
QPushButton[variant="accent"]:disabled {{
    background: {t.fill_disabled}; color: {t.text_disabled}; border-color: {t.fill_disabled};
}}

QPushButton[variant="subtle"] {{ background: transparent; border: 1px solid transparent; }}
QPushButton[variant="subtle"]:hover {{ background: {t.overlay_hover}; }}
QPushButton[variant="subtle"]:pressed {{ background: {t.overlay_pressed}; }}
QPushButton[variant="subtle"]:focus {{ border: {tk.FOCUS_RING_PX}px solid {t.accent}; }}
QPushButton[variant="subtle"]:disabled {{ background: transparent; border-color: transparent; }}

QPushButton[variant="danger"] {{
    background: {t.estop}; color: {t.estop_text}; border: 1px solid {t.estop};
    font-weight: {tk.W_SEMIBOLD};
}}
QPushButton[variant="danger"]:hover {{ background: {t.estop_hover}; border-color: {t.estop_hover}; }}
QPushButton[variant="danger"]:pressed {{ background: {t.estop_pressed}; border-color: {t.estop_pressed}; }}
QPushButton[variant="danger"]:focus {{ border: {tk.FOCUS_RING_PX}px solid {t.estop_text}; }}

QPushButton[variant="caution"] {{
    background: {t.caution_fill}; color: {t.caution_text_on}; border: 1px solid {t.caution_fill};
    font-weight: {tk.W_SEMIBOLD};
}}
QPushButton[variant="caution"]:hover {{ background: {t.caution_fill}; border-color: {t.caution_text_on}; }}
QPushButton[variant="caution"]:pressed {{ background: {t.caution_fill}; border-color: {t.caution_text_on}; }}
QPushButton[variant="caution"]:focus {{ border: {tk.FOCUS_RING_PX}px solid {t.caution_text_on}; }}
QPushButton[variant="caution"]:disabled {{
    background: {t.fill_disabled}; color: {t.text_disabled}; border-color: {t.fill_disabled};
}}

QPushButton[large="true"] {{ min-height: {tk.H_PRIMARY - 2}px; padding: 0 {tk.SP_SECTION}px; }}
QPushButton[large="true"]:focus {{ padding: 0 {tk.SP_SECTION - 1}px; }}
QPushButton[iconOnly="true"] {{ padding: 0; min-width: {tk.H_CONTROL - 2}px; }}
QPushButton[iconOnly="true"]:focus {{ padding: 0; }}
QPushButton[align="left"] {{ text-align: left; }}
QPushButton[variant="danger"] QLabel {{ color: {t.estop_text}; background: transparent; }}

/* 濃い面(非常停止バナー)の上に置く反転ボタン */
QPushButton[variant="inverse"] {{
    background: {t.estop_text}; color: {t.estop}; border: 1px solid {t.estop_text};
    font-weight: {tk.W_SEMIBOLD};
}}
QPushButton[variant="inverse"]:hover {{ background: {t.neutral_surface}; border-color: {t.neutral_surface}; }}
QPushButton[variant="inverse"]:focus {{ border: {tk.FOCUS_RING_PX}px solid {t.bg_sunken}; }}

/* 押下保持ボタン(JOG)。押している間は塗り */
QPushButton[variant="hold"] {{
    background: {t.bg_control}; color: {t.text_primary}; border: 1px solid {t.stroke_control};
    font-weight: {tk.W_SEMIBOLD}; min-height: {tk.H_JOG - 2}px;
}}
QPushButton[variant="hold"][holding="true"] {{
    background: {t.accent}; color: {t.accent_text_on}; border-color: {t.accent};
}}

/* ================= セグメント ================= */
QFrame#Segmented {{
    background: {t.seg_track}; border: 1px solid {t.stroke_divider};
    border-radius: {tk.R_CARD + 2}px;
}}
QFrame#Segmented:focus {{ border: {tk.FOCUS_RING_PX}px solid {t.accent}; }}
QPushButton[seg="true"] {{
    background: transparent; border: 1px solid transparent; border-radius: {tk.R_CONTROL}px;
    color: {t.text_secondary}; padding: 0 {tk.SP_GROUP}px; min-height: {tk.H_CONTROL - 8}px;
}}
QPushButton[seg="true"]:hover {{ background: {t.overlay_hover}; color: {t.text_primary}; }}
QPushButton[seg="true"]:checked {{
    background: {t.seg_selected}; color: {t.text_primary}; font-weight: {tk.W_SEMIBOLD};
    border: 1px solid {t.stroke_control};
}}
QPushButton[seg="true"][tone="caution"]:checked {{
    background: {t.caution_surface}; color: {t.caution}; border: 1px solid {t.caution};
}}
QPushButton[seg="true"]:disabled {{ background: transparent; color: {t.text_disabled}; border-color: transparent; }}
QPushButton[seg="true"]:checked:disabled {{
    background: {t.fill_disabled}; color: {t.text_disabled}; border: 1px solid transparent;
}}

/* ================= バッジ・バナー・トースト・ポップオーバー ================= */
QFrame#Badge {{ border-radius: {tk.R_PILL}px; background: transparent; }}
QFrame#Badge[tone="neutral"] {{ background: {t.neutral_surface}; }}
QFrame#Badge[tone="success"] {{ background: {t.success_surface}; }}
QFrame#Badge[tone="caution"] {{ background: {t.caution_surface}; }}
QFrame#Badge[tone="critical"] {{ background: {t.critical_surface}; }}
QFrame#Badge[tone="neutral"] QLabel {{ color: {t.text_secondary}; }}
QFrame#Badge[tone="accent"] QLabel {{ color: {t.accent}; }}
QFrame#Badge[tone="success"] QLabel {{ color: {t.success}; }}
QFrame#Badge[tone="caution"] QLabel {{ color: {t.caution}; }}
QFrame#Badge[tone="critical"] QLabel {{ color: {t.critical}; }}

QFrame#InfoBar {{ background: {t.bg_card}; border: none; }}
QFrame#InfoBar[severity="caution"] {{ background: {t.caution_surface}; }}
QFrame#InfoBar[severity="critical"] {{ background: {t.critical_surface}; }}
QFrame#InfoBar[severity="estop"] {{ background: {t.estop}; }}
QFrame#InfoBar QLabel[role="secondary"] {{ color: {t.text_secondary}; }}
QFrame#InfoBar[severity="estop"] QLabel {{ color: {t.estop_text}; }}
QFrame#InfoBarBand[severity="info"] {{ background: {t.text_tertiary}; }}
QFrame#InfoBarBand[severity="caution"] {{ background: {t.caution}; }}
QFrame#InfoBarBand[severity="critical"] {{ background: {t.critical}; }}
QFrame#InfoBarBand[severity="estop"] {{ background: {t.estop_text}; }}

QFrame#Toast, QFrame#PopoverBody {{
    background: {t.bg_card}; border: 1px solid {t.stroke_control}; border-radius: {tk.R_OVERLAY}px;
}}

/* ================= 入力 ================= */
QLineEdit, QAbstractSpinBox {{
    background: {b}; color: {t.text_primary}; border: 1px solid {t.stroke_control};
    border-radius: {tk.R_CONTROL}px; padding: 0 {tk.SP_RELATED}px; min-height: {tk.H_CONTROL - 2}px;
    selection-background-color: {t.accent}; selection-color: {t.accent_text_on};
}}
QLineEdit:hover, QAbstractSpinBox:hover {{ background: {t.bg_control_hover}; }}
QLineEdit:focus, QAbstractSpinBox:focus {{
    border: {tk.FOCUS_RING_PX}px solid {t.accent}; padding: 0 {tk.SP_RELATED - 1}px;
}}
QLineEdit[invalid="true"], QAbstractSpinBox[invalid="true"] {{
    border: {tk.FOCUS_RING_PX}px solid {t.critical}; padding: 0 {tk.SP_RELATED - 1}px;
}}
QLineEdit:disabled, QAbstractSpinBox:disabled {{
    background: transparent; color: {t.text_disabled}; border-color: {t.stroke_divider};
}}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QAbstractSpinBox[mono="true"], QLineEdit[mono="true"] {{ font-family: "{mono}"; }}

QComboBox {{
    background: {b}; color: {t.text_primary}; border: 1px solid {t.stroke_control};
    border-radius: {tk.R_CONTROL}px; padding: 0 {tk.SP_RELATED}px; min-height: {tk.H_CONTROL - 2}px;
}}
QComboBox:hover {{ background: {t.bg_control_hover}; }}
QComboBox:focus {{ border: {tk.FOCUS_RING_PX}px solid {t.accent}; padding: 0 {tk.SP_RELATED - 1}px; }}
QComboBox:disabled {{ background: transparent; color: {t.text_disabled}; border-color: {t.stroke_divider}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({chev_down}); width: {tk.ICON_S}px; height: {tk.ICON_S}px; }}
QComboBox QAbstractItemView {{
    background: {t.bg_card}; color: {t.text_primary}; border: 1px solid {t.stroke_control};
    border-radius: {tk.R_CONTROL}px; padding: {tk.SP_ICON}px; outline: 0;
    selection-background-color: {t.accent_surface}; selection-color: {t.text_primary};
}}
QComboBox QAbstractItemView::item {{ min-height: {tk.H_ROW}px; padding: 0 {tk.SP_RELATED}px; border-radius: {tk.R_CONTROL}px; }}
QComboBox QAbstractItemView::item:hover {{ background: {t.bg_control_hover}; }}

QCheckBox {{ spacing: {tk.SP_RELATED}px; min-height: 24px; }}
QCheckBox::indicator {{
    width: {tk.ICON_S}px; height: {tk.ICON_S}px; border: 1px solid {t.text_tertiary};
    border-radius: {tk.R_CONTROL}px; background: {b};
}}
QCheckBox::indicator:hover {{ border-color: {t.text_secondary}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url({check_on}); }}
QCheckBox::indicator:disabled {{ border-color: {t.stroke_divider}; background: transparent; }}
QCheckBox:disabled {{ color: {t.text_disabled}; }}

/* ================= 表・リスト・テキスト ================= */
QTableView, QTreeView, QListView {{
    background: {t.bg_card}; color: {t.text_primary}; border: 1px solid {t.stroke_divider};
    border-radius: {tk.R_CONTROL}px; gridline-color: transparent; outline: 0;
    selection-background-color: {t.accent_surface}; selection-color: {t.text_primary};
}}
QTableView::item {{ padding: 0 {tk.SP_RELATED}px; border: none; }}
QTableView::item:hover {{ background: {t.overlay_hover}; }}
QTableView::item:selected {{ background: {t.accent_surface}; color: {t.text_primary}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {t.bg_card}; color: {t.text_secondary}; border: none;
    border-bottom: 1px solid {t.stroke_divider}; padding: 0 {tk.SP_RELATED}px;
    min-height: {tk.H_ROW}px; font-weight: {tk.W_SEMIBOLD};
}}
QTableCornerButton::section {{ background: {t.bg_card}; border: none; }}
QPlainTextEdit, QTextEdit {{
    background: {t.bg_sunken}; color: {t.text_primary}; border: 1px solid {t.stroke_divider};
    border-radius: {tk.R_CONTROL}px; selection-background-color: {t.accent}; selection-color: {t.accent_text_on};
}}

/* ================= スクロールバー・スプリッタ ================= */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.stroke_control}; border-radius: 4px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {t.text_tertiary}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.stroke_control}; border-radius: 4px; min-width: 32px; }}
QScrollBar::handle:horizontal:hover {{ background: {t.text_tertiary}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {t.accent}; }}

/* ================= メニュー・ステータスバー ================= */
QMenu {{
    background: {t.bg_card}; color: {t.text_primary}; border: 1px solid {t.stroke_control};
    border-radius: {tk.R_CARD}px; padding: {tk.SP_ICON}px;
}}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: {tk.R_CONTROL}px; min-height: 20px; }}
QMenu::item:selected {{ background: {t.bg_control_hover}; }}
QMenu::item:disabled {{ color: {t.text_disabled}; }}
QMenu::separator {{ height: 1px; background: {t.stroke_divider}; margin: {tk.SP_ICON}px {tk.SP_RELATED}px; }}
QMenu::icon {{ padding-left: 8px; }}
QStatusBar {{ background: {t.bg_base}; color: {t.text_secondary}; border: none; font-size: {tk.FS_CAPTION}px; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {t.text_secondary}; font-size: {tk.FS_CAPTION}px; padding: 0 {tk.SP_RELATED}px; }}
QProgressBar {{
    background: {t.seg_track}; border: none; border-radius: 3px; min-height: 6px; max-height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}

/* ================= シェル ================= */
QFrame#Header {{ background: {t.bg_base}; }}
QFrame#NavRail {{ background: {t.bg_base}; }}
QWidget#PageHost {{ background: {t.bg_layer}; }}
"""


class ThemeManager(QObject):
    """テーマ切替。`changed(name)` で自前描画の部品が再描画する。"""

    changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._name = "dark"

    @property
    def name(self) -> str:
        return self._name

    @property
    def tokens(self) -> Tokens:
        return THEMES[self._name]

    def apply(self, name: str, app: QApplication | None = None) -> None:
        """テーマをアプリ全体(パレット・フォント・QSS)に適用して `changed` を発行する。"""
        app = app or QApplication.instance()  # type: ignore[assignment]
        if app is None:
            return
        self._name = name if name in THEMES else "dark"
        t = self.tokens
        if app.style().objectName().lower() != "fusion":
            app.setStyle("Fusion")
        app.setPalette(_palette(t))
        app.setFont(make_font(tk.FS_BODY))
        app.setStyleSheet(build_qss(t))
        self.changed.emit(self._name)


def resolve_system_theme() -> str:
    """OS の配色(ダーク / ライト)。取れないときはダーク(アプリの既定)。"""
    hints = QGuiApplication.styleHints()
    scheme = hints.colorScheme() if hints is not None else Qt.ColorScheme.Unknown
    return "light" if scheme == Qt.ColorScheme.Light else "dark"


_MANAGER: ThemeManager | None = None


def theme_manager() -> ThemeManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ThemeManager()
    return _MANAGER


def tokens() -> Tokens:
    """現在のテーマのトークン。"""
    return theme_manager().tokens


def repolish(widget: QObject) -> None:
    """動的プロパティを変えた後にスタイルを再評価させる。"""
    from PySide6.QtWidgets import QWidget

    if isinstance(widget, QWidget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()


def set_prop(widget: QObject, name: str, value: object) -> None:
    """動的プロパティを設定し、値が変わったときだけ再評価する。"""
    if widget.property(name) != value:
        widget.setProperty(name, value)
        repolish(widget)
