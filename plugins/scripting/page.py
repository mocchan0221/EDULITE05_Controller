"""「スクリプト」ページ: [コンソール | シーケンス] の 2 つの区画(ui_design.md v2)。

- 区画ごとに主ボタンは 1 つ(コンソール = 実行 / 中断、シーケンス = 実行 / 中断)。両方を同時には
  見せない(セグメントで切り替える)ので、強調色は常に画面に 1 つ。
- 上部に SafetyGuard の注記(InfoBar info。閉じられない常設の説明)。
- 非常停止(`bridge.estop_changed`)でスクリプトとシーケンスを中断する。非常停止そのものは
  シェル(Pause / Esc / ヘッダーのボタン)が `MotorService.estop()` を同期で呼ぶので、
  スクリプトが動いていても常に効く。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.pages.base import PageBase, ShellApi
from edulite05.app.ui.infobar import InfoBar
from edulite05.app.ui.label import Label
from edulite05.app.ui.segmented import Segmented
from edulite05.app.ui.tokens import SP_GROUP, SP_PAD, SP_SECTION
from edulite05.app.ui.util import hbox

from .console import ConsoleView
from .sequence_view import SequenceView


class ScriptingPage(PageBase):
    def __init__(
        self,
        bridge: Bridge,
        shell: ShellApi | None = None,
        parent: QWidget | None = None,
        *,
        project_root: Path,
        history: list[str] | None = None,
        save_history=None,  # noqa: ANN001 - Callable[[list[str]], None]
    ) -> None:
        super().__init__(bridge, shell, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(SP_SECTION, SP_SECTION, SP_SECTION, SP_PAD)
        root.setSpacing(SP_GROUP)

        head = hbox(spacing=SP_GROUP)
        self.heading = Label(tr("script.title"), "title")
        head.addWidget(self.heading)
        head.addStretch(1)
        self.view_switch = Segmented(
            [("console", tr("script.view_console")), ("sequence", tr("script.view_sequence"))],
            current="console",
        )
        head.addWidget(self.view_switch)
        root.addLayout(head)
        root.addWidget(Label(tr("script.subtitle"), "secondary", wrap=True))

        self.safety_note = InfoBar("info", tr("script.safety_title"), tr("script.safety_text"))
        root.addWidget(self.safety_note)

        self.stack = QStackedWidget()
        self.console = ConsoleView(
            history=history, save_history=save_history, project_root=project_root
        )
        self.sequence = SequenceView(
            bridge, project_root=project_root,
            console_running=lambda: self.console.running,
            on_toast=self._toast,
        )
        self.stack.addWidget(self.console)
        self.stack.addWidget(self.sequence)
        root.addWidget(self.stack, 1)

        self.view_switch.selected.connect(self.show_view)
        bridge.estop_changed.connect(self._on_estop)
        bridge.motors_changed.connect(self._on_motors_changed)

    # ============================================================ 公開
    def show_view(self, view: str) -> None:
        """"console" / "sequence"(シェルの navigate(view=…) からも呼ばれる)。"""
        if view not in ("console", "sequence"):
            return
        self.view_switch.set_current(view)
        self.stack.setCurrentWidget(self.console if view == "console" else self.sequence)
        if view == "sequence":
            self.sequence.refresh_files()
            self.sequence.refresh_motors()
        else:
            self.console.input.setFocus()

    def current_view(self) -> str:
        return "console" if self.stack.currentWidget() is self.console else "sequence"

    def shutdown(self) -> None:
        self.console.shutdown()
        self.sequence.shutdown()

    # ============================================================ PageBase
    def activated(self) -> None:
        self.console.refresh_status()
        if self.current_view() == "sequence":
            self.sequence.refresh_files()
        else:
            self.console.input.setFocus()

    # ============================================================ 内部
    def _toast(self, text: str, kind: str) -> None:
        if self.shell is not None:
            self.shell.toast(text, kind)

    def _on_estop(self, latched: bool) -> None:
        if not latched:
            return
        if self.console.running:
            self.console.abort()
        if self.sequence.running:
            self.sequence.abort()

    def _on_motors_changed(self, _motors: list) -> None:
        self.sequence.refresh_motors()
