"""読み値セル(ui_design.md §5.1・§6)。ラベル → 実測値(28 等幅)→ 単位 → 指令 → ゲージ。

ゲージ(横バー 6px): 全幅 = 物理レンジ、薄い帯 = 現在の出力段階で許される範囲、
◆ 中抜き = 指令、● 塗り = 実測。数値だけでなく「どこにいてどこへ向かうか」が分かる。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from edulite05.app.i18n import tr
from edulite05.app.ui.label import Label
from edulite05.app.ui.style import theme_manager, tokens
from edulite05.app.ui.tokens import H_GAUGE, SP_ICON, SP_PAD, SP_RELATED

MINUS = "−"  # 表示用のマイナス記号


def fmt_signed(v: float, decimals: int) -> str:
    text = f"{abs(v):.{decimals}f}"
    if round(v, decimals) == 0:
        return "+" + text
    return ("+" if v > 0 else MINUS) + text


class Gauge(QWidget):
    """物理レンジの横バー + 許容帯 + 指令(◆ 中抜き)+ 実測(● 塗り)。"""

    _HEIGHT = 14

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lo, self._hi = 0.0, 1.0
        self._allowed: tuple[float, float] | None = None
        self._value: float | None = None
        self._command: float | None = None
        self.setFixedHeight(self._HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme_manager().changed.connect(self._on_theme)

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    def set_range(self, lo: float, hi: float) -> None:
        self._lo, self._hi = lo, max(hi, lo + 1e-9)
        self.update()

    def set_allowed(self, lo: float | None, hi: float | None) -> None:
        self._allowed = None if lo is None or hi is None else (lo, hi)
        self.update()

    def set_value(self, v: float | None) -> None:
        self._value = v
        self.update()

    def set_command(self, v: float | None) -> None:
        self._command = v
        self.update()

    def _x(self, v: float, a: float, b: float) -> float:
        f = (v - self._lo) / (self._hi - self._lo)
        return a + (b - a) * min(max(f, 0.0), 1.0)

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = self.height() / 2
        a, b = 6.0, self.width() - 6.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.gauge_track))
        p.drawRoundedRect(QRectF(a, cy - H_GAUGE / 2, b - a, H_GAUGE), H_GAUGE / 2, H_GAUGE / 2)
        if self._allowed is not None:
            x0, x1 = self._x(self._allowed[0], a, b), self._x(self._allowed[1], a, b)
            p.setBrush(QColor(t.gauge_band))
            band = QRectF(x0, cy - H_GAUGE / 2, x1 - x0, H_GAUGE)
            p.drawRoundedRect(band, H_GAUGE / 2, H_GAUGE / 2)
        if self._lo < 0.0 < self._hi:  # ゼロの目盛り
            zx = self._x(0.0, a, b)
            p.setBrush(QColor(t.text_tertiary))
            p.drawRect(QRectF(zx - 0.5, cy - H_GAUGE / 2 - 1, 1, H_GAUGE + 2))
        if self._command is not None:  # ◆ 中抜き
            cx = self._x(self._command, a, b)
            r = 5.0
            poly = QPolygonF(
                [QPointF(cx, cy - r), QPointF(cx + r, cy), QPointF(cx, cy + r), QPointF(cx - r, cy)]
            )
            p.setPen(QPen(QColor(t.text_primary), 1.5))
            p.setBrush(QColor(t.bg_card))
            p.drawPolygon(poly)
        if self._value is not None:  # ● 塗り
            vx = self._x(self._value, a, b)
            p.setPen(QPen(QColor(t.bg_card), 1.5))
            p.setBrush(QColor(t.text_primary))
            p.drawEllipse(QPointF(vx, cy), 5.0, 5.0)
        p.end()


class ReadoutCell(QFrame):
    """`ReadoutCell("位置", "rad", decimals=3, minimum=-12.57, maximum=12.57)`。

    - `set_value(v)` / `set_command(c)` … 実測・指令(None で「—」)
    - `set_range(lo, hi)` … ゲージの物理レンジ / `set_allowed(lo, hi)` … 現在の出力段階の許容帯
    - `set_meta("多回転 +3.45 rad")` … 右下の補足(12 二次色)
    - `set_note("低出力上限で制限中")` … 琥珀の注記(meta より優先)
    - `set_header_widget(w)` … セル右上に小さな部品(位置の rad/deg 切替など)を置く
    """

    def __init__(
        self,
        label: str,
        unit: str,
        *,
        decimals: int = 2,
        minimum: float = 0.0,
        maximum: float = 1.0,
        signed: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._decimals = decimals
        self._signed = signed
        self._value: float | None = None
        self._command: float | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP_PAD, SP_PAD - 4, SP_PAD, SP_PAD - 2)
        lay.setSpacing(SP_ICON)

        self._head = QHBoxLayout()
        self._head.setContentsMargins(0, 0, 0, 0)
        self.label = Label(label, "caption")
        self._head.addWidget(self.label)
        self._head.addStretch(1)
        lay.addLayout(self._head)

        vrow = QHBoxLayout()
        vrow.setContentsMargins(0, 0, 0, 0)
        vrow.setSpacing(SP_RELATED)
        self.value_label = Label("—", "readout")
        self.unit_label = Label(unit, "secondary")
        vrow.addWidget(self.value_label)
        vrow.addWidget(self.unit_label, 0, Qt.AlignmentFlag.AlignBottom)
        vrow.addStretch(1)
        lay.addLayout(vrow)

        mrow = QHBoxLayout()
        mrow.setContentsMargins(0, 0, 0, 0)
        mrow.setSpacing(SP_RELATED)
        self.command_label = Label("", "caption")
        self.meta_label = Label("", "caption")
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.command_label.setMinimumHeight(16)
        mrow.addWidget(self.command_label)
        mrow.addStretch(1)
        mrow.addWidget(self.meta_label)
        lay.addLayout(mrow)

        self.gauge = Gauge()
        self.gauge.set_range(minimum, maximum)
        lay.addWidget(self.gauge)
        self._meta = ""
        self._note = ""
        self._render()

    # ---- 値 ----
    def set_value(self, v: float | None) -> None:
        self._value = v
        self.gauge.set_value(v)
        self._render()

    def set_command(self, v: float | None) -> None:
        self._command = v
        self.gauge.set_command(v)
        self._render()

    def set_range(self, lo: float, hi: float) -> None:
        self.gauge.set_range(lo, hi)

    def set_allowed(self, lo: float | None, hi: float | None) -> None:
        self.gauge.set_allowed(lo, hi)

    def set_unit(self, unit: str) -> None:
        self.unit_label.setText(unit)

    def set_decimals(self, decimals: int) -> None:
        self._decimals = decimals
        self._render()

    def set_meta(self, text: str | None) -> None:
        self._meta = text or ""
        self._render()

    def set_note(self, text: str | None) -> None:
        self._note = text or ""
        self._render()

    def set_header_widget(self, w: QWidget) -> None:
        self._head.addWidget(w)

    def value_text(self) -> str:
        return self.value_label.text()

    def _fmt(self, v: float) -> str:
        return fmt_signed(v, self._decimals) if self._signed else f"{v:.{self._decimals}f}"

    def _render(self) -> None:
        self.value_label.setText("—" if self._value is None else self._fmt(self._value))
        self.command_label.setText(
            "" if self._command is None else tr("ui.command", value=self._fmt(self._command))
        )
        if self._note:
            self.meta_label.setText(self._note)
            self.meta_label.set_role("caption")
            self.meta_label.set_tone("caution")
        else:
            self.meta_label.setText(self._meta)
            self.meta_label.set_tone(None)


class ReadoutDualCell(QFrame):
    """温度 + 電圧のように 2 量を縦に並べるセル(ゲージなし)。ReadoutCell と同じ高さにそろえる。"""

    def __init__(
        self,
        first: tuple[str, str, int],
        second: tuple[str, str, int],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP_PAD, SP_PAD - 4, SP_PAD, SP_PAD - 2)
        lay.setSpacing(SP_RELATED)
        self._decimals = (first[2], second[2])
        self._values: list[Label] = []
        for (label, unit, _d) in (first, second):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(SP_RELATED)
            row.addWidget(Label(label, "caption"))
            row.addStretch(1)
            val = Label("—", "readout-s")
            row.addWidget(val)
            row.addWidget(Label(unit, "secondary"), 0, Qt.AlignmentFlag.AlignBottom)
            lay.addLayout(row)
            self._values.append(val)
        self._power_row: tuple[Label, Label] | None = None
        self._power_lay = lay
        lay.addStretch(1)

    def add_extra_row(self, label: str, tag: str, unit: str) -> None:
        """3 行目(消費電力など)を足す。`[label tag] ……… [値 unit]`。tag は淡い注記(推定など)。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SP_RELATED)
        name = Label(label, "caption")
        row.addWidget(name)
        row.addWidget(Label(tag, "caption-tertiary"))
        row.addStretch(1)
        val = Label("—", "readout-s")
        row.addWidget(val)
        row.addWidget(Label(unit, "secondary"), 0, Qt.AlignmentFlag.AlignBottom)
        self._power_lay.insertLayout(self._power_lay.count() - 1, row)  # 末尾の stretch の前
        self._power_row = (name, val)

    def set_extra(self, label: str, value_text: str) -> None:
        """3 行目の名前(通常は「消費電力」、回生中は「回生」)と値の文字。"""
        if self._power_row is not None:
            self._power_row[0].setText(label)
            self._power_row[1].setText(value_text)

    def extra_texts(self) -> tuple[str, str]:
        assert self._power_row is not None
        return self._power_row[0].text(), self._power_row[1].text()

    def set_values(self, first: float | None, second: float | None) -> None:
        for lab, v, d in zip(self._values, (first, second), self._decimals, strict=True):
            lab.setText("—" if v is None else f"{v:.{d}f}")

    def value_texts(self) -> tuple[str, str]:
        return self._values[0].text(), self._values[1].text()
