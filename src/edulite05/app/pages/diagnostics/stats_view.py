"""通信統計(ui_design.md §5.3): 送受信レート・制御周期・ジッタ・パース失敗・タイムアウト。

`Bridge.stats_updated(BusStats)` を受けて更新する(約 2 Hz)。レート系の 4 項目には直近 30 秒ほどの
推移を小さな折れ線で添える(色は付けない。異常かどうかは数字で判断する)。
パース失敗・タイムアウトは `BusStats.parse_errors` / `timeouts`(累積件数)。値が None の
Bridge なら「計測していません」と言う(0 と偽らない)。
"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QGridLayout, QSizePolicy, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.ui import Label, theme_manager
from edulite05.app.ui.style import tokens
from edulite05.app.ui.tokens import SP_ICON, SP_PAD, SP_RELATED
from edulite05.app.ui.util import hbox, vbox
from edulite05.app.viewmodels import BusStats

HISTORY = 60  # 点数(約 2 Hz で 30 秒)
SPARK_H = 32


class Sparkline(QWidget):
    """小さな折れ線。0 を下端、直近の最大値を上端として描く。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self.setFixedHeight(SPARK_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme_manager().changed.connect(self._on_theme)

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    def set_values(self, values: list[float]) -> None:
        self._values = values
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        if len(self._values) < 2:
            return
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width() - 4.0, self.height() - 6.0
        top = max(max(self._values) * 1.15, 1e-9)
        n = len(self._values)
        pts = [
            QPointF(2.0 + w * i / (HISTORY - 1), 3.0 + h * (1.0 - v / top))
            for i, v in enumerate(self._values[-HISTORY:])
        ]
        # 右端に寄せる(データが貯まるまでは右から伸びる)
        shift = w * (HISTORY - n) / (HISTORY - 1) if n < HISTORY else 0.0
        pts = [QPointF(pt.x() + shift, pt.y()) for pt in pts]
        p.setPen(QPen(QColor(t.stroke_divider), 1))
        base_y = self.height() - 3.0
        p.drawLine(QPointF(2.0, base_y), QPointF(self.width() - 2.0, base_y))
        p.setPen(QPen(QColor(t.text_tertiary), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(QPolygonF(pts))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.text_primary))
        p.drawEllipse(pts[-1], 3.0, 3.0)
        p.end()


class _Tile(QFrame):
    """ラベル → 値(28 等幅)+ 単位 → 推移(または注記)。"""

    def __init__(self, label: str, unit: str, *, spark: bool, decimals: int = 0) -> None:
        super().__init__()
        self.setProperty("card", True)
        self._decimals = decimals
        lay = vbox(self, margins=(SP_PAD, SP_PAD - 4, SP_PAD, SP_PAD - 2), spacing=SP_ICON)
        self.label = Label(label, "caption")
        lay.addWidget(self.label)
        row = hbox(spacing=SP_RELATED)
        self.value_label = Label("—", "readout")
        self.unit_label = Label(unit, "secondary")
        row.addWidget(self.value_label)
        row.addWidget(self.unit_label, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        lay.addLayout(row)
        self.spark = Sparkline() if spark else None
        self.note = Label("", "caption")
        if self.spark is not None:
            lay.addWidget(self.spark)
        else:
            lay.addWidget(self.note)
        self.history: deque[float] = deque(maxlen=HISTORY)

    def set_value(self, v: float | None, *, tone: str | None = None) -> None:
        self.value_label.setText("—" if v is None else f"{v:.{self._decimals}f}")
        self.value_label.set_tone(tone)
        if v is not None and self.spark is not None:
            self.history.append(v)
            self.spark.set_values(list(self.history))


class StatsView(QWidget):
    def __init__(self, bridge: Bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(SP_PAD)
        self.tx = _Tile(tr("diag.stat_tx"), tr("diag.unit_fps"), spark=True)
        self.rx = _Tile(tr("diag.stat_rx"), tr("diag.unit_fps"), spark=True)
        self.loop = _Tile(tr("diag.stat_loop"), "Hz", spark=True)
        self.jitter = _Tile(tr("diag.stat_jitter"), "ms", spark=True, decimals=2)
        self.parse = _Tile(tr("diag.stat_parse"), tr("diag.unit_count"), spark=False)
        self.timeout = _Tile(tr("diag.stat_timeout"), tr("diag.unit_count"), spark=False)
        self.retries = _Tile(tr("diag.stat_retry"), tr("diag.unit_count"), spark=False)
        self.dropped = _Tile(tr("diag.stat_dropped"), tr("diag.unit_count"), spark=False)
        tiles = (
            self.tx, self.rx, self.loop, self.jitter, self.parse, self.timeout,
            self.retries, self.dropped,
        )
        for i, tile in enumerate(tiles):
            grid.addWidget(tile, i // 3, i % 3)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        grid.setRowStretch(3, 1)
        self.updates = 0
        bridge.stats_updated.connect(self.on_stats)

    def on_stats(self, st: BusStats) -> None:
        self.updates += 1
        self.tx.set_value(st.tx_fps)
        self.rx.set_value(st.rx_fps)
        self.loop.set_value(st.loop_hz)
        self.jitter.set_value(st.jitter_ms)
        counters = (
            (self.parse, "parse_errors"),
            (self.timeout, "timeouts"),
            (self.retries, "retries"),
            (self.dropped, "frames_dropped"),
        )
        for tile, name in counters:
            n = getattr(st, name, None)
            # 間引きの件数は表示だけの話(通信には影響しない)ので、警告色にしない
            tone = "caution" if n and tile is not self.dropped else None
            tile.set_value(None if n is None else float(n), tone=tone)
            tile.note.setText("" if n is not None else tr("diag.stat_unmeasured"))
        thinned = bool(getattr(st, "frames_dropped", 0))
        self.dropped.note.setText(tr("diag.stat_dropped_note") if thinned else "")

