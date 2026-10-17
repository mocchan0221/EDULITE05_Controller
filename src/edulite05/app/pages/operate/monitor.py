"""モニタ(ui_design.md §5.1): ツールバー + `[グラフ | 3D]`。

ツールバー: `[グラフ|3D]` / 表示プリセット(+ FFT)/ 時間幅 / ⏸ 一時停止 / ⏺ 記録。
グラフ専用の操作(プリセット・時間幅・一時停止)は 3D 表示のとき隠す。
トリガと表示の設定は、グラフ下の凡例行にある「トリガ・解析」ボタンのポップオーバーに畳む
(ツールバーを増やさない)。
FFT は表示プリセットの末尾で、選ぶと「対象」のチャネル選択が出る。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.pages.operate.analysis_popover import AnalysisPopover
from edulite05.app.pages.operate.graph_view import (
    DEFAULT_WINDOW_S,
    FFT_KEY,
    VIEW_ORDER,
    WINDOWS_S,
    GraphView,
)
from edulite05.app.pages.operate.view3d import Viewer3D
from edulite05.app.ui.button import Button
from edulite05.app.ui.icon import icon
from edulite05.app.ui.inputs import Combo
from edulite05.app.ui.label import Label
from edulite05.app.ui.segmented import Segmented
from edulite05.app.ui.style import theme_manager
from edulite05.app.ui.tokens import SP_GROUP, SP_PAD


class Monitor(QFrame):
    """グラフと 3D を切り替えるカード。`record_toggled(bool)` は記録ボタンの押下を通知する。"""

    record_toggled = Signal(bool)

    def __init__(self, bridge: Bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar_host = QWidget()
        bar = QHBoxLayout(bar_host)
        bar.setContentsMargins(SP_PAD, SP_GROUP, SP_PAD, SP_GROUP)
        bar.setSpacing(SP_GROUP)
        self.view_seg = Segmented(
            [("graph", tr("operate.monitor.view_graph")), ("3d", tr("operate.monitor.view_3d"))]
        )
        self.view_seg.setToolTip(tr("operate.monitor.view_tip"))
        bar.addWidget(self.view_seg)

        self._graph_controls = QWidget()
        gc = QHBoxLayout(self._graph_controls)
        gc.setContentsMargins(0, 0, 0, 0)
        gc.setSpacing(SP_GROUP)
        preset_caption = tr("operate.monitor.preset")
        if preset_caption:  # 言語によっては見出しを省く(空文字): 項目名だけで足りるとき幅を節約する
            gc.addWidget(Label(preset_caption, "caption"))
        self.preset_combo = Combo()
        for key in VIEW_ORDER:
            self.preset_combo.addItem(tr(f"operate.preset.{key}"), key)
            # 狭い幅でも収まるよう表示は短い名前にし、含まれる量は項目のツールチップで言う
            self.preset_combo.setItemData(
                self.preset_combo.count() - 1, tr(f"operate.preset_desc.{key}"),
                Qt.ItemDataRole.ToolTipRole,
            )
        gc.addWidget(self.preset_combo)
        self.fft_label = Label(tr("operate.analysis.fft_channel"), "caption")
        self.fft_combo = Combo()
        self.fft_combo.setToolTip(tr("operate.analysis.fft_channel_tip"))
        self.fft_label.setVisible(False)
        self.fft_combo.setVisible(False)
        gc.addWidget(self.fft_label)
        gc.addWidget(self.fft_combo)
        gc.addWidget(Label(tr("operate.monitor.window"), "caption"))
        self.window_combo = Combo()
        for w in WINDOWS_S:
            self.window_combo.addItem(tr("operate.monitor.window_item", s=w), float(w))
        self.window_combo.setCurrentIndex(WINDOWS_S.index(DEFAULT_WINDOW_S))
        gc.addWidget(self.window_combo)
        bar.addWidget(self._graph_controls)
        bar.addStretch(1)

        self.pause_btn = Button(
            tr("operate.monitor.pause"), "standard", icon="pause",
            tooltip=tr("operate.monitor.pause_tip"),
        )
        bar.addWidget(self.pause_btn)
        self.record_btn = Button(
            tr("operate.monitor.record"), "standard", icon="circle-dot",
            tooltip=tr("operate.monitor.record_tip"),
        )
        bar.addWidget(self.record_btn)
        root.addWidget(bar_host)

        self.stack = QStackedWidget()
        self.graph = GraphView(bridge)
        self.view3d = Viewer3D(bridge)
        self.stack.addWidget(self.graph)
        self.stack.addWidget(self.view3d)
        root.addWidget(self.stack, 1)
        # 表示キー → ウィジェット(プラグインの `add_monitor_view` が増やす)
        self._views: dict[str, QWidget] = {"graph": self.graph, "3d": self.view3d}
        self._view_titles: list[tuple[str, str]] = [
            ("graph", tr("operate.monitor.view_graph")), ("3d", tr("operate.monitor.view_3d")),
        ]

        self._recording = False
        theme_manager().changed.connect(self._tint_record_icon)
        self.view_seg.selected.connect(self.set_view)
        self.preset_combo.currentIndexChanged.connect(
            lambda _i: self.graph.set_preset(str(self.preset_combo.currentData()))
        )
        self.window_combo.currentIndexChanged.connect(
            lambda _i: self.graph.set_window(float(self.window_combo.currentData()))
        )
        self.pause_btn.clicked.connect(self.graph.toggle_pause)
        self.graph.paused_changed.connect(self._on_paused)
        # 「トリガ・解析」のボタンはグラフ下の凡例行(右端)にある。ツールバーを増やさず、
        # 1280 px の最小幅(ui_design.md §13)に収めるため。3D 表示ではグラフごと隠れる
        self.analysis_btn = self.graph.legend.tools_btn
        self.analysis = AnalysisPopover(self.graph, self)
        self.analysis_btn.clicked.connect(lambda: self.analysis.open_below(self.analysis_btn))
        self.graph.trigger_changed.connect(self._on_trigger)
        self.graph.channels_changed.connect(self._sync_fft_combo)
        self.preset_combo.currentIndexChanged.connect(lambda _i: self._sync_fft_combo())
        self.fft_combo.currentIndexChanged.connect(
            lambda _i: self.graph.set_fft_channel(str(self.fft_combo.currentData() or ""))
        )
        self._sync_fft_combo()
        self.record_btn.clicked.connect(lambda: self.record_toggled.emit(not self._recording))

    # ---- 表示 ----
    def view(self) -> str:
        return str(self.view_seg.current())

    def set_view(self, key: str) -> None:
        self.view_seg.set_current(key)
        is_graph = key == "graph"
        self.stack.setCurrentWidget(self._views.get(key, self.view3d))
        self._graph_controls.setVisible(is_graph)
        self.pause_btn.setVisible(is_graph)

    def add_view(self, key: str, title: str, widget: QWidget) -> None:
        """`[グラフ | 3D | …]` に表示を足す(プラグインの `add_monitor_view`)。"""
        self._views[key] = widget
        self.stack.addWidget(widget)
        self._view_titles.append((key, title))
        self.view_seg.set_items(self._view_titles, current=self.view())

    def set_preset(self, key: str) -> None:
        """表示プリセットを選ぶ(設定ページの既定値)。"""
        i = self.preset_combo.findData(key)
        if i >= 0:
            self.preset_combo.setCurrentIndex(i)

    def set_window(self, seconds: float) -> None:
        """時間幅を選ぶ(設定ページの既定値)。選択肢に無ければ近い方へ。"""
        best = min(range(self.window_combo.count()),
                   key=lambda i: abs(float(self.window_combo.itemData(i)) - seconds))
        self.window_combo.setCurrentIndex(best)

    def _sync_fft_combo(self) -> None:
        """FFT を選んだときだけ「対象」を出し、チャネル一覧を現在のモータに合わせる。"""
        is_fft = str(self.preset_combo.currentData()) == FFT_KEY
        self.fft_combo.blockSignals(True)
        self.fft_combo.clear()
        for name, label in self.graph.fft_channels():
            self.fft_combo.addItem(label, name)
        i = self.fft_combo.findData(self.graph.fft_channel())
        self.fft_combo.setCurrentIndex(max(i, 0))
        self.fft_combo.blockSignals(False)
        self.fft_label.setVisible(is_fft)
        self.fft_combo.setVisible(is_fft)

    def _on_trigger(self, state: str) -> None:
        """トリガの状態(待機中・成立)をボタンのアイコンとツールチップで示す。

        文字は変えない(ツールバーの最小幅を増やさない)。詳しい文言はグラフ下の状態行に出る。"""
        key, icon_name = {
            "armed": ("button_armed", "circle-dot"),
            "fired": ("button_fired", "check"),
            "captured": ("button_fired", "check"),
        }.get(state, ("button_tip", "activity"))
        self.analysis_btn.setProperty("trigger", state)
        self.analysis_btn.set_icon_name(icon_name)
        self.analysis_btn.set_base_tooltip(tr(f"operate.analysis.{key}"))

    def _on_paused(self, on: bool) -> None:
        self.pause_btn.setText(tr("operate.monitor.resume" if on else "operate.monitor.pause"))
        self.pause_btn.set_icon_name("play" if on else "pause")

    # ---- 記録 ----
    def set_recording(self, on: bool, elapsed_text: str = "") -> None:
        """記録中は「● 記録中 00:12」(赤い点はこの 1 か所だけの例外。ui_design.md §5.1)。"""
        self._recording = on
        if on:
            self.record_btn.setText(tr("operate.monitor.recording", time=elapsed_text or "00:00"))
            self.record_btn.set_base_tooltip(tr("operate.monitor.record_stop_tip"))
        else:
            self.record_btn.setText(tr("operate.monitor.record"))
            self.record_btn.set_base_tooltip(tr("operate.monitor.record_tip"))
        self._tint_record_icon()

    def _tint_record_icon(self, _name: str = "") -> None:
        self.record_btn.set_icon_name("circle-dot")  # 基底の着色(通常色)に戻す
        if self._recording:
            self.record_btn.setIcon(icon("circle-dot", "critical"))

    def is_recording(self) -> bool:
        return self._recording

