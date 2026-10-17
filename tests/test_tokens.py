"""デザイントークン(ui_design.md §7): 全トークンがダーク/ライト両方にあり、コントラストを満たす。"""

from __future__ import annotations

import dataclasses
import re

import pytest

from edulite05.app.ui import tokens as tk
from edulite05.app.ui.tokens import DARK, LIGHT, SPEC_NAMES, THEMES, Tokens, contrast

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
COLOR = re.compile(r"^(#[0-9A-Fa-f]{6}|rgba\(\d+, \d+, \d+, \d+\)|transparent)$")


def test_dark_and_light_define_every_token() -> None:
    assert set(THEMES) == {"dark", "light"}
    fields = [f.name for f in dataclasses.fields(Tokens)]
    for t in (DARK, LIGHT):
        for name in fields:
            value = getattr(t, name)
            assert value not in (None, ""), (t.name, name)
            if name == "motor":
                assert len(value) == 3 and all(HEX.match(c) for c in value), (t.name, name)
            elif name in ("name", "shadow_alpha"):
                continue
            else:
                assert COLOR.match(str(value)), (t.name, name, value)
    assert DARK.name == "dark" and LIGHT.name == "light"


def test_spec_names_resolve_in_both_themes() -> None:
    """ui_design.md §7.1 の名前がすべてトークンとして存在する。"""
    assert len(SPEC_NAMES) >= 20
    for spec, attr in SPEC_NAMES.items():
        for t in (DARK, LIGHT):
            assert hasattr(t, attr), (spec, attr)


def test_spec_values_match_design_doc() -> None:
    assert (DARK.bg_base, DARK.bg_layer, DARK.bg_card, DARK.bg_sunken) == (
        "#1D1F23", "#23262B", "#2A2D33", "#17191C")
    assert (LIGHT.bg_base, LIGHT.bg_layer, LIGHT.bg_card, LIGHT.bg_sunken) == (
        "#F3F4F6", "#FAFAFB", "#FFFFFF", "#FFFFFF")
    assert (DARK.accent, DARK.accent_text_on, LIGHT.accent) == ("#479EF5", "#0B1A2B", "#0F6CBD")
    assert (DARK.success, DARK.caution, DARK.critical) == ("#54C287", "#F2B84B", "#F26D6A")
    assert (LIGHT.success, LIGHT.caution, LIGHT.critical) == ("#0E7A43", "#8A5300", "#C42B1C")
    assert (DARK.estop, DARK.estop_text, DARK.estop_hover) == ("#C42B1C", "#FFFFFF", "#D13A2C")
    assert LIGHT.caution_surface == "#FFF4CE"
    assert DARK.motor == ("#199E70", "#9085E9", "#E87BA4")
    assert LIGHT.motor == ("#1BAF7A", "#4A3AA7", "#E87BA4")
    assert DARK.motor_color(3) == DARK.motor_color(0)  # 4 台目以降は循環(順は固定)


def test_typography_spacing_and_sizes_follow_spec() -> None:
    assert tk.FONT_SIZES == (12, 14, 16, 20, 28)
    assert min(tk.FONT_SIZES) >= 12  # 12px 未満は使わない
    assert (tk.W_REGULAR, tk.W_SEMIBOLD) == (400, 600)
    assert tk.SPACING == (4, 8, 12, 16, 24)
    assert tk.SP_RELATED < tk.SP_PAD < tk.SP_SECTION  # グループ内 < グループ間
    assert (tk.H_CONTROL, tk.H_ROW, tk.H_PRIMARY, tk.H_JOG) == (32, 28, 40, 48)
    assert (tk.H_ESTOP, tk.W_ESTOP_MIN, tk.H_HEADER, tk.H_STATUSBAR) == (40, 140, 56, 24)
    assert (tk.R_CONTROL, tk.R_CARD, tk.R_OVERLAY) == (4, 6, 8)
    assert tk.W_NAV == 72 and tk.W_INSPECTOR == 360


@pytest.mark.parametrize("t", [DARK, LIGHT], ids=lambda t: t.name)
def test_text_contrast_on_surfaces(t: Tokens) -> None:
    """本文 4.5:1(WCAG)。三次文字は最小/最大などの補助用なので 3:1。"""
    for bg in (t.bg_base, t.bg_layer, t.bg_card, t.bg_sunken, t.bg_control):
        assert contrast(t.text_primary, bg) >= 4.5, bg
        assert contrast(t.text_secondary, bg) >= 4.5, bg
    for bg in (t.bg_layer, t.bg_card, t.bg_sunken):
        assert contrast(t.text_tertiary, bg) >= 3.0, bg


@pytest.mark.parametrize("t", [DARK, LIGHT], ids=lambda t: t.name)
def test_state_colors_contrast(t: Tokens) -> None:
    for bg in (t.bg_layer, t.bg_card):
        for fg in (t.accent, t.success, t.caution, t.critical):
            assert contrast(fg, bg) >= 4.5, (fg, bg)
    # バッジ: 状態色の文字は自分の薄い面の上で 4.5:1
    assert contrast(t.success, t.success_surface) >= 4.5
    assert contrast(t.caution, t.caution_surface) >= 4.5
    assert contrast(t.critical, t.critical_surface) >= 4.5
    assert contrast(t.text_secondary, t.neutral_surface) >= 4.5
    # 塗りのボタン上の文字
    assert contrast(t.accent_text_on, t.accent) >= 4.5
    assert contrast(t.estop_text, t.estop) >= 4.5
    assert contrast(t.caution_text_on, t.caution_fill) >= 4.5
    # アクセント(フォーカスリング・選択)は面に対して 3:1
    for bg in (t.bg_base, t.bg_layer, t.bg_card):
        assert contrast(t.accent, bg) >= 3.0


def test_dark_motor_colors_visible_on_plot_surface() -> None:
    """ダークのモータ色はグラフ面に対して 3:1 以上(ui_design.md §7.2)。"""
    for c in DARK.motor:
        assert contrast(c, DARK.bg_sunken) >= 3.0, c


def test_mix_and_contrast_helpers() -> None:
    assert tk.mix("#FFFFFF", "#000000", 0.5) == "#808080"
    assert tk.mix("#123456", "#ABCDEF", 1.0) == "#123456"
    assert contrast("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert tk.rgba("#FF8000", 0.5) == "rgba(255, 128, 0, 128)"
