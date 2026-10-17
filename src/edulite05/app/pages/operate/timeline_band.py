"""グラフ下の補助表示(phase4_5.md §K)。

- `TimelineBand`: 操作対象のモータの「状態 / モード / 制限」の発生時間帯を 3 本の帯で出す。
  横軸はグラフ(最上段のプロット)と揃える。色は状態の意味だけ(ui_design.md §2 原則 1・7):
  状態の帯 = 状態バッジと同じ tone(停止中=無彩色・有効=アクセント・運転中=緑・故障=赤)、
  モードの帯 = 無彩色、制限の帯 = 注意(琥珀)。帯の中にはかならず文字を入れ、
  ホバーで内容と継続時間をツールチップに出す(色だけに頼らない)。
- `StepSummary`: ステップ応答の評価(立上り時間・オーバーシュート・整定時間)を 1 行で出す。
  行は常に表示する(結果が無いときは淡い「ステップ応答 —」)。出たり消えたりするとグラフの
  高さが跳ぶため、高さを常に確保する(ui_design.md §5.1「枠を動かさない」)。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QHBoxLayout, QToolTip, QWidget

from edulite05.app.i18n import catalog, tr
from edulite05.app.pages.operate.analysis import Segment, StepMetrics
from edulite05.app.ui.badge import ui_state_style
from edulite05.app.ui.label import Label
from edulite05.app.ui.style import make_font, theme_manager, tokens
from edulite05.app.ui.tokens import FS_CAPTION, R_CONTROL, SP_ICON, SP_PAD, SP_RELATED, mix
from edulite05.core.control.controllers import CONTROLLERS

if TYPE_CHECKING:
    from edulite05.app.pages.operate.graph_view import GraphView

LANES = ("state", "mode", "clamp")
LANE_H = 14
LANE_GAP = 2
PAD_Y = 4
BAND_H = 2 * PAD_Y + len(LANES) * LANE_H + (len(LANES) - 1) * LANE_GAP
_FILL_MIX = 0.30  # 帯の塗り: 意味色を面に 30 % 重ねる
_SQ = 8


def lane_label(lane: str, key: str) -> str:
    """帯に書く文字。state = 状態名、mode = コントローラ名、clamp = 「制限中」。"""
    if lane == "state":
        return ui_state_style(key)[2]
    if lane == "mode":
        if f"controller.{key}" in catalog():
            return tr(f"controller.{key}")
        cls = CONTROLLERS.get(key)  # プラグインのコントローラ: label(tr キーか文字列)
        return tr(cls.label) if cls is not None else key
    return tr("operate.timeline.clamp")


def lane_tone(lane: str, key: str) -> str:
    """帯の意味色(tone)。mode は常に neutral。"""
    if lane == "state":
        return ui_state_style(key)[0]
    return "caution" if lane == "clamp" else "neutral"


class TimelineBand(QWidget):
    """`TimelineBand(graph)`。描画だけ(データは GraphView が持つ)。"""

    def __init__(self, view: GraphView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self.setFixedHeight(BAND_H)
        self.setMouseTracking(True)
        self._hits: list[tuple[QRectF, str]] = []
        theme_manager().changed.connect(self._on_theme)

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    # ---- 表示する区間(テストでも使う)----
    def visible_segments(self) -> dict[str, list[tuple[Segment, float, float]]]:
        """lane → [(区間, 開始の相対秒, 終了の相対秒)]。相対秒はグラフの横軸(現在 = 0)。"""
        out: dict[str, list[tuple[Segment, float, float]]] = {}
        v = self._view
        mid = v.timeline_motor()
        lanes = v.timeline_lanes(mid) if mid is not None else {}
        lo, hi = v.x_range()
        ref = v.now_ref()
        for name in LANES:
            lane = lanes.get(name)
            if lane is None:
                out[name] = []
                continue
            rows = []
            for seg in lane.visible(ref + lo, ref + hi):
                end = (seg.end if seg.end is not None else ref) - ref
                rows.append((seg, seg.start - ref, end))
            out[name] = rows
        return out

    def _style(self, lane: str, key: str) -> tuple[QColor, QColor]:
        t = tokens()
        tone = lane_tone(lane, key)
        base = t.text_secondary if tone == "neutral" else t.tone_color(tone)
        fill = mix(base, t.bg_sunken, 0.22 if tone == "neutral" else _FILL_MIX)
        return QColor(fill), QColor(base)

    def paintEvent(self, _e) -> None:  # noqa: ANN001, N802
        v = self._view
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(t.bg_sunken))
        self._hits = []
        rect = v.first_plot_rect()
        font = make_font(FS_CAPTION, semibold=True)
        fm = QFontMetrics(font)
        p.setFont(font)
        # 左: 帯の名前 / 右: 対象のモータ(色の四角 + ID)
        for i, name in enumerate(LANES):
            y = PAD_Y + i * (LANE_H + LANE_GAP)
            p.setPen(QColor(t.text_secondary))
            p.drawText(
                QRectF(SP_RELATED, y, max(rect.left() - SP_RELATED - SP_ICON, 20), LANE_H),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                tr(f"operate.timeline.lane_{name}"),
            )
        mid = v.timeline_motor()
        if mid is not None and rect.width() > 20:
            x = rect.right() + SP_RELATED
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(v.motor_color(mid)))
            p.drawRoundedRect(QRectF(x, PAD_Y + 3, _SQ, _SQ), 2, 2)
            p.setPen(QColor(t.text_primary))
            p.drawText(
                QRectF(x + _SQ + SP_ICON, PAD_Y, 80, LANE_H),
                Qt.AlignmentFlag.AlignVCenter, tr("ui.motor_id", id=mid),
            )
        if rect.width() <= 20:
            p.end()
            return
        now = time.perf_counter()
        visible = self.visible_segments()
        for i, name in enumerate(LANES):
            y = PAD_Y + i * (LANE_H + LANE_GAP)
            for seg, s, e in visible.get(name, []):
                x0, x1 = v.time_to_x(s), v.time_to_x(e)
                if x0 is None or x1 is None:
                    continue
                x0 = min(max(x0, rect.left()), rect.right())
                x1 = min(max(x1, rect.left()), rect.right())
                if x1 - x0 < 2:
                    x1 = min(x0 + 2, rect.right())
                    x0 = x1 - 2
                r = QRectF(x0, y, x1 - x0, LANE_H)
                fill, fg = self._style(name, seg.key)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(r, R_CONTROL / 2, R_CONTROL / 2)
                label = lane_label(name, seg.key)
                if r.width() >= fm.horizontalAdvance(label) + 2 * SP_ICON:
                    p.setPen(fg)
                    p.drawText(
                        r.adjusted(SP_ICON, 0, -SP_ICON, 0),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label,
                    )
                dur = ((seg.end if seg.end is not None else now) - seg.start)
                tip_key = "segment_tip" if seg.end is not None else "segment_tip_now"
                tip = tr(f"operate.timeline.{tip_key}", label=label, dur=f"{dur:.1f}")
                if name == "clamp":
                    tip += "\n" + tr("operate.timeline.clamp_tip")
                    note = v.clamp_detail(mid) if seg.end is None and mid is not None else ""
                    if note:
                        tip += "\n" + note
                self._hits.append((r, tip))
        p.end()

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.ToolTip:
            pos = e.pos()  # type: ignore[attr-defined]
            for r, tip in self._hits:
                if r.contains(QPointF(pos)):
                    QToolTip.showText(e.globalPos(), tip, self)  # type: ignore[attr-defined]
                    return True
            QToolTip.hideText()
            return True
        return super().event(e)


class StepSummary(QWidget):
    """`立上り時間 0.12 s   オーバーシュート 8.5 %   整定時間 0.45 s(±2 %)` の 1 行。

    結果が無いときは `ステップ応答 —` だけを淡い色(caption-tertiary)で出す。行の高さは固定。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(28)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP_PAD, 0, SP_PAD, 0)
        lay.setSpacing(SP_RELATED)
        self.title = Label("", "caption-strong")
        lay.addWidget(self.title)
        lay.addSpacing(SP_RELATED)
        self._cells: dict[str, Label] = {}
        self._parts: list[QWidget] = []  # 結果があるときだけ出す見出し・値
        for key in ("rise", "overshoot", "settle"):
            text = tr(f"operate.step.{key}")
            if key == "settle":
                text += f"({tr('operate.step.settle_band')})"
            caption = Label(text, "caption")
            value = Label(tr("operate.step.none"), "body-mono")
            lay.addWidget(caption)
            lay.addWidget(value)
            lay.addSpacing(SP_RELATED)
            self._cells[key] = value
            self._parts += [caption, value]
        lay.addStretch(1)
        self.setToolTip(tr("operate.step.tip"))
        self._metrics: StepMetrics | None = None
        self.set_metrics(None)

    def texts(self) -> dict[str, str]:
        return {k: lab.text() for k, lab in self._cells.items()}

    def set_metrics(self, m: StepMetrics | None, motor_id: int = 0, quantity: str = "") -> None:
        self._metrics = m
        if m is None:  # 行は残し、淡いプレースホルダにする(高さを変えない)
            self.title.setText(tr("operate.step.title_none"))
            self.title.set_role("caption-tertiary")
            for w in self._parts:
                w.setVisible(False)
            return
        self.title.set_role("caption-strong")
        for w in self._parts:
            w.setVisible(True)
        self.title.setText(tr("operate.step.title", id=motor_id, quantity=quantity))
        none = tr("operate.step.none")
        self._cells["rise"].setText(none if m.rise_time is None else f"{m.rise_time:.3f} s")
        self._cells["overshoot"].setText(f"{m.overshoot_pct:.1f} %")
        settle = none if m.settling_time is None else f"{m.settling_time:.3f} s"
        self._cells["settle"].setText(settle)


__all__ = ["BAND_H", "LANES", "StepSummary", "TimelineBand", "lane_label", "lane_tone"]
