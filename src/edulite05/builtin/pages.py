"""組込みのナビページ(運転・パラメータ・診断・保守・設定)を `UiRegistry.add_page` で登録する。

以前 `app/pages/__init__.py` の `PAGES` に書いていた一覧と同じ内容(ID・名前・アイコン・並び)。
ページのクラスは最初に呼ばれたとき import する(`LazyClassFactory`)。
"""

from __future__ import annotations

from typing import ClassVar

from edulite05.plugin_api import LazyClassFactory, Plugin, PluginContext

# (id, title の tr キー, Lucide アイコン, モジュール, クラス, order, bottom)
BUILTIN_PAGES: tuple[tuple[str, str, str, str, str, int, bool], ...] = (
    ("operate", "nav.operate", "gauge", "edulite05.app.pages.operate", "OperatePage", 10, False),
    (
        "params", "nav.params", "sliders-horizontal", "edulite05.app.pages.params", "ParamsPage",
        20, False,
    ),
    (
        "diagnostics", "nav.diagnostics", "stethoscope", "edulite05.app.pages.diagnostics",
        "DiagnosticsPage", 30, False,
    ),
    (
        "maintenance", "nav.maintenance", "wrench", "edulite05.app.pages.maintenance",
        "MaintenancePage", 40, False,
    ),
    (
        "settings", "nav.settings", "settings", "edulite05.app.pages.settings", "SettingsPage",
        90, True,
    ),
)


class BuiltinPages(Plugin):
    id: ClassVar[str] = "edulite05.builtin.pages"
    name: ClassVar[str] = "組込みページ"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "運転・パラメータ・診断・保守・設定"

    def activate(self, ctx: PluginContext) -> None:
        if ctx.ui is None:
            return
        for pid, title, icon, module, cls, order, bottom in BUILTIN_PAGES:
            ctx.ui.add_page(
                pid, title, icon, LazyClassFactory(module, cls), order, bottom=bottom
            )
