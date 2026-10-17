"""ナビの並びとページ切替ショートカットの順序(ui_design.md §4・sequences.md)。

同梱プラグイン(スクリプト・サンプルの三角波)を両方読み込んだとき、
Ctrl+5 = スクリプト、サンプルのページはその後ろ(Ctrl+6)・設定の前に並ぶ。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.main_window import MainWindow
from edulite05.app.plugin_host import create_manager
from edulite05.app.ui.style import theme_manager

ROOT = Path(__file__).resolve().parents[2]


def test_scripting_is_ctrl5_and_sample_page_follows(qtbot, tmp_path: Path) -> None:
    for name in ("example_triangle", "scripting"):
        shutil.copytree(
            ROOT / "plugins" / name, tmp_path / "plugins" / name,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    mgr = create_manager(None, project_root=tmp_path)
    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0)
    win = MainWindow(bridge, settings_path=None, enable_3d=False, plugins=mgr)
    qtbot.addWidget(win)
    win.show()
    bridge.start()
    try:
        ids = win.nav.item_ids()
        assert ids == [
            "operate", "params", "diagnostics", "maintenance", "scripting",
            "example_triangle", "settings",
        ]
        # Ctrl+N はナビの順(Ctrl+5 = スクリプト、Ctrl+6 = サンプル)
        assert len(win.page_shortcuts) == len(ids)
        win.page_shortcuts[4].activated.emit()
        assert win.current_page_id() == "scripting"
        win.page_shortcuts[5].activated.emit()
        assert win.current_page_id() == "example_triangle"
    finally:
        win.close()
        bridge.shutdown()
