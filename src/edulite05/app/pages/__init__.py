"""ページの共通基底(ナビの項目は `UiRegistry.add_page` で登録する)。

ナビのページ一覧は `edulite05.builtin.pages`(組込み)と、プラグインの `ctx.ui.add_page(...)` の
登録内容から、シェル(`MainWindow`)が `PluginManager.ui.pages()` で受け取って組み立てる。
各ページは `PageBase` のサブクラス。
"""

from __future__ import annotations

from edulite05.app.pages.base import PageBase, ShellApi

__all__ = ["PageBase", "ShellApi"]
