"""グラフ(ui_design.md §5.1 モニタ)。1 グラフ 1 物理量を縦に積む。

- 色 = モータ(`tokens().motor_color`)、実線 = 実測、破線 = 指令
- 表示プリセット(標準 / MIT 解析 / 温度・電圧 / すべて)。チェックボックスは使わない
- 凡例は下の 1 行(`── 実測  - - 指令  ■ID1 ■ID2`)。モータの■をクリックで表示/非表示
- 各グラフ右端に最新値タグ(モータ色の四角 + ID + 値)。左上に量と単位
- 格子は横線のみ・ごく薄く。一時停止中はマウス位置の縦カーソルを全グラフで同期表示し、各値を出す
- 一時停止中にクリックで 2 点(A・B)を選ぶと Δt・Δ値・傾きを出す(右クリックで解除)
- 表示プリセットに FFT(選んだ 1 チャネルの Hann 窓スペクトル)
- トリガ(単発): 条件が成立したら前後 N 秒を残して止める。ステップ応答の評価・状態の帯は自動
  (計算は `analysis.py`、帯と評価の行は `timeline_band.py`、設定は `analysis_popover.py`)

性能(v1 スパイクの結果。tech_stack.md): pen 幅は 1(2 以上は非コスメティック扱いで遅い)、
`clipToView` + 自動ダウンサンプル(peak)、X 軸は「現在 = 0」の相対時間で固定、Y 軸の
オートスケールは自前(ヒステリシス付き)、`setYRange` には Python の float を渡す(numpy の
float32 は pyqtgraph 内部で overflow 警告になる)、更新タイマは PreciseTimer。
"""

from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.motor_colors import fallback_index
from edulite05.app.pages.operate import analysis as an
from edulite05.app.pages.operate.timeline_band import LANES, StepSummary, TimelineBand
from edulite05.app.ui.button import Button
from edulite05.app.ui.colors import qcolor
from edulite05.app.ui.label import Label
from edulite05.app.ui.readout import MINUS
from edulite05.app.ui.style import make_font, theme_manager, tokens
from edulite05.app.ui.tokens import (
    FS_CAPTION,
    R_CARD,
    R_CONTROL,
    SP_ICON,
    SP_PAD,
    SP_RELATED,
)
from edulite05.app.viewmodels import RUN_MODE_KEYS, ChannelInfo, MotorViewState

KINDS = ("pos", "vel", "torque", "temp", "vbus", "current", "power")
PRESETS: dict[str, tuple[str, ...]] = {
    "standard": ("pos", "vel", "torque"),
    "mit": ("pos", "torque"),
    "env": ("temp", "vbus"),
    "all": KINDS,
}
PRESET_ORDER = ("standard", "mit", "env", "all")
FFT_KEY = "fft"  # 表示プリセットの末尾に出す FFT 表示(選んだ 1 チャネルのスペクトル)
VIEW_ORDER = (*PRESET_ORDER, FFT_KEY)
WINDOWS_S = (2, 5, 10, 30, 60, 120)  # 120 s は Bridge のリングバッファ(BUFFER_S)の長さ
DEFAULT_WINDOW_S = 10
MIN_SPAN = {
    "pos": 0.5, "vel": 0.5, "torque": 0.4, "temp": 2.0, "vbus": 0.5, "current": 0.2, "power": 2.0,
    "fft": 0.02,
}
DECIMALS = {
    "pos": 2, "vel": 2, "torque": 3, "temp": 1, "vbus": 2, "current": 3, "power": 1, "fft": 3,
}
TIMELINE_KEEP_S = 150.0  # 状態の帯に溜める長さ(Bridge のリングバッファ 120 s より長く)
MARK_LABELS = ("A", "B")
REFRESH_MS = 25
TAG_W = 100  # 右端の最新値タグの幅
AXIS_W = 46  # 左の目盛の幅
_SQ = 8  # タグ・凡例の四角


def fmt_value(kind: str, v: float) -> str:
    dec = DECIMALS[kind]
    text = f"{abs(v):.{dec}f}"
    if kind in ("temp", "vbus"):
        return text
    return (MINUS if v < 0 and round(v, dec) != 0 else "+") + text


def axis_label(kind: str) -> str:
    return tr("operate.graph.axis_vbus") if kind == "vbus" else tr(f"scope.axis.{kind}")


def unit_of(kind: str) -> str:
    """軸ラベル「位置 [rad]」の単位部分(「rad」)。"""
    text = axis_label(kind)
    return text[text.rfind("[") + 1:text.rfind("]")] if "[" in text else ""


def _signed(v: float, dec: int) -> str:
    text = f"{abs(v):.{dec}f}"
    return (MINUS if v < 0 and round(v, dec) != 0 else "+") + text


# ======================================================================== 凡例
class _MotorToggle(QAbstractButton):
    """凡例のモータ■。クリックで表示/非表示(非表示は中抜き + 文字を薄く)。"""

    def __init__(
        self, motor_id: int, color_index: int | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.motor_id = motor_id
        self._color_index = fallback_index(motor_id) if color_index is None else color_index
        self.setCheckable(True)
        self.setChecked(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setToolTip(tr("operate.graph.legend_tip", id=motor_id))
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        fm = QFontMetrics(make_font(FS_CAPTION, semibold=True))
        w = SP_RELATED + _SQ + SP_RELATED + fm.horizontalAdvance(self._text()) + SP_RELATED
        self.setFixedSize(w, 24)

    def _text(self) -> str:
        return tr("ui.motor_id", id=self.motor_id)

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(t.overlay_hover))
            p.drawRoundedRect(r, R_CONTROL, R_CONTROL)
        if self.hasFocus():
            p.setPen(QPen(QColor(t.accent), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), R_CONTROL, R_CONTROL)
        col = QColor(t.motor_color(self._color_index))
        sq = QRectF(SP_RELATED, r.center().y() - _SQ / 2, _SQ, _SQ)
        on = self.isChecked()
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
        else:
            p.setPen(QPen(col, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(sq, 2, 2)
        p.setFont(make_font(FS_CAPTION, semibold=True))
        p.setPen(QColor(t.text_primary if on else t.text_tertiary))
        p.drawText(
            QRectF(sq.right() + SP_RELATED, 0, r.width(), r.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._text(),
        )
        p.end()


class _LineKey(QWidget):
    """`── 実測  - - 指令` の線種の見本(色はモータごとに違うので無彩色)。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        f = make_font(FS_CAPTION)
        fm = QFontMetrics(f)
        self._a = tr("operate.graph.actual")
        self._c = tr("operate.graph.command")
        self._seg = 22
        w = (
            2 * self._seg + 2 * SP_ICON + fm.horizontalAdvance(self._a)
            + fm.horizontalAdvance(self._c) + SP_PAD
        )
        self.setFixedSize(w, 24)

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = make_font(FS_CAPTION)
        p.setFont(f)
        fm = QFontMetrics(f)
        cy = self.height() / 2
        x = 0.0
        pen = QPen(QColor(t.text_secondary), 1.5)
        p.setPen(pen)
        p.drawLine(QPointF(x, cy), QPointF(x + self._seg, cy))
        x += self._seg + SP_ICON
        p.drawText(QRectF(x, 0, 200, self.height()), Qt.AlignmentFlag.AlignVCenter, self._a)
        x += fm.horizontalAdvance(self._a) + SP_PAD
        pen2 = QPen(QColor(t.text_secondary), 1.5)
        pen2.setDashPattern([3, 2.5])
        p.setPen(pen2)
        p.drawLine(QPointF(x, cy), QPointF(x + self._seg, cy))
        x += self._seg + SP_ICON
        p.setPen(QColor(t.text_secondary))
        p.drawText(QRectF(x, 0, 200, self.height()), Qt.AlignmentFlag.AlignVCenter, self._c)
        p.end()


class LegendRow(QWidget):
    """グラフ下の凡例 1 行。モータの■をクリックで表示/非表示。"""

    motor_toggled = Signal(int, bool)  # motor_id, visible

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(32)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP_PAD, 0, SP_RELATED, 0)
        lay.setSpacing(SP_ICON)
        lay.addWidget(_LineKey())
        lay.addSpacing(SP_RELATED)
        self._toggles_lay = QHBoxLayout()
        self._toggles_lay.setContentsMargins(0, 0, 0, 0)
        self._toggles_lay.setSpacing(SP_ICON)
        lay.addLayout(self._toggles_lay)
        lay.addStretch(1)
        self.status = Label("", "caption")
        lay.addWidget(self.status)
        # 「トリガ・解析」(ポップオーバーを開く)。アイコンだけ、文言はツールチップ
        self.tools_btn = Button(
            variant="subtle", icon="activity", tooltip=tr("operate.analysis.button_tip")
        )
        self.tools_btn.setAccessibleName(tr("operate.analysis.button"))
        lay.addWidget(self.tools_btn)
        self._toggles: dict[int, _MotorToggle] = {}

    def set_motors(
        self, ids: list[int], hidden: set[int], color_of: Callable[[int], int] | None = None
    ) -> None:
        for tg in self._toggles.values():
            self._toggles_lay.removeWidget(tg)
            tg.deleteLater()
        self._toggles.clear()
        for mid in ids:
            tg = _MotorToggle(mid, color_of(mid) if color_of is not None else None)
            tg.setChecked(mid not in hidden)
            tg.toggled.connect(lambda on, m=mid: self.motor_toggled.emit(m, on))
            self._toggles_lay.addWidget(tg)
            self._toggles[mid] = tg

    def toggle(self, motor_id: int) -> _MotorToggle:
        return self._toggles[motor_id]

    def set_status(self, text: str) -> None:
        self.status.setText(text)


# ======================================================================== オーバーレイ
class _Overlay(QWidget):
    """プロットの上に重ねる透明な層: 量の名前・最新値タグ・同期カーソル。"""

    def __init__(self, view: GraphView, parent: QWidget) -> None:
        super().__init__(parent)
        self._view = view
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        v = self._view
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f_mono = make_font(FS_CAPTION, mono=True)
        rects = {k: v.plot_rect(k) for k in v.kinds()}
        for kind, r in rects.items():
            if r.width() < 20 or r.height() < 10:
                continue
            # 量と単位(左上)
            p.setFont(make_font(FS_CAPTION, semibold=True))
            p.setPen(QColor(t.text_secondary))
            p.drawText(QPointF(r.left() + SP_RELATED, r.top() + 15), v.axis_text(kind))
            # 最新値タグ(右端)
            tags: list[tuple[float, int, str]] = []
            for mid, val in v.latest(kind).items():
                if mid in v.hidden_motors():
                    continue
                y = v.value_to_y(kind, val)
                if y is None:
                    continue
                y = min(max(y, r.top() + 8), r.bottom() - 8)
                tags.append((y, mid, fmt_value(kind, val)))
            tags.sort()
            last = -1e9
            placed: list[tuple[float, int, str]] = []
            for y, mid, text in tags:  # 重なったらずらす
                y = max(y, last + 16)
                last = y
                placed.append((y, mid, text))
            for y, mid, text in placed:
                x = r.right() + SP_RELATED
                col = QColor(v.motor_color(mid))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                p.drawRoundedRect(QRectF(x, y - _SQ / 2, _SQ, _SQ), 2, 2)
                p.setFont(f_mono)
                p.setPen(QColor(t.text_primary))
                label = f"{tr('ui.motor_id', id=mid)} {text}"
                p.drawText(
                    QRectF(x + _SQ + SP_ICON, y - 10, TAG_W, 20),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    label,
                )
        self._paint_trigger(p, rects)
        self._paint_step(p, rects)
        if v.fft_active():
            self._paint_fft(p, rects)
        elif v.delta_points() is not None or v.marks():
            self._paint_delta(p, rects)
        else:
            ct = v.cursor_time()
            if ct is not None and rects:
                self._paint_hover(p, rects, ct)
        p.end()

    # ---------------------------------------------------------------- 一時停止中のホバー値
    def _paint_hover(self, p: QPainter, rects: dict[str, QRectF], ct: float) -> None:
        v = self._view
        t = tokens()
        f_cap = make_font(FS_CAPTION)
        f_mono = make_font(FS_CAPTION, mono=True)
        fm_mono = QFontMetrics(f_mono)
        xs = v.time_to_x(ct)
        if xs is not None:
            first = min(rects.values(), key=lambda r: r.top())
            last_r = max(rects.values(), key=lambda r: r.bottom())
            p.setPen(QPen(QColor(t.text_secondary), 1))
            p.drawLine(QPointF(xs, first.top()), QPointF(xs, last_r.bottom()))
            vals = v.cursor_values()
            for kind, r in rects.items():
                rows = vals.get(kind, [])
                if not rows:
                    continue
                lines: list[tuple[str, str]] = []
                for mid, actual, cmd in rows:
                    s = f"{tr('ui.motor_id', id=mid)} {fmt_value(kind, actual)}"
                    if cmd is not None:
                        s += "  " + tr(
                            "operate.graph.cursor_command", value=fmt_value(kind, cmd)
                        )
                    lines.append((str(mid), s))
                    # 交点の丸
                    yy = v.value_to_y(kind, actual)
                    if yy is not None and r.top() <= yy <= r.bottom():
                        p.setPen(QPen(QColor(t.bg_sunken), 1.5))
                        p.setBrush(QColor(v.motor_color(mid)))
                        p.drawEllipse(QPointF(xs, yy), 4, 4)
                text_w = max(fm_mono.horizontalAdvance(s) for _m, s in lines)
                w = text_w + 2 * SP_RELATED + _SQ + SP_ICON
                h = len(lines) * 18 + 2 * SP_ICON
                bx = xs + SP_RELATED
                if bx + w > r.right():
                    bx = xs - SP_RELATED - w
                by = r.top() + 22
                box = QRectF(bx, by, w, h)
                p.setPen(QPen(QColor(t.stroke_control), 1))
                p.setBrush(QColor(t.bg_card))
                p.drawRoundedRect(box, R_CARD, R_CARD)
                p.setFont(f_mono)
                for i, (mid_s, s) in enumerate(lines):
                    ly = by + SP_ICON + i * 18
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(v.motor_color(int(mid_s))))
                    p.drawRoundedRect(QRectF(bx + SP_RELATED, ly + 5, _SQ, _SQ), 2, 2)
                    p.setPen(QColor(t.text_primary))
                    p.drawText(
                        QRectF(bx + SP_RELATED + _SQ + SP_ICON, ly, w, 18),
                        Qt.AlignmentFlag.AlignVCenter,
                        s,
                    )
            # 時刻
            p.setFont(f_cap)
            p.setPen(QColor(t.text_secondary))
            label = tr("operate.graph.cursor_time", t=f"{ct:+.2f}".replace("-", MINUS))
            tw = QFontMetrics(f_cap).horizontalAdvance(label)
            p.drawText(
                QPointF(min(max(xs - tw / 2, 0), self.width() - tw), first.top() - 2), label
            )

    # ---------------------------------------------------------------- Δ カーソル(A・B)
    def _paint_delta(self, p: QPainter, rects: dict[str, QRectF]) -> None:
        v = self._view
        t = tokens()
        if not rects:
            return
        f_cap = make_font(FS_CAPTION, semibold=True)
        f_mono = make_font(FS_CAPTION, mono=True)
        fm_mono = QFontMetrics(f_mono)
        first = min(rects.values(), key=lambda r: r.top())
        last_r = max(rects.values(), key=lambda r: r.bottom())
        marks = v.marks()
        pts = v.delta_points()
        xs: list[float] = []
        for i, mt in enumerate(marks):
            x = v.time_to_x(mt)
            if x is None:
                continue
            xs.append(x)
            p.setPen(QPen(QColor(t.text_primary), 1))
            p.drawLine(QPointF(x, first.top()), QPointF(x, last_r.bottom()))
            p.setFont(f_cap)
            p.setPen(QColor(t.text_primary))
            p.drawText(QPointF(x + SP_ICON, first.top() + 11), MARK_LABELS[i])
        if pts is None:
            return
        if len(marks) == 1:  # 仮の B(ホバー位置)は点線
            xb = v.time_to_x(pts[1])
            if xb is not None:
                pen = QPen(QColor(t.text_secondary), 1)
                pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawLine(QPointF(xb, first.top()), QPointF(xb, last_r.bottom()))
                xs.append(xb)
        if len(xs) == 2:  # A と B の間をうっすら塗る
            lo, hi = min(xs), max(xs)
            p.fillRect(
                QRectF(lo, first.top(), hi - lo, last_r.bottom() - first.top()),
                qcolor(t.overlay_hover),
            )
        # Δt(最上段の上)
        text = tr("operate.graph_delta.dt", value=_signed(pts[1] - pts[0], 3))
        p.setFont(f_cap)
        p.setPen(QColor(t.text_primary))
        tw = QFontMetrics(f_cap).horizontalAdvance(text)
        if len(xs) == 2:
            cx = (xs[0] + xs[1]) / 2
            p.drawText(QPointF(min(max(cx - tw / 2, 0), self.width() - tw), first.top() - 2), text)
        # 各段の Δ・傾き
        rows = v.delta_rows()
        for kind, r in rects.items():
            lines = rows.get(kind, [])
            if not lines:
                continue
            unit = unit_of(kind)
            items: list[tuple[int, str]] = []
            for mid, d in lines:
                slope = (
                    tr("operate.graph_delta.no_slope") if d.slope is None
                    else _signed(d.slope, DECIMALS[kind])
                )
                items.append((mid, tr(
                    "operate.graph_delta.row", id=mid, dv=_signed(d.dv, DECIMALS[kind]),
                    slope=slope, unit=unit,
                )))
            text_w = max(fm_mono.horizontalAdvance(s) for _m, s in items)
            w = text_w + 2 * SP_RELATED + _SQ + SP_ICON
            h = len(items) * 18 + 2 * SP_ICON
            bx = r.right() - w - SP_RELATED
            by = r.top() + SP_RELATED
            if len(xs) == 2 and bx < max(xs) + SP_ICON:  # A–B の区間にかぶるなら、よけて置く
                left_x = min(xs) - SP_RELATED - w
                if left_x >= r.left() + 110:  # 左上の量名を避ける
                    bx = left_x
                elif max(xs) + SP_RELATED + w <= r.right():
                    bx = max(xs) + SP_RELATED
            p.setPen(QPen(QColor(t.stroke_control), 1))
            p.setBrush(QColor(t.bg_card))
            p.drawRoundedRect(QRectF(bx, by, w, h), R_CARD, R_CARD)
            p.setFont(f_mono)
            for i, (mid, s) in enumerate(items):
                ly = by + SP_ICON + i * 18
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(v.motor_color(mid)))
                p.drawRoundedRect(QRectF(bx + SP_RELATED, ly + 5, _SQ, _SQ), 2, 2)
                p.setPen(QColor(t.text_primary))
                p.drawText(
                    QRectF(bx + SP_RELATED + _SQ + SP_ICON, ly, w, 18),
                    Qt.AlignmentFlag.AlignVCenter, s,
                )

    # ---------------------------------------------------------------- トリガの成立時刻
    def _paint_trigger(self, p: QPainter, rects: dict[str, QRectF]) -> None:
        v = self._view
        rel = v.trigger_rel()
        if rel is None or not rects or v.fft_active():
            return
        x = v.time_to_x(rel)
        if x is None:
            return
        t = tokens()
        first = min(rects.values(), key=lambda r: r.top())
        last_r = max(rects.values(), key=lambda r: r.bottom())
        if not (first.left() <= x <= first.right()):
            return
        pen = QPen(QColor(t.text_secondary), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(QPointF(x, first.top()), QPointF(x, last_r.bottom()))
        p.setFont(make_font(FS_CAPTION, semibold=True))
        p.setPen(QColor(t.text_primary))
        p.drawText(
            QPointF(x + SP_ICON, first.top() + 11), tr("operate.analysis.trigger_marker")
        )

    # ---------------------------------------------------------------- ステップ応答の目印
    def _paint_step(self, p: QPainter, rects: dict[str, QRectF]) -> None:
        v = self._view
        res = v.step_result()
        if res is None or v.fft_active():
            return
        kind, _mid, m = res
        r = rects.get(kind)
        if r is None or r.width() < 20:
            return
        t = tokens()
        band = an.SETTLE_BAND * abs(m.amplitude)
        x0, x1 = v.time_to_x(m.t0), v.time_to_x(0.0)
        y_hi, y_lo = v.value_to_y(kind, m.final + band), v.value_to_y(kind, m.final - band)
        y_f = v.value_to_y(kind, m.final)
        if x0 is None or x1 is None or y_hi is None or y_lo is None or y_f is None:
            return
        left = min(max(x0, r.left()), r.right())
        right = min(max(x1, r.left()), r.right())
        top = min(max(min(y_hi, y_lo), r.top()), r.bottom())
        bot = min(max(max(y_hi, y_lo), r.top()), r.bottom())
        p.fillRect(QRectF(left, top, right - left, max(bot - top, 1.0)), qcolor(t.overlay_hover))
        pen = QPen(QColor(t.text_tertiary), 1)
        pen.setStyle(Qt.PenStyle.DotLine)
        p.setPen(pen)
        if r.top() <= y_f <= r.bottom():
            p.drawLine(QPointF(left, y_f), QPointF(right, y_f))
        for tt in (m.t0, m.t_settle):
            if tt is None:
                continue
            x = v.time_to_x(tt)
            if x is not None and r.left() <= x <= r.right():
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))

    # ---------------------------------------------------------------- FFT のピーク
    def _paint_fft(self, p: QPainter, rects: dict[str, QRectF]) -> None:
        v = self._view
        sp = v.fft_spectrum()
        r = rects.get(FFT_KEY)
        t = tokens()
        if r is None or r.width() < 20:
            return
        p.setFont(make_font(FS_CAPTION))
        p.setPen(QColor(t.text_secondary))
        if sp is None:
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, tr("operate.analysis.fft_none"))
            return
        x = v.time_to_x(sp.peak_freq)
        if x is not None and r.left() <= x <= r.right():
            pen = QPen(QColor(t.text_tertiary), 1)
            pen.setStyle(Qt.PenStyle.DotLine)
            p.setPen(pen)
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        text = tr(
            "operate.analysis.fft_peak", freq=f"{sp.peak_freq:.2f}",
            amp=f"{sp.peak_amp:.{DECIMALS['fft']}f}", unit=v.fft_unit(),
        )
        f_mono = make_font(FS_CAPTION, mono=True)
        p.setFont(f_mono)
        p.setPen(QColor(t.text_primary))
        tw = QFontMetrics(f_mono).horizontalAdvance(text)
        p.drawText(QPointF(r.right() - tw - SP_RELATED, r.top() + 15), text)


# ======================================================================== 本体
class GraphView(QWidget):
    """縦に積んだ小さな複数グラフ + 凡例行。`refresh()` を内部タイマが呼ぶ(表示中だけ)。"""

    paused_changed = Signal(bool)
    trigger_changed = Signal(str)  # idle / armed / fired / captured
    channels_changed = Signal()  # モータ・チャネルの構成が変わった(FFT・トリガの選択肢の更新用)

    def __init__(self, bridge: Bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._preset = "standard"
        self._window_s = float(DEFAULT_WINDOW_S)
        self._paused = False
        self._hidden: set[int] = set()
        self._channels: list[ChannelInfo] = []
        self._motor_ids: list[int] = []
        self._plots: dict[str, pg.PlotItem] = {}
        self._curves: dict[tuple[str, int, bool], pg.PlotDataItem] = {}
        self._data: dict[tuple[str, int, bool], tuple[np.ndarray, np.ndarray]] = {}
        self._latest: dict[str, dict[int, float]] = {}
        self._yrange: dict[str, tuple[float, float]] = {}
        self._cursor_t: float | None = None
        self._timeline_on = True
        self._marks: list[float] = []  # Δ カーソルの A・B(一時停止中のクリック。相対秒)
        self._trigger = an.SingleShotTrigger()
        self._trigger_note = ""
        self._timelines: dict[int, dict[str, an.LaneTimeline]] = {}
        self._now_ref = time.perf_counter()  # データを取った時刻(横軸の「現在 = 0」)
        self._step: tuple[str, int, an.StepMetrics] | None = None
        self._fft_channel = ""
        self._spec: an.Spectrum | None = None
        self._fft_curve: pg.PlotDataItem | None = None
        self._fps_count = 0
        self._fps_t = time.perf_counter()
        self.fps = 0.0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.glw.ci.layout.setSpacing(2)
        self.glw.ci.layout.setContentsMargins(SP_RELATED, SP_PAD, SP_RELATED, SP_RELATED)
        self.glw.setFrameShape(self.glw.Shape.NoFrame)
        self.glw.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.glw.installEventFilter(self)
        self.glw.viewport().installEventFilter(self)
        self.glw.viewport().setMouseTracking(True)
        self._overlay = _Overlay(self, self.glw.viewport())
        lay.addWidget(self.glw, 1)
        self.band = TimelineBand(self)
        lay.addWidget(self.band)
        self.step_summary = StepSummary()
        lay.addWidget(self.step_summary)
        self.legend = LegendRow()
        self.legend.motor_toggled.connect(self.set_motor_visible)
        lay.addWidget(self.legend)

        theme_manager().changed.connect(self._on_theme)
        bridge.motors_changed.connect(self._on_motors)
        bridge.state_updated.connect(self._on_states)
        self._on_states(bridge.states())

        self._timer = QTimer(self)
        # Windows の粗いタイマ(分解能 15.6 ms)だと設定より遅くなるため precise
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.rebuild()

    # ------------------------------------------------------------ 公開 API
    def kinds(self) -> list[str]:
        return list(self._plots)

    def preset(self) -> str:
        return self._preset

    def window_s(self) -> float:
        return self._window_s

    def is_paused(self) -> bool:
        return self._paused

    def hidden_motors(self) -> set[int]:
        return set(self._hidden)

    def motor_ids(self) -> list[int]:
        return list(self._motor_ids)

    def latest(self, kind: str) -> dict[int, float]:
        return dict(self._latest.get(kind, {}))

    def cursor_time(self) -> float | None:
        return self._cursor_t if self._paused and not self.fft_active() else None

    def fft_active(self) -> bool:
        return self._preset == FFT_KEY

    # ---- 軸・座標の補助(帯とオーバーレイが使う) ----
    def axis_text(self, kind: str) -> str:
        if kind == FFT_KEY:
            return tr("operate.analysis.fft_axis", unit=self.fft_unit())
        return axis_label(kind)

    def now_ref(self) -> float:
        """直近にデータを取った時刻(`time.perf_counter()`)。横軸の「現在 = 0」に当たる。"""
        return self._now_ref

    def x_range(self) -> tuple[float, float]:
        p = next(iter(self._plots.values()), None)
        if p is None:
            return (-self._window_s, 0.0)
        lo, hi = p.vb.viewRange()[0]
        return (float(lo), float(hi))

    def first_plot_rect(self) -> QRectF:
        for kind in self._plots:
            return self.plot_rect(kind)
        return QRectF()

    # ---- 状態の帯 ----
    def timeline_motor(self) -> int | None:
        """帯・ステップ評価の対象(操作対象のモータ。無ければ先頭)。"""
        sel = self._bridge.selected_motor()
        if sel in self._motor_ids:
            return sel
        return self._motor_ids[0] if self._motor_ids else None

    def timeline_lanes(self, motor_id: int) -> dict[str, an.LaneTimeline]:
        return self._timelines.get(motor_id, {})

    def clamp_detail(self, motor_id: int | None) -> str:
        return self._bridge.clamp_note(motor_id) if motor_id is not None else ""

    def timeline_visible(self) -> bool:
        return self._timeline_on

    def set_timeline_visible(self, on: bool) -> None:
        self._timeline_on = bool(on)
        self._sync_aux()

    def _sync_aux(self) -> None:
        self.band.setVisible(self._timeline_on and not self.fft_active())
        # ステップ評価の行は常に出す(高さを確保。FFT 表示だけは意味が無いので隠す = 表示の切替)
        self.step_summary.setVisible(not self.fft_active())

    # ---- チャネルの選択肢(FFT・トリガ) ----
    def trigger_channels(self) -> list[tuple[str, str]]:
        """[(チャネル名, 表示名)]。実測値だけ(指令値は除く)。"""
        return [
            (c.name, f"{tr('ui.motor_id', id=c.motor_id)} {c.label}")
            for c in self._channels if not c.command
        ]

    def default_trigger_channel(self) -> str:
        mid = self.timeline_motor()
        if mid is not None:
            for c in self._channels:
                if c.motor_id == mid and c.kind == "vel" and not c.command:
                    return c.name
        chans = self.trigger_channels()
        return chans[0][0] if chans else ""

    def channel_info(self, name: str) -> ChannelInfo | None:
        return next((c for c in self._channels if c.name == name), None)

    def channel_unit(self, name: str) -> str:
        c = self.channel_info(name)
        return c.unit if c is not None else ""

    def fft_channels(self) -> list[tuple[str, str]]:
        return self.trigger_channels()

    def fft_channel(self) -> str:
        names = [n for n, _l in self.trigger_channels()]
        if self._fft_channel in names:
            return self._fft_channel
        return self.default_trigger_channel()

    def set_fft_channel(self, name: str) -> None:
        if name == self._fft_channel:
            return
        self._fft_channel = name
        self._spec = None
        if self.fft_active():
            self.rebuild()

    def fft_unit(self) -> str:
        return self.channel_unit(self.fft_channel())

    def fft_spectrum(self) -> an.Spectrum | None:
        return self._spec

    # ---- トリガ(単発) ----
    def trigger_state(self) -> str:
        return self._trigger.state

    def trigger_spec(self) -> an.TriggerSpec | None:
        return self._trigger.spec

    def trigger_rel(self) -> float | None:
        """トリガ成立の時刻(横軸の相対秒)。成立していなければ None。"""
        if self._trigger.state in ("fired", "captured") and self._trigger.t_trigger is not None:
            return self._trigger.t_trigger - self._now_ref
        return None

    def arm_trigger(self, spec: an.TriggerSpec) -> None:
        """待機を始める。一時停止中なら再開する。FFT 表示では標準表示に戻す。"""
        if self.fft_active():
            self.set_preset("standard")
        self.clear_marks()
        if self._paused:
            self.set_paused(False)
        self._trigger.arm(spec, time.perf_counter())
        self._trigger_note = tr(
            "operate.analysis.armed_status", **self._trigger_text(spec)
        )
        self.legend.set_status(self._trigger_note)
        self.trigger_changed.emit("armed")

    def disarm_trigger(self) -> None:
        was = self._trigger.state
        self._trigger.reset()
        self._trigger_note = ""
        if was == "captured":
            self._restore_view()
        self.legend.set_status(
            tr("operate.graph_delta.paused_hint") if self._paused else ""
        )
        if was != "idle":
            self.trigger_changed.emit("idle")

    def _trigger_text(self, spec: an.TriggerSpec) -> dict[str, str]:
        ch = self.channel_info(spec.channel)
        kind = ch.kind if ch is not None else "vel"
        label = (
            f"{tr('ui.motor_id', id=ch.motor_id)} {ch.label}" if ch is not None else spec.channel
        )
        return {
            "channel": label,
            "level": f"{spec.level:g}",
            "unit": ch.unit if ch is not None else unit_of(kind),
            "edge": tr(f"operate.analysis.edge_{'rising' if spec.rising else 'falling'}"),
            "s": f"{spec.span_s:g}",
        }

    def _capture_trigger(self) -> None:
        """成立から `span_s` 経った: 成立を中央に前後 `span_s` を見せて止める。"""
        spec = self._trigger.spec
        rel = self.trigger_rel()
        if spec is None or rel is None:
            return
        self._trigger.capture()
        view = (rel - spec.span_s, rel + spec.span_s)
        for pl in self._plots.values():
            pl.setXRange(view[0], view[1], padding=0)
        self.set_paused(True)
        self._trigger_note = tr("operate.analysis.fired_status", **self._trigger_text(spec))
        self.legend.set_status(self._trigger_note)
        self.trigger_changed.emit("captured")

    def _restore_view(self) -> None:
        for pl in self._plots.values():
            if not self.fft_active():
                pl.setXRange(-self._window_s, 0, padding=0)

    # ---- Δ カーソル ----
    def marks(self) -> list[float]:
        return list(self._marks) if self._paused else []

    def clear_marks(self) -> None:
        if self._marks:
            self._marks = []
            self._sync_paused_hint()
            self._overlay.update()

    def add_mark(self, t: float) -> None:
        """一時停止中に 1 点を足す(3 点目は A からやり直し)。"""
        if not self._paused or self.fft_active():
            return
        self._marks = [t] if len(self._marks) >= 2 else [*self._marks, t]
        self._sync_paused_hint()
        self._overlay.update()

    def delta_points(self) -> tuple[float, float] | None:
        """(A, B)。A だけのときは、ホバー位置を仮の B にする。"""
        m = self.marks()
        if len(m) >= 2:
            return (m[0], m[1])
        if len(m) == 1 and self._cursor_t is not None:
            return (m[0], self._cursor_t)
        return None

    def delta_rows(self) -> dict[str, list[tuple[int, an.DeltaResult]]]:
        """量ごとに、各モータの Δt・Δ値・傾き(実測)。A・B が決まっていなければ空。"""
        pts = self.delta_points()
        out: dict[str, list[tuple[int, an.DeltaResult]]] = {}
        if pts is None:
            return out
        a, b = pts
        for kind in self._plots:
            rows: list[tuple[int, an.DeltaResult]] = []
            for mid in self._motor_ids:
                if mid in self._hidden:
                    continue
                d = self._data.get((kind, mid, False))
                if d is None:
                    continue
                va, vb = an.sample_at(d[0], d[1], a), an.sample_at(d[0], d[1], b)
                if va is not None and vb is not None:
                    rows.append((mid, an.delta_between(a, va, b, vb)))
            if rows:
                out[kind] = rows
        return out

    def _sync_paused_hint(self) -> None:
        if not self._paused or self._trigger.state == "captured":
            return
        key = "paused_hint_one" if len(self._marks) == 1 else "paused_hint"
        self.legend.set_status(tr(f"operate.graph_delta.{key}"))

    # ---- ステップ応答 ----
    def step_result(self) -> tuple[str, int, an.StepMetrics] | None:
        return self._step

    def motor_color(self, motor_id: int) -> str:
        """モータの色(Bridge の割り当て。初登録順で固定。ui_design.md §7.2)。"""
        return tokens().motor_color(self._bridge.motor_color_index(motor_id))

    def set_preset(self, key: str) -> None:
        if (key in PRESETS or key == FFT_KEY) and key != self._preset:
            self._preset = key
            self.clear_marks()
            self.rebuild()

    def set_window(self, seconds: float) -> None:
        self._window_s = float(seconds)
        if not self.fft_active() and self._trigger.state != "captured":
            for p in self._plots.values():
                p.setXRange(-self._window_s, 0, padding=0)
        self.refresh(force=True)

    def set_paused(self, on: bool) -> None:
        if on == self._paused:
            return
        self._paused = on
        if not on:
            self._cursor_t = None
            self._marks = []
            self._restore_view()
            if self._trigger.state in ("fired", "captured"):  # 再開でトリガ成立の表示を解除
                self._trigger.reset()
                self._trigger_note = ""
                self.trigger_changed.emit("idle")
        if on:
            self._sync_paused_hint()
            self.legend.set_status(
                self._trigger_note or tr("operate.graph_delta.paused_hint")
            )
        else:
            self.legend.set_status(self._trigger_note)
        self.paused_changed.emit(on)
        self._overlay.update()

    def toggle_pause(self) -> None:
        self.set_paused(not self._paused)

    def set_motor_visible(self, motor_id: int, visible: bool) -> None:
        if visible:
            self._hidden.discard(motor_id)
        else:
            self._hidden.add(motor_id)
        tg = self.legend._toggles.get(motor_id)
        if tg is not None and tg.isChecked() != visible:
            tg.setChecked(visible)
        for (_k, mid, _c), curve in self._curves.items():
            if mid == motor_id:
                curve.setVisible(visible)
        self._yrange.clear()
        self.refresh(force=True)

    def cursor_values(self) -> dict[str, list[tuple[int, float, float | None]]]:
        """カーソル位置(一時停止中)の各量の値: kind → [(motor_id, 実測, 指令 or None)]。"""
        ct = self.cursor_time()
        out: dict[str, list[tuple[int, float, float | None]]] = {}
        if ct is None:
            return out
        for kind in self._plots:
            rows: list[tuple[int, float, float | None]] = []
            for mid in self._motor_ids:
                if mid in self._hidden:
                    continue
                a = self._sample_at(self._data.get((kind, mid, False)), ct)
                if a is None:
                    continue
                c = self._sample_at(self._data.get((kind, mid, True)), ct)
                rows.append((mid, a, c))
            if rows:
                out[kind] = rows
        return out

    def clear(self) -> None:
        self._data.clear()
        self._latest.clear()
        self._marks = []
        self._step = None
        self._spec = None
        self._timelines.clear()
        self.step_summary.set_metrics(None)
        for c in self._curves.values():
            c.setData([], [])
        self._overlay.update()

    # ------------------------------------------------------------ 座標
    def plot_rect(self, kind: str) -> QRectF:
        p = self._plots.get(kind)
        if p is None:
            return QRectF()
        poly = self.glw.mapFromScene(p.vb.sceneBoundingRect())
        return QRectF(poly.boundingRect())

    def value_to_y(self, kind: str, value: float) -> float | None:
        p = self._plots.get(kind)
        if p is None:
            return None
        sp = p.vb.mapViewToScene(QPointF(0.0, float(value)))
        return float(self.glw.mapFromScene(sp).y())

    def time_to_x(self, t: float) -> float | None:
        if not self._plots:
            return None
        p = next(iter(self._plots.values()))
        sp = p.vb.mapViewToScene(QPointF(float(t), 0.0))
        return float(self.glw.mapFromScene(sp).x())

    def x_to_time(self, x: float) -> float | None:
        if not self._plots:
            return None
        p = next(iter(self._plots.values()))
        sp = self.glw.mapToScene(int(x), 0)
        return float(p.vb.mapSceneToView(sp).x())

    # ------------------------------------------------------------ 構築
    def _on_motors(self, _motors: list) -> None:
        self.rebuild()
        for mid in [m for m in self._timelines if m not in self._motor_ids]:
            del self._timelines[mid]

    def _on_states(self, states: dict[int, MotorViewState]) -> None:
        """状態の帯に ui_state / モード / 制限の変化を溜める(変化したときだけ区間を切る)。"""
        if not states:
            self._timelines.clear()
            return
        now = time.perf_counter()
        for mid, st in states.items():
            lanes = self._timelines.get(mid)
            if lanes is None:
                lanes = self._timelines[mid] = {
                    n: an.LaneTimeline(TIMELINE_KEEP_S) for n in LANES
                }
            lanes["state"].push(now, st.ui_state)
            active = st.ui_state in ("enabled", "running")
            mode: str | None = None
            if active:
                mode = (
                    st.controller if st.controller and st.ui_state == "running"
                    else RUN_MODE_KEYS.get(st.run_mode)
                )
            lanes["mode"].push(now, mode)
            clamped = st.ui_state == "running" and bool(self._bridge.clamp_note(mid))
            lanes["clamp"].push(now, "clamp" if clamped else None)

    def _on_theme(self, _name: str = "") -> None:
        self.apply_theme()

    def rebuild(self) -> None:
        self._channels = self._bridge.available_channels()
        self._motor_ids = sorted({c.motor_id for c in self._channels})
        self._hidden &= set(self._motor_ids)
        self.legend.set_motors(self._motor_ids, self._hidden, self._bridge.motor_color_index)
        self.glw.clear()
        self._plots.clear()
        self._curves.clear()
        self._yrange.clear()
        self._fft_curve = None
        kinds = [FFT_KEY] if self.fft_active() else list(PRESETS[self._preset])
        first: pg.PlotItem | None = None
        for row, kind in enumerate(kinds):
            p = self.glw.addPlot(row=row, col=0)
            p.setMenuEnabled(False)
            p.hideButtons()
            p.setMouseEnabled(x=False, y=False)
            p.enableAutoRange(False)
            if kind == FFT_KEY:
                p.setXRange(0.0, 100.0, padding=0)
                p.setYRange(0.0, 1.0, padding=0)
            else:
                p.setXRange(-self._window_s, 0, padding=0)
                p.setYRange(-1.0, 1.0, padding=0)
            left = p.getAxis("left")
            left.setWidth(AXIS_W)
            left.enableAutoSIPrefix(False)
            left.setStyle(tickLength=0, autoExpandTextSpace=False)
            p.showGrid(x=False, y=True, alpha=1.0)
            p.showAxis("right")
            right = p.getAxis("right")
            right.setStyle(showValues=False, tickLength=0)
            right.setWidth(TAG_W)
            right.setPen(pg.mkPen(None))
            if first is None:
                first = p
            else:
                p.setXLink(first)
            if row < len(kinds) - 1:
                p.hideAxis("bottom")
            else:
                bottom = p.getAxis("bottom")
                bottom.setHeight(40)
                bottom.setStyle(tickLength=0)
                p.setLabel(
                    "bottom",
                    tr("operate.analysis.fft_freq" if kind == FFT_KEY else "scope.axis.time"),
                )
            self._plots[kind] = p
            if kind == FFT_KEY:
                self._fft_curve = p.plot([], [], connect="all")
                continue
            for mid in self._motor_ids:
                for is_cmd in (False, True):
                    if is_cmd and kind not in ("pos", "vel", "torque"):
                        continue
                    curve = p.plot([], [], connect="finite")
                    curve.setClipToView(True)
                    curve.setDownsampling(auto=True, method="peak")
                    curve.setVisible(mid not in self._hidden)
                    self._curves[(kind, mid, is_cmd)] = curve
        self.apply_theme()
        self._sync_aux()
        self.channels_changed.emit()
        self.refresh(force=True)

    def apply_theme(self) -> None:
        t = tokens()
        self.glw.setBackground(t.bg_sunken)
        tick_font = make_font(FS_CAPTION, mono=True)
        for p in self._plots.values():
            for ax_name in ("left", "bottom"):
                ax = p.getAxis(ax_name)
                ax.setPen(pg.mkPen(t.plot_grid))
                ax.setTextPen(pg.mkPen(t.text_secondary))
                ax.setTickFont(tick_font)
            p.getAxis("bottom").label.setFont(make_font(FS_CAPTION))
            p.getAxis("bottom").setTextPen(pg.mkPen(t.text_secondary))
            p.showGrid(x=False, y=True, alpha=1.0)
            p.vb.setBackgroundColor(None)
        for (_kind, mid, is_cmd), curve in self._curves.items():
            color = QColor(self.motor_color(mid))
            pen = QPen(color, 1)
            pen.setCosmetic(True)
            if is_cmd:
                pen.setDashPattern([4, 3])
            curve.setPen(pen)
        if self._fft_curve is not None:
            ch = self.channel_info(self.fft_channel())
            fcol = self.motor_color(ch.motor_id) if ch else t.text_secondary
            fpen = QPen(QColor(fcol), 1)
            fpen.setCosmetic(True)
            self._fft_curve.setPen(fpen)
        self.band.update()
        self._overlay.update()

    # ------------------------------------------------------------ 更新
    def refresh(self, force: bool = False) -> None:
        if not force and (self._paused or not self.isVisible()):
            return
        if not self._plots:
            return
        now = time.perf_counter()
        names: list[str] = []
        key_of: dict[str, tuple[str, int, bool]] = {}
        if self.fft_active():
            ch = self.channel_info(self.fft_channel())
            if ch is not None:
                names.append(ch.name)
                key_of[ch.name] = (ch.kind, ch.motor_id, False)
        else:
            for c in self._channels:
                key = (c.kind, c.motor_id, c.command)
                if c.kind in self._plots and key in self._curves:
                    names.append(c.name)
                    key_of[c.name] = key
        trig = self._trigger
        fetch_s = self._window_s
        trig_ch: str | None = None
        if trig.state in ("armed", "fired") and trig.spec is not None:
            fetch_s = max(fetch_s, 2 * trig.spec.span_s + 0.5)  # 前後を残せる長さ
            if trig.state == "armed":
                trig_ch = trig.spec.channel
                if trig_ch not in names:
                    names.append(trig_ch)  # 表示していない量でもトリガの条件には使う
        data = self._bridge.scope_data(names, fetch_s) if names else {}
        fresh = not self._paused or force
        if fresh:
            self._now_ref = now
            for name, (t, y) in data.items():
                key = key_of.get(name)
                if key is not None:
                    self._data[key] = (t, y)
            if trig_ch is not None and trig_ch in data and trig.feed(now, *data[trig_ch]):
                if trig.spec is not None:
                    self._trigger_note = tr(
                        "operate.analysis.fired_status", **self._trigger_text(trig.spec)
                    )
                    self.legend.set_status(self._trigger_note)
                self.trigger_changed.emit("fired")
        if self.fft_active():
            if fresh:
                self._update_fft()
        else:
            per_kind: dict[str, list[np.ndarray]] = {k: [] for k in self._plots}
            self._latest = {k: {} for k in self._plots}
            for (kind, mid, is_cmd), curve in self._curves.items():
                d = self._data.get((kind, mid, is_cmd))
                if d is None:
                    continue
                t, y = d
                curve.setData(t, y)
                if mid in self._hidden:
                    continue
                per_kind[kind].append(y)
                if not is_cmd:
                    last = self._last_finite(y)
                    if last is not None:
                        self._latest[kind][mid] = last
            for kind, arrays in per_kind.items():
                self._fit_y(kind, arrays)
            if fresh:
                self._update_step()
        self._overlay.update()
        if self.band.isVisible():
            self.band.update()
        self._fps_count += 1
        if now - self._fps_t >= 1.0:
            self.fps = self._fps_count / (now - self._fps_t)
            self._fps_count = 0
            self._fps_t = now
        if trig.state == "fired" and trig.due(now):
            self._capture_trigger()

    def _update_fft(self) -> None:
        ch = self.channel_info(self.fft_channel())
        d = self._data.get((ch.kind, ch.motor_id, False)) if ch is not None else None
        self._spec = an.spectrum(d[0], d[1]) if d is not None else None
        curve, pl = self._fft_curve, self._plots.get(FFT_KEY)
        if curve is None or pl is None:
            return
        sp = self._spec
        if sp is None:
            curve.setData([], [])
            return
        curve.setData(sp.freq, sp.amp)
        pl.setXRange(0.0, float(sp.freq[-1]), padding=0)
        top = max(float(np.max(sp.amp[1:])) * 1.15, MIN_SPAN["fft"])
        cur = self._yrange.get(FFT_KEY)
        if cur is None or top > cur[1] or cur[1] > 2.6 * top:  # ヒステリシス(他の量と同じ)
            self._yrange[FFT_KEY] = (0.0, top)
            pl.setYRange(0.0, top, padding=0)

    def _update_step(self) -> None:
        """操作対象の実測・指令からステップ応答を探して評価する(StepTest 実行中・後)。"""
        mid = self.timeline_motor()
        best: tuple[str, int, an.StepMetrics] | None = None
        if mid is not None and mid not in self._hidden:
            for kind in ("vel", "pos", "torque"):
                a = self._data.get((kind, mid, False))
                c = self._data.get((kind, mid, True))
                if a is None or c is None or a[0].size < 8 or c[0].size < 3:
                    continue
                m = an.step_metrics(a[0], a[1], c[0], c[1])
                if m is not None and (best is None or m.t0 > best[2].t0):
                    best = (kind, mid, m)
        self._step = best
        if best is None or mid is None:
            self.step_summary.set_metrics(None)
        else:
            self.step_summary.set_metrics(best[2], mid, tr(f"channel.{best[0]}"))

    @staticmethod
    def _last_finite(y: np.ndarray) -> float | None:
        if y.size == 0:
            return None
        idx = np.flatnonzero(np.isfinite(y[-256:]))
        if idx.size == 0:
            idx = np.flatnonzero(np.isfinite(y))
            return float(y[idx[-1]]) if idx.size else None
        return float(y[-256:][idx[-1]])

    def _fit_y(self, kind: str, arrays: list[np.ndarray]) -> None:
        lo = hi = None
        for y in arrays:
            if y.size == 0:
                continue
            with np.errstate(all="ignore"):
                a, b = np.fmin.reduce(y), np.fmax.reduce(y)
            if np.isfinite(a) and np.isfinite(b):
                a, b = float(a), float(b)  # np.float32 のまま渡すと pyqtgraph 内で overflow 警告
                lo = a if lo is None else min(lo, a)
                hi = b if hi is None else max(hi, b)
        if lo is None or hi is None:
            return
        span = max(hi - lo, MIN_SPAN[kind])
        pad = span * 0.12
        want = (float(lo - pad), float(hi + pad))
        if hi - lo < MIN_SPAN[kind]:
            mid = (hi + lo) / 2
            want = (float(mid - MIN_SPAN[kind] * 0.6), float(mid + MIN_SPAN[kind] * 0.6))
        cur = self._yrange.get(kind)
        if cur is not None:
            cspan = cur[1] - cur[0]
            inside = cur[0] <= lo and hi <= cur[1]
            if inside and cspan < 2.6 * (want[1] - want[0]):
                return
        self._yrange[kind] = want
        self._plots[kind].setYRange(want[0], want[1], padding=0)

    @staticmethod
    def _sample_at(d: tuple[np.ndarray, np.ndarray] | None, t: float) -> float | None:
        if d is None:
            return None
        tx, y = d
        if tx.size == 0:
            return None
        i = int(np.searchsorted(tx, t))
        i = min(max(i, 0), tx.size - 1)
        if i > 0 and abs(tx[i - 1] - t) < abs(tx[i] - t):
            i -= 1
        v = float(y[i])
        return v if np.isfinite(v) else None

    # ------------------------------------------------------------ 入力(Space・カーソル)
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        et = event.type()
        if obj is self.glw and et == QEvent.Type.KeyPress:
            ke = event
            if isinstance(ke, QKeyEvent) and ke.key() == Qt.Key.Key_Space and not ke.isAutoRepeat():
                self.toggle_pause()
                return True
        elif obj is self.glw.viewport():
            if et == QEvent.Type.Resize:
                self._overlay.setGeometry(self.glw.viewport().rect())
            elif et == QEvent.Type.MouseMove and self._paused and isinstance(event, QMouseEvent):
                self._cursor_t = self.x_to_time(event.position().x())
                self._overlay.update()
            elif et == QEvent.Type.Leave and self._cursor_t is not None:
                self._cursor_t = None
                self._overlay.update()
            elif et == QEvent.Type.MouseButtonPress:
                self.glw.setFocus(Qt.FocusReason.MouseFocusReason)
                if self._paused and not self.fft_active() and isinstance(event, QMouseEvent):
                    if event.button() == Qt.MouseButton.LeftButton:
                        t = self.x_to_time(event.position().x())
                        if t is not None:
                            lo, hi = self.x_range()
                            self.add_mark(min(max(t, lo), hi))
                    elif event.button() == Qt.MouseButton.RightButton:
                        self.clear_marks()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._overlay.setGeometry(self.glw.viewport().rect())
        self._overlay.update()

    def set_cursor_time(self, t: float | None) -> None:
        """カーソル位置を直接指定(テスト・スクリーンショット用)。"""
        self._cursor_t = t
        self._overlay.update()


__all__ = [
    "FFT_KEY",
    "KINDS",
    "PRESETS",
    "PRESET_ORDER",
    "VIEW_ORDER",
    "WINDOWS_S",
    "GraphView",
    "LegendRow",
    "fmt_value",
]
