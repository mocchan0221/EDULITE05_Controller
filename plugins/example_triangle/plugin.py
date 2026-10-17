"""サンプルプラグイン: 三角波の試験信号(コア側 Controller + 運転モード)と情報ページ。

`plugins/<name>/plugin.py` に置くだけで読み込まれる。このファイルは、プラグインの最小構成
(Plugin のサブクラス + `activate`)と、コア側・UI 側それぞれの登録の仕方の見本。
詳しくは docs/design/plugin_guide.md。
"""

from __future__ import annotations

from typing import ClassVar

from edulite05.plugin_api import GROUP_OTHER, Plugin, PluginContext

from .triangle import TriangleTest

VERSION = "0.1.0"
# スクリプト(50)の次・設定(90)の前。後から足すページはナビの順に並ぶので、
# サンプルがスクリプトの Ctrl+5 を奪わないよう 50 より大きくする
PAGE_ORDER = 60


def _make_page(bridge, shell):  # noqa: ANN001, ANN202 - GUI のときだけ呼ばれる(Qt はここで import)
    from .page import TrianglePage

    return TrianglePage(bridge, shell, plugin_name=ExampleTriangle.name, version=VERSION)


class ExampleTriangle(Plugin):
    id: ClassVar[str] = "example_triangle"
    name: ClassVar[str] = "三角波テスト信号(サンプル)"
    version: ClassVar[str] = VERSION
    requires_gui: ClassVar[bool] = False  # Controller だけなら CLI でも可
    description: ClassVar[str] = "運転モード「三角波」と情報ページを追加するサンプル"

    def activate(self, ctx: PluginContext) -> None:
        # 1) コア側: Controller を登録(CLI でも有効)。指令は SafetyGuard を通る
        ctx.core.register_controller(TriangleTest)
        if ctx.ui is None:  # CLI(GUI なし)のときは UI を足さない
            return
        # 2) UI: 運転ページの「その他 ▾」にモードを追加(Params から入力フォームを自動生成)
        ctx.ui.add_mode(
            TriangleTest, GROUP_OTHER,
            title="三角波",
            description="目標の量に三角波を出します",
            info="対象(位置・速度・電流・トルク)に、振幅と周波数を指定した三角波を出します。",
        )
        # 3) UI: ナビに小さな情報ページを追加(スクリプトの次・設定の上に並ぶ)
        ctx.ui.add_page(
            "example_triangle", "三角波", "activity", _make_page, order=PAGE_ORDER
        )


PLUGIN = ExampleTriangle()
