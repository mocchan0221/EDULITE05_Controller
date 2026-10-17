"""「原点に設定」(機械ゼロ点の設定)を運転ページから行う流れ(ui_design.md §5.1)。

位置の読み値カードの [原点に設定] → 確認ポップオーバー「現在位置を 0 にします」→ 実行。
実行は次の順で、Bridge だけを使う:

    有効 / 運転中 … `disable([id])`(運転停止 → 減速を待って無効化。`stop_and_disable`)
        → 状態が disabled になったら
    `run_tool("set_zero", id, {})`(MotorService.set_zero。type 6 は停止中だけ効く)
        → `tool_finished` を受けて結果をトーストにする

JOG などで多回転位置が大きくなった(±4π 付近は MIT の位置保持が減衰のみになる)ときの解消にも使う。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QHBoxLayout, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.ui.button import Button
from edulite05.app.ui.label import Label
from edulite05.app.ui.popover import Popover
from edulite05.app.ui.tokens import SP_RELATED
from edulite05.app.viewmodels import MotorViewState

SETTLE_TIMEOUT_MS = 10000  # 停止 → 無効化を待つ上限(減速の待ち 0.5 s + 余裕)
RESULT_TIMEOUT_MS = 15000  # 結果通知を待つ上限(保守操作 1 件。応答の再試行を含む)
_RUNNING = ("enabled", "running")


def zero_availability(
    state: MotorViewState | None, estop_latched: bool
) -> tuple[bool, str]:
    """原点に設定できるか、できないときの理由(ツールチップ用)。"""
    if state is None or state.ui_state == "offline":
        return False, tr("operate.zero.unavailable_offline")
    if estop_latched:
        return False, tr("operate.zero.unavailable_estop")
    if state.ui_state == "fault":
        return False, tr("operate.zero.unavailable_fault")
    return True, ""


class ZeroPopover(Popover):
    """確認ポップオーバー。`confirmed` は実行ボタン、閉じるのはキャンセル / 外側クリック / Esc。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, width=300)
        self.body.addWidget(Label(tr("operate.zero.title"), "heading"))
        self.body.addWidget(Label(tr("operate.zero.body"), "secondary", wrap=True))
        self.warn_label = Label(tr("operate.zero.will_stop"), "caption", tone="caution", wrap=True)
        self.body.addWidget(self.warn_label)
        row = QHBoxLayout()
        row.setSpacing(SP_RELATED)
        row.addStretch(1)
        self.cancel_btn = Button(tr("dialog.cancel"), "standard")
        self.confirm_btn = Button(tr("operate.zero.confirm"), "accent", icon="crosshair")
        row.addWidget(self.cancel_btn)
        row.addWidget(self.confirm_btn)
        self.body.addLayout(row)
        self.cancel_btn.clicked.connect(self.hide)
        self.confirm_btn.clicked.connect(self._on_confirm)
        self._on_ok: Callable[[], None] | None = None

    def open_for(
        self, anchor: QWidget, *, will_stop: bool, on_confirm: Callable[[], None]
    ) -> None:
        """`will_stop`: 有効 / 運転中なので、先に停止して無効化することを知らせる。"""
        self._on_ok = on_confirm
        self.warn_label.setVisible(will_stop)
        self.show_below(anchor, align="left")

    def _on_confirm(self) -> None:
        cb, self._on_ok = self._on_ok, None
        self.hide()
        if cb is not None:
            cb()


class ZeroOriginFlow(QObject):
    """停止 → 無効化 → set_zero → トースト。モータ 1 台につき 1 件だけ進める。"""

    def __init__(
        self,
        bridge: Bridge,
        toast: Callable[[str, str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._toast = toast
        self._motor: int | None = None
        self._phase = ""  # "" / "settle"(無効化待ち)/ "tool"(結果待ち)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        bridge.state_updated.connect(self._on_states)
        bridge.tool_finished.connect(self._on_tool_finished)

    def busy(self) -> bool:
        return self._phase != ""

    def needs_stop(self, motor_id: int) -> bool:
        s = self._bridge.states().get(motor_id)
        return s is not None and s.ui_state in _RUNNING

    def start(self, motor_id: int) -> bool:
        """実行する。すでに別の設定を進行中、または今は設定できない状態なら False。"""
        if self.busy():
            return False
        ok, reason = zero_availability(
            self._bridge.states().get(motor_id), self._bridge.estop_latched()
        )
        if not ok:
            self._toast(tr("operate.zero.failed", id=motor_id, reason=reason), "caution")
            return False
        self._motor = motor_id
        if self.needs_stop(motor_id):
            self._phase = "settle"
            self._timer.start(SETTLE_TIMEOUT_MS)
            self._bridge.disable([motor_id])
        else:
            self._run_tool()
        return True

    # ---- 内部 ----
    def _run_tool(self) -> None:
        assert self._motor is not None
        self._phase = "tool"
        self._timer.start(RESULT_TIMEOUT_MS)
        self._bridge.run_tool("set_zero", self._motor, {})

    def _finish(self) -> None:
        self._timer.stop()
        self._motor = None
        self._phase = ""

    def _on_states(self, states: dict[int, MotorViewState]) -> None:
        if self._phase != "settle" or self._motor is None:
            return
        s = states.get(self._motor)
        if s is None:
            return
        if s.ui_state == "disabled":
            self._run_tool()
        elif s.ui_state in ("offline", "fault"):
            mid = self._motor
            self._finish()
            self._toast(
                tr("operate.zero.failed", id=mid, reason=tr("operate.zero.stop_failed")), "caution"
            )

    def _on_tool_finished(self, tool: str, motor_id: int, ok: bool, message: str) -> None:
        if tool != "set_zero" or self._phase != "tool" or motor_id != self._motor:
            return
        self._finish()
        if ok:
            self._toast(tr("operate.zero.done", id=motor_id), "success")
        else:
            reason = message or tr("operate.zero.unknown")
            self._toast(tr("operate.zero.failed", id=motor_id, reason=reason), "caution")

    def _on_timeout(self) -> None:
        mid, phase = self._motor, self._phase
        self._finish()
        if mid is None:
            return
        key = "operate.zero.stop_failed" if phase == "settle" else "operate.zero.no_result"
        self._toast(tr("operate.zero.failed", id=mid, reason=tr(key)), "caution")
