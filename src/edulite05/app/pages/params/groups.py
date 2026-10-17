"""パラメータの分類・表示名・値の検証(Qt に依存しない純粋なロジック)。

分類は ui_design.md §5.2 のグループ(制御ゲイン / 制限 / 指令 / 状態 / 設定)。
読み取り専用(R)は index にかかわらず「状態」。表示名は ja.toml の
`param_page.name.<index 16 進>` に置き、無ければ ParamDef.description を使う。
index と名称の対応は el05_protocol.md §5.2。
"""

from __future__ import annotations

import math

from edulite05.app.i18n import catalog, tr
from edulite05.app.viewmodels import ParamDef
from edulite05.core.control.service import MOTION_PARAM_KEYS

GROUPS: tuple[str, ...] = ("gains", "limits", "command", "status", "settings")

_GROUP_BY_INDEX: dict[int, str] = {
    # 制御ゲイン
    0x7010: "gains", 0x7011: "gains", 0x7014: "gains", 0x701E: "gains",
    0x701F: "gains", 0x7020: "gains", 0x7021: "gains",
    # 制限(出力・速度・加速度の上限)
    0x700B: "limits", 0x7017: "limits", 0x7018: "limits", 0x7022: "limits",
    0x7024: "limits", 0x7025: "limits", 0x702E: "limits",
    # 指令(動かす目標。一時的な値)
    0x7005: "command", 0x7006: "command", 0x700A: "command", 0x7016: "command",
}

# 既定値に戻す・ファイル取り込みの対象外(指令は一時的な値で、取り込むと意図せず動くため)
TRANSIENT_GROUPS: frozenset[str] = frozenset({"command", "status"})


def group_of(d: ParamDef) -> str:
    """読み取り専用は「状態」。それ以外は表にあれば該当グループ、無ければ「設定」。"""
    if "W" not in d.rw:
        return "status"
    return _GROUP_BY_INDEX.get(d.index, "settings")


def group_title(group: str) -> str:
    return tr(f"param_page.group.{group}")


def is_writable(d: ParamDef) -> bool:
    return "W" in d.rw


def is_motion_locked(d: ParamDef) -> bool:
    """運転系(MotorService が書込を拒否するキー)。表では値だけ見せ、編集欄は出さない。
    設定は運転画面・出力段階(SafetyGuard)で行う。"""
    return d.name in MOTION_PARAM_KEYS


def title_of(d: ParamDef) -> str:
    """日本語の表示名。カタログに無ければ説明文。"""
    return catalog().get(f"param_page.name.{d.index:04X}", d.description)


def key_text(d: ParamDef) -> str:
    """名称セルの 2 行目: `spd_kp  0x701F`。"""
    return f"{d.name}  0x{d.index:04X}"


def is_integer(d: ParamDef) -> bool:
    return d.dtype != "float"


def fmt_param(v: float | int | None, d: ParamDef | None = None) -> str:
    """表の値の書式。整数型は整数、浮動小数は有効数字 6 桁(末尾の 0 は落とす)。"""
    if v is None:
        return "—"
    if d is not None and is_integer(d):
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    return f"{v:.6g}"


def decimals_of(d: ParamDef) -> int:
    """表示の小数桁数(パラメータごとに固定)。既定値/範囲の大きさから有効数字 3 桁ぶん。

    整数型は 0。浮動小数は 2〜4 桁(読み取り専用は計測値なので最低 3 桁)。
    """
    if is_integer(d):
        return 0
    cands = [abs(x) for x in (d.default, d.maximum, d.minimum) if x]
    ref = d.default if d.default else (max(cands) if cands else 1.0)
    mag = math.floor(math.log10(abs(ref))) if ref else 0
    lo = 2 if is_writable(d) else 3
    return max(lo, min(4, 2 - mag))


def fmt_cell(v: float | int | None, d: ParamDef) -> str:
    """表のセルの値(パラメータごとに小数桁数をそろえる)。"""
    if v is None:
        return "—"
    return f"{float(v):.{decimals_of(d)}f}"


def range_cell(d: ParamDef) -> str:
    """範囲列の表示。長い説明つきの定義は「最小 – 最大」に縮める(全文はツールチップ)。"""
    text = range_text(d)
    if len(text) <= 14:
        return text
    if d.minimum is not None and d.maximum is not None:
        return f"{fmt_param(d.minimum, d)} – {fmt_param(d.maximum, d)}"
    if d.minimum is not None:
        return tr("param_page.range_min", lo=fmt_param(d.minimum, d))
    if d.maximum is not None:
        return tr("param_page.range_max", hi=fmt_param(d.maximum, d))
    return text


def range_text(d: ParamDef) -> str:
    """範囲欄の表示。定義の range_text を優先し、無ければ最小/最大から作る。"""
    if d.range_text:
        return d.range_text.replace(" ~ ", " – ")
    if d.minimum is not None and d.maximum is not None:
        return f"{fmt_param(d.minimum, d)} – {fmt_param(d.maximum, d)}"
    if d.minimum is not None:
        return tr("param_page.range_min", lo=fmt_param(d.minimum, d))
    if d.maximum is not None:
        return tr("param_page.range_max", hi=fmt_param(d.maximum, d))
    return "—"


def check_value(d: ParamDef, text: str) -> tuple[float | None, str]:
    """入力文字列を検証する。(値, 問題の理由)。空文字は (None, "")(= 編集なし)。"""
    text = text.strip()
    if not text:
        return None, ""
    try:
        v = float(text.replace(",", ""))
    except ValueError:
        return None, tr("form.not_a_number")
    if math.isnan(v) or math.isinf(v):
        return None, tr("form.not_a_number")
    if is_integer(d) and v != int(v):
        return None, tr("param_page.need_integer")
    lo, hi = d.minimum, d.maximum
    if lo is not None and hi is not None and not (lo <= v <= hi):
        return None, tr("form.out_of_range", lo=fmt_param(lo, d), hi=fmt_param(hi, d))
    if lo is not None and v < lo:
        return None, tr("param_page.too_small", lo=fmt_param(lo, d))
    if hi is not None and v > hi:
        return None, tr("param_page.too_large", hi=fmt_param(hi, d))
    return v, ""


def same_value(a: float | int | None, b: float | int | None) -> bool:
    """モータの値と新しい値が同じか(読出は 6 桁に丸められているため相対誤差で比較)。"""
    if a is None or b is None:
        return False
    return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-9)
