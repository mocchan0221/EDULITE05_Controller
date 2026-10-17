"""インスペクタ(操作。ui_design.md §5.1・§3)。右 360px、ISA-101 のレベル 2。

見出し(ID・名前・状態バッジ)→ 制御モード → 目標 → ▸ 制限と加速度 → 主ボタン領域(最下部固定)。
主ボタン領域は §3 の状態表のとおり:

    disabled  有効化(強調色)               モード選択: 可
    enabled   運転開始(強調色)/ JOG は押下保持ボタン 2 つ + 無効化     不可
    running   運転停止(枠付き)+ 無効化                               不可
    fault     故障をリセット(原因を表示した後)                       不可
    offline   (操作なし。「接続すると…」を案内)                       不可

コントローラへの指令は Bridge の `enable` / `start_controller` / `update_controller_params` /
`stop_controller` / `disable` / `clear_fault` だけを使う(v1 control.py の流れを引き継ぐ)。
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.pages.base import ShellApi
from edulite05.app.pages.operate import modes as md
from edulite05.app.pages.operate.link import LinkPanel, link_assign
from edulite05.app.ui.badge import StatusBadge
from edulite05.app.ui.button import Button, HoldButton
from edulite05.app.ui.empty import EmptyState
from edulite05.app.ui.inputs import Combo, Switch
from edulite05.app.ui.label import Label
from edulite05.app.ui.param_row import ParamForm, ParamRow, fmt_short
from edulite05.app.ui.popover import Menu
from edulite05.app.ui.readout import fmt_signed
from edulite05.app.ui.section import Expander
from edulite05.app.ui.segmented import Segmented
from edulite05.app.ui.tokens import (
    SP_GROUP,
    SP_ICON,
    SP_PAD,
    SP_RELATED,
    W_INSPECTOR,
    W_INSPECTOR_MAX,
)
from edulite05.app.ui.util import divider, styled_background
from edulite05.app.viewmodels import MotorViewState
from edulite05.core import clock
from edulite05.core.control.params import PARAMS_BY_CONTROLLER, BilateralParams
from edulite05.core.control.targets import (
    DEFAULT_TARGET_REF,
    REF_MULTI_TURN,
    REF_WITHIN_TURN,
    TARGET_REFS,
    nearest_target,
    wrap_to_pi,
)
from edulite05.plugin_api import GROUP_OTHER

JOG_REVERSE = -1.0
JOG_FORWARD = 1.0

# MIT プリセット: 設定する値。Kp・Kd は出力段階の上限(ParamForm の範囲)で自動的に切られる。
# 「その場で保持」は今の位置を目標にする。
# 減衰・ばねは実機で手応えを確かめた値(hw_bench.md HW-4 T1/T2)。LOW の 0.5 N·m で頭打ちに
# なるまでの範囲が広く(ダンパ: 約 3 rad/s、ばね: 約 0.25 rad)、速さ・ずれの差が手で分かる。
# 抵抗ゼロ: 摩擦補償(PC 側。実測のクーロン摩擦 ≈ 0.03 N·m、hw_bench.md HW-4)だけで軸を軽くする。
# どのプリセットも摩擦補償を明示する(前のプリセットの値を引きずらない)。
MIT_PRESETS: dict[str, dict[str, float]] = {
    "free": {"kp": 0.0, "kd": 0.0, "velocity": 0.0, "torque_ff": 0.0, "friction_comp": 0.0},
    "damper": {"kp": 0.0, "kd": 0.15, "velocity": 0.0, "torque_ff": 0.0, "friction_comp": 0.0},
    "zero": {"kp": 0.0, "kd": 0.0, "velocity": 0.0, "torque_ff": 0.0, "friction_comp": 0.03},
    "spring": {
        "kp": 2.0, "kd": 0.1, "position": 0.0, "velocity": 0.0, "torque_ff": 0.0,
        "friction_comp": 0.0,
    },
    "hold": {"kp": 10.0, "kd": 0.5, "velocity": 0.0, "torque_ff": 0.0, "friction_comp": 0.0},
}
MIT_PRESET_ORDER = ("free", "damper", "zero", "spring", "hold")
ENGAGE_GRACE_S = 1.0  # 有効化直後は、古い「停止中」の状態更新でモード固定を解かない

# 目標の基準(位置モード)の保存先: shell.settings() の `operate/target_ref/<モード>`
TARGET_REF_KEY = "operate/target_ref/{mode}"
TARGET_REF_MODES = ("position",)
_POSITION_CTRLS = ("csp", "pp")


class Inspector(QFrame):
    """`Inspector(bridge, shell)`。`set_motor(id)` / `set_state(state)` で操作対象を渡す。"""

    def __init__(
        self, bridge: Bridge, shell: ShellApi | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.shell = shell
        styled_background(self, "layer")
        self.setMinimumWidth(W_INSPECTOR)
        self.setMaximumWidth(W_INSPECTOR_MAX)
        self._mid: int | None = None
        self._state: MotorViewState | None = None
        self._models: dict[int, md.MotorModel] = {}
        self._loading = False
        self._sig: tuple = ()
        self._jog_dir = 0.0
        self._key_hold: HoldButton | None = None
        self._deg = False  # 角度の表示単位(読み値カードの rad / deg に合わせる)
        self._sent_abs: float | None = None  # 運転中に送っている位置の目標(多回転)
        self._target_refs: dict[str, str] = self._load_target_refs()
        self.primary_kind = ""  # enable / start / stop / reset / ""(なし)
        self.forms: dict[str, ParamForm] = {
            name: ParamForm(cls, bridge) for name, cls in PARAMS_BY_CONTROLLER.items()
        }
        self._build()
        for name, form in self.forms.items():
            form.changed.connect(lambda field, value, n=name: self._on_field(n, field, value))
            form.params_changed.connect(lambda _p, n=name: self._on_form_changed(n))
        self._widen_edits()
        bridge.power_tier_changed.connect(self._on_tier_changed)  # ParamForm の後に実行
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)  # type: ignore[attr-defined]
        self._install_filters()
        self.set_motor(bridge.selected_motor() if bridge.motors() else None)
        self.set_state(bridge.states().get(self._mid) if self._mid is not None else None)

    # ================================================================ 構築
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 見出し ----
        self.head = head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(SP_PAD, SP_PAD, SP_PAD, SP_GROUP)
        hl.setSpacing(SP_RELATED)
        self.heading = Label("", "inspector")
        self.badge = StatusBadge.for_ui_state("offline")
        hl.addWidget(self.heading, 1)
        hl.addWidget(self.badge)
        root.addWidget(head)
        self.head_divider = divider()
        root.addWidget(self.head_divider)

        # ---- モータが無いときの空状態(縦に中央。主ボタン領域・ヒントは出さない)----
        self.empty = EmptyState("plug", tr("operate.heading.empty"))
        root.addWidget(self.empty, 1)

        # ---- スクロールする本体 ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(SP_PAD, SP_PAD, SP_PAD, SP_PAD)
        self.body.setSpacing(SP_GROUP)
        self.scroll.setWidget(body)
        root.addWidget(self.scroll, 1)

        # 制御モード
        self.mode_heading = Label(tr("operate.mode.heading"), "heading")
        self.body.addWidget(self.mode_heading)
        self.mode_seg = Segmented(
            [
                *((k, tr(md.MODE_SPECS[k].title)) for k in md.MAIN_MODES),  # 登録簿(builtin)の並び
                ("other", tr("operate.mode.other") + " ▾"),
            ],
            current="velocity",
        )
        for key in self.mode_seg.keys():  # 360px に 5 項目を収める(既定は最大の項目に幅をそろえる)
            self.mode_seg.button(key).setMinimumWidth(0)
        self.mode_seg.selected.connect(self._on_mode_selected)
        self.body.addWidget(self.mode_seg)
        desc_row = QHBoxLayout()
        desc_row.setContentsMargins(0, 0, 0, 0)
        desc_row.setSpacing(SP_RELATED)
        self.desc_label = Label("", "secondary", wrap=True)
        self.info_btn = Button(variant="subtle", icon="info")
        self.info_btn.setFixedSize(24, 24)
        desc_row.addWidget(self.desc_label, 1)
        desc_row.addWidget(self.info_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(desc_row)

        # 目標
        self.target_heading = Label(tr("operate.target.heading"), "heading")
        self.body.addSpacing(SP_RELATED)
        self.body.addWidget(self.target_heading)
        self._build_target_controls()
        self._target_boxes: dict[str, QWidget] = {}
        self._limit_boxes: dict[str, QWidget] = {}
        for name, form in self.forms.items():
            tbox = self._collect(form, name, md.TARGET_FIELDS[name])
            self.body.addWidget(tbox)
            self._target_boxes[name] = tbox
        self.body.addSpacing(SP_RELATED)

        # 制限と加速度
        self.limits = Expander(tr("operate.target.limits"))
        for name, form in self.forms.items():
            lbox = self._collect(form, name, md.LIMIT_FIELDS[name])
            self.limits.content_layout.addWidget(lbox)
            self._limit_boxes[name] = lbox
        period_row = QHBoxLayout()
        period_row.setContentsMargins(0, 0, 0, 0)
        period_row.setSpacing(SP_RELATED)
        period_label = Label(tr("operate.target.period"), "body")
        period_label.setToolTip(tr("operate.target.period_tip"))
        self.period_combo = Combo()
        for ms in md.PERIODS_MS:
            self.period_combo.addItem(
                tr("operate.target.period_item", hz=int(1000 / ms), ms=ms), ms
            )
        self.period_combo.setCurrentIndex(md.PERIODS_MS.index(md.DEFAULT_PERIOD_MS))
        period_row.addWidget(period_label, 1)
        period_row.addWidget(self.period_combo)
        self.limits.content_layout.addLayout(period_row)
        self.continuous_row = QWidget()
        cr = QHBoxLayout(self.continuous_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.setSpacing(SP_RELATED)
        cr_label = Label(tr("operate.target.continuous"), "body")
        cr_label.setToolTip(tr("operate.target.continuous_tip"))
        self.continuous_switch = Switch(True)
        self.continuous_switch.setToolTip(tr("operate.target.continuous_tip"))
        cr.addWidget(cr_label, 1)
        cr.addWidget(self.continuous_switch)
        self.limits.content_layout.addWidget(self.continuous_row)
        self.body.addWidget(self.limits)
        self.body.addStretch(1)

        # ---- 主ボタン領域(最下部固定)----
        self.foot_divider = divider()
        root.addWidget(self.foot_divider)
        self.foot = foot = QWidget()
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(SP_PAD, SP_GROUP, SP_PAD, SP_PAD)
        fl.setSpacing(SP_RELATED)
        self.fault_block = QFrame()
        self.fault_block.setProperty("card", True)
        fb = QVBoxLayout(self.fault_block)
        fb.setContentsMargins(SP_GROUP, SP_RELATED, SP_GROUP, SP_RELATED)
        fb.setSpacing(2)
        self.fault_title = Label("", "heading", tone="critical", wrap=True)
        self.fault_desc = Label("", "secondary", wrap=True)
        self.fault_remedy = Label("", "caption", wrap=True)
        fb.addWidget(self.fault_title)
        fb.addWidget(self.fault_desc)
        fb.addWidget(self.fault_remedy)
        fl.addWidget(self.fault_block)
        self.hint_label = Label("", "caption", wrap=True)
        fl.addWidget(self.hint_label)
        self.primary = Button(tr("operate.action.enable"), "accent", icon="power", large=True)
        self.primary.clicked.connect(self._on_primary)
        fl.addWidget(self.primary)
        self.jog_row = QWidget()
        jr = QHBoxLayout(self.jog_row)
        jr.setContentsMargins(0, 0, 0, 0)
        jr.setSpacing(SP_RELATED)
        self.jog_rev = HoldButton(tr("operate.action.jog_reverse"))
        self.jog_fwd = HoldButton(tr("operate.action.jog_forward"))
        for hb in (self.jog_rev, self.jog_fwd):
            jr.addWidget(hb, 1)
        self.jog_rev.hold_started.connect(lambda: self._jog_start(JOG_REVERSE))
        self.jog_fwd.hold_started.connect(lambda: self._jog_start(JOG_FORWARD))
        self.jog_rev.hold_ended.connect(lambda r, b=self.jog_rev: self._jog_end(b, r))
        self.jog_fwd.hold_ended.connect(lambda r, b=self.jog_fwd: self._jog_end(b, r))
        fl.addWidget(self.jog_row)
        self.secondary = Button(tr("operate.action.disable"), "standard", icon="power")
        self.secondary.clicked.connect(self._on_disable)
        fl.addWidget(self.secondary)
        root.addWidget(foot)

        self.mode_seg.set_all_enabled(False, tr("operate.mode.locked_offline"))
        self.period_combo.currentIndexChanged.connect(self._on_period)
        self.continuous_switch.toggled.connect(self._on_continuous)

    def _build_target_controls(self) -> None:
        """モード固有の部品: MIT プリセット・位置の動き方・試験信号の波形・2台連動の相手。"""
        # MIT プリセット(押すと Kp / Kd / 目標を設定)
        self.preset_box = QWidget()
        pv = QVBoxLayout(self.preset_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(SP_ICON)
        pv.addWidget(Label(tr("operate.target.presets"), "caption"))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(SP_RELATED)
        self.preset_buttons: dict[str, Button] = {}
        for i, key in enumerate(MIT_PRESET_ORDER):
            b = Button(tr(f"operate.target.preset_{key}"), "standard",
                       tooltip=tr(f"operate.target.preset_{key}_tip"))
            b.clicked.connect(lambda _c=False, k=key: self.apply_preset(k))
            # 3 列 × 2 行。最後の「その場で保持」は下段の残り 2 列を使って行をそろえる
            row, col = divmod(i, 3)
            last = i == len(MIT_PRESET_ORDER) - 1
            grid.addWidget(b, row, col, 1, 3 - col if last else 1)
            self.preset_buttons[key] = b
        for c in range(3):
            grid.setColumnStretch(c, 1)
        pv.addLayout(grid)
        self.body.addWidget(self.preset_box)
        # 位置の動き方
        self.style_box = QWidget()
        sv = QVBoxLayout(self.style_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(SP_ICON)
        sv.addWidget(Label(tr("operate.target.position_style"), "caption"))
        self.style_seg = Segmented(
            [("csp", tr("operate.target.style_csp")), ("pp", tr("operate.target.style_pp"))]
        )
        self.style_seg.selected.connect(self._on_style)
        sv.addWidget(self.style_seg)
        self.body.addWidget(self.style_box)
        # 位置の目標の基準(1 回転内(最短)/ 多回転)
        self.ref_box = QWidget()
        rv = QVBoxLayout(self.ref_box)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(SP_ICON)
        ref_caption = Label(tr("operate.target.ref"), "caption")
        ref_caption.setToolTip(tr("operate.target.ref_tip"))
        rv.addWidget(ref_caption)
        self.ref_seg = Segmented(
            [
                (REF_WITHIN_TURN, tr("operate.target.ref_turn")),
                (REF_MULTI_TURN, tr("operate.target.ref_multi")),
            ],
            current=self._target_refs["position"],
        )
        self.ref_seg.setToolTip(tr("operate.target.ref_tip"))
        self.ref_seg.selected.connect(self._on_ref)
        rv.addWidget(self.ref_seg)
        self.body.addWidget(self.ref_box)
        # 1 回転内の基準のときの目標(角度 θ)とその結果(絶対位置・移動量)
        self.turn_box = QWidget()
        tv = QVBoxLayout(self.turn_box)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(SP_ICON)
        self.turn_row = ParamRow(
            tr("operate.target.turn_angle"), unit="rad", minimum=-math.pi, maximum=math.pi,
            step=0.01, decimals=3, default=0.0, tooltip=tr("operate.target.turn_angle_tip"),
        )
        self.turn_row.value_changed.connect(self._on_turn_changed)
        self.target_hint = Label("", "caption", wrap=True)
        tv.addWidget(self.turn_row)
        tv.addWidget(self.target_hint)
        self.body.addWidget(self.turn_box)
        # 試験信号の波形
        self.wave_box = QWidget()
        wv = QVBoxLayout(self.wave_box)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(SP_ICON)
        wv.addWidget(Label(tr("operate.target.test_wave"), "caption"))
        self.wave_seg = Segmented(
            [("sine", tr("operate.target.wave_sine")), ("step", tr("operate.target.wave_step"))]
        )
        self.wave_seg.selected.connect(self._on_wave)
        wv.addWidget(self.wave_seg)
        self.body.addWidget(self.wave_box)
        # 2台連動
        # (core の Bilateral。設定は LinkPanel、運転は主ボタンの 1 つ)
        self.link_box = LinkPanel(self.bridge)
        self.follower_combo = self.link_box.follower_combo
        self.link_box.follower_changed.connect(self._on_follower)
        self.link_box.params_changed.connect(self._on_link_params)
        self.body.addWidget(self.link_box)

    def _collect(self, form: ParamForm, name: str, fields: tuple[str, ...]) -> QWidget:
        """ParamForm の行を、目標用/制限用の箱へ移す(フォーム自体はモデルとして残す)。"""
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP_PAD)
        for f in fields:
            row = form.row(f)
            key = md.LABEL_OVERRIDES.get((name, f))
            label = getattr(row, "label", None)
            if key is not None and label is not None:
                label.setText(tr(key))
            lay.addWidget(row)
        box.setVisible(False)
        return box

    # ================================================================ 公開 API
    def motor_id(self) -> int | None:
        return self._mid

    def model(self) -> md.MotorModel | None:
        return self._models.get(self._mid) if self._mid is not None else None

    def mode(self) -> str:
        m = self.model()
        return m.mode if m is not None else ""

    def controller_name(self) -> str:
        m = self.model()
        return md.controller_of(m) if m is not None else ""

    def _latest_state(self) -> MotorViewState | None:
        """位置を目標にするときは、間引き前の最新の状態を使う。"""
        return self.bridge.states().get(self._mid) if self._mid is not None else None

    def ui_state(self) -> str:
        s = self._state
        return s.ui_state if s is not None else "offline"

    def set_motor(self, motor_id: int | None) -> None:
        """操作対象を切り替える。入力は ID ごとに覚えている。"""
        if motor_id == self._mid and self._sig:
            return
        self._end_all_holds("motor_changed")
        self._mid = motor_id
        self._state = None
        self._sent_abs = None
        self._sig = ()
        if motor_id is not None:
            self._models.setdefault(motor_id, md.MotorModel())
        self._load_model()
        self._refresh_heading()
        self._apply()

    def set_state(self, state: MotorViewState | None) -> None:
        """約 30 Hz。見た目の再構成は状態(ui_state・故障・実行中コントローラ)が変わったときだけ。"""
        if state is not None and state.id != self._mid:
            return
        self._state = state
        if state is None or state.ui_state in ("disabled", "offline", "fault"):
            self._sent_abs = None
        m = self.model()
        if m is not None and state is not None:
            self._sync_model(m, state)
        sig = (
            state.ui_state if state is not None else "offline",
            state.faults if state is not None else (),
            state.controller if state is not None else None,
            self.bridge.estop_latched(),
            self.bridge.connection_info().read_only,
            m.mode if m is not None else "",
            m.pos_style if m is not None else "",
            m.wave if m is not None else "",
            len(self.bridge.motors()),
        )
        if sig != self._sig:
            self._sig = sig
            self._load_model_mode_ui()
            self._apply()
        self._update_markers()
        self._refresh_target_hint()

    def refresh(self) -> None:
        """非常停止・台数・接続の変化を反映する。"""
        self._sig = ()
        self.set_state(self._state)

    def trigger_primary(self) -> bool:
        """F5: 有効化 / 運転開始 / 故障をリセット(押せるときだけ)。"""
        if self.primary_kind in ("enable", "start", "reset") and self.primary.isEnabled() \
                and self.primary.isVisible():
            self._on_primary()
            return True
        return False

    def trigger_stop(self) -> bool:
        """F6: 運転停止(運転中だけ)。"""
        if self.primary_kind == "stop" and self.primary.isEnabled():
            self._on_primary()
            return True
        return False

    def apply_preset(self, key: str) -> None:
        """MIT プリセット(脱力・ダンパ・ばね・その場で保持)。値は出力段階の上限で切られる。"""
        form = self.forms["mit"]
        values = dict(MIT_PRESETS[key])
        if key == "hold":
            s = self._latest_state()
            values["position"] = s.position_multi if s is not None else 0.0
        for name, v in values.items():
            form.set_value(name, v)

    def deactivate(self) -> None:
        """ページが隠れた: 押下保持を解除する。"""
        self._end_all_holds("page_hidden")

    # ================================================================ モデルと画面の同期
    def _sync_model(self, m: md.MotorModel, s: MotorViewState) -> None:
        """外部で有効化・運転されたモータは、実際の状態に合わせて表示モードを決める。"""
        if s.ui_state in ("enabled", "running"):
            if not m.engaged:
                before = (m.mode, m.pos_style, m.wave)
                md.apply_derived(m, s.controller, s.run_mode)
                if (m.mode, m.pos_style, m.wave) != before:
                    self._sig = ()  # 表示するモードが変わった: 画面を組み直す
        elif s.ui_state in ("disabled", "offline", "fault"):
            if m.engaged and clock.now() - m.engaged_at > ENGAGE_GRACE_S:
                m.engaged = False

    def _load_model(self) -> None:
        """モータを切り替えたとき、記憶している入力をフォームへ戻す。"""
        m = self.model()
        self._loading = True
        try:
            if m is not None:
                for name, form in self.forms.items():
                    form.set_params(m.params[name])
                self.period_combo.setCurrentIndex(max(0, self.period_combo.findData(m.period_ms)))
                self.continuous_switch.setChecked(m.continuous)
                self.link_box.set_params(m.params.setdefault("bilateral", BilateralParams()))
                self._sync_turn_row(m)
        finally:
            self._loading = False
        self._load_model_mode_ui()

    def _load_model_mode_ui(self) -> None:
        m = self.model()
        self._loading = True
        try:
            if m is None:
                return
            self.style_seg.set_current(m.pos_style)
            self.wave_seg.set_current(m.wave)
            if m.mode in md.MAIN_MODES:
                self.mode_seg.set_current(m.mode)
                self.mode_seg.set_item_text("other", tr("operate.mode.other") + " ▾")
            else:
                self.mode_seg.set_current("other")
                self.mode_seg.set_item_text("other", tr(md.short_title(m.mode)) + " ▾")
            self._fill_followers(m)
        finally:
            self._loading = False

    def _fill_followers(self, m: md.MotorModel) -> None:
        others = [i.id for i in self.bridge.motors() if i.id != self._mid]
        self.link_box.set_followers(others, m.follower)
        m.follower = self.link_box.follower()

    def _refresh_heading(self) -> None:
        if self._mid is None:
            self.heading.setText(tr("operate.heading.none"))
            return
        names = {i.id: i.name for i in self.bridge.motors()}
        self.heading.setText(
            tr("operate.heading.motor", id=tr("ui.motor_id", id=self._mid),
               name=names.get(self._mid, "")).strip()
        )

    # ================================================================ 画面の反映
    def _apply(self) -> None:
        """状態・モードに応じて、モード選択・目標・主ボタン領域を組み直す。"""
        self._refresh_heading()
        m = self.model()
        ui = self.ui_state()
        has_motor = m is not None and self._mid is not None
        estop = self.bridge.estop_latched()
        readonly = self.bridge.connection_info().read_only  # ログ再生: 指令を送らない
        self.badge.setVisible(has_motor)
        # モータが無いときは操作部品を並べず、空状態だけを縦中央に出す
        for w in (self.head, self.head_divider, self.scroll, self.foot, self.foot_divider):
            w.setVisible(has_motor)
        self.empty.setVisible(not has_motor)
        if has_motor:
            self.badge.set_ui_state(ui)
        # ---- モード選択 ----
        selectable = has_motor and ui == "disabled" and not estop and not readonly
        if selectable:
            self.mode_seg.set_all_enabled(True)
        else:
            if not has_motor or ui == "offline":
                reason = tr("operate.mode.locked_offline")
            elif readonly:
                reason = tr("operate.mode.locked_readonly")
            elif estop:
                reason = tr("operate.mode.locked_estop")
            elif ui == "fault":
                reason = tr("operate.mode.locked_fault")
            else:
                reason = tr("operate.mode.locked")
            self.mode_seg.set_all_enabled(False, reason)
        for w in (self.style_seg, self.wave_seg):
            w.set_all_enabled(selectable, "" if selectable else tr("operate.mode.locked"))
        self.link_box.set_follower_enabled(selectable)
        # ---- 説明・目標 ----
        if m is not None:
            self.desc_label.setText(tr(md.mode_desc_key(m)))
            self.info_btn.setToolTip(tr(md.mode_info_key(m)))
            self.info_btn.setVisible(True)
            ctrl = md.controller_of(m)
        else:
            self.desc_label.setText("")
            self.info_btn.setVisible(False)
            ctrl = ""
        self.preset_box.setVisible(m is not None and m.mode == "mit")
        self.style_box.setVisible(m is not None and m.mode == "position")
        self.ref_box.setVisible(m is not None and m.mode == "position")
        turn = self._turn_mode()
        self.turn_box.setVisible(turn)
        self.wave_box.setVisible(m is not None and m.mode == "test")
        self.link_box.setVisible(m is not None and m.mode == "link")
        self.target_heading.setVisible(m is not None and m.mode != "link")
        for name, box in self._target_boxes.items():
            # 1 回転内の基準のときは、多回転の位置欄の代わりに角度の欄(turn_box)を出す
            hide_pos = turn and name in _POSITION_CTRLS
            box.setVisible(name == ctrl and m is not None and m.mode != "link" and not hide_pos)
        for name, box in self._limit_boxes.items():
            box.setVisible(name == ctrl and m is not None and m.mode != "link")
        self.continuous_row.setVisible(m is not None and m.mode in ("mit",))
        self.limits.setVisible(m is not None)
        # 入力は接続中なら常に編集できる(運転中の変更は即座に反映される)
        editable = has_motor and ui != "offline"
        for form in self.forms.values():
            form.set_editable(editable)
        self.turn_box.setEnabled(editable)
        ref_ok = editable and ui != "running"  # 運転中に基準を切り替えない(目標が急に変わるため)
        self.ref_seg.set_all_enabled(ref_ok, "" if ref_ok else tr("operate.mode.locked"))
        self.link_box.set_editable(editable)
        self.period_combo.setEnabled(editable and ui in ("disabled", "enabled"))
        self.continuous_switch.setEnabled(editable and ui in ("disabled", "enabled"))
        self._apply_footer(m, ui, estop, readonly)

    def _apply_footer(
        self, m: md.MotorModel | None, ui: str, estop: bool, readonly: bool = False
    ) -> None:
        """主ボタン領域。表示/非表示は最後に 1 回だけ設定する(押下保持中の JOG ボタンを隠さない)"""
        show_fault = show_primary = show_jog = show_secondary = False
        self.primary_kind = ""
        hint = ""
        if m is None or ui == "offline":
            hint = tr("operate.action.connect_hint")
        elif ui == "fault":
            self._show_fault()
            show_fault = show_primary = True
            self._set_primary("reset", tr("operate.action.reset_fault"), "accent", "rotate-ccw")
        elif ui == "disabled":
            show_primary = True
            self._set_primary("enable", tr("operate.action.enable"), "accent", "power")
            if readonly:
                self.primary.set_available(False, tr("operate.action.readonly"))
            elif estop:
                self.primary.set_available(False, tr("operate.action.estop_latched"))
            hint = tr("operate.action.hint_disabled")
        else:  # enabled / running
            show_secondary = True
            if m.mode == "jog":
                show_jog = True
                hint = tr("operate.action.jog_hint")
            elif ui == "enabled":
                show_primary = True
                single = m.mode == "mit" and not m.continuous
                label = tr("operate.action.send_once" if single else "operate.action.start")
                self._set_primary("start", label, "accent", "play")
                hint = tr("operate.action.hint_enabled")
            else:
                show_primary = True
                self._set_primary("stop", tr("operate.action.stop"), "standard", "square")
                hint = tr("operate.action.sending", hz=int(1000 / m.period_ms))
        self.fault_block.setVisible(show_fault)
        self.primary.setVisible(show_primary)
        self.jog_row.setVisible(show_jog)
        self.secondary.setVisible(show_secondary)
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))

    def _set_primary(self, kind: str, text: str, variant: str, icon_name: str) -> None:
        self.primary_kind = kind
        self.primary.setText(text)
        self.primary.set_variant(variant)
        self.primary.set_icon_name(icon_name)
        self.primary.set_available(True)

    def _show_fault(self) -> None:
        details = self.bridge.fault_details(self._mid) if self._mid is not None else []
        if details:
            self.fault_title.setText("、".join(d.name for d in details))
            self.fault_desc.setText(details[0].description)
            self.fault_remedy.setText(tr("operate.action.fault_remedy", remedy=details[0].remedy))
        else:
            names = "、".join(tr(f"fault.{k}") for k in (self._state.faults if self._state else ()))
            self.fault_title.setText(names)
            self.fault_desc.setText("")
            self.fault_remedy.setText("")
        self.fault_desc.setVisible(bool(self.fault_desc.text()))
        self.fault_remedy.setVisible(bool(self.fault_remedy.text()))

    def _update_markers(self) -> None:
        """スライダー上の実測位置の縦線。"""
        s = self._state
        if s is None:
            return
        for name in ("csp", "pp", "mit"):
            self.forms[name].set_actual("position", s.position_multi)
        self.forms["mit"].set_actual("velocity", s.velocity)
        self.forms["velocity"].set_actual("velocity", s.velocity)
        self.forms["jog"].set_actual("speed", abs(s.velocity))
        self.forms["current"].set_actual("iq", None)
        self.turn_row.set_actual(self._to_display(wrap_to_pi(s.position_multi)))

    # ================================================================ 入力
    def _on_mode_selected(self, key: str) -> None:
        m = self.model()
        if m is None or self._loading:
            return
        if key == "other":
            self._open_other_menu()
            return
        m.mode = key
        self._sig = ()
        self._load_model_mode_ui()
        self._apply()

    def _open_other_menu(self) -> None:
        m = self.model()
        if m is None:
            return
        menu = Menu(self)
        chosen: list[str] = []
        groups = md.other_menu(len(self.bridge.motors()))  # 2台連動はモータが 2 台以上のときだけ
        for gi, (group, entries) in enumerate(groups):
            if gi:
                menu.addSeparator()
            if group != GROUP_OTHER:  # プラグインが付けたグループ名は見出しとして出す
                menu.addAction(tr(group)).setEnabled(False)
            for key, title in entries:
                act = menu.addAction(tr(title))
                act.triggered.connect(lambda _c=False, k=key: chosen.append(k))
        self._other_menu = menu  # 参照を保持
        menu.aboutToHide.connect(lambda: QTimer.singleShot(0, lambda: self._after_other(chosen)))
        btn = self.mode_seg.button("other")
        menu.popup(btn.mapToGlobal(QPoint(0, btn.height() + 4)))

    def _after_other(self, chosen: list[str]) -> None:
        if not isValid(self):  # メニューを閉じた直後にページが破棄されることがある
            return
        m = self.model()
        if m is None:
            return
        if chosen:
            m.mode = chosen[-1]
            self._sig = ()
        self._load_model_mode_ui()  # 選ばなかったときは元のモードの選択に戻す
        self._apply()

    def pick_other(self, key: str) -> None:
        """「その他 ▾」のメニュー項目を選んだことにする(テスト・キーボード用)。"""
        self._after_other([key])

    def _on_style(self, key: str) -> None:
        m = self.model()
        if m is not None and not self._loading:
            m.pos_style = key
            self._sig = ()
            self._apply()

    def _on_wave(self, key: str) -> None:
        m = self.model()
        if m is None or self._loading:
            return
        prev = m.wave
        m.wave = key
        if prev != key:  # 対象の量は引き継ぐ
            self.forms[key].set_value("target", self.forms[prev].value("target"))
        self._sig = ()
        self._apply()

    def _on_follower(self, follower: object) -> None:
        m = self.model()
        if m is None or self._loading:
            return
        m.follower = follower if isinstance(follower, int) else None

    def _on_link_params(self, params: object) -> None:
        """2台連動の設定が変わった: 覚えておき、運転中ならそのまま反映する(目標の更新と同じ)。"""
        m = self.model()
        if m is None or self._loading or self._mid is None:
            return
        m.params["bilateral"] = params
        s = self._state
        running = s is not None and s.ui_state == "running" and s.controller == "bilateral"
        if running and m.follower is not None:
            self.bridge.update_controller_params(
                "bilateral", link_assign(self._mid, m.follower), params
            )

    def _on_period(self, _i: int) -> None:
        m = self.model()
        if m is not None and not self._loading:
            m.period_ms = int(self.period_combo.currentData())
            self._apply()

    def _on_continuous(self, on: bool) -> None:
        m = self.model()
        if m is not None and not self._loading:
            m.continuous = on
            self._apply()

    def _on_tier_changed(self, _tier: str) -> None:
        self._widen_edits()
        self.link_box.widen_edits()

    def _widen_edits(self) -> None:
        """出力段階の上限より大きい値を入力されても受け取れるよう、入力欄の範囲を物理レンジに戻す。

        スライダーは段階の上限までのまま。上限を超えた入力は `_on_field` が上限に切って注記を出す
        (赤いエラーで拒否するのではなく、段階に従って制限する)。
        """
        for form in self.forms.values():
            for f in dataclasses.fields(form.params()):
                row = form.row(f.name) if f.name in form._rows else None  # noqa: SLF001
                meta = getattr(row, "meta", None)
                if meta is None or not meta.get("limit"):
                    continue
                row.edit.set_range(float(meta.get("min", -1e9)), float(meta.get("max", 1e9)))

    def _on_field(self, ctrl: str, field: str, value: object) -> None:
        """入力が現在の出力段階の上限を超えていたら、上限に切って琥珀の注記を出す。"""
        form = self.forms[ctrl]
        row = form._rows.get(field)  # noqa: SLF001
        meta = getattr(row, "meta", None)
        if meta is None or not meta.get("limit") or not isinstance(value, float):
            return
        lo, hi = row.range()
        if lo <= value <= hi:
            # MIT の負の kd・摩擦補償は PC 側で計算する(暴走時は制動)ことを行内で知らせる
            if ctrl == "mit" and (
                (field == "kd" and value < 0.0) or (field == "friction_comp" and value > 0.0)
            ):
                row.set_note(tr("operate.target.host_note"), tone="neutral")
            return
        cap = hi if value > hi else lo
        form.set_value(field, cap)
        tier = self.bridge.power_limits().tier
        row.set_note(
            tr(
                "operate.target.clamped",
                tier=tr(f"tier.{tier}.short"),
                cap=fmt_short(abs(cap), 2),
                unit=str(meta.get("unit", "")),
            )
        )

    def _on_form_changed(self, name: str) -> None:
        m = self.model()
        if m is None or self._loading or self._mid is None:
            return
        params = self.forms[name].params()
        m.params[name] = params
        s = self._state
        if s is None or s.ui_state != "running" or not m.continuous:
            return
        if name == "jog" and s.controller == "jog":
            if self._jog_dir:
                self.bridge.update_controller_params(
                    "jog", {"motor": self._mid},
                    dataclasses.replace(params, speed=self._jog_dir * params.speed),
                )
        elif s.controller == name:
            self.bridge.update_controller_params(
                name, {"motor": self._mid}, self._resolved(name, params)
            )

    # ================================================================ 位置の目標の基準
    def _settings(self):  # noqa: ANN202
        get = getattr(self.shell, "settings", None)
        return get() if callable(get) else None

    def _load_target_refs(self) -> dict[str, str]:
        """保存済みの基準(モードごと)。無い・不正なら既定(1 回転内)。"""
        out = dict.fromkeys(TARGET_REF_MODES, DEFAULT_TARGET_REF)
        s = self._settings()
        if s is not None:
            for mode in out:
                v = str(s.value(TARGET_REF_KEY.format(mode=mode), DEFAULT_TARGET_REF))
                if v in TARGET_REFS:
                    out[mode] = v
        return out

    def target_ref(self, mode: str = "position") -> str:
        """位置モードの目標の基準: "turn"(1 回転内・最短)/ "multi"(多回転)。"""
        return self._target_refs.get(mode, DEFAULT_TARGET_REF)

    def set_target_ref(self, ref: str, mode: str = "position") -> None:
        """基準を切り替えて保存する。物理的な目標は変えず、入力欄の意味だけを変換する。"""
        if ref not in TARGET_REFS:
            raise ValueError(f"未知の目標の基準: {ref!r}")
        m = self.model()
        old = self.target_ref(mode)
        if m is not None and mode == "position" and ref != old:
            self._loading = True  # 切り替えで運転中の目標を動かさない(送らない)
            try:
                if ref == REF_MULTI_TURN:  # 1 回転内 → 多回転: いまの絶対目標を欄へ
                    self.forms[m.pos_style].set_value("position", self._target_abs())
                else:  # 多回転 → 1 回転内: 欄の値を 1 回転内の角度へ
                    m.target_theta = wrap_to_pi(float(self.forms[m.pos_style].value("position")))
                self._sync_turn_row(m)
            finally:
                self._loading = False
        self._target_refs[mode] = ref
        s = self._settings()
        if s is not None:
            s.setValue(TARGET_REF_KEY.format(mode=mode), ref)
            s.sync()
        self._loading = True
        try:
            self.ref_seg.set_current(ref)
        finally:
            self._loading = False
        self._sig = ()
        self._apply()
        self._refresh_target_hint()

    def _on_ref(self, key: str) -> None:
        if not self._loading and key != self.target_ref():
            self.set_target_ref(key)

    def _turn_mode(self) -> bool:
        m = self.model()
        return m is not None and m.mode == "position" and self.target_ref() == REF_WITHIN_TURN

    def set_angle_unit(self, deg: bool) -> None:
        """角度の表示単位(読み値カードの rad / deg 切替に合わせる)。"""
        if deg == self._deg:
            return
        self._deg = deg
        m = self.model()
        if m is not None:
            self._loading = True
            try:
                self._sync_turn_row(m)
            finally:
                self._loading = False
        self._refresh_target_hint()

    def angle_unit(self) -> str:
        return "deg" if self._deg else "rad"

    def _to_display(self, rad: float) -> float:
        return math.degrees(rad) if self._deg else rad

    def _from_display(self, v: float) -> float:
        return math.radians(v) if self._deg else v

    def _sync_turn_row(self, m: md.MotorModel) -> None:
        """角度の欄に、単位・範囲・値(モデルの θ)を反映する。"""
        if self._deg:
            self.turn_row.reconfigure("deg", -180.0, 180.0, 1.0, 1)
        else:
            self.turn_row.reconfigure("rad", -math.pi, math.pi, 0.01, 3)
        self.turn_row.set_value(self._to_display(m.target_theta))

    def _position_now(self) -> float:
        s = self._latest_state()
        return s.position_multi if s is not None else 0.0

    def _target_abs(self) -> float:
        """いまの目標(多回転)。運転中は送っている値、そうでなければ最短の等価角。"""
        m = self.model()
        running = self._state is not None and self._state.ui_state == "running"
        if self._sent_abs is not None and running:
            return self._sent_abs
        if m is None:
            return 0.0
        return nearest_target(m.target_theta, self._position_now())

    def _resolved(self, name: str, params: Any) -> Any:
        """送る Params。1 回転内の基準なら、位置を多回転の目標にして返す。"""
        if name in _POSITION_CTRLS and self._turn_mode():
            return dataclasses.replace(params, position=self._target_abs())
        return params

    def _on_turn_changed(self, value: float) -> None:
        """角度の欄が変わった。運転中なら新しい目標へ即座に切り替える(ライブ更新)。"""
        m = self.model()
        if m is None or self._loading or self._mid is None:
            return
        m.target_theta = wrap_to_pi(self._from_display(value))
        s = self._state
        if (
            s is not None and s.ui_state == "running" and m.continuous
            and s.controller in _POSITION_CTRLS
        ):
            self._sent_abs = nearest_target(m.target_theta, self._position_now())
            params = dataclasses.replace(self.forms[s.controller].params(), position=self._sent_abs)
            self.bridge.update_controller_params(s.controller, self._assign(), params)
        self._refresh_target_hint()

    def target_hint_text(self) -> str:
        return self.target_hint.text()

    def _refresh_target_hint(self) -> None:
        """「→ +401.20 rad へ(+0.83 rad)」: 結果の絶対位置と移動量。"""
        if not self._turn_mode() or self._state is None or self._state.ui_state == "offline":
            text = ""
        else:
            p = self._position_now()
            target = self._target_abs()
            dec = 1 if self._deg else 2
            text = tr(
                "operate.target.turn_hint",
                target=fmt_signed(self._to_display(target), dec),
                delta=fmt_signed(self._to_display(target - p), dec),
                unit=self.angle_unit(),
            )
        if text != self.target_hint.text():
            self.target_hint.setText(text)
        self.target_hint.setVisible(bool(text))

    # ================================================================ 操作
    def _assign(self) -> dict[str, int]:
        assert self._mid is not None
        return {"motor": self._mid}

    def _on_primary(self) -> None:
        kind = self.primary_kind
        if kind == "enable":
            self._do_enable()
        elif kind == "start":
            self._do_start()
        elif kind == "stop":
            self._do_stop()
        elif kind == "reset" and self._mid is not None:
            self.bridge.clear_fault([self._mid])

    def _do_enable(self) -> None:
        m = self.model()
        if m is None or self._mid is None:
            return
        # 位置を目標にするモードは、有効化したとき今の位置を目標にして急に動かないようにする
        s = self._latest_state()
        if s is not None and m.mode in ("position", "mit"):
            ctrl = md.controller_of(m)
            self.forms[ctrl].set_value("position", s.position_multi)
            if m.mode == "position":  # 1 回転内の基準でも、今の角度を目標にする
                m.target_theta = wrap_to_pi(s.position_multi)
                self._sync_turn_row(m)
        ids = [self._mid]
        if m.mode == "link":
            if m.follower is None:
                return
            ids.append(m.follower)
        m.engaged = True
        m.engaged_at = clock.now()
        self.bridge.enable(ids, md.run_mode_for(m))

    def _do_start(self) -> None:
        m = self.model()
        if m is None or self._mid is None:
            return
        if m.mode == "link":
            if m.follower is None:
                return
            self.bridge.start_controller(
                "bilateral", link_assign(self._mid, m.follower), self.link_box.params(),
                continuous=True, period_ms=m.period_ms,
            )
            return
        name = md.controller_of(m)
        params = self.forms[name].params()
        if self._turn_mode() and name in _POSITION_CTRLS:
            # 1 回転内の基準: いまの多回転位置から最短の等価角を、開始の瞬間に決めて送る
            self._sent_abs = nearest_target(m.target_theta, self._position_now())
            params = dataclasses.replace(params, position=self._sent_abs)
        self.bridge.start_controller(
            name, self._assign(), params, continuous=m.continuous, period_ms=m.period_ms,
        )

    def _do_stop(self) -> None:
        m = self.model()
        if m is None or self._mid is None:
            return
        if m.mode == "link":
            if m.follower is not None:
                self.bridge.stop_controller("bilateral", link_assign(self._mid, m.follower))
            return
        s = self._state
        name = s.controller if s is not None and s.controller else md.controller_of(m)
        self._sent_abs = None
        self.bridge.stop_controller(name, self._assign())

    def _on_disable(self) -> None:
        m = self.model()
        if self._mid is None:
            return
        self._end_all_holds("disable")
        ids = [self._mid]
        if m is not None and m.mode == "link" and m.follower is not None:
            ids.append(m.follower)  # 2台連動は 2 台とも無効化する
        if m is not None:
            m.engaged = False
        self._sent_abs = None
        self.bridge.disable(ids)

    # ---- JOG(押下保持。離す・外れる・フォーカスを失う・通信断で即停止)----
    def _jog_start(self, direction: float) -> None:
        m = self.model()
        if m is None or self._mid is None or self._jog_dir:
            return
        p = self.forms["jog"].params()
        self._jog_dir = direction
        self.bridge.start_controller(
            "jog", self._assign(), dataclasses.replace(p, speed=direction * p.speed),
            continuous=True, period_ms=m.period_ms,
        )

    def _jog_end(self, button: HoldButton, _reason: str) -> None:
        if self._key_hold is button:
            self._key_hold = None
        if not self._jog_dir:
            return
        self._jog_dir = 0.0
        if self._mid is not None:
            self.bridge.stop_controller("jog", self._assign())

    def jog_active(self) -> bool:
        return bool(self._jog_dir)

    def _end_all_holds(self, reason: str) -> None:
        for b in (self.jog_rev, self.jog_fwd):
            if b.is_holding():
                b.release(reason)
        self._key_hold = None

    # ---- キーボード: JOG で ← / → の長押し ----
    def _install_filters(self) -> None:
        self.installEventFilter(self)
        for w in self.findChildren(QWidget):
            w.installEventFilter(self)

    def _jog_ready(self) -> bool:
        m = self.model()
        return (
            m is not None and m.mode == "jog" and self.jog_row.isVisible()
            and self.jog_rev.isEnabled() and self.ui_state() in ("enabled", "running")
        )

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        et = event.type()
        if et in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and isinstance(event, QKeyEvent):
            key = event.key()
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self._jog_ready() \
                    and not isinstance(obj, QLineEdit):
                btn = self.jog_rev if key == Qt.Key.Key_Left else self.jog_fwd
                if event.isAutoRepeat():
                    return True
                if et == QEvent.Type.KeyPress:
                    if self._key_hold is None and not self._jog_dir:
                        self._key_hold = btn
                        btn.press()
                    return True
                if self._key_hold is btn:
                    btn.release("release")
                    return True
        return super().eventFilter(obj, event)

    def _on_focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        """キーで JOG している間にインスペクタの外へフォーカスが移ったら止める。"""
        if self._key_hold is not None and (new is None or not self.isAncestorOf(new)):
            self._end_all_holds("focus_out")

    def hideEvent(self, event: Any) -> None:  # noqa: N802
        self._end_all_holds("hidden")
        super().hideEvent(event)

