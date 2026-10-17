"""プラグイン「スクリプト」: Python コンソールとシーケンス(YAML)の実行ページ(phase4_5.md M)。

プラグインにした理由(docs/design/plugin_guide.md に沿う):
- 任意の Python を実行できる機能なので、利用者が**設定ページの「プラグイン」で無効にできる**ように
  `plugins/` に置く(組込みは無効にできない)。設定画面で無効にすると
  `activate` を呼ばず page.py も読み込まない(plugin.py は読み込む。plugin_guide.md §1)。
- 他の機能(運転・パラメータ・診断・保守)には依存しない独立した追加ページで、プラグイン API だけで
  成り立つ(`add_page`)ことの実例にもなる。
- 実行の中身(シーケンスの検証・実行、コンソールの実行エンジン)は **Qt を使わない core**
  (`edulite05.core.sequence` / `edulite05.core.scripting`)にあり、CLI(`run-sequence`)とテストが
  同じものを使う。このプラグインは画面とその配線だけを持つ。

モータへの指令はすべて `MotorService`(`ctx.service`)経由で、
SafetyGuard が出力段階の上限を掛ける。生フレームは送らない。
"""

from __future__ import annotations

import weakref
from typing import ClassVar

from edulite05.plugin_api import Plugin, PluginContext

VERSION = "0.1.0"
PAGE_ID = "scripting"
PAGE_ORDER = 50  # 保守(40)の次・設定(90)の前。既存の Ctrl+数字 を動かさない


class Scripting(Plugin):
    id: ClassVar[str] = "scripting"
    name: ClassVar[str] = "スクリプト"
    version: ClassVar[str] = VERSION
    requires_gui: ClassVar[bool] = True
    description: ClassVar[str] = "Python コンソールとシーケンス(YAML)でモータを動かすページ"

    def __init__(self) -> None:
        self._pages: list[weakref.ref] = []

    def activate(self, ctx: PluginContext) -> None:
        if ctx.ui is None:
            return
        root = ctx.project_root

        def make_page(bridge, shell):  # noqa: ANN001, ANN202 - GUI のときだけ呼ばれる
            from pathlib import Path

            from .page import ScriptingPage

            page = ScriptingPage(
                bridge, shell,
                project_root=Path(root) if root is not None else Path.cwd(),
                history=list(ctx.settings.get("history", []) or []),
                save_history=lambda h: ctx.settings.set("history", h[-50:]),
            )
            self._pages.append(weakref.ref(page))
            return page

        ctx.ui.add_page(PAGE_ID, "script.nav", "terminal", make_page, order=PAGE_ORDER)

    def deactivate(self) -> None:
        for ref in self._pages:
            page = ref()
            if page is not None:
                try:
                    page.shutdown()
                except RuntimeError:  # Qt オブジェクトが先に破棄されていた
                    pass
        self._pages.clear()


PLUGIN = Scripting()
