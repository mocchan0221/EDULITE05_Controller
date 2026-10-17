"""読み値ストリップ(ui_design.md §5.1): 位置 / 速度 / トルク / 温度+電圧。操作対象のモータの要約。

セルは `ui.ReadoutCell`(ラベル → 実測値 → 単位 → 指令 → ゲージ)。ゲージの全幅は物理レンジ
(Params dataclass の metadata から取り、直書きしない)、薄い帯は現在の出力段階で許される範囲。
位置セルには rad/deg の切替・[原点に設定](控えめなアイコン付き文字ボタン。`zero_requested`)・
多回転値。指令が出力段階の上限に張り付いているときは、セル内に琥珀の文字で
「低出力上限で制限中」を出す。
"""

from __future__ import annotations

import dataclasses
import math

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget
from shiboken6 import isValid

from edulite05.app.i18n import tr
from edulite05.app.ui.colors import qcolor
from edulite05.app.ui.icon import pixmap
from edulite05.app.ui.readout import MINUS, ReadoutCell, ReadoutDualCell
from edulite05.app.ui.style import make_font, theme_manager, tokens
from edulite05.app.ui.tokens import FS_CAPTION, ICON_S, R_CONTROL, SP_GROUP, SP_ICON
from edulite05.app.viewmodels import MotorViewState, PowerLimits
from edulite05.core import clock
from edulite05.core.control.params import MitParams
from edulite05.core.telemetry.power import LowPass, PowerEstimate, smooth_estimate

_P_RANGE = 4.0 * math.pi  # type 2 の角度は ±4π(el05_protocol.md §2.3)。DemoBridge と同じ巻き戻し


def wrap_angle(theta: float) -> float:
    """多回転の角度を ±4π に巻き戻す(`MotorViewState.position` と同じ表現にそろえる)。"""
    return (theta + _P_RANGE) % (2 * _P_RANGE) - _P_RANGE


def physical_range(field_name: str) -> tuple[float, float]:
    """Params dataclass の metadata から物理レンジ(直書きしない)。"""
    for f in dataclasses.fields(MitParams):
        if f.name == field_name:
            return float(f.metadata["min"]), float(f.metadata["max"])
    raise KeyError(field_name)


class UnitToggle(QWidget):
    """位置セル右上の小さな rad / deg 切替。文字だけ(選択中は Semibold + 面)。"""

    changed = Signal(str)  # "rad" / "deg"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._unit = "rad"
        self._hover: str | None = None
        self.setFixedSize(76, 20)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("operate.readout.unit_tip"))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        theme_manager().changed.connect(self._on_theme)

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    def unit(self) -> str:
        return self._unit

    def set_unit(self, unit: str) -> None:
        if unit in ("rad", "deg") and unit != self._unit:
            self._unit = unit
            self.update()
            self.changed.emit(unit)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(76, 20)

    def _rect(self, which: str) -> QRectF:
        w = self.width() / 2
        return QRectF(0 if which == "rad" else w, 0, w, self.height())

    def _at(self, x: float) -> str:
        return "rad" if x < self.width() / 2 else "deg"

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.set_unit(self._at(e.position().x()))

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        h = self._at(e.position().x())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, _e) -> None:  # noqa: ANN001, N802
        self._hover = None
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for which in ("rad", "deg"):
            key = f"operate.readout.unit_{which}"
            r = self._rect(which).adjusted(2, 2, -2, -2)
            sel = which == self._unit
            if sel:
                p.setPen(QColor(t.stroke_control))
                p.setBrush(QColor(t.seg_selected))
                p.drawRoundedRect(r, R_CONTROL, R_CONTROL)
            elif which == self._hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(qcolor(t.overlay_hover))
                p.drawRoundedRect(r, R_CONTROL, R_CONTROL)
            p.setFont(make_font(FS_CAPTION, semibold=sel))
            p.setPen(QColor(t.text_primary if sel else t.text_secondary))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, tr(key))
        p.end()


def power_texts(est: PowerEstimate) -> tuple[str, str]:
    """(行の名前, 値の文字)。負(回生)は「回生」と大きさ、そうでなければ「消費電力」。"""
    if est.p_in < 0.0:
        w = f"{-est.p_in:.1f}"
        return tr("operate.readout.power_regen"), tr("operate.readout.power_value", w=w)
    return tr("operate.readout.power"), tr("operate.readout.power_value", w=f"{est.p_in:.1f}")


def power_tooltip(est: PowerEstimate) -> str:
    """内訳(機械出力・銅損・待機)と、推定であることの説明。"""
    return tr(
        "operate.readout.power_tip",
        mech=f"{est.p_mech:+.2f}", cu=f"{est.p_cu:.2f}", base=f"{est.p_base:.2f}",
        total=f"{est.p_in:+.2f}",
    )


class QuietButton(QWidget):
    """読み値カードの隅に置く控えめなボタン(アイコン + 文字。枠なし・面なし、ホバーで薄い面)。

    `UnitToggle` と同じ 20 px の高さ。幅は「文字つき」が希望、最小は「アイコンだけ」。
    カードが狭くて希望の幅をもらえないときは、文字を省いてアイコンだけを描く
    (ツールチップに全文)。無効のときは `set_available(False, 理由)`。
    """

    clicked = Signal()
    _H = 20
    _PAD = 6

    def __init__(self, text: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._icon = icon_name
        self._hover = False
        self._tip = text
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self._H)
        self.setAccessibleName(text)
        theme_manager().changed.connect(self._on_theme)

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    def text(self) -> str:
        return self._text

    def full_width(self) -> int:
        """文字つきの幅。"""
        fm = QFontMetrics(make_font(FS_CAPTION))
        return 2 * self._PAD + ICON_S + SP_ICON + fm.horizontalAdvance(self._text)

    def compact_width(self) -> int:
        return 2 * self._PAD + ICON_S

    def is_compact(self) -> bool:
        """いま文字を省いて描いているか。"""
        return self.width() < self.full_width()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.full_width(), self._H)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.compact_width(), self._H)

    def set_available(self, available: bool, reason: str = "") -> None:
        self.setEnabled(available)
        self.setToolTip(self._tip if available else (reason or self._tip))

    def set_tooltip(self, text: str) -> None:
        self._tip = text
        if self.isEnabled():
            self.setToolTip(text)

    def enterEvent(self, _e) -> None:  # noqa: ANN001, N802
        self._hover = True
        self.update()

    def leaveEvent(self, _e) -> None:  # noqa: ANN001, N802
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if (
            e.button() == Qt.MouseButton.LeftButton
            and self.isEnabled()
            and self.rect().contains(e.position().toPoint())
        ):
            self.clicked.emit()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.isEnabled():
            self.clicked.emit()
            e.accept()
            return
        super().keyPressEvent(e)

    def changeEvent(self, e: QEvent) -> None:  # noqa: N802
        if e.type() == QEvent.Type.EnabledChange:
            self.update()
        super().changeEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        en = self.isEnabled()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if en and (self._hover or self.hasFocus()):
            p.setPen(QColor(t.accent) if self.hasFocus() else Qt.PenStyle.NoPen)
            p.setBrush(qcolor(t.overlay_hover))
            p.drawRoundedRect(r, R_CONTROL, R_CONTROL)
        iy = (self.height() - ICON_S) // 2
        p.drawPixmap(
            self._PAD, iy, pixmap(self._icon, "text_secondary" if en else "text_disabled")
        )
        if not self.is_compact():
            p.setFont(make_font(FS_CAPTION))
            p.setPen(QColor(t.text_secondary if en else t.text_disabled))
            text_rect = QRectF(self._PAD + ICON_S + SP_ICON, 0, self.width(), self.height())
            p.drawText(
                text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text
            )
        p.end()


class ReadoutStrip(QWidget):
    """4 セルの読み値ストリップ。`set_state(state, limits)` で更新する。

    `zero_requested`: 位置セルの [原点に設定] が押された(確認と実行は運転ページが行う)。
    """

    zero_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP_GROUP)
        pos_lo, pos_hi = physical_range("position")
        vel_lo, vel_hi = physical_range("velocity")
        tq_lo, tq_hi = physical_range("torque_ff")
        self._pos_range = (pos_lo, pos_hi)
        self.pos_cell = ReadoutCell(
            tr("operate.readout.position"), "rad", decimals=3, minimum=pos_lo, maximum=pos_hi
        )
        self.unit_toggle = UnitToggle()
        self.zero_btn = QuietButton(tr("operate.readout.zero"), "crosshair")
        self.zero_btn.set_tooltip(tr("operate.readout.zero_tip"))
        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(SP_ICON)
        hl.addWidget(self.zero_btn)
        hl.addWidget(self.unit_toggle)
        self.pos_cell.set_header_widget(header)
        self.zero_btn.clicked.connect(self.zero_requested)
        self.vel_cell = ReadoutCell(
            tr("operate.readout.velocity"), "rad/s", decimals=2, minimum=vel_lo, maximum=vel_hi
        )
        self.torque_cell = ReadoutCell(
            tr("operate.readout.torque"), "N·m", decimals=3, minimum=tq_lo, maximum=tq_hi
        )
        self.env_cell = ReadoutDualCell(
            (tr("operate.readout.temperature"), "℃", 1), (tr("operate.readout.voltage"), "V", 1)
        )
        # 3 行目: 消費電力の概算(未較正。「推定」の注記つき。ツールチップに内訳)
        self.env_cell.add_extra_row(
            tr("operate.readout.power"), tr("operate.readout.power_tag"), "W"
        )
        self.env_cell.set_extra(tr("operate.readout.power"), "—")
        self.env_cell.setToolTip(tr("operate.readout.power_tip_none"))
        self._power_filters = (LowPass(), LowPass(), LowPass())
        self._power_motor: int | None = None
        self.env_cell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._cells = (self.pos_cell, self.vel_cell, self.torque_cell, self.env_cell)
        for cell in self._cells:
            lay.addWidget(cell, 1)
        self._equalized = False
        self._deg = False
        self._last: tuple[MotorViewState | None, PowerLimits | None] = (None, None)
        self.unit_toggle.changed.connect(self._on_unit)

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        if not self._equalized:
            self._equalized = True
            QTimer.singleShot(0, self.equalize)

    def _set_power(self, s: MotorViewState | None) -> None:
        """消費電力の概算。内訳ごとに約 0.3 s のローパスをかけ、負なら「回生 ≈ x W」と出す。"""
        if s is None or s.power is None:
            for f in self._power_filters:
                f.reset()
            self._power_motor = None
            self.env_cell.set_extra(tr("operate.readout.power"), "—")
            self.env_cell.setToolTip(tr("operate.readout.power_tip_none"))
            return
        if s.id != self._power_motor:
            for f in self._power_filters:
                f.reset()
            self._power_motor = s.id
        est = smooth_estimate(self._power_filters, clock.now(), s.power)
        self.env_cell.set_extra(*power_texts(est))
        self.env_cell.setToolTip(power_tooltip(est))

    def set_zero_available(self, available: bool, reason: str = "") -> None:
        """[原点に設定] を押せるか(接続・非常停止・故障で変わる)。"""
        self.zero_btn.set_available(available, reason or tr("operate.readout.zero_tip"))

    def equalize(self) -> None:
        """4 セルを同じ高さにそろえる(位置セルだけ右上に切替があり、そのままでは少し高くなる)。"""
        if not isValid(self):
            return
        for c in self._cells:
            c.setMinimumHeight(0)
            c.setMaximumHeight(16777215)
        h = max(c.sizeHint().height() for c in self._cells)
        for c in self._cells:
            c.setFixedHeight(h)

    # ---- 単位 ----
    def use_degrees(self) -> bool:
        return self._deg

    def set_degrees(self, on: bool) -> None:
        self.unit_toggle.set_unit("deg" if on else "rad")

    def _on_unit(self, unit: str) -> None:
        self._deg = unit == "deg"
        lo, hi = self._pos_range
        if self._deg:
            self.pos_cell.set_unit("deg")
            self.pos_cell.set_decimals(1)
            self.pos_cell.set_range(math.degrees(lo), math.degrees(hi))
        else:
            self.pos_cell.set_unit("rad")
            self.pos_cell.set_decimals(3)
            self.pos_cell.set_range(lo, hi)
        self.set_state(*self._last)

    # ---- 更新 ----
    def set_state(self, s: MotorViewState | None, limits: PowerLimits | None) -> None:
        self._last = (s, limits)
        self._set_power(s)
        conv = math.degrees if self._deg else (lambda v: v)
        if limits is not None:
            self.vel_cell.set_allowed(-limits.speed, limits.speed)
            self.torque_cell.set_allowed(-limits.torque, limits.torque)
        if s is None:
            for c in (self.pos_cell, self.vel_cell, self.torque_cell):
                c.set_value(None)
                c.set_command(None)
                c.set_meta(None)
                c.set_note(None)
            self.env_cell.set_values(None, None)
            return
        self.pos_cell.set_value(conv(s.position))
        cmd = None if s.cmd_position is None else conv(wrap_angle(s.cmd_position))
        self.pos_cell.set_command(cmd)
        unit = tr("operate.readout.unit_deg") if self._deg else tr("operate.readout.unit_rad")
        multi = f"{conv(s.position_multi):+.1f}" if self._deg else f"{s.position_multi:+.2f}"
        self.pos_cell.set_meta(
            tr("operate.readout.multi_turn", value=f"{multi.replace('-', MINUS)} {unit}")
        )
        self.vel_cell.set_value(s.velocity)
        self.vel_cell.set_command(s.cmd_velocity)
        self.torque_cell.set_value(s.torque)
        self.torque_cell.set_command(s.cmd_torque)
        self.env_cell.set_values(s.temperature, s.vbus)
        note = None
        if limits is not None:
            note = tr("operate.readout.limited", tier=tr(f"tier.{limits.tier}.short"))
        self.vel_cell.set_note(
            note if limits and s.cmd_velocity is not None
            and abs(s.cmd_velocity) >= limits.speed - 1e-6 else None
        )
        self.torque_cell.set_note(
            note if limits and s.cmd_torque is not None
            and abs(s.cmd_torque) >= limits.torque - 1e-6 else None
        )

