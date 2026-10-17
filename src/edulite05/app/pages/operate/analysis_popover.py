"""「トリガ・解析」ポップオーバー(phase4_5.md §K。ui_design.md §5.1 のツールバーを散らかさない)。

ツールバーには 1 つのボタンだけを置き、トリガの設定と表示の設定はここへ畳む:
  トリガ … チャネル・条件(立上り/立下り)・レベル・残す時間・[待機を開始](強調は 1 つ)
  表示   … 状態の帯のオン/オフ
Δ カーソル(一時停止中のクリック)と FFT(表示プリセット)とステップ応答(自動)は
ここに設定が無い。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QHBoxLayout, QWidget

from edulite05.app.i18n import tr
from edulite05.app.pages.operate.analysis import TriggerSpec
from edulite05.app.ui.button import Button
from edulite05.app.ui.inputs import Combo, Switch
from edulite05.app.ui.label import Label
from edulite05.app.ui.param_row import NumberEdit
from edulite05.app.ui.popover import Popover
from edulite05.app.ui.segmented import Segmented
from edulite05.app.ui.tokens import SP_ICON, SP_RELATED
from edulite05.app.ui.util import divider

if TYPE_CHECKING:
    from edulite05.app.pages.operate.graph_view import GraphView

SPANS_S = (1, 2, 5, 10)  # 前後に残す秒数
DEFAULT_SPAN_S = 2
_LABEL_W = 72


def _row(label: str, control: QWidget, *extra: QWidget) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(SP_RELATED)
    cap = Label(label, "secondary")
    cap.setFixedWidth(_LABEL_W)
    lay.addWidget(cap)
    lay.addWidget(control, 1)
    for e in extra:
        lay.addWidget(e)
    return w


class AnalysisPopover(Popover):
    """`AnalysisPopover(graph)`。開く直前に `sync()` で現在の状態を反映する。"""

    def __init__(self, graph: GraphView, parent: QWidget | None = None) -> None:
        super().__init__(parent, width=340)
        self.graph = graph
        body = self.body
        body.setSpacing(SP_RELATED + SP_ICON)

        body.addWidget(Label(tr("operate.analysis.popover_trigger"), "heading"))
        body.addWidget(Label(tr("operate.analysis.trigger_desc"), "caption", wrap=True))
        self.channel_combo = Combo()
        body.addWidget(_row(tr("operate.analysis.trigger_channel"), self.channel_combo))
        self.edge_seg = Segmented(
            [
                ("rising", tr("operate.analysis.edge_rising")),
                ("falling", tr("operate.analysis.edge_falling")),
            ],
            current="rising",
        )
        body.addWidget(_row(tr("operate.analysis.trigger_edge"), self.edge_seg))
        self.level_edit = NumberEdit()
        self.level_edit.configure(-1.0e6, 1.0e6, 0.1, 3)
        self.unit_label = Label("", "secondary")
        self.unit_label.setMinimumWidth(48)
        body.addWidget(_row(tr("operate.analysis.trigger_level"), self.level_edit, self.unit_label))
        self.span_combo = Combo()
        for s in SPANS_S:
            self.span_combo.addItem(tr("operate.analysis.trigger_span_item", s=s), float(s))
        self.span_combo.setCurrentIndex(SPANS_S.index(DEFAULT_SPAN_S))
        body.addWidget(_row(tr("operate.analysis.trigger_span"), self.span_combo))
        self.arm_btn = Button(tr("operate.analysis.trigger_arm"), "accent", icon="activity")
        body.addWidget(self.arm_btn)

        body.addWidget(divider())
        body.addWidget(Label(tr("operate.analysis.popover_view"), "heading"))
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(SP_RELATED)
        rl.addWidget(Label(tr("operate.analysis.timeline_switch"), "body"), 1)
        self.timeline_switch = Switch(graph.timeline_visible())
        self.timeline_switch.setToolTip(tr("operate.analysis.timeline_switch_tip"))
        rl.addWidget(self.timeline_switch)
        body.addWidget(row)

        self.channel_combo.currentIndexChanged.connect(self._update_unit)
        self.arm_btn.clicked.connect(self._on_arm)
        self.timeline_switch.toggled.connect(graph.set_timeline_visible)

    # ---- 状態の反映 ----
    def sync(self) -> None:
        """チャネル一覧と、トリガの状態(待機中は設定を固定して [待機を解除])を反映する。"""
        g = self.graph
        spec = g.trigger_spec()
        keep = spec.channel if spec is not None else self.channel_combo.currentData()
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        for name, label in g.trigger_channels():
            self.channel_combo.addItem(label, name)
        i = self.channel_combo.findData(keep)
        if i < 0:
            i = self.channel_combo.findData(g.default_trigger_channel())
        self.channel_combo.setCurrentIndex(max(i, 0))
        self.channel_combo.blockSignals(False)
        if spec is not None:
            self.edge_seg.set_current("rising" if spec.rising else "falling")
            self.level_edit.set_value(spec.level)
            self.span_combo.setCurrentIndex(
                min(range(len(SPANS_S)), key=lambda k: abs(SPANS_S[k] - spec.span_s))
            )
        self._update_unit()
        idle = g.trigger_state() == "idle"
        for w in (self.channel_combo, self.edge_seg, self.level_edit, self.span_combo):
            w.setEnabled(idle)
        has_channel = self.channel_combo.count() > 0
        self.arm_btn.setEnabled(has_channel or not idle)
        self.arm_btn.setText(
            tr("operate.analysis.trigger_arm" if idle else "operate.analysis.trigger_disarm")
        )
        self.arm_btn.set_variant("accent" if idle else "standard")
        self.arm_btn.set_icon_name("activity" if idle else "x")
        self.timeline_switch.setChecked(g.timeline_visible())

    def spec(self) -> TriggerSpec | None:
        name = self.channel_combo.currentData()
        if name is None:
            return None
        return TriggerSpec(
            channel=str(name),
            level=self.level_edit.value(),
            rising=self.edge_seg.current() != "falling",
            span_s=float(self.span_combo.currentData()),
        )

    def _update_unit(self, _i: int = 0) -> None:
        name = self.channel_combo.currentData()
        self.unit_label.setText(self.graph.channel_unit(str(name)) if name is not None else "")

    def _on_arm(self) -> None:
        if self.graph.trigger_state() != "idle":
            self.graph.disarm_trigger()
        else:
            spec = self.spec()
            if spec is None:
                return
            self.graph.arm_trigger(spec)
        self.hide()

    def open_below(self, anchor: QWidget) -> None:
        """アンカー(凡例行の右端)の上に開く。画面の下端に近いので、下ではなく上へ出す。"""
        self.sync()
        self.adjustSize()
        gp = anchor.mapToGlobal(QPoint(0, 0))
        margin = 20  # Popover の影の余白
        x = gp.x() + anchor.width() - self.width() + margin
        y = gp.y() - self.height() + margin - 4
        screen = anchor.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            x = min(max(x, avail.left()), avail.right() - self.width())
            y = max(y, avail.top())
        self.move(x, y)
        self.show()


__all__ = ["DEFAULT_SPAN_S", "SPANS_S", "AnalysisPopover"]
