"""情報ページ(サンプル)。`add_page` で登録する小さなページの作り方の例。

ページは `PageBase` を継承し、`Bridge` と `ShellApi`(ページ移動・トースト)だけを使う。
色・寸法・文字サイズは直書きせず `edulite05.app.ui`(部品・トークン)と `boxed_list` を使い、
各区画の強調ボタン(accent)は 1 つ以下にする(ui_design.md)。
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.pages.base import PageBase, ShellApi
from edulite05.app.pages.boxed_list import BoxedList, GroupHeader, ListRow, centered_scroll
from edulite05.app.ui.button import Button
from edulite05.app.ui.label import Label
from edulite05.app.ui.tokens import SP_GROUP, SP_RELATED, SP_SECTION


class TrianglePage(PageBase):
    def __init__(
        self, bridge: Bridge, shell: ShellApi | None = None, parent: QWidget | None = None,
        *, plugin_name: str = "", version: str = "",
    ) -> None:
        super().__init__(bridge, shell, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(SP_SECTION, SP_SECTION, SP_SECTION, 0)
        root.setSpacing(SP_GROUP)
        scroll, col = centered_scroll()
        root.addWidget(scroll, 1)
        self.heading = Label(tr("三角波の試験信号"), "title")
        col.addWidget(self.heading)
        col.addWidget(Label(
            tr("プラグインのサンプルです。運転ページのモード選択に「三角波」を追加します。"),
            "secondary", wrap=True,
        ))

        col.addSpacing(SP_SECTION - SP_RELATED)
        col.addWidget(GroupHeader(tr("使い方")))
        box = BoxedList()
        col.addWidget(box)
        self.open_button = Button(tr("運転ページを開く"), "standard")
        self.open_button.clicked.connect(self._open_operate)
        box.add_row(ListRow(
            tr("モードを選ぶ"),
            tr("運転ページの「その他 ▾」から「三角波」を選び、"
               "目標(対象・振幅・周波数)を入れます。"),
            trailing=self.open_button,
        ))
        box.add_row(ListRow(
            tr("安全"),
            tr("指令は他のモードと同じく SafetyGuard を通り、"
               "出力段階(LOW/HIGH)の上限で制限されます。"),
        ))
        box.add_row(ListRow(
            tr("プラグイン"),
            tr("{name} {version}(plugins/example_triangle)。"
               "設定ページで有効・無効を切り替えられます。",
               name=tr(plugin_name), version=version),
        ))
        col.addStretch(1)

    def _open_operate(self) -> None:
        if self.shell is not None:
            self.shell.navigate("operate")
