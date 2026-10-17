"""イベントログ(ui_design.md §5.3): 重要度フィルタ・検索・クリア・書き出し。

ログはページが隠れていても溜める(リングバッファ)。表示中だけ 100 ms ごとにまとめて表へ足す。
"""

from __future__ import annotations

import csv
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHeaderView,
    QLineEdit,
    QTableView,
    QWidget,
)

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.pages.diagnostics.models import LEVEL_RANK, LogModel, fmt_time
from edulite05.app.ui import Button, Combo, Label, icon, theme_manager
from edulite05.app.ui.tokens import H_ROW, ICON_S, SP_GROUP, SP_RELATED
from edulite05.app.ui.util import hbox, vbox
from edulite05.app.viewmodels import LogEntry

MAX_ENTRIES = 5000
FLUSH_MS = 100
LEVELS = ("debug", "info", "warn", "error")


def configure_table(view: QTableView, *, row_h: int = H_ROW) -> None:
    """診断ページの表の共通設定(行の塗り分けなし・縦ヘッダーなし)。"""
    view.setAlternatingRowColors(False)
    view.setShowGrid(False)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setWordWrap(False)
    view.verticalHeader().setVisible(False)
    view.verticalHeader().setDefaultSectionSize(row_h)
    view.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    view.horizontalHeader().setHighlightSections(False)
    view.horizontalHeader().setSectionsClickable(False)
    view.horizontalHeader().setStretchLastSection(True)
    view.horizontalHeader().setDefaultAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )
    view.setIconSize(QSize(ICON_S, ICON_S))


class LogView(QWidget):
    def __init__(self, bridge: Bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._raw: deque[LogEntry] = deque(maxlen=MAX_ENTRIES)
        self._pending: list[LogEntry] = []

        lay = vbox(self, spacing=SP_RELATED)
        bar = hbox(spacing=SP_GROUP)
        self.level_combo = Combo()
        for key in LEVELS:
            self.level_combo.addItem(tr(f"log.level_{key}"), key)
        self.level_combo.setCurrentIndex(LEVELS.index("info"))
        self.level_combo.setMinimumWidth(150)
        self.level_combo.setToolTip(tr("diag.level_tip"))
        bar.addWidget(self.level_combo)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("diag.log_search"))
        self.search.setFixedWidth(240)
        self._search_action = self.search.addAction(
            icon("search", "text_tertiary"), QLineEdit.ActionPosition.LeadingPosition
        )
        bar.addWidget(self.search)
        self.autoscroll = QCheckBox(tr("log.autoscroll"))
        self.autoscroll.setChecked(True)
        bar.addWidget(self.autoscroll)
        bar.addStretch(1)
        self.count_label = Label("", "caption")
        bar.addWidget(self.count_label)
        self.clear_btn = Button(tr("diag.clear"), "standard", icon="trash-2")
        self.export_btn = Button(tr("diag.export"), "standard", icon="download")
        bar.addWidget(self.clear_btn)
        bar.addWidget(self.export_btn)
        lay.addLayout(bar)

        self.model = LogModel(MAX_ENTRIES)
        self.table = QTableView()
        self.table.setModel(self.model)
        configure_table(self.table)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(1, 110)
        lay.addWidget(self.table, 1)

        self.level_combo.currentIndexChanged.connect(self.rebuild)
        self.search.textChanged.connect(self.rebuild)
        self.clear_btn.clicked.connect(self.clear)
        self.export_btn.clicked.connect(self._on_export)
        bridge.log_message.connect(self.add)
        theme_manager().changed.connect(self._on_theme)

        self._timer = QTimer(self)
        self._timer.setInterval(FLUSH_MS)
        self._timer.timeout.connect(self.flush)
        self._timer.start()

    # ---- 絞り込み ----
    def min_rank(self) -> int:
        return LEVEL_RANK[str(self.level_combo.currentData())]

    def accepts(self, e: LogEntry) -> bool:
        needle = self.search.text().strip().lower()
        return LEVEL_RANK.get(e.level, 1) >= self.min_rank() and (
            not needle or needle in e.text.lower()
        )

    # ---- 追加・表示 ----
    def add(self, e: LogEntry) -> None:
        self._raw.append(e)
        if self.accepts(e):
            self._pending.append(e)
            if len(self._pending) > MAX_ENTRIES:
                del self._pending[: len(self._pending) - MAX_ENTRIES]

    def flush(self) -> None:
        if not self._pending or not self.isVisible():
            return
        sb = self.table.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 2
        rows, self._pending = self._pending, []
        self.model.append(rows)
        if self.autoscroll.isChecked() and at_bottom:
            self.table.scrollToBottom()
        self._update_count()

    def rebuild(self, *_args: object) -> None:
        """フィルタ変更・表示開始時に、保持している全ログから表を作り直す。"""
        self._pending.clear()
        self.model.reset([e for e in self._raw if self.accepts(e)])
        if self.autoscroll.isChecked():
            self.table.scrollToBottom()
        self._update_count()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.rebuild()

    def _update_count(self) -> None:
        self.count_label.setText(tr("diag.count", n=self.model.rowCount()))

    def _on_theme(self, _name: str = "") -> None:
        self._search_action.setIcon(icon("search", "text_tertiary"))
        self.table.viewport().update()

    def row_count(self) -> int:
        return self.model.rowCount()

    # ---- 操作 ----
    def clear(self) -> None:
        self._raw.clear()
        self._pending.clear()
        self.model.clear()
        self._update_count()

    def export_csv(self, path: Path) -> int:
        """表示中(フィルタ後)のログを CSV で書き出す。Excel で開けるよう BOM 付き UTF-8。"""
        rows = self.model.rows() + self._pending
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([tr("diag.col_time"), tr("diag.col_level"), tr("diag.col_message")])
            for e in rows:
                w.writerow([fmt_time(e.t), e.level, e.text])
        return len(rows)

    def _on_export(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("diag.export"), f"user_data/event_log_{stamp}.csv", "CSV (*.csv)"
        )
        if path:
            self.export_csv(Path(path))


__all__ = ["LogView", "configure_table"]
