"""CAN フレーム表示(ui_design.md §5.3。v1 の frames.py と同等の機能)。

時刻・方向・ID・type・モータ・データ(16 進)・デコード結果を等幅で出す。行数上限つきのリング
バッファ。描画負荷を抑えるため、到着フレームは 100 ms ごとにまとめて表へ足す。
フィルタ(type / モータ / 方向)と一時停止に対応する。一時停止中も到着フレームは溜め続け、
再開で追いつく。
"""

from __future__ import annotations

from collections import deque
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QCheckBox, QHeaderView, QTableView, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import catalog, tr
from edulite05.app.pages.diagnostics.log_view import configure_table
from edulite05.app.pages.diagnostics.models import FrameModel, comm_type, motor_of
from edulite05.app.ui import Button, Combo, Label
from edulite05.app.ui.tokens import SP_GROUP, SP_RELATED
from edulite05.app.ui.util import hbox, vbox
from edulite05.app.viewmodels import FrameEntry

MAX_ROWS = 2000  # 表示行数の上限
RAW_ROWS = 6000  # フィルタ変更時の再構築用に保持する生フレーム数
FLUSH_MS = 100
# el05_protocol.md §2 の通信タイプ(表示名は ja.toml の diag.type.<n>)
FRAME_TYPES = (0, 1, 2, 3, 4, 6, 7, 17, 18, 21, 22, 23, 24, 25)


class FramesView(QWidget):
    def __init__(self, bridge: Bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._raw: deque[FrameEntry] = deque(maxlen=RAW_ROWS)
        self._pending: list[FrameEntry] = []
        self._paused = False

        lay = vbox(self, spacing=SP_RELATED)
        bar = hbox(spacing=SP_GROUP)
        self.pause_btn = Button(tr("frames.pause"), "standard", icon="pause")
        bar.addWidget(self.pause_btn)
        bar.addWidget(Label(tr("frames.filter_type"), "secondary"))
        self.type_combo = Combo()
        self.type_combo.addItem(tr("frames.all"), None)
        cat = catalog()
        for t in FRAME_TYPES:
            self.type_combo.addItem(cat.get(f"diag.type.{t}", tr("frames.type_item", t=t)), t)
        self.type_combo.setMinimumWidth(190)
        bar.addWidget(self.type_combo)
        bar.addWidget(Label(tr("frames.filter_motor"), "secondary"))
        self.motor_combo = Combo()
        self.motor_combo.addItem(tr("frames.all"), None)
        self.motor_combo.setMinimumWidth(96)
        bar.addWidget(self.motor_combo)
        bar.addWidget(Label(tr("frames.filter_dir"), "secondary"))
        self.dir_combo = Combo()
        self.dir_combo.addItem(tr("frames.all"), None)
        self.dir_combo.addItem("TX", "TX")
        self.dir_combo.addItem("RX", "RX")
        self.dir_combo.setMinimumWidth(96)
        bar.addWidget(self.dir_combo)
        self.autoscroll = QCheckBox(tr("frames.autoscroll"))
        self.autoscroll.setChecked(True)
        bar.addWidget(self.autoscroll)
        bar.addStretch(1)
        self.count_label = Label("", "caption")
        bar.addWidget(self.count_label)
        self.clear_btn = Button(tr("diag.clear"), "standard", icon="trash-2")
        bar.addWidget(self.clear_btn)
        lay.addLayout(bar)

        self.model = FrameModel(MAX_ROWS)
        self.table = QTableView()
        self.table.setModel(self.model)
        configure_table(self.table)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        for i, w in enumerate((120, 64, 120, 64, 80, 250)):
            self.table.setColumnWidth(i, w)
        lay.addWidget(self.table, 1)

        self.pause_btn.clicked.connect(lambda: self.set_paused(not self._paused))
        self.clear_btn.clicked.connect(self.clear)
        for c in (self.type_combo, self.motor_combo, self.dir_combo):
            c.currentIndexChanged.connect(self.rebuild)
        bridge.frame_logged.connect(self.add)
        bridge.motors_changed.connect(self._on_motors)
        self._on_motors(bridge.motors())

        self._timer = QTimer(self)
        self._timer.setInterval(FLUSH_MS)
        self._timer.timeout.connect(self.flush)
        self._timer.start()

    def _on_motors(self, motors: list[Any]) -> None:
        cur = self.motor_combo.currentData()
        self.motor_combo.blockSignals(True)
        self.motor_combo.clear()
        self.motor_combo.addItem(tr("frames.all"), None)
        for m in motors:
            self.motor_combo.addItem(tr("ui.motor_id", id=m.id), m.id)
        self.motor_combo.setCurrentIndex(max(0, self.motor_combo.findData(cur)))
        self.motor_combo.blockSignals(False)

    # ---- 絞り込み ----
    def accepts(self, f: FrameEntry) -> bool:
        t = self.type_combo.currentData()
        m = self.motor_combo.currentData()
        d = self.dir_combo.currentData()
        return (
            (t is None or comm_type(f) == t)
            and (m is None or motor_of(f) == m)
            and (d is None or f.direction == d)
        )

    # ---- 追加・表示 ----
    def add(self, f: FrameEntry) -> None:
        self._raw.append(f)
        if self.accepts(f):
            self._pending.append(f)
            if len(self._pending) > MAX_ROWS:
                del self._pending[: len(self._pending) - MAX_ROWS]

    def flush(self) -> None:
        if not self.isVisible():
            return
        if self._paused:
            self._update_count()  # 保留件数だけ更新する
            return
        if not self._pending:
            return
        sb = self.table.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 2
        rows, self._pending = self._pending, []
        self.model.append(rows)
        if self.autoscroll.isChecked() and at_bottom:
            self.table.scrollToBottom()
        self._update_count()

    def rebuild(self, *_args: object) -> None:
        self._pending.clear()
        self.model.reset([f for f in self._raw if self.accepts(f)])
        if self.autoscroll.isChecked():
            self.table.scrollToBottom()
        self._update_count()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._paused:
            self.rebuild()

    # ---- 操作 ----
    def is_paused(self) -> bool:
        return self._paused

    def set_paused(self, on: bool) -> None:
        self._paused = on
        self.pause_btn.setText(tr("frames.resume") if on else tr("frames.pause"))
        self.pause_btn.set_icon_name("play" if on else "pause")
        if not on:
            self.flush()
        self._update_count()

    def clear(self) -> None:
        self._raw.clear()
        self._pending.clear()
        self.model.clear()
        self._update_count()

    def row_count(self) -> int:
        return self.model.rowCount()

    def pending_count(self) -> int:
        return len(self._pending)

    def _update_count(self) -> None:
        text = tr("diag.count", n=self.model.rowCount())
        if self._paused:
            text += " · " + tr("diag.paused_note", n=len(self._pending))
        self.count_label.setText(text)
