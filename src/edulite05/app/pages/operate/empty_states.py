"""運転ページの空状態(ui_design.md §5.1 未接続時)。

モニタ領域の中央に、いまの状況と「次に押すもの」を 1 つだけ出す:
- offline   … 「アダプタが接続されていません」+ アダプタ/ポート選択 + [接続](強調)
- lost      … 「アダプタとの通信が途絶えました」+ [再接続]
- connecting / scanning … 進行中の説明(ボタンなし)
- none      … 「モータが見つかりません」+ 確認項目(電源・配線・終端抵抗・ID)+ [再スキャン]
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.pages.operate.connection_popover import ConnectionForm
from edulite05.app.ui.empty import EmptyState
from edulite05.app.ui.label import Label
from edulite05.app.ui.tokens import SP_ICON

MODES = ("offline", "lost", "connecting", "scanning", "none")
_FORM_W = 320


class ConnectionStateView(QWidget):
    """未接続・通信断・接続中・スキャン中・モータなしの空状態。操作は信号で page へ伝える。"""

    connect_requested = Signal(dict)  # ConnectionForm.config()
    reconnect_requested = Signal()
    rescan_requested = Signal()

    def __init__(
        self,
        bridge: Bridge,
        parent: QWidget | None = None,
        *,
        host_id: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = "offline"
        self._form_reason = ""  # 接続設定が足りないときの理由(ログ未選択など)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.empty = EmptyState(
            "plug", tr("operate.empty.offline_title"), tr("operate.empty.offline_desc"),
            action=tr("operate.empty.connect"),
        )
        lay.addWidget(self.empty)
        self.form = ConnectionForm(bridge, host_id=host_id)
        self.form.setFixedWidth(_FORM_W)
        self.empty.extra_layout.addWidget(self.form, 0, Qt.AlignmentFlag.AlignHCenter)
        self.checklist = QWidget()
        cl = QVBoxLayout(self.checklist)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(SP_ICON)
        for key in ("power", "wiring", "termination", "id"):
            cl.addWidget(Label("・" + tr(f"operate.empty.check_{key}"), "secondary"))
        self.empty.extra_layout.addWidget(self.checklist, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty.action_clicked.connect(self._on_action)
        self.form.validity_changed.connect(self._on_form_validity)
        self._form_reason = self.form.invalid_reason()
        self.set_mode("offline")

    @property
    def mode(self) -> str:
        return self._mode

    def action_button(self):  # noqa: ANN201
        return self.empty.action_button

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(mode)
        self._mode = mode
        e = self.empty
        btn = e.action_button
        assert btn is not None
        show_form = mode == "offline"
        self.form.setVisible(show_form)
        self.checklist.setVisible(mode == "none")
        if mode == "offline":
            e.set_icon_name("plug")
            e.set_texts(tr("operate.empty.offline_title"), tr("operate.empty.offline_desc"))
            btn.setText(tr("operate.empty.connect"))
        elif mode == "lost":
            e.set_icon_name("unplug")
            e.set_texts(tr("operate.empty.lost_title"), tr("operate.empty.lost_desc"))
            btn.setText(tr("operate.empty.reconnect"))
        elif mode == "connecting":
            e.set_icon_name("plug")
            e.set_texts(tr("operate.empty.connecting_title"), tr("operate.empty.connecting_desc"))
        elif mode == "scanning":
            e.set_icon_name("search")
            e.set_texts(tr("operate.empty.scanning_title"), tr("operate.empty.scanning_desc"))
        else:  # none
            e.set_icon_name("search")
            e.set_texts(tr("operate.empty.none_title"), tr("operate.empty.none_desc"))
            btn.setText(tr("operate.empty.rescan"))
        btn.setVisible(mode in ("offline", "lost", "none"))
        self._apply_action_available()

    def _on_form_validity(self, _ok: bool, reason: str) -> None:
        self._form_reason = reason
        self._apply_action_available()

    def _apply_action_available(self) -> None:
        """未接続の [接続] は、設定が足りない間(ログ未選択など)押せなくして理由を言う。"""
        btn = self.empty.action_button
        if btn is not None:
            reason = self._form_reason if self._mode == "offline" else ""
            btn.set_available(not reason, reason)

    def _on_action(self) -> None:
        if self._mode == "offline":
            self.connect_requested.emit(self.form.config())
        elif self._mode == "lost":
            self.reconnect_requested.emit()
        elif self._mode == "none":
            self.rescan_requested.emit()
