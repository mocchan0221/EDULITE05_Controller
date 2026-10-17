"""部品ギャラリー(`python -m edulite05 --gallery`)。`app/ui/` の全部品とトークンを一覧する。

右上のセグメントでダーク / ライトを切り替える(スクリーンショットは `tools/capture_ui_mock.py`)。
部品の見た目を直すときは、まずここで両テーマを確認する。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from edulite05.app.i18n import tr
from edulite05.app.ui import (
    Button,
    Combo,
    ConfirmDialog,
    EmptyState,
    Expander,
    HoldButton,
    InfoBar,
    Label,
    MotorChip,
    NavRail,
    ParamForm,
    ParamRow,
    ReadoutCell,
    ReadoutDualCell,
    Section,
    Segmented,
    StatusBadge,
    Switch,
    ToastHost,
    theme_manager,
)
from edulite05.app.ui.style import tokens
from edulite05.app.ui.tokens import SP_GROUP, SP_PAD, SP_RELATED, SP_SECTION
from edulite05.app.ui.util import hbox, styled_background, vbox
from edulite05.core.control.params import VelocityParams


class _Swatch(QWidget):
    """トークンの色見本(テーマ切替に追従して再描画)。"""

    def __init__(self, label: str, color: Callable[[], str]) -> None:
        super().__init__()
        self._color = color
        self.setFixedSize(54, 50)
        self.setToolTip(label)
        self._caption = label
        theme_manager().changed.connect(self._on_theme)

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = self._color()
        r = QRectF(0.5, 0.5, self.width() - 1, 28)
        p.setPen(QPen(QColor(t.stroke_control), 1))
        p.setBrush(QColor(col))
        p.drawRoundedRect(r, 4, 4)
        p.setPen(QColor(t.text_secondary))
        f = p.font()
        f.setPixelSize(12)
        p.setFont(f)
        p.drawText(QRectF(0, 31, self.width(), 18), Qt.AlignmentFlag.AlignCenter, self._caption)
        p.end()


def _flow(*widgets: QWidget, spacing: int = SP_RELATED) -> QWidget:
    host = QWidget()
    lay = hbox(host, spacing=spacing)
    for w in widgets:
        lay.addWidget(w)
    lay.addStretch(1)
    return host


class GalleryWindow(QWidget):
    """全部品の一覧。`GalleryWindow()` を show() する(テーマは `theme_manager()` に従う)。"""

    COLUMNS = 4

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{tr('app.title')} — {tr('gallery.title')}")
        self.resize(1536, 912)
        styled_background(self, "layer")
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # 起動直後にフォーカスリングを出さない
        root = vbox(self)

        # ---- 上部: タイトル + テーマ切替 ----
        bar = QFrame()
        bar.setObjectName("Header")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setFixedHeight(56)
        bl = hbox(bar, margins=(SP_PAD, 0, SP_PAD, 0), spacing=SP_GROUP)
        bl.addWidget(Label(tr("gallery.title"), "title"))
        bl.addStretch(1)
        self.theme_seg = Segmented(
            [("dark", tr("gallery.theme_dark")), ("light", tr("gallery.theme_light"))],
            current=theme_manager().name,
        )
        self.theme_seg.selected.connect(lambda k: theme_manager().apply(k))
        bl.addWidget(self.theme_seg)
        root.addWidget(bar)

        # ---- 本体: 4 カラム ----
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body.setProperty("surface", "layer")
        grid = hbox(body, margins=SP_PAD, spacing=SP_SECTION)
        cols: list[QVBoxLayout] = []
        for _ in range(self.COLUMNS):
            col = vbox(spacing=SP_SECTION)
            holder = QWidget()
            holder.setLayout(col)
            holder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            grid.addWidget(holder, 1)
            cols.append(col)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        a, b, c, d = cols
        a.addWidget(self._colors())
        a.addWidget(self._buttons())
        a.addWidget(self._segmented())
        b.addWidget(self._badges())
        b.addWidget(self._infobars())
        b.addWidget(self._readouts())
        c.addWidget(self._param_rows())
        c.addWidget(self._param_form())
        c.addWidget(self._inputs())
        d.addWidget(self._structure())
        d.addWidget(self._empty_and_nav())
        for col in cols:
            col.addStretch(1)
        self.toast_host = ToastHost(self)

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    # ------------------------------------------------------------ セクション
    def _colors(self) -> QWidget:
        sec = Section(tr("gallery.colors"))
        items: list[tuple[str, Callable[[], str]]] = [
            ("base", lambda: tokens().bg_base), ("layer", lambda: tokens().bg_layer),
            ("card", lambda: tokens().bg_card), ("sunken", lambda: tokens().bg_sunken),
            ("control", lambda: tokens().bg_control), ("stroke", lambda: tokens().stroke_control),
            ("primary", lambda: tokens().text_primary),
            ("secondary", lambda: tokens().text_secondary),
            ("tertiary", lambda: tokens().text_tertiary),
            ("divider", lambda: tokens().stroke_divider),
            ("accent", lambda: tokens().accent), ("success", lambda: tokens().success),
            ("caution", lambda: tokens().caution), ("critical", lambda: tokens().critical),
            ("estop", lambda: tokens().estop),
            ("ID1", lambda: tokens().motor_color(0)), ("ID2", lambda: tokens().motor_color(1)),
            ("ID3", lambda: tokens().motor_color(2)),
        ]
        grid = QWidget()
        gl = QGridLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setHorizontalSpacing(SP_RELATED)
        gl.setVerticalSpacing(SP_RELATED)
        for i, (name, fn) in enumerate(items):
            gl.addWidget(_Swatch(name, fn), i // 6, i % 6)
        sec.add(grid)
        return sec

    def _buttons(self) -> QWidget:
        sec = Section(tr("gallery.buttons"))
        sec.add(_flow(Button(tr("gallery.sample_start"), "accent", icon="play", large=True)))
        std = Button(tr("gallery.sample_standard"), "standard", icon="power")
        sub = Button(tr("gallery.sample_subtle"), "subtle", icon="chevron-right")
        sec.add(_flow(std, sub, Button(icon="ellipsis", variant="subtle", tooltip="…"),
                      Button(icon="rotate-ccw", variant="standard", tooltip="…")))
        sec.add(_flow(Button(tr("gallery.sample_enable"), "accent", icon="power"),
                      Button(tr("gallery.sample_caution"), "caution"),
                      Button(tr("gallery.sample_danger"), "danger")))
        dis = Button(tr("gallery.sample_disabled"), "standard", icon="play")
        dis.set_available(False, tr("gallery.sample_disabled_reason"))
        dis_acc = Button(tr("gallery.sample_disabled"), "accent", icon="play")
        dis_acc.set_available(False, tr("gallery.sample_disabled_reason"))
        sec.add(_flow(dis, dis_acc))
        rev = HoldButton(tr("gallery.sample_hold_rev"))
        fwd = HoldButton(tr("gallery.sample_hold_fwd"))
        row = QWidget()
        rl = hbox(row, spacing=SP_RELATED)
        rl.addWidget(rev, 1)
        rl.addWidget(fwd, 1)
        sec.add(row)
        return sec

    def _segmented(self) -> QWidget:
        sec = Section(tr("gallery.segmented"))
        modes = Segmented(
            [("jog", "JOG"), ("vel", "速度"), ("pos", "位置"), ("mit", "MIT")], current="vel")
        sec.add(Label(tr("gallery.seg_modes"), "caption"))
        locked = Segmented(
            [("jog", "JOG"), ("vel", "速度"), ("pos", "位置"), ("mit", "MIT")], current="vel")
        locked.set_all_enabled(False, tr("gallery.seg_mode_reason"))
        sec.add(_flow(modes))
        sec.add(_flow(locked))
        sec.add(Label(tr("gallery.seg_tier"), "caption"))
        tier = Segmented([("low", tr("header.tier_low")), ("high", tr("header.tier_high"))],
                         current="high")
        tier.set_item_tone("high", "caution")
        sec.add(_flow(tier))
        return sec

    def _badges(self) -> QWidget:
        sec = Section(tr("gallery.badges"))
        sec.add(_flow(*[StatusBadge.for_ui_state(s)
                        for s in ("offline", "disabled", "enabled", "running", "fault")]))
        chips = []
        for i, st in enumerate(("running", "disabled", "fault")):
            chip = MotorChip(i + 1)
            chip.set_state(st)
            chip.set_selected(i == 0)
            chips.append(chip)
        sec.add(_flow(*chips[:2]))
        sec.add(_flow(chips[2]))
        return sec

    def _infobars(self) -> QWidget:
        sec = Section(tr("gallery.infobars"))
        bars = [
            InfoBar("info", tr("banner.simulator_title"), tr("gallery.infobar_info"),
                    closable=True),
            InfoBar("caution", tr("banner.high_title"), tr("gallery.infobar_caution"),
                    action=tr("banner.high_action")),
            InfoBar("critical", tr("banner.fault_title", ids="ID2"), tr("fault.overtemp"),
                    action=tr("gallery.infobar_action")),
            InfoBar("estop", tr("banner.estop_title"), tr("banner.estop_text"),
                    action=tr("banner.estop_action")),
        ]
        for bar in bars:
            bar.setMinimumWidth(0)
            sec.add(bar)
        return sec

    def _readouts(self) -> QWidget:
        sec = Section(tr("gallery.readouts"))
        pos = ReadoutCell(tr("gallery.cell_position"), "rad", decimals=3, minimum=-12.57,
                          maximum=12.57)
        pos.set_value(0.736)
        pos.set_command(1.0)
        pos.set_allowed(-12.57, 12.57)
        pos.set_meta("多回転 +3.45")
        vel = ReadoutCell(tr("gallery.cell_velocity"), "rad/s", decimals=2, minimum=-50, maximum=50)
        vel.set_value(-2.07)
        vel.set_command(-2.0)
        vel.set_allowed(-5, 5)
        vel.set_note(tr("gallery.row_note"))
        tq = ReadoutCell(tr("gallery.cell_torque"), "N·m", decimals=3, minimum=-6, maximum=6)
        tq.set_value(-0.011)
        tq.set_command(0.0)
        tq.set_allowed(-0.5, 0.5)
        dual = ReadoutDualCell(
            (tr("gallery.cell_temp"), "℃", 1), (tr("gallery.cell_voltage"), "V", 1)
        )
        dual.set_values(28.7, 24.0)
        grid = QWidget()
        gl = QVBoxLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(SP_RELATED)
        for pair in ((pos, vel), (tq, dual)):
            row = QWidget()
            rl = hbox(row, spacing=SP_RELATED)
            for w in pair:
                rl.addWidget(w, 1)
            gl.addWidget(row)
        sec.add(grid)
        return sec

    def _param_rows(self) -> QWidget:
        sec = Section(tr("gallery.params"))
        r1 = ParamRow(tr("gallery.row_speed"), unit="rad/s", minimum=-5, maximum=5, step=0.01,
                      decimals=3, default=0.0, value=1.0)
        r1.set_actual(0.92)
        r2 = ParamRow(tr("gallery.row_limited"), unit="A", minimum=0, maximum=1, step=0.05,
                      decimals=2, default=1.0, value=1.0)
        r2.set_note(tr("form.clamped", cap="1", unit="A"))
        r3 = ParamRow(tr("gallery.row_error"), unit="rad", minimum=-12.57, maximum=12.57,
                      step=0.01, decimals=3, default=0.0, value=0.0)
        r3.edit.setText("20")
        r3.edit.textEdited.emit("20")
        for r in (r1, r2, r3):
            sec.add(r)
        return sec

    def _param_form(self) -> QWidget:
        sec = Section(tr("gallery.form_title"))
        form = ParamForm(VelocityParams(velocity=1.0))
        sec.add(form)
        return sec

    def _inputs(self) -> QWidget:
        sec = Section(tr("gallery.inputs"))
        combo = Combo()
        for text in tr("gallery.combo_items").split("|"):
            combo.addItem(text)
        from PySide6.QtWidgets import QLineEdit

        line = QLineEdit()
        line.setPlaceholderText(tr("gallery.line_edit"))
        line.setFixedHeight(32)
        sec.add(_flow(combo, line))
        check = QCheckBox(tr("gallery.check_label"))
        check.setChecked(True)
        sw_row = QWidget()
        sl = hbox(sw_row, spacing=SP_RELATED)
        sl.addWidget(Label(tr("gallery.switch_label"), "body"))
        sl.addWidget(Switch(True))
        sl.addWidget(Switch(False))
        sec.add(check)
        sec.add(sw_row)
        toast_btn = Button(tr("gallery.toast_button"), "standard")
        toast_btn.clicked.connect(lambda: self.toast_host.show_message(tr("gallery.toast_text")))
        dlg_btn = Button(tr("gallery.dialog_button"), "standard")
        dlg_btn.clicked.connect(self._open_dialog)
        sec.add(_flow(toast_btn, dlg_btn))
        return sec

    def _open_dialog(self) -> None:
        ConfirmDialog.ask(
            self,
            title=tr("confirm.high_tier.title"),
            body=tr("confirm.high_tier.body"),
            checks=[tr("confirm.high_tier.check_fixed"), tr("confirm.high_tier.check_clear")],
            confirm_text=tr("confirm.high_tier.ok"),
            confirm_variant="caution",
            tone="caution",
        )

    def _structure(self) -> QWidget:
        host = QWidget()
        lay = vbox(host, spacing=SP_SECTION)
        sec = Section(tr("gallery.section_title"))
        sec.add(Label(tr("gallery.section_body"), "secondary", wrap=True))
        card = Section(tr("gallery.section_card"), card=True)
        card.add(Label(tr("gallery.section_body"), "secondary", wrap=True))
        exp = Expander(tr("gallery.expander"), expanded=True)
        exp.set_content(Label(tr("gallery.expander_body"), "caption"))
        card.add(exp)
        sec.add(card)
        lay.addWidget(sec)
        return host

    def _empty_and_nav(self) -> QWidget:
        host = QFrame()
        host.setProperty("card", True)
        lay = hbox(host, margins=SP_RELATED, spacing=SP_RELATED)
        nav = NavRail()
        nav.setFixedHeight(5 * 60 + 8)
        for i, (key, icon_name) in enumerate((
            ("operate", "gauge"), ("params", "sliders-horizontal"), ("diagnostics", "stethoscope"),
            ("maintenance", "wrench"),
        )):
            nav.add_item(key, tr(f"nav.{key}"), icon_name)
            if i == 2:
                nav.set_badge(key, True)
        nav.add_item("settings", tr("nav.settings"), "settings", bottom=True)
        lay.addWidget(nav)
        empty = EmptyState("plug", tr("gallery.empty_title"), tr("gallery.empty_desc"),
                           action=tr("gallery.empty_action"))
        empty.setMinimumHeight(300)
        lay.addWidget(empty, 1)
        return host
