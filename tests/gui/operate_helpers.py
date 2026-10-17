"""運転ページのテスト用の共通部品(フィクスチャ・疑似シェル)。"""

from __future__ import annotations

from pathlib import Path

from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.pages.operate import OperatePage
from edulite05.app.ui.style import theme_manager


class FakeShell:
    """ShellApi の代わり。呼ばれたことを記録する。"""

    def __init__(self) -> None:
        self.navigated: list[str] = []
        self.navigated_views: list[str | None] = []
        self.toasts: list[tuple[str, str]] = []
        self.opened = 0

    def navigate(self, page_id: str, view: str | None = None) -> None:
        self.navigated.append(page_id)
        self.navigated_views.append(view)

    def toast(self, text: str, kind: str = "success") -> None:
        self.toasts.append((text, kind))

    def open_connection(self) -> None:
        self.opened += 1


def make_page(qtbot, tmp_path: Path, *, start_offline: bool = False, size=(1464, 800)):
    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0, start_offline=start_offline)
    shell = FakeShell()
    page = OperatePage(bridge, shell, record_dir=tmp_path)
    qtbot.addWidget(page)
    page.resize(*size)
    page.show()
    bridge.start()
    qtbot.waitExposed(page)
    return page, bridge, shell


def teardown(page, bridge) -> None:
    page.shutdown()
    bridge.shutdown()
