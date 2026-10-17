"""診断ページ(ui_design.md §5.3): 故障 / イベントログ / CAN フレーム / 通信統計。

ナビの「診断」に付く未確認の赤い点は、このページが決める:
- `has_unseen_faults()` … 発生中の故障のうち、故障ビューを開いて見たことがないものがあるか
- `unseen_changed(bool)` … 上の値が変わったとき(シェルは `nav.set_badge("diagnostics", on)` する)
故障が解消すると「見た」記録も消えるので、再発すれば再び点が付く。

`show_view("log")` などで開くビューを指定できる(イベント行 → イベントログなど)。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QStackedWidget, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.pages.base import PageBase, ShellApi
from edulite05.app.pages.diagnostics.faults_view import FaultsView
from edulite05.app.pages.diagnostics.frames_view import FramesView
from edulite05.app.pages.diagnostics.log_view import LogView
from edulite05.app.pages.diagnostics.stats_view import StatsView
from edulite05.app.ui import Segmented
from edulite05.app.ui.tokens import SP_PAD, SP_RELATED, SP_SECTION
from edulite05.app.ui.util import hbox, vbox

VIEWS = ("faults", "log", "frames", "stats")


class DiagnosticsPage(PageBase):
    unseen_changed = Signal(bool)

    def __init__(
        self, bridge: Bridge, shell: ShellApi | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(bridge, shell, parent)
        self._active = False
        self._seen: set[tuple[int, str]] = set()
        self._unseen = False

        lay = vbox(self, margins=(SP_SECTION, SP_PAD, SP_SECTION, SP_PAD), spacing=SP_PAD)
        top = hbox(spacing=SP_RELATED)
        self.seg = Segmented([(k, tr(f"diag.view_{k}")) for k in VIEWS], current="faults")
        top.addWidget(self.seg)
        top.addStretch(1)
        lay.addLayout(top)

        self.stack = QStackedWidget()
        self.faults_view = FaultsView(bridge)
        self.log_view = LogView(bridge)
        self.frames_view = FramesView(bridge)
        self.stats_view = StatsView(bridge)
        self._views: dict[str, QWidget] = {
            "faults": self.faults_view,
            "log": self.log_view,
            "frames": self.frames_view,
            "stats": self.stats_view,
        }
        for k in VIEWS:
            self.stack.addWidget(self._views[k])
        lay.addWidget(self.stack, 1)

        self.seg.current_changed.connect(self._on_view)
        self.faults_view.active_changed.connect(self._on_active_changed)
        self._on_active_changed()

    # ================================================================ 公開
    def current_view(self) -> str:
        return self.seg.current() or "faults"

    def show_view(self, key: str) -> None:
        """開くビューを指定する(faults / log / frames / stats)。"""
        if key in self._views:
            self.seg.set_current(key)

    def has_unseen_faults(self) -> bool:
        return self._unseen

    def view_widget(self, key: str) -> QWidget:
        return self._views[key]

    # ================================================================ ページの出入り
    def activated(self) -> None:
        self._active = True
        if self.faults_view.active_faults() - self._seen:
            self.show_view("faults")  # 未確認の故障があれば故障ビューから見せる
        self._mark_seen()

    def deactivated(self) -> None:
        self._active = False

    # ================================================================ 内部
    def _on_view(self, key: str) -> None:
        self.stack.setCurrentWidget(self._views[key])
        self._mark_seen()

    def _on_active_changed(self) -> None:
        active = self.faults_view.active_faults()
        self._seen &= active  # 解消した故障の記録は捨てる(再発で再び未確認になる)
        n = len(active)
        self.seg.set_item_text(
            "faults", tr("diag.view_faults_n", n=n) if n else tr("diag.view_faults")
        )
        self._mark_seen()

    def _mark_seen(self) -> None:
        """故障ビューが表示されていれば、発生中の故障を確認済みにする。"""
        active = self.faults_view.active_faults()
        if self._active and self.current_view() == "faults":
            self._seen |= active
        unseen = bool(active - self._seen)
        if unseen != self._unseen:
            self._unseen = unseen
            self.unseen_changed.emit(unseen)


__all__ = ["DiagnosticsPage", "VIEWS"]
