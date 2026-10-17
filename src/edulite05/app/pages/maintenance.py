"""保守ページ(ui_design.md §5.4)。

GNOME の boxed list 形式で 2 グループ:
- よく使う設定: ゼロ点を設定 / 主動上報 / 通信タイムアウト
- 注意が必要な操作(見出しに ⚠): CAN ID を変更 / CAN ボーレートを変更 / プロトコルを切り替え /
  CANopen・MIT プロトコルから戻す

各行はタイトル + 1 行説明 + 影響タグ + `[設定…]`。ダイアログ(`app/ui/dialogs/maintenance.py`)で
目的・影響・前提を確認してから `Bridge.run_tool` を呼ぶ。操作対象は選択中のモータ
(ヘッダーのモータチップ)で、その `ui_state` が `disabled` のときだけ実行できる。
そうでないとき `[設定…]` は押せず、理由をツールチップとページ上部に出す(原則 6)。

例外が「CANopen・MIT プロトコルから戻す」(`needs_motor=False`)。この状態のモータは独自プロトコルの
スキャンで見つからないことが多いので、選択中のモータを要求しない。接続中で、有効・運転中のモータが無く、
非常停止がラッチされていないときに押せる。対象のモータ ID はダイアログで入力する。
項目は `MaintenancePage.add_item(MaintenanceItem(...))` で足せる(ui_design.md §10 の
`add_maintenance_item`)。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.pages.base import PageBase, ShellApi
from edulite05.app.pages.boxed_list import BoxedList, GroupHeader, ListRow, centered_scroll
from edulite05.app.ui.badge import StatusBadge
from edulite05.app.ui.button import Button
from edulite05.app.ui.dialogs.maintenance import (
    ActiveReportDialog,
    CanTimeoutDialog,
    MotorTarget,
    RestoreProtocolDialog,
    SetBaudDialog,
    SetCanIdDialog,
    SetZeroDialog,
    SwitchProtocolDialog,
    ToolDialog,
)
from edulite05.app.ui.infobar import InfoBar
from edulite05.app.ui.label import Label
from edulite05.app.ui.tokens import SP_GROUP, SP_RELATED, SP_SECTION

GROUP_COMMON = "common"
GROUP_CAUTION = "caution"

DialogFactory = Callable[[QWidget, MotorTarget | None], ToolDialog]


@dataclass(frozen=True, slots=True)
class MaintenanceItem:
    """保守ページの 1 行。`dialog_factory(parent, motor)` が `ToolDialog` を返す。"""

    key: str
    group: str  # GROUP_COMMON / GROUP_CAUTION
    title: str
    description: str
    tags: tuple[str, ...]
    dialog_factory: DialogFactory
    needs_motor: bool = True  # False なら選択中のモータを要求しない(対象はダイアログで入力する)


def builtin_items() -> list[MaintenanceItem]:
    c, w = GROUP_COMMON, GROUP_CAUTION
    return [
        MaintenanceItem(
            "set_zero", c, tr("maint.zero.title"), tr("maint.zero.desc"),
            (tr("maint.tag_reference"),), SetZeroDialog,
        ),
        MaintenanceItem(
            "active_report", c, tr("maint.report.title"), tr("maint.report.desc"),
            (tr("maint.tag_bandwidth"),), ActiveReportDialog,
        ),
        MaintenanceItem(
            "can_timeout", c, tr("maint.timeout.title"), tr("maint.timeout.desc"),
            (tr("maint.tag_temporary"),), CanTimeoutDialog,
        ),
        MaintenanceItem(
            "set_can_id", w, tr("maint.set_id.title"), tr("maint.set_id.desc"),
            (tr("maint.tag_new_id"), tr("maint.tag_save")), SetCanIdDialog,
        ),
        MaintenanceItem(
            "set_baud", w, tr("maint.baud.title"), tr("maint.baud.desc"),
            (tr("maint.tag_reboot"), tr("maint.tag_comm_loss")), SetBaudDialog,
        ),
        MaintenanceItem(
            "switch_protocol", w, tr("maint.protocol.title"), tr("maint.protocol.desc"),
            (tr("maint.tag_reboot"), tr("maint.tag_comm_loss")), SwitchProtocolDialog,
        ),
        MaintenanceItem(
            "restore_protocol", w, tr("maint.restore.title"), tr("maint.restore.desc"),
            (tr("maint.tag_no_scan"), tr("maint.tag_reboot")), RestoreProtocolDialog,
            needs_motor=False,
        ),
    ]


class MaintenanceRow(ListRow):
    """保守 1 行。右端が `[設定…]`。"""

    def __init__(self, item: MaintenanceItem) -> None:
        self.item = item
        self.button = Button(tr("maint.configure"), "standard")
        super().__init__(item.title, item.description, tags=item.tags, trailing=self.button)
        self.setObjectName(f"maint_{item.key}")
        self.button.setObjectName(f"maint_btn_{item.key}")
        self.button.setAccessibleName(f"{item.title} {tr('maint.configure')}")


class MaintenancePage(PageBase):
    """保守ページ。選択中のモータが停止中(disabled)のときだけ各行の `[設定…]` が押せる。"""

    def __init__(
        self, bridge: Bridge, shell: ShellApi | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(bridge, shell, parent)
        self._items: list[MaintenanceItem] = []
        self.rows: dict[str, MaintenanceRow] = {}
        self._sig: tuple = ()

        root = QVBoxLayout(self)
        root.setContentsMargins(SP_SECTION, SP_SECTION, SP_SECTION, 0)
        root.setSpacing(SP_GROUP)
        self._scroll, self._column = centered_scroll()
        root.addWidget(self._scroll, 1)
        col = self._column

        # ---- 見出し行: 保守 …… 対象: ID1 EL05 #1 [停止中] ----
        head = QHBoxLayout()
        head.setSpacing(SP_RELATED)
        self.heading = Label(tr("nav.maintenance"), "title")
        head.addWidget(self.heading)
        head.addStretch(1)
        self.target_caption = Label(tr("maint.target"), "caption")
        self.target_label = Label("", "heading")
        self.target_badge = StatusBadge.for_ui_state("offline")
        head.addWidget(self.target_caption)
        head.addWidget(self.target_label)
        head.addWidget(self.target_badge)
        col.addLayout(head)
        self.intro = Label(tr("maint.intro"), "secondary", wrap=True)
        col.addWidget(self.intro)

        # ---- いまは実行できない理由(押せなくして理由を言う。説明文の代わりに出す) ----
        self.lock_bar = InfoBar("info", tr("maint.locked"), "")
        self.lock_bar.setVisible(False)
        col.addWidget(self.lock_bar)
        col.addSpacing(SP_GROUP)

        # ---- グループ ----
        self._groups: dict[str, BoxedList] = {}
        self._build_groups()
        for item in builtin_items():
            self.add_item(item)
        self._column.addStretch(1)

        bridge.selected_motor_changed.connect(self._refresh)
        bridge.motors_changed.connect(self._refresh)
        bridge.state_updated.connect(self._refresh)
        bridge.estop_changed.connect(self._refresh)
        bridge.connection_changed.connect(self._refresh)
        self._refresh()

    # ================================================================ 構築
    def _build_groups(self) -> None:
        specs = (
            (GROUP_COMMON, tr("maint.group_common"), None, None),
            (GROUP_CAUTION, tr("maint.group_caution"), "triangle-alert", "caution"),
        )
        for i, (key, title, icon, tone) in enumerate(specs):
            if i:
                self._column.addSpacing(SP_SECTION - SP_RELATED)  # グループ間 > グループ内
            box = BoxedList()
            self._column.addWidget(GroupHeader(title, icon=icon, tone=tone))
            self._column.addWidget(box)
            self._groups[key] = box

    def add_item(self, item: MaintenanceItem) -> MaintenanceRow:
        """行を足す(プラグインの `add_maintenance_item` もここへ)。"""
        row = MaintenanceRow(item)
        row.button.clicked.connect(lambda _c=False, k=item.key: self.run_item(k))
        self._groups[item.group].add_row(row)
        self._items.append(item)
        self.rows[item.key] = row
        self._apply_availability()
        return row

    # ================================================================ 対象と実行可否
    def target(self) -> MotorTarget | None:
        """選択中のモータ(無ければ None)。"""
        mid = self.bridge.selected_motor()
        info = next((m for m in self.bridge.motors() if m.id == mid), None)
        if info is None:
            return None
        s = self.bridge.states().get(mid)
        return MotorTarget(mid, info.name, s.ui_state if s is not None else "offline")

    def lock_reason(self, item: MaintenanceItem | None = None) -> str:
        """実行できない理由(実行できるなら空文字)。`item` が選択中のモータを要求しない行なら、
        接続・有効なモータの有無・非常停止だけを見る。"""
        if item is not None and not item.needs_motor:
            return self._general_reason()
        t = self.target()
        if t is None:
            return tr("maint.reason_no_motor")
        if self.bridge.connection_info().read_only:
            return tr("maint.reason_replay")
        if self.bridge.estop_latched():
            return tr("maint.reason_estop")
        key = {
            "offline": "maint.reason_offline",
            "enabled": "maint.reason_enabled",
            "running": "maint.reason_running",
            "fault": "maint.reason_fault",
        }.get(t.ui_state)
        return tr(key, id=t.id) if key else ""

    def _general_reason(self) -> str:
        """選択中のモータを要求しない操作の実行不可の理由。"""
        info = self.bridge.connection_info()
        if not info.connected:
            return tr("maint.reason_not_connected")
        if info.read_only:
            return tr("maint.reason_replay")
        if self.bridge.estop_latched():
            return tr("maint.reason_estop")
        if any(s.ui_state in ("enabled", "running") for s in self.bridge.states().values()):
            return tr("maint.reason_any_enabled")
        return ""

    def _refresh(self, *_args: object) -> None:
        t = self.target()
        busy = tuple(sorted(i for i, s in self.bridge.states().items()
                            if s.ui_state in ("enabled", "running")))
        info = self.bridge.connection_info()
        sig = (t, self.bridge.estop_latched(), info.connected, info.read_only, busy)
        if sig == self._sig:  # state_updated は 60 Hz。変化したときだけ更新する
            return
        self._sig = sig
        if t is None:
            self.target_label.setText("—")
            self.target_badge.set_ui_state("offline")
        else:
            self.target_label.setText(t.label)
            self.target_badge.set_ui_state(t.ui_state)
        self._apply_availability()

    def _apply_availability(self) -> None:
        reason = self.lock_reason()
        for row in self.rows.values():
            r = self.lock_reason(row.item) if not row.item.needs_motor else reason
            row.button.set_available(r == "", r)
        self.lock_bar.set_texts(tr("maint.locked"), reason)
        self.lock_bar.setVisible(reason != "")
        self.intro.setVisible(reason == "")

    # ================================================================ 実行
    def run_item(self, key: str) -> bool:
        """ダイアログで確認し、承認されたら `Bridge.run_tool`。実行したら True。"""
        item = next((i for i in self._items if i.key == key), None)
        if item is None or self.lock_reason(item):
            return False
        target = self.target()
        if item.needs_motor and target is None:
            return False
        dlg = item.dialog_factory(self.window(), target)
        if not self._exec(dlg):
            return False
        # ダイアログを開いている間に状態が変わっていたら実行しない(故障・有効化など)
        now = self.target()
        changed = self.lock_reason(item) != "" or (
            item.needs_motor and (now is None or target is None or now.id != target.id)
        )
        if changed:
            self._toast(tr("maint.refused"), "caution")
            return False
        motor_id = dlg.target_id()
        self.bridge.run_tool(dlg.tool, motor_id, dlg.tool_args())
        if dlg.reboot_required:
            self._toast(tr("maint.done_reboot", id=motor_id, title=item.title), "caution")
        else:
            self._toast(tr("maint.done", id=motor_id, title=item.title), "success")
        return True

    def _exec(self, dlg: QDialog) -> bool:
        """テストで差し替える(モーダルを開かずに結果だけ返す)。"""
        return dlg.exec() == QDialog.DialogCode.Accepted

    def _toast(self, text: str, kind: str) -> None:
        if self.shell is not None:
            self.shell.toast(text, kind)

    def activated(self) -> None:
        self._sig = ()
        self._refresh()
