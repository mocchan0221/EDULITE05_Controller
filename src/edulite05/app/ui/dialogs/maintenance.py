"""保守ダイアログ(ui_design.md §5.4)。目的・影響・前提チェック → 動詞の実行ボタン。

「注意が必要な操作」(CAN ID・ボーレート・プロトコル・プロトコルから戻す)は、対象モータ ID の入力で
確定する。各ダイアログは `tool`(`Bridge.run_tool` のツール名)と `tool_args()` を持つ。
内容の根拠は el05_protocol.md の §2.7(ゼロ点)・§2.8(CAN ID)・§2.13(ボーレート)・§2.14(主動上報)・
§2.15(プロトコル切替)・§4.11 / §6.4(独自プロトコルへ戻す)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QSpinBox, QWidget

from edulite05.app.i18n import tr
from edulite05.app.ui.dialogs.base import ActionDialog
from edulite05.app.ui.inputs import Combo
from edulite05.app.ui.label import Label
from edulite05.app.ui.segmented import Segmented
from edulite05.app.ui.tokens import SP_RELATED

LABEL_W = 120
FIELD_W = 200

# F_CMD: el05_protocol.md §2.13
BAUDS: tuple[tuple[str, int], ...] = (
    ("1 Mbps", 1),
    ("500 kbps", 2),
    ("250 kbps", 3),
    ("125 kbps", 4),
)
# F_CMD: §2.15
PROTOCOLS: tuple[tuple[str, int, str], ...] = (
    ("private", 0, "dlg.protocol.private"),
    ("canopen", 1, "dlg.protocol.canopen"),
    ("mit", 2, "dlg.protocol.mit"),
)
REPORT_PERIODS: tuple[int, ...] = (0, 1, 3, 10)  # N。周期 = 10 ms + N × 5 ms(§8.0 実測)
CAN_ID_RANGE = (0, 127)  # §1.3


def report_period_ms(n: int) -> int:
    return 10 + n * 5


@dataclass(frozen=True, slots=True)
class MotorTarget:
    """保守の対象モータ。"""

    id: int
    name: str = ""
    ui_state: str = "disabled"

    @property
    def label(self) -> str:
        base = tr("ui.motor_id", id=self.id)
        return f"{base} {self.name}" if self.name else base


class ToolDialog(ActionDialog):
    """保守ダイアログの基底。`tool` と `tool_args()` を `Bridge.run_tool` に渡す。"""

    tool = ""
    reboot_required = False  # 実行後に電源の再投入が必要(完了のトーストで案内する)

    def __init__(self, parent: QWidget | None, motor: MotorTarget | None, **kwargs: Any) -> None:
        """`motor` が None のダイアログは、対象モータの ID をダイアログ内で入力する
        (`target_id()` を実装する)。"""
        self.motor = motor
        kwargs.setdefault("icon", "wrench")
        target = (motor.label, motor.ui_state) if motor is not None else None
        super().__init__(parent, target=target, **kwargs)

    def target_id(self) -> int:
        """`Bridge.run_tool` に渡すモータ ID。"""
        assert self.motor is not None
        return self.motor.id

    def tool_args(self) -> dict[str, Any]:
        return {}

    def add_field(self, label: str, widget: QWidget) -> None:
        """入力フォームに「ラベル + 部品」の行を足す。"""
        row = QHBoxLayout()
        row.setSpacing(SP_RELATED)
        lab = Label(label, "secondary")
        lab.setFixedWidth(LABEL_W)
        row.addWidget(lab)
        row.addWidget(widget)
        row.addStretch(1)
        self.form.addLayout(row)


def _combo(placeholder: bool) -> Combo:
    c = Combo()
    c.setMinimumWidth(FIELD_W)
    if placeholder:
        c.addItem(tr("dlg.choose"), None)
    return c


# ---------------------------------------------------------------- よく使う設定
class SetZeroDialog(ToolDialog):
    """ゼロ点を設定(type 6。§2.7)。"""

    tool = "set_zero"

    def __init__(self, parent: QWidget | None, motor: MotorTarget) -> None:
        super().__init__(
            parent,
            motor,
            title=tr("dlg.zero.title", id=motor.id),
            purpose=tr("dlg.zero.purpose"),
            impacts=[tr("dlg.zero.impact1"), tr("dlg.zero.impact2"), tr("dlg.zero.impact3")],
            checks=[tr("dlg.zero.check1"), tr("dlg.zero.check2")],
            confirm_text=tr("dlg.zero.confirm"),
        )


class ActiveReportDialog(ToolDialog):
    """主動上報の ON/OFF と周期(type 24 + 0x7026。§2.14)。"""

    tool = "active_report"

    def __init__(self, parent: QWidget | None, motor: MotorTarget) -> None:
        super().__init__(
            parent,
            motor,
            title=tr("dlg.report.title", id=motor.id),
            purpose=tr("dlg.report.purpose"),
            checks=[tr("dlg.report.check")],
        )
        self.mode = Segmented(
            [("on", tr("dlg.report.mode_on")), ("off", tr("dlg.report.mode_off"))], current="on"
        )
        self.add_field(tr("dlg.report.mode"), self.mode)
        self.period = _combo(False)
        for n in REPORT_PERIODS:
            self.period.addItem(f"{report_period_ms(n)} ms", n)
        self.add_field(tr("dlg.report.period"), self.period)
        self.mode.current_changed.connect(self._sync)
        self.period.currentIndexChanged.connect(self._sync)
        self._sync()

    def is_on(self) -> bool:
        return self.mode.current() == "on"

    def _sync(self, *_args: object) -> None:
        on = self.is_on()
        self.period.setEnabled(on)
        if on:
            ms = report_period_ms(self.period.currentData())
            self.set_impacts(
                [tr("dlg.report.impact_on", ms=ms, fps=round(1000 / ms)),
                 tr("dlg.report.impact_on2")]
            )
            self.set_confirm_text(tr("dlg.report.confirm_on", ms=ms))
        else:
            self.set_impacts([tr("dlg.report.impact_off"), tr("dlg.report.impact_off2")])
            self.set_confirm_text(tr("dlg.report.confirm_off"))

    def tool_args(self) -> dict[str, Any]:
        return {"on": self.is_on(), "period_n": self.period.currentData()}


class CanTimeoutDialog(ToolDialog):
    """通信タイムアウト(0x7028。20000 = 1 s、0 = 無効。§5.2)。"""

    tool = "can_timeout"

    def __init__(self, parent: QWidget | None, motor: MotorTarget) -> None:
        super().__init__(
            parent,
            motor,
            title=tr("dlg.timeout.title", id=motor.id),
            purpose=tr("dlg.timeout.purpose"),
            checks=[tr("dlg.timeout.check")],
        )
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(2)
        self.spin.setRange(0.0, 5.0)
        self.spin.setSingleStep(0.1)
        self.spin.setValue(0.5)
        self.spin.setSuffix(" s")
        self.spin.setSpecialValueText(tr("dlg.timeout.off"))
        self.spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.spin.setProperty("mono", True)
        self.spin.setFixedWidth(FIELD_W)
        self.add_field(tr("dlg.timeout.time"), self.spin)
        self.spin.valueChanged.connect(self._sync)
        self._sync()

    def seconds(self) -> float:
        return float(self.spin.value())

    def _sync(self, *_args: object) -> None:
        v = self.seconds()
        if v > 0:
            text = f"{v:g} s"
            self.set_impacts([tr("dlg.timeout.impact_on", text=text), tr("dlg.timeout.impact2")])
            self.set_confirm_text(tr("dlg.timeout.confirm_on", text=text))
        else:
            self.set_impacts([tr("dlg.timeout.impact_off"), tr("dlg.timeout.impact2")])
            self.set_confirm_text(tr("dlg.timeout.confirm_off"))

    def tool_args(self) -> dict[str, Any]:
        v = self.seconds()
        return {"seconds": v, "seconds_text": f"{v:g} s" if v > 0 else "off"}


# ---------------------------------------------------------------- 注意が必要な操作
class SetCanIdDialog(ToolDialog):
    """CAN ID を変更(type 7。§2.8)。対象モータ ID の入力で確定。"""

    tool = "set_can_id"

    def __init__(self, parent: QWidget | None, motor: MotorTarget) -> None:
        lo, hi = CAN_ID_RANGE
        new = motor.id + 1 if motor.id < hi else motor.id - 1
        super().__init__(
            parent,
            motor,
            icon="triangle-alert",
            icon_color="caution",
            title=tr("dlg.set_id.title", id=motor.id),
            purpose=tr("dlg.set_id.purpose"),
            checks=["", tr("dlg.set_id.check2")],
            require_text=str(motor.id),
        )
        self.spin = QSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setValue(new)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.spin.setProperty("mono", True)
        self.spin.setFixedWidth(FIELD_W)
        self.add_field(tr("dlg.set_id.new_id"), self.spin)
        self.spin.valueChanged.connect(self._sync)
        self._sync()

    def new_id(self) -> int:
        return int(self.spin.value())

    def _sync(self, *_args: object) -> None:
        new = self.new_id()
        self.set_impacts(
            [tr("dlg.set_id.impact1"), tr("dlg.set_id.impact2", new=new),
             tr("dlg.set_id.impact3")]
        )
        self.set_check_text(0, tr("dlg.set_id.check1", new=new))
        self.set_confirm_text(tr("dlg.set_id.confirm", new=new))
        self.set_blocker(tr("dlg.set_id.same") if new == self.motor.id else "")

    def tool_args(self) -> dict[str, Any]:
        return {"new_id": self.new_id()}


class SetBaudDialog(ToolDialog):
    """CAN ボーレートを変更(type 23。§2.13)。再投入後に有効。"""

    tool = "set_baud"
    reboot_required = True

    def __init__(self, parent: QWidget | None, motor: MotorTarget) -> None:
        super().__init__(
            parent,
            motor,
            icon="triangle-alert",
            icon_color="caution",
            title=tr("dlg.baud.title", id=motor.id),
            purpose=tr("dlg.baud.purpose"),
            impacts=[tr("dlg.baud.impact1"), tr("dlg.baud.impact2"), tr("dlg.baud.impact3")],
            checks=[tr("dlg.baud.check1"), tr("dlg.baud.check2")],
            require_text=str(motor.id),
        )
        self.combo = _combo(True)
        for label, code in BAUDS:
            self.combo.addItem(label, code)
        self.add_field(tr("dlg.baud.baud"), self.combo)
        self.combo.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self, *_args: object) -> None:
        code = self.combo.currentData()
        if code is None:
            self.set_confirm_text(tr("dialog.execute"))
            self.set_blocker(tr("dlg.choose"), show_note=False)
        else:
            self.set_confirm_text(tr("dlg.baud.confirm", baud=self.combo.currentText()))
            self.set_blocker("")

    def tool_args(self) -> dict[str, Any]:
        return {"baud": self.combo.currentData(), "baud_text": self.combo.currentText()}


class SwitchProtocolDialog(ToolDialog):
    """プロトコルを切り替え(type 25。§2.15)。再投入後に有効。"""

    tool = "switch_protocol"
    reboot_required = True

    def __init__(self, parent: QWidget | None, motor: MotorTarget) -> None:
        super().__init__(
            parent,
            motor,
            icon="triangle-alert",
            icon_color="caution",
            title=tr("dlg.protocol.title", id=motor.id),
            purpose=tr("dlg.protocol.purpose"),
            checks=[tr("dlg.protocol.check1"), tr("dlg.protocol.check2")],
            require_text=str(motor.id),
        )
        self.combo = _combo(True)
        for _key, code, name_key in PROTOCOLS:
            self.combo.addItem(tr(name_key), code)
        self.add_field(tr("dlg.protocol.protocol"), self.combo)
        self.combo.currentIndexChanged.connect(self._sync)
        self._sync()

    def choice(self) -> str | None:
        code = self.combo.currentData()
        for key, c, _name in PROTOCOLS:
            if c == code:
                return key
        return None

    def _sync(self, *_args: object) -> None:
        key = self.choice()
        if key is None:
            self.set_impacts([tr("dlg.protocol.impact_any")])
            self.set_confirm_text(tr("dialog.execute"))
            self.set_blocker(tr("dlg.protocol.choose_reason"), show_note=False)
            return
        if key == "private":
            self.set_impacts([tr("dlg.protocol.impact_private")])
        else:
            self.set_impacts([tr(f"dlg.protocol.impact_{key}"), tr("dlg.protocol.impact_back")])
        self.set_confirm_text(tr("dlg.protocol.confirm", protocol=self.combo.currentText()))
        self.set_blocker("")

    def tool_args(self) -> dict[str, Any]:
        return {"protocol": self.combo.currentData(), "protocol_text": self.combo.currentText()}


class RestoreProtocolDialog(ToolDialog):
    """CANopen・MIT プロトコルから独自プロトコルへ戻す(§4.11 / §6.4)。再投入後に有効。

    この状態のモータは独自プロトコルのスキャンに応答しないことが多く、選択中のモータが無くても
    使う必要がある。そのため対象は選択中のモータではなく、ダイアログで入力した ID(0〜127)と
    「いまのプロトコル」で決める。`motor` は入力欄の初期値(選択中のモータがあれば)。
    """

    tool = "restore_protocol"
    reboot_required = True

    def __init__(self, parent: QWidget | None, motor: MotorTarget | None = None) -> None:
        super().__init__(
            parent,
            None,
            icon="triangle-alert",
            icon_color="caution",
            title=tr("dlg.restore.title"),
            purpose=tr("dlg.restore.purpose"),
            checks=[tr("dlg.restore.check1"), tr("dlg.restore.check2")],
        )
        lo, hi = CAN_ID_RANGE
        self.id_spin = QSpinBox()
        self.id_spin.setRange(lo, hi)
        self.id_spin.setValue(motor.id if motor is not None else 1)
        self.id_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.id_spin.setProperty("mono", True)
        self.id_spin.setFixedWidth(FIELD_W)
        self.add_field(tr("dlg.restore.target_id"), self.id_spin)
        self.combo = _combo(True)
        self.combo.addItem(tr("dlg.protocol.canopen"), "canopen")
        self.combo.addItem(tr("dlg.protocol.mit"), "mit")
        self.add_field(tr("dlg.restore.current"), self.combo)
        self.id_spin.valueChanged.connect(self._sync)
        self.combo.currentIndexChanged.connect(self._sync)
        self._sync()

    def target_id(self) -> int:
        return int(self.id_spin.value())

    def choice(self) -> str | None:
        data = self.combo.currentData()
        return str(data) if data else None

    def _sync(self, *_args: object) -> None:
        key = self.choice()
        self.set_confirm_text(
            tr("dlg.restore.confirm", id=self.target_id()) if key else tr("dialog.execute")
        )
        if key is None:
            self.set_impacts([tr("dlg.restore.impact2")])
            self.set_blocker(tr("dlg.restore.choose_reason"), show_note=False)
            return
        self.set_impacts(
            [tr(f"dlg.restore.impact_{key}", id=self.target_id()), tr("dlg.restore.impact2")]
        )
        self.set_blocker("")

    def tool_args(self) -> dict[str, Any]:
        return {"from_protocol": self.choice() or ""}
