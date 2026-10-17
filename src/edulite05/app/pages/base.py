"""ページの共通基底とシェルとの接点。

ページは `PageBase` を継承し、`Bridge` と `ShellApi`(シェルへの要求)だけを使う。
色・寸法・文字サイズは直書きせず `edulite05.app.ui`(トークン・部品)から取る。
"""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QWidget

from edulite05.app.bridge import Bridge


class ShellApi(Protocol):
    """ページがシェル(MainWindow)に頼めること。"""

    def navigate(self, page_id: str, view: str | None = None) -> None:
        """別のページへ移る(例: イベント行 → 診断ページ)。`view` はそのページの `show_view(view)`
        へ渡す表示の指定(診断の faults / log / frames / stats など)。"""

    def settings(self) -> QSettings | None:
        """UI 状態の保存先(`layout/` 以下に区画の幅などを保存する)。保存しないなら None。"""

    def toast(self, text: str, kind: str = "success") -> None:
        """一時的な結果通知(3 s)。kind: success / info / caution。故障は toast にしない。"""

    def open_connection(self) -> None:
        """ヘッダーの接続ポップオーバーを開く(未接続の空状態の [接続] から)。"""


class PageBase(QWidget):
    """ナビのページ 1 枚。`activated()` / `deactivated()` はページ切替時にシェルが呼ぶ。"""

    def __init__(
        self, bridge: Bridge, shell: ShellApi | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.shell = shell
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("surface", "layer")

    def activated(self) -> None:  # noqa: B027
        """このページが表示された(重い更新の再開など)。"""

    def deactivated(self) -> None:  # noqa: B027
        """このページが隠れた(重い更新の停止など)。"""
