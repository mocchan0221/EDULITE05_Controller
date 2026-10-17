"""故障ビュー(ui_design.md §5.3 / §2 原則 1)。

- 発生中の故障を、モータごとにカードで出す(重大度アイコン・名称・平易な説明・測定値と閾値・
  考えられる原因・対処・件数)。モータごとに [故障をリセット] を 1 つ(強調色)。
- 正常時は 1 行「故障はありません」だけ(ランプを並べない)。
- 下に履歴表(時刻・モータ・故障・発生/解消)。履歴は `state_updated` の故障ビットの変化から作る。
- 件数と測定値は `Bridge.fault_details()` を 0.5 s ごとに確認して、変わったときだけ作り直す
  (同じ故障の繰返しは故障ビットの集合が変わらないため)。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.pages.diagnostics.models import fmt_time
from edulite05.app.ui import Button, Label, icon, pixmap, theme_manager
from edulite05.app.ui.style import make_font, tokens
from edulite05.app.ui.tokens import (
    FS_BODY,
    H_ROW,
    ICON_M,
    ICON_S,
    SP_GROUP,
    SP_ICON,
    SP_PAD,
    SP_RELATED,
    SP_SECTION,
)
from edulite05.app.ui.util import hbox, vbox
from edulite05.app.viewmodels import FaultDetail, MotorViewState

MAX_HISTORY = 200
POLL_MS = 500
SWATCH = 12  # モータ色の四角の一辺

# 故障の重大度(tone)。未校正は「これから行う作業」であり危険な異常ではないため注意止まり
SEVERITY: dict[str, str] = {
    "undervoltage": "critical",
    "overcurrent": "critical",
    "overtemp": "critical",
    "encoder": "critical",
    "overload": "critical",
    "uncalibrated": "caution",
}


class FaultsView(QScrollArea):
    """発生中の故障と履歴。`active_changed` は発生中の (モータ, 故障) の集合が変わったとき。"""

    active_changed = Signal()

    def __init__(self, bridge: Bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._prev: dict[int, frozenset[str]] = {}
        self._active: set[tuple[int, str]] = set()
        self._details: dict[int, list[FaultDetail]] = {}
        self._history_count = 0

        body = QWidget()
        self.setWidget(body)
        lay = vbox(body, margins=(0, 0, SP_RELATED, 0), spacing=SP_SECTION)
        self.summary = QWidget()
        self.summary_layout = vbox(self.summary, spacing=SP_GROUP)
        lay.addWidget(self.summary)

        head = hbox(spacing=SP_RELATED)
        head.addWidget(Label(tr("diag.history"), "heading"))
        head.addStretch(1)
        self.clear_history_btn = Button(tr("diag.clear_history"), "subtle")
        head.addWidget(self.clear_history_btn)
        hist = vbox(spacing=SP_RELATED)
        hist.addLayout(head)
        self.history = QTableWidget(0, 4)
        self.history.setHorizontalHeaderLabels(
            [tr("diag.col_time"), tr("diag.col_motor"), tr("diag.col_fault"), tr("diag.col_event")]
        )
        self.history.setMinimumHeight(H_ROW * 7)
        self.history.verticalHeader().setVisible(False)
        self.history.verticalHeader().setDefaultSectionSize(H_ROW)
        self.history.setShowGrid(False)
        self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.history.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.history.setIconSize(QSize(ICON_S, ICON_S))
        hh = self.history.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setSectionsClickable(False)
        hh.setStretchLastSection(True)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        for c, w in enumerate((120, 96, 200)):
            self.history.setColumnWidth(c, w)
        hist.addWidget(self.history, 1)
        self.history_empty = Label(tr("diag.history_empty"), "caption")
        hist.addWidget(self.history_empty)
        lay.addLayout(hist, 1)

        self.clear_history_btn.clicked.connect(self.clear_history)
        bridge.state_updated.connect(self._on_states)
        bridge.motors_changed.connect(self._on_rebuild_signal)
        bridge.connection_changed.connect(self._on_rebuild_signal)
        theme_manager().changed.connect(self._on_rebuild_signal)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

        self._update_history_empty()
        self._on_states(bridge.states(), initial=True)
        self.rebuild()

    # ================================================================ 公開
    def active_faults(self) -> set[tuple[int, str]]:
        return set(self._active)

    def card_count(self) -> int:
        return len(self.findChildren(QFrame, "FaultCard"))

    def history_rows(self) -> int:
        return self.history.rowCount()

    def _on_rebuild_signal(self, *_args: object) -> None:
        self.rebuild()

    def clear_history(self) -> None:
        self.history.setRowCount(0)
        self._update_history_empty()

    # ================================================================ Bridge → 状態
    def _on_states(self, states: dict[int, MotorViewState], *, initial: bool = False) -> None:
        changed = False
        for mid, s in states.items():
            now = frozenset(s.faults)
            before = self._prev.get(mid, frozenset())
            if now != before:
                for key in sorted(now - before):
                    self._add_history(mid, key, occurred=True)
                for key in sorted(before - now):
                    self._add_history(mid, key, occurred=False)
                self._prev[mid] = now
                changed = True
        for mid in [m for m in self._prev if m not in states]:  # 切断などでモータが消えた
            for key in sorted(self._prev.pop(mid)):
                self._add_history(mid, key, occurred=False)
            changed = True
        if changed or initial:
            self._active = {(mid, k) for mid, ks in self._prev.items() for k in ks}
            self._details = self._read_details()
            self.rebuild()
            self.active_changed.emit()

    def _read_details(self) -> dict[int, list[FaultDetail]]:
        out: dict[int, list[FaultDetail]] = {}
        for mid in sorted({m for m, _k in self._active}):
            out[mid] = self.bridge.fault_details(mid)
        return out

    def _poll(self) -> None:
        """件数・測定値の更新(故障ビットが変わらない再発)を拾う。"""
        if not self._active:
            return
        details = self._read_details()
        if details != self._details:
            self._details = details
            self.rebuild()

    # ================================================================ 描画
    def rebuild(self) -> None:
        """発生中の故障カード(または「故障はありません」)を作り直す。"""
        lay = self.summary_layout
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not self._active:
            lay.addWidget(self._healthy_line())
            return
        motors = {m.id: m for m in self.bridge.motors()}
        order = {m.id: m.color_index for m in self.bridge.motors()}
        for mid in sorted({m for m, _k in self._active}):
            name = motors[mid].name if mid in motors else ""
            faults = self._details.get(mid, [])
            lay.addWidget(self._motor_group(mid, name, order.get(mid, 0), faults))

    def _healthy_line(self) -> QWidget:
        w = QWidget()
        row = hbox(w, spacing=SP_RELATED)
        has_motor = bool(self.bridge.motors()) and self.bridge.connection_info().connected
        ic = QLabel()
        ic.setFixedSize(ICON_M, ICON_M)
        if has_motor:
            ic.setPixmap(pixmap("circle-check", "success", ICON_M))
            text = tr("diag.no_fault")
        else:
            ic.setPixmap(pixmap("circle", "text_tertiary", ICON_M))
            text = tr("diag.no_motor")
        row.addWidget(ic)
        label = Label(text, "heading")
        label.setObjectName("NoFaultLabel")
        row.addWidget(label)
        row.addStretch(1)
        return w

    def _motor_group(
        self, mid: int, name: str, color_index: int, faults: list[FaultDetail]
    ) -> QWidget:
        w = QWidget()
        lay = vbox(w, spacing=SP_RELATED)
        head = hbox(spacing=SP_RELATED)
        sw = QLabel()
        px = QPixmap(SWATCH, SWATCH)
        px.fill(QColor(tokens().motor_color(color_index)))
        sw.setPixmap(px)
        sw.setFixedSize(SWATCH, SWATCH)
        head.addWidget(sw)
        head.addWidget(Label(tr("diag.motor_heading", id=mid, name=name), "heading"))
        head.addWidget(Label(tr("diag.fault_count", n=len(faults)), "caption", tone="critical"))
        head.addStretch(1)
        reset = Button(tr("diag.fault_reset"), "accent", icon="rotate-ccw")
        reset.setObjectName("FaultResetButton")
        reset.setToolTip(tr("diag.fault_reset_tip"))
        reset.clicked.connect(lambda _c=False, m=mid: self.bridge.clear_fault([m]))
        head.addWidget(reset)
        lay.addLayout(head)
        for fd in faults:
            lay.addWidget(self._card(fd))
        return w

    def _card(self, fd: FaultDetail) -> QFrame:
        tone = SEVERITY.get(fd.key, "critical")
        card = QFrame()
        card.setObjectName("FaultCard")
        card.setProperty("card", True)
        lay = vbox(card, margins=SP_PAD, spacing=SP_RELATED)

        head = hbox(spacing=SP_RELATED)
        ic = QLabel()
        ic.setFixedSize(ICON_M, ICON_M)
        ic.setPixmap(pixmap("triangle-alert", tone, ICON_M))
        ic.setToolTip(tr(f"diag.severity_{tone}"))
        head.addWidget(ic)
        head.addWidget(Label(fd.name, "heading"))
        head.addStretch(1)
        count = tr("diag.occurrences", n=fd.count)
        head.addWidget(Label(f"{count}  ·  {tr('diag.last_seen', t=_hms(fd.last_t))}", "caption"))
        lay.addLayout(head)
        lay.addWidget(Label(fd.description, "secondary", wrap=True))

        grid = QGridLayout()
        grid.setContentsMargins(0, SP_ICON, 0, 0)
        grid.setHorizontalSpacing(SP_PAD)
        grid.setVerticalSpacing(SP_RELATED)
        grid.setColumnMinimumWidth(0, 112)
        grid.setColumnStretch(1, 1)
        r = 0
        if fd.measured is not None or fd.threshold is not None:
            vals = hbox(spacing=SP_PAD)
            if fd.measured is not None:
                vals.addWidget(Label(
                    f"{fd.measured:.1f} {fd.unit}".strip(), "body-mono", tone=tone))
            if fd.threshold is not None:
                vals.addWidget(Label(
                    tr("diag.threshold", v=f"{fd.threshold:.1f} {fd.unit}".strip()),
                    "body-mono"))
            vals.addStretch(1)
            self._grid_row(grid, r, tr("diag.measured"), vals)
            r += 1
        if fd.causes:
            causes = Label("\n".join(f"・{c}" for c in fd.causes), "body", wrap=True)
            self._grid_row(grid, r, tr("diag.causes"), causes)
            r += 1
        if fd.remedy:
            self._grid_row(grid, r, tr("diag.remedy"), Label(fd.remedy, "body", wrap=True))
        lay.addLayout(grid)
        return card

    @staticmethod
    def _grid_row(grid: QGridLayout, r: int, label: str, content: QWidget | object) -> None:
        grid.addWidget(Label(label, "caption"), r, 0, Qt.AlignmentFlag.AlignTop)
        if isinstance(content, QWidget):
            grid.addWidget(content, r, 1)
        else:
            grid.addLayout(content, r, 1)  # type: ignore[arg-type]

    # ================================================================ 履歴
    def _add_history(self, mid: int, key: str, *, occurred: bool) -> None:
        t = tokens()
        h = self.history
        h.insertRow(0)
        name = tr(f"fault.{key}")
        cells = [fmt_time(datetime.now().timestamp()), tr("ui.motor_id", id=mid), name]
        for c, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setForeground(QColor(t.text_secondary if c == 0 else t.text_primary))
            if c == 0:
                item.setFont(make_font(FS_BODY, mono=True))
            h.setItem(0, c, item)
        ev = QTableWidgetItem(tr("diag.event_occurred") if occurred else tr("diag.event_cleared"))
        if occurred:
            ev.setIcon(icon("triangle-alert", "critical", ICON_S))
            ev.setForeground(QColor(t.critical))
        else:
            ev.setIcon(icon("circle-check", "text_secondary", ICON_S))
            ev.setForeground(QColor(t.text_secondary))
        h.setItem(0, 3, ev)
        while h.rowCount() > MAX_HISTORY:
            h.removeRow(h.rowCount() - 1)
        self._update_history_empty()

    def _update_history_empty(self) -> None:
        empty = self.history.rowCount() == 0
        self.history_empty.setVisible(empty)
        self.history.setVisible(not empty)


def _hms(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M:%S")
