"""2台連動(ui_design.md §5.1 のインスペクタ「その他 ▾ → 2台連動」)の操作部。

core のバイラテラル制御 `Bilateral`(phase4_5.md §O)を使う。画面は次の 2 点だけを決める:
- 操作対象 = リーダ、「追従させるモータ」= フォロワ(`start_controller("bilateral",
  {"leader": id, "follower": id}, BilateralParams)`。run_mode 0 = MIT の 2 台)
- `BilateralParams`(方式・剛性・減衰・位置差の扱い…)。出力段階(LOW/HIGH)の上限は
  Params の `limit` で入力欄の範囲に反映し、超える入力は上限に切って注記する。
  実際の制限は core の SafetyGuard が掛ける(UI は迂回しない)。

主な操作は今までどおりインスペクタ最下部の主ボタン 1 つ(有効化 → 運転開始 → 運転停止)。
ここには設定だけを置く。方式・剛性・減衰は常に見せ、それ以外は「詳細設定」へ畳む。
"""

from __future__ import annotations

import dataclasses
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.ui.inputs import Combo
from edulite05.app.ui.label import Label
from edulite05.app.ui.param_row import ParamForm, fmt_short
from edulite05.app.ui.section import Expander
from edulite05.app.ui.tokens import SP_ICON, SP_PAD
from edulite05.core.control.params import BilateralParams

ASSIGN_LEADER = "leader"
ASSIGN_FOLLOWER = "follower"


def link_assign(leader: int, follower: int) -> dict[str, int]:
    """`Bridge` の start / update / stop_controller に渡す割り当て。"""
    return {ASSIGN_LEADER: leader, ASSIGN_FOLLOWER: follower}


class LinkPanel(QWidget):
    """追従モータの選択 + 方式 + 剛性・減衰 + 詳細設定(畳む)。

    - `follower_changed(int | None)`: 追従させるモータが変わった(ユーザー操作)
    - `params_changed(BilateralParams)`: 設定が変わった(ユーザー操作。`set_params` では出さない)
    """

    follower_changed = Signal(object)
    params_changed = Signal(object)

    def __init__(self, bridge: Bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._loading = False
        self._follower: int | None = None
        self.form = ParamForm(BilateralParams, bridge, self)
        self.form.setVisible(False)  # 行だけを取り出して使う(値の保持と範囲の管理はフォームが担う)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP_PAD)

        # 追従させるモータ
        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(SP_ICON)
        box.addWidget(Label(tr("operate.target.link_follower"), "caption"))
        self.follower_combo = Combo()
        self.follower_combo.currentIndexChanged.connect(self._on_follower)
        box.addWidget(self.follower_combo)
        lay.addLayout(box)

        # 方式
        self.mode_combo = self._choice_row(lay, "mode")
        self.mode_combo.currentIndexChanged.connect(lambda _i: self._on_choice("mode"))
        # 剛性・減衰
        for name in ("stiffness", "damping"):
            lay.addWidget(self.form.row(name))
        self.note = Label("", "caption", wrap=True)
        lay.addWidget(self.note)

        # 詳細設定(位置差の扱い・収束速度・力の順送り・リーダ側の減衰)
        self.details = Expander(tr("operate.link.details"))
        cl = self.details.content_layout
        self.offset_mode_combo = self._choice_row(cl, "offset_mode")
        self.offset_mode_combo.currentIndexChanged.connect(
            lambda _i: self._on_choice("offset_mode")
        )
        for name in ("offset", "converge_speed", "force_ff", "leader_damping"):
            cl.addWidget(self.form.row(name))
        lay.addWidget(self.details)

        self.form.changed.connect(self._on_field)
        self.form.params_changed.connect(self._on_form_changed)
        self._sync_view()

    # ---- 構築の補助 ----
    def _choice_row(self, layout: Any, name: str) -> Combo:
        """選択肢の項目は(文言が長いので)セグメントではなくコンボで出す。"""
        meta = next(
            dict(f.metadata) for f in dataclasses.fields(BilateralParams) if f.name == name
        )
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SP_ICON)
        col.addWidget(Label(tr(meta["label"]), "body"))
        combo = Combo()
        for value, key in meta["choices"]:
            combo.addItem(tr(key), value)
        combo.setCurrentIndex(max(0, combo.findData(getattr(self.form.params(), name))))
        col.addWidget(combo)
        layout.addLayout(col)
        return combo

    # ---- 公開 API ----
    def follower(self) -> int | None:
        return self._follower

    def set_followers(self, ids: list[int], current: int | None) -> None:
        """追従先の候補(操作対象以外のモータ)。`current` が候補に無ければ先頭にする。"""
        self.follower_combo.blockSignals(True)
        self.follower_combo.clear()
        for mid in ids:
            self.follower_combo.addItem(tr("ui.motor_id", id=mid), mid)
        if current not in ids:
            current = ids[0] if ids else None
        if current is not None:
            self.follower_combo.setCurrentIndex(self.follower_combo.findData(current))
        self.follower_combo.blockSignals(False)
        self._follower = current
        self._sync_view()

    def params(self) -> BilateralParams:
        return self.form.params()

    def set_params(self, p: BilateralParams) -> None:
        """モータごとに覚えた設定を表示へ戻す(`params_changed` は出さない)。"""
        self._loading = True
        try:
            self.form.set_params(p)
            for name, combo in (
                ("mode", self.mode_combo), ("offset_mode", self.offset_mode_combo)
            ):
                self.form.set_value(name, getattr(p, name))
                combo.blockSignals(True)
                combo.setCurrentIndex(max(0, combo.findData(getattr(p, name))))
                combo.blockSignals(False)
        finally:
            self._loading = False
        self._sync_view()

    def set_editable(self, editable: bool) -> None:
        self.form.set_editable(editable)
        for c in (self.mode_combo, self.offset_mode_combo):
            c.setEnabled(editable)

    def set_follower_enabled(self, enabled: bool) -> None:
        """追従先はモード選択と同じく、停止中(disabled)だけ変えられる。"""
        self.follower_combo.setEnabled(enabled)

    def widen_edits(self) -> None:
        """段階の上限より大きい入力も受け取れるよう、入力欄の範囲を物理レンジに戻す。

        スライダーは段階の上限までのまま。超えた入力は `_on_field` が上限に切って注記する。"""
        for f in dataclasses.fields(self.form.params()):
            row = self.form.row(f.name) if f.name in self.form._rows else None  # noqa: SLF001
            meta = getattr(row, "meta", None)
            if meta is None or not meta.get("limit"):
                continue
            row.edit.set_range(float(meta.get("min", -1e9)), float(meta.get("max", 1e9)))

    # ---- 内部 ----
    def _on_follower(self, _i: int) -> None:
        if self._loading:
            return
        self._follower = self.follower_combo.currentData()
        self._sync_view()
        self.follower_changed.emit(self._follower)

    def _on_choice(self, name: str) -> None:
        combo = self.mode_combo if name == "mode" else self.offset_mode_combo
        if self._loading or combo.currentData() is None:
            return
        self.form.set_value(name, combo.currentData())
        self._sync_view()
        self.params_changed.emit(self.form.params())

    def _on_form_changed(self, _p: object) -> None:
        if not self._loading:
            self.params_changed.emit(self.form.params())

    def _on_field(self, name: str, value: object) -> None:
        """入力が現在の出力段階の上限を超えていたら、上限に切って琥珀の注記を出す。"""
        row = self.form._rows.get(name)  # noqa: SLF001
        meta = getattr(row, "meta", None)
        if meta is None or not meta.get("limit") or not isinstance(value, float):
            return
        lo, hi = row.range()
        if lo <= value <= hi:
            return
        cap = hi if value > hi else lo
        self.form.set_value(name, cap)
        tier = self._bridge.power_limits().tier
        row.set_note(tr(
            "operate.target.clamped", tier=tr(f"tier.{tier}.short"),
            cap=fmt_short(abs(cap), 2), unit=str(meta.get("unit", "")),
        ))

    def _sync_view(self) -> None:
        """方式に合わせて、説明とリーダ側の減衰(マスタ・スレーブのときだけ)を切り替える。"""
        p = self.form.params()
        self.form.row("leader_damping").setVisible(p.mode == "master_slave")
        if self._follower is None:
            self.note.setText(tr("operate.action.need_two"))
            return
        key = "note_master" if p.mode == "master_slave" else "note_symmetric"
        self.note.setText(tr(f"operate.link.{key}", follower=tr("ui.motor_id", id=self._follower)))


__all__ = ["ASSIGN_FOLLOWER", "ASSIGN_LEADER", "LinkPanel", "link_assign"]
