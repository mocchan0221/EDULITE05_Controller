"""デザイントークン(ui_design.md §7)。色・寸法・文字・間隔の唯一の定義場所。

ページ・部品は色や寸法を直書きせず、ここ(または `style.tokens()`)から取る。
ダーク(既定)とライトは同じ `Tokens` 型の 2 インスタンスなので、片方にだけ欠けることはない。
仕様書のドット記法(`bg.base` など)は `SPEC_NAMES` で属性名に対応づける。
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------- 色の補助
def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(fg: str, bg: str, alpha: float) -> str:
    """`fg` を不透明度 `alpha` で `bg` に重ねた不透明色(`#rrggbb`)。ステートレイヤーの合成用。"""
    r1, g1, b1 = _rgb(fg)
    r2, g2, b2 = _rgb(bg)
    r = round(r1 * alpha + r2 * (1 - alpha))
    g = round(g1 * alpha + g2 * (1 - alpha))
    b = round(b1 * alpha + b2 * (1 - alpha))
    return f"#{r:02X}{g:02X}{b:02X}"


def rgba(hex_color: str, alpha: float) -> str:
    """QSS 用の `rgba(r, g, b, a)`(a は 0〜255)。"""
    r, g, b = _rgb(hex_color)
    return f"rgba({r}, {g}, {b}, {round(alpha * 255)})"


def luminance(hex_color: str) -> float:
    def ch(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = _rgb(hex_color)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a: str, b: str) -> float:
    """WCAG のコントラスト比(1〜21)。"""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------- 色トークン
@dataclass(frozen=True, slots=True)
class Tokens:
    name: str  # "dark" / "light"
    # 面
    bg_base: str  # ウィンドウの地・ナビ・ヘッダー
    bg_layer: str  # ページ本体・インスペクタの面
    bg_card: str  # グループ面(読み値セル、保守の行)
    bg_sunken: str  # グラフ・3D・ログの描画面
    bg_control: str  # ボタン・入力欄
    bg_control_hover: str
    bg_control_pressed: str
    stroke_control: str  # コントロール枠
    stroke_divider: str  # 区切り線
    card_border: str  # カードの縁(ダークは面と同色で見えない。ライトは白地を分けるため薄く出す)
    # 文字
    text_primary: str
    text_secondary: str
    text_tertiary: str
    text_disabled: str
    fill_disabled: str  # 無効コントロールの面(約 10%)
    # 操作・選択
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_text_on: str
    accent_surface: str  # 選択中の背景(accent 15%)
    # 状態
    success: str
    success_surface: str
    caution: str  # 文字・アイコン・帯
    caution_surface: str
    caution_fill: str  # HIGH 確定ボタンの塗り
    caution_text_on: str
    critical: str
    critical_surface: str
    estop: str
    estop_text: str
    estop_hover: str
    estop_pressed: str
    neutral_surface: str  # 状態バッジ(平常)の面
    # 部品専用
    seg_track: str  # セグメントの溝
    seg_selected: str  # セグメントの選択中面
    gauge_track: str
    gauge_band: str
    plot_grid: str
    # オーバーレイ(rgba)
    overlay_hover: str
    overlay_pressed: str
    shadow: str  # 影の色(#rrggbb)
    shadow_alpha: int  # 0〜255
    # モータ色 §7.2
    motor: tuple[str, str, str]

    def motor_color(self, index: int) -> str:
        """モータ ID の順(0 始まり)で固定。台数が変わっても塗り替えない。"""
        return self.motor[index % len(self.motor)]

    def tone_color(self, tone: str) -> str:
        """意味(tone)から前景色。tone: neutral / accent / success / caution / critical。"""
        return {
            "neutral": self.text_secondary,
            "accent": self.accent,
            "success": self.success,
            "caution": self.caution,
            "critical": self.critical,
        }[tone]

    def tone_surface(self, tone: str) -> str:
        return {
            "neutral": self.neutral_surface,
            "accent": "transparent",  # 「有効」は文字だけ青系(面なし)
            "success": self.success_surface,
            "caution": self.caution_surface,
            "critical": self.critical_surface,
        }[tone]


def _dark() -> Tokens:
    layer = "#23262B"
    text = "#E9EBEE"
    accent = "#479EF5"
    on = "#0B1A2B"
    return Tokens(
        name="dark",
        bg_base="#1D1F23",
        bg_layer=layer,
        bg_card="#2A2D33",
        bg_sunken="#17191C",
        bg_control="#30343A",
        bg_control_hover="#383C43",
        bg_control_pressed="#2C3035",
        stroke_control="#41454D",
        stroke_divider="#2F3238",
        card_border="#2A2D33",
        text_primary=text,
        text_secondary="#A8ADB5",
        text_tertiary="#7B818A",
        text_disabled=mix(text, layer, 0.38),
        fill_disabled=mix(text, layer, 0.10),
        accent=accent,
        accent_hover=mix(on, accent, 0.10),
        accent_pressed=mix(on, accent, 0.20),
        accent_text_on=on,
        accent_surface=mix(accent, layer, 0.15),
        success="#54C287",
        success_surface=mix("#54C287", layer, 0.12),
        caution="#F2B84B",
        caution_surface=mix("#F2B84B", layer, 0.12),
        caution_fill="#F2B84B",
        caution_text_on="#2B1D00",
        critical="#F26D6A",
        critical_surface=mix("#F26D6A", layer, 0.10),
        estop="#C42B1C",
        estop_text="#FFFFFF",
        estop_hover="#D13A2C",
        estop_pressed="#A82316",
        neutral_surface=mix(text, layer, 0.08),
        seg_track="#15171A",
        seg_selected="#3A3E46",
        gauge_track="#383C43",
        gauge_band="#5B616B",
        plot_grid="#2A2D33",
        overlay_hover=rgba(text, 0.08),
        overlay_pressed=rgba(text, 0.12),
        shadow="#000000",
        shadow_alpha=115,
        motor=("#199E70", "#9085E9", "#E87BA4"),
    )


def _light() -> Tokens:
    layer = "#FAFAFB"
    text = "#1B1D21"
    accent = "#0F6CBD"
    on = "#FFFFFF"
    return Tokens(
        name="light",
        bg_base="#F3F4F6",
        bg_layer=layer,
        bg_card="#FFFFFF",
        bg_sunken="#FFFFFF",
        bg_control="#FFFFFF",
        bg_control_hover="#F4F5F7",
        bg_control_pressed="#ECEEF1",
        stroke_control="#D5D8DD",
        stroke_divider="#E4E6EA",
        card_border="#E4E6EA",
        text_primary=text,
        text_secondary="#5A606A",
        text_tertiary="#878C95",
        text_disabled=mix(text, layer, 0.38),
        fill_disabled=mix(text, layer, 0.08),
        accent=accent,
        accent_hover=mix(on, accent, 0.12),
        accent_pressed=mix("#000000", accent, 0.14),
        accent_text_on=on,
        accent_surface=mix(accent, layer, 0.12),
        success="#0E7A43",
        success_surface=mix("#0E7A43", layer, 0.10),
        caution="#8A5300",
        caution_surface="#FFF4CE",
        caution_fill="#F2B84B",
        caution_text_on="#2B1D00",
        critical="#C42B1C",
        critical_surface=mix("#C42B1C", layer, 0.10),
        estop="#C42B1C",
        estop_text="#FFFFFF",
        estop_hover="#D13A2C",
        estop_pressed="#A82316",
        neutral_surface=mix(text, layer, 0.07),
        seg_track="#E4E7EC",
        seg_selected="#FFFFFF",
        gauge_track="#DDE0E5",
        gauge_band="#B7BCC5",
        plot_grid="#ECEEF1",
        overlay_hover=rgba(text, 0.06),
        overlay_pressed=rgba(text, 0.10),
        shadow="#000000",
        shadow_alpha=56,
        motor=("#1BAF7A", "#4A3AA7", "#E87BA4"),
    )


DARK = _dark()
LIGHT = _light()
THEMES: dict[str, Tokens] = {"dark": DARK, "light": LIGHT}

# ui_design.md §7.1 の名前(ドット記法)→ 属性名
SPEC_NAMES: dict[str, str] = {
    "bg.base": "bg_base",
    "bg.layer": "bg_layer",
    "bg.card": "bg_card",
    "bg.sunken": "bg_sunken",
    "bg.control": "bg_control",
    "bg.control.hover": "bg_control_hover",
    "bg.control.pressed": "bg_control_pressed",
    "stroke.control": "stroke_control",
    "stroke.divider": "stroke_divider",
    "text.primary": "text_primary",
    "text.secondary": "text_secondary",
    "text.tertiary": "text_tertiary",
    "accent": "accent",
    "accent.text_on": "accent_text_on",
    "success": "success",
    "caution": "caution",
    "critical": "critical",
    "estop": "estop",
    "estop.text": "estop_text",
    "estop.hover": "estop_hover",
}


# ---------------------------------------------------------------- 文字 §7.3
UI_FONT_FAMILIES: tuple[str, ...] = ("Yu Gothic UI", "Meiryo UI", "Segoe UI")
MONO_FONT_FAMILIES: tuple[str, ...] = ("Cascadia Mono", "Consolas")

FS_CAPTION = 12  # 補足・最小/最大・ナビ文字
FS_BODY = 14  # 本文・ラベル・ボタン(見出しも 14 Semibold)
FS_INSPECTOR = 16  # インスペクタ見出しだけ
FS_TITLE = 20  # ページ見出し・空状態
FS_READOUT = 28  # 読み値
FONT_SIZES: tuple[int, ...] = (FS_CAPTION, FS_BODY, FS_INSPECTOR, FS_TITLE, FS_READOUT)

W_REGULAR = 400
W_SEMIBOLD = 600

# ---------------------------------------------------------------- 間隔 §7.4
SP_ICON = 4  # アイコンと文字
SP_RELATED = 8  # 関連要素の間・ラベルと入力
SP_GROUP = 12  # 区画内のまとまりの間
SP_PAD = 16  # 区画の内側余白
SP_SECTION = 24  # グループ間
SPACING: tuple[int, ...] = (SP_ICON, SP_RELATED, SP_GROUP, SP_PAD, SP_SECTION)

# ---------------------------------------------------------------- 高さ・幅
H_CONTROL = 32
H_ROW = 28  # 表の行
H_PRIMARY = 40  # 主ボタン
H_JOG = 48
H_ESTOP = 40
W_ESTOP_MIN = 140
H_HEADER = 56
H_STATUSBAR = 24
H_BADGE = 24
W_NAV = 72
W_INSPECTOR = 360  # 既定・最小幅(運転ページの区画境界をドラッグして広げられる)
W_INSPECTOR_MAX = 520
H_GAUGE = 6

ICON_S = 16
ICON_M = 20
ICON_XL = 32

# ---------------------------------------------------------------- 角丸
R_CONTROL = 4
R_CARD = 6
R_OVERLAY = 8  # ポップオーバー・ダイアログ・トースト
R_PILL = 12  # バッジ(高さ 24 の半分)
R_NONE = 0

FOCUS_RING_PX = 2
