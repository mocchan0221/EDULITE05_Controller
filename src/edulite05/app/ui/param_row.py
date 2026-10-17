"""パラメータ入力: NumberEdit / ParamRow(1 項目の行)/ ParamForm(dataclass から自動生成)。

ParamRow(ui_design.md §6): 1 行目 = ラベル … 右に値入力(右寄せ等幅)+ 単位 + ↺(既定に戻す)、
2 行目 = スライダー(実測位置の縦線付き)+ 両端の最小/最大(12 三次色)。ラベルと入力の間隔 8、行間 16。

ParamForm は Params dataclass の field metadata(`core/control/params.py`)から行を作る。
`limit` 付きの項目は `Bridge.power_limits()` の現在の出力段階の上限で範囲を動的に制限し、
値が切られたら行内に琥珀の文字で知らせる。
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics, QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.ui.button import Button
from edulite05.app.ui.inputs import Combo, Switch, ValueSlider
from edulite05.app.ui.label import Label
from edulite05.app.ui.segmented import Segmented
from edulite05.app.ui.style import make_font, repolish, set_prop
from edulite05.app.ui.tokens import (
    FS_BODY,
    H_CONTROL,
    SP_PAD,
    SP_RELATED,
)
from edulite05.app.viewmodels import PowerLimits

_EDIT_W = 96
_MINMAX_W = 44


def effective_range(meta: dict[str, Any], limits: PowerLimits | None) -> tuple[float, float]:
    """metadata の min/max を、`limit` キーの出力段階上限で制限した範囲。"""
    lo = float(meta.get("min", -math.inf))
    hi = float(meta.get("max", math.inf))
    key = meta.get("limit")
    if key and limits is not None:
        cap = float(getattr(limits, key))
        neg = meta.get("neg_limit")
        if neg:  # 負側に別の上限がある(MIT の kd: 負の減衰は PC 側で計算する)
            lo = max(lo, -float(getattr(limits, neg)))
        else:
            lo = max(lo, -cap) if lo < 0 else min(lo, cap)
        hi = min(hi, cap)
    return lo, hi


def fmt_value(v: float, decimals: int) -> str:
    """表示用の数値(符号は U+2212 にしない: 入力欄で再パースするため ASCII のまま)。"""
    return f"{v:.{decimals}f}"


def fmt_short(v: float, decimals: int) -> str:
    """最小/最大ラベル用: 末尾の 0 を落とし、桁は最大 2。"""
    text = f"{v:.{min(decimals, 2)}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


class NumberEdit(QLineEdit):
    """右寄せ等幅の数値入力。Enter / フォーカス外れで確定、範囲外は赤枠(確定しない)。

    - `committed(float)`: 有効な値が確定した
    - `problem(str)`: 入力の問題(空文字なら解消)
    ↑↓ で 1 ステップ(Shift で 10 倍)。Esc は使わない(運転中の Esc は非常停止)。
    """

    committed = Signal(float)
    problem = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setProperty("mono", True)
        self.setFixedSize(_EDIT_W, H_CONTROL)
        self._lo, self._hi = -math.inf, math.inf
        self._step = 0.01
        self._decimals = 3
        self._value = 0.0
        self._problem = ""
        self.textEdited.connect(self._live_check)
        self.editingFinished.connect(self._commit)

    def configure(self, lo: float, hi: float, step: float, decimals: int) -> None:
        self._lo, self._hi, self._step, self._decimals = lo, hi, step, decimals
        self.set_value(self._value)

    def set_range(self, lo: float, hi: float) -> None:
        self._lo, self._hi = lo, hi

    def value(self) -> float:
        return self._value

    def set_value(self, v: float) -> None:
        self._value = v
        self.setText(fmt_value(v, self._decimals))
        self._set_problem("")

    def has_problem(self) -> bool:
        return bool(self._problem)

    def _check(self, text: str) -> tuple[float | None, str]:
        try:
            v = float(text.strip().replace(",", ""))
        except ValueError:
            return None, tr("form.not_a_number")
        if math.isnan(v) or not (self._lo <= v <= self._hi):
            return None, tr(
                "form.out_of_range", lo=fmt_short(self._lo, self._decimals),
                hi=fmt_short(self._hi, self._decimals),
            )
        return v, ""

    def _set_problem(self, msg: str) -> None:
        if msg != self._problem:
            self._problem = msg
            set_prop(self, "invalid", bool(msg))
            self.problem.emit(msg)

    def _live_check(self, text: str) -> None:
        _v, msg = self._check(text)
        self._set_problem(msg)

    def _commit(self) -> None:
        v, msg = self._check(self.text())
        self._set_problem(msg)
        if v is None:
            return
        self.setText(fmt_value(v, self._decimals))
        if v != self._value:
            self._value = v
            self.committed.emit(v)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            mult = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            sign = 1 if event.key() == Qt.Key.Key_Up else -1
            v, _msg = self._check(self.text())
            base = self._value if v is None else v
            nv = min(max(base + sign * self._step * mult, self._lo), self._hi)
            self.setText(fmt_value(nv, self._decimals))
            self._commit()
            event.accept()
            return
        super().keyPressEvent(event)


class ParamRow(QWidget):
    """数値 1 項目。`ParamRow("速度", unit="rad/s", minimum=-5, maximum=5, default=0)`。

    - `value_changed(float)`: ユーザー操作(スライダー・入力・↺)で値が変わった
    - `set_value(v, emit=False)` / `set_range(lo, hi)`(値が切られたら True)
    - `set_actual(v)` … スライダー上の実測位置の縦線(None で消す)
    - `set_note("低出力上限で制限中")` … 行内の補足(琥珀)。`set_note(None)` で消す
    """

    value_changed = Signal(float)

    def __init__(
        self,
        label: str,
        *,
        unit: str = "",
        minimum: float = 0.0,
        maximum: float = 1.0,
        step: float = 0.01,
        decimals: int = 3,
        default: float = 0.0,
        value: float | None = None,
        tooltip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._decimals = decimals
        self._default = default
        self._lo, self._hi = minimum, maximum
        self._value = default if value is None else value
        self._ext_error: str | None = None
        self._edit_problem = ""
        self._note: tuple[str | None, str] | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(SP_RELATED)
        self.label = Label(label, "body")
        head.addWidget(self.label, 1)
        self.edit = NumberEdit()
        head.addWidget(self.edit)
        self.unit_label = Label(unit, "secondary")
        self._unit_w = QFontMetrics(make_font(FS_BODY)).horizontalAdvance(unit) + 2 if unit else 0
        self.unit_label.setFixedWidth(max(self._unit_w, 0))
        head.addWidget(self.unit_label)
        self.reset_btn = Button(variant="subtle", icon="rotate-ccw")
        self.reset_btn.setToolTip(tr("form.reset_default", default=fmt_value(default, decimals)))
        head.addWidget(self.reset_btn)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SP_RELATED)
        self.min_label = Label("", "caption-tertiary")
        self.max_label = Label("", "caption-tertiary")
        self.min_label.setFixedWidth(_MINMAX_W)
        self.max_label.setFixedWidth(_MINMAX_W)
        self.min_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.max_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.slider = ValueSlider()
        body.addWidget(self.min_label)
        body.addWidget(self.slider, 1)
        body.addWidget(self.max_label)
        root.addLayout(body)

        self.note_label = Label("", "caption", tone="caution")
        self.note_label.setVisible(False)
        self.note_label.setWordWrap(True)
        root.addWidget(self.note_label)

        self.edit.configure(minimum, maximum, step, decimals)
        self.slider.set_range(minimum, maximum, step)
        self._refresh_range_labels()
        self._write(self._value)
        if tooltip:
            self.setToolTip(tooltip)

        self.slider.value_changed.connect(self._on_slider)
        self.edit.committed.connect(self._on_edit)
        self.edit.problem.connect(self._on_problem)
        self.reset_btn.clicked.connect(self._on_reset)

    # ---- 公開 API ----
    def value(self) -> float:
        return self._value

    def set_value(self, v: float, emit: bool = False) -> None:
        v = min(max(v, self._lo), self._hi)
        changed = v != self._value
        self._value = v
        self._write(v)
        if emit and changed:
            self.value_changed.emit(v)

    def set_range(self, lo: float, hi: float) -> bool:
        """範囲を更新する。現在値が切られたら True。"""
        self._lo, self._hi = lo, hi
        self.edit.set_range(lo, hi)
        self.slider.set_range(lo, hi)
        self._refresh_range_labels()
        clamped = min(max(self._value, lo), hi)
        changed = clamped != self._value
        self._value = clamped
        self._write(clamped)
        return changed

    def range(self) -> tuple[float, float]:
        return self._lo, self._hi

    def reconfigure(
        self, unit: str, minimum: float, maximum: float, step: float, decimals: int
    ) -> None:
        """単位・範囲・刻み・桁数を取り替える(rad ⇔ deg の切替など)。値は範囲内に切る。"""
        self._decimals = decimals
        self._lo, self._hi = minimum, maximum
        self.unit_label.setText(unit)
        self._unit_w = QFontMetrics(make_font(FS_BODY)).horizontalAdvance(unit) + 2 if unit else 0
        self.unit_label.setFixedWidth(max(self._unit_w, self.unit_label.width()))
        self.edit.configure(minimum, maximum, step, decimals)
        self.slider.set_range(minimum, maximum, step)
        self._value = min(max(self._value, minimum), maximum)
        self._refresh_range_labels()
        self._write(self._value)

    def set_default(self, v: float) -> None:
        self._default = v
        self.reset_btn.setToolTip(tr("form.reset_default", default=fmt_value(v, self._decimals)))

    def set_actual(self, v: float | None) -> None:
        self.slider.set_marker(v)

    def set_note(self, text: str | None, tone: str = "caution") -> None:
        self._note = (text, tone) if text else None
        self._refresh_note()

    def set_error(self, text: str | None) -> None:
        """外部からの入力エラー(範囲外など)。入力欄を赤枠にして行内に理由を出す。"""
        self._ext_error = text or None
        set_prop(self.edit, "invalid", bool(text) or self.edit.has_problem())
        self._refresh_note()

    def set_unit_width(self, w: int) -> None:
        """フォーム内で単位の幅をそろえる(入力欄の右端がそろう)。"""
        self.unit_label.setFixedWidth(max(w, self._unit_w))

    def unit_width(self) -> int:
        return self._unit_w

    def set_enabled_editing(self, enabled: bool) -> None:
        for w in (self.edit, self.slider, self.reset_btn):
            w.setEnabled(enabled)

    # ---- 内部 ----
    def _write(self, v: float) -> None:
        self.edit.set_value(v)
        self.slider.set_value(v)

    def _refresh_range_labels(self) -> None:
        self.min_label.setText(fmt_short(self._lo, self._decimals))
        self.max_label.setText(fmt_short(self._hi, self._decimals))

    def _refresh_note(self) -> None:
        err = self._ext_error or self._edit_problem
        if err:
            self.note_label.setText(err)
            self.note_label.set_tone("critical")
            self.note_label.setVisible(True)
        elif self._note is not None:
            self.note_label.setText(self._note[0] or "")
            self.note_label.set_tone(self._note[1])
            self.note_label.setVisible(True)
        else:
            self.note_label.setText("")
            self.note_label.setVisible(False)

    def _on_problem(self, msg: str) -> None:
        self._edit_problem = msg
        self._refresh_note()
        repolish(self.edit)

    def _on_slider(self, v: float) -> None:
        self._value = v
        self.edit.set_value(v)
        self._note = None
        self._refresh_note()
        self.value_changed.emit(v)

    def _on_edit(self, v: float) -> None:
        self._value = v
        self.slider.set_value(v)
        self._note = None
        self._refresh_note()
        self.value_changed.emit(v)

    def _on_reset(self) -> None:
        self.set_value(self._default, emit=True)


class _ChoiceRow(QWidget):
    value_changed = Signal(str, object)

    def __init__(self, name: str, default: str, meta: dict[str, Any], parent: QWidget | None):
        super().__init__(parent)
        self.name = name
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP_RELATED)
        lay.addWidget(Label(tr(meta["label"]), "body"))
        choices = [(v, tr(k)) for v, k in meta["choices"]]
        self._seg: Segmented | None = None
        self._combo: Combo | None = None
        if len(choices) <= 3:
            self._seg = Segmented(choices, current=default)
            self._seg.selected.connect(lambda k: self.value_changed.emit(self.name, k))
            lay.addWidget(self._seg)
        else:
            self._combo = Combo()
            for v, text in choices:
                self._combo.addItem(text, v)
            self._combo.setCurrentIndex(max(0, self._combo.findData(default)))
            self._combo.currentIndexChanged.connect(
                lambda _i: self.value_changed.emit(self.name, self._combo.currentData())  # type: ignore[union-attr]
            )
            lay.addWidget(self._combo)

    def value(self) -> str:
        if self._seg is not None:
            return str(self._seg.current())
        return str(self._combo.currentData())  # type: ignore[union-attr]

    def set_value(self, v: str) -> None:
        if self._seg is not None:
            self._seg.set_current(v)
        elif self._combo is not None:
            self._combo.setCurrentIndex(max(0, self._combo.findData(v)))


class _SwitchRow(QWidget):
    value_changed = Signal(str, object)

    def __init__(self, name: str, default: bool, meta: dict[str, Any], parent: QWidget | None):
        super().__init__(parent)
        self.name = name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP_RELATED)
        lay.addWidget(Label(tr(meta["label"]), "body"), 1)
        self.switch = Switch(default)
        self.switch.toggled.connect(lambda on: self.value_changed.emit(self.name, on))
        lay.addWidget(self.switch)

    def value(self) -> bool:
        return self.switch.isChecked()

    def set_value(self, v: bool) -> None:
        self.switch.setChecked(bool(v))


class _FloatRowAdapter(ParamRow):
    """ParamForm 内の数値行(フィールド名を持つ)。"""

    field_changed = Signal(str, float)

    def __init__(self, name: str, default: float, meta: dict[str, Any], parent: QWidget | None):
        lo, hi = effective_range(meta, None)
        super().__init__(
            tr(meta["label"]),
            unit=str(meta.get("unit", "")),
            minimum=lo if math.isfinite(lo) else -1e9,
            maximum=hi if math.isfinite(hi) else 1e9,
            step=float(meta.get("step", 0.01)),
            decimals=int(meta.get("decimals", 3)),
            default=default,
            value=default,
            parent=parent,
        )
        self.name = name
        self.meta = meta
        if meta.get("neg_limit"):
            self.slider.set_zero_tick(True)  # 負側のある項目(MIT の kd)は 0 に目盛り
        self.value_changed.connect(lambda v: self.field_changed.emit(self.name, v))


class ParamForm(QWidget):
    """Params dataclass(クラスまたはインスタンス)から入力フォームを自動生成する。"""

    changed = Signal(str, object)  # フィールド名, 値
    params_changed = Signal(object)  # 全体(dataclass のコピー)

    def __init__(
        self, params: Any, bridge: Bridge | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._p = params() if isinstance(params, type) else dataclasses.replace(params)
        self._bridge = bridge
        self._limits: PowerLimits | None = bridge.power_limits() if bridge else None
        self._rows: dict[str, QWidget] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP_PAD)
        defaults = type(self._p)()  # 既定値(↺ で戻す先)はクラスの既定
        for f in dataclasses.fields(self._p):
            meta = dict(f.metadata)
            if "label" not in meta:
                continue
            value = getattr(self._p, f.name)
            row: QWidget
            if "choices" in meta:
                row = _ChoiceRow(f.name, value, meta, self)
                row.value_changed.connect(self._on_row_changed)  # type: ignore[attr-defined]
            elif isinstance(value, bool):
                row = _SwitchRow(f.name, value, meta, self)
                row.value_changed.connect(self._on_row_changed)  # type: ignore[attr-defined]
            else:
                row = _FloatRowAdapter(f.name, float(getattr(defaults, f.name)), meta, self)
                row.set_value(float(value))
                row.field_changed.connect(self._on_row_changed)  # type: ignore[attr-defined]
            self._rows[f.name] = row
            lay.addWidget(row)
        lay.addStretch(1)
        float_rows = [r for r in self._rows.values() if isinstance(r, _FloatRowAdapter)]
        if float_rows:
            w = max(r.unit_width() for r in float_rows)
            for r in float_rows:
                r.set_unit_width(w)
        self._apply_limits()
        if bridge is not None:
            bridge.power_tier_changed.connect(self._on_tier_changed)

    # ---- 公開 API ----
    def params(self) -> Any:
        return dataclasses.replace(self._p)

    def set_params(self, p: Any) -> None:
        for f in dataclasses.fields(p):
            if f.name in self._rows:
                self.set_value(f.name, getattr(p, f.name))

    def set_value(self, name: str, value: Any) -> None:
        row = self._rows[name]
        if isinstance(row, _FloatRowAdapter):
            row.set_value(float(value), emit=True)
        else:
            row.set_value(value)  # type: ignore[attr-defined]
            setattr(self._p, name, value)

    def value(self, name: str) -> Any:
        return getattr(self._p, name)

    def row(self, name: str) -> QWidget:
        return self._rows[name]

    def field_range(self, name: str) -> tuple[float, float]:
        row = self._rows[name]
        assert isinstance(row, _FloatRowAdapter)
        return row.range()

    def set_limits(self, limits: PowerLimits | None) -> None:
        self._limits = limits
        self._apply_limits()

    def set_actual(self, name: str, value: float | None) -> None:
        """スライダー上に実測位置の縦線を出す(数値行のみ)。"""
        row = self._rows.get(name)
        if isinstance(row, _FloatRowAdapter):
            row.set_actual(value)

    def set_editable(self, editable: bool) -> None:
        for r in self._rows.values():
            r.setEnabled(editable)

    # ---- 内部 ----
    def _apply_limits(self) -> None:
        for name, row in self._rows.items():
            if not isinstance(row, _FloatRowAdapter):
                continue
            lo, hi = effective_range(row.meta, self._limits)
            limited = bool(row.meta.get("limit")) and self._limits is not None
            if row.set_range(lo, hi):
                setattr(self._p, name, row.value())
                unit = str(row.meta.get("unit", ""))
                row.set_note(tr("form.clamped", cap=fmt_short(hi, 2), unit=unit))
                self.changed.emit(name, row.value())
                self.params_changed.emit(self.params())
            elif limited:
                row.set_note(None)
            tip = tr("form.range_tip", lo=f"{lo:g}", hi=f"{hi:g}")
            if limited and self._limits is not None:
                tip += "\n" + tr("form.limited_tip", tier=tr(f"tier.{self._limits.tier}.short"))
            if row.meta.get("hint"):
                tip += "\n" + tr(str(row.meta["hint"]))
            row.setToolTip(tip)

    def _on_tier_changed(self, _tier: str) -> None:
        if self._bridge is not None:
            self.set_limits(self._bridge.power_limits())

    def _on_row_changed(self, name: str, value: Any) -> None:
        setattr(self._p, name, value)
        row = self._rows.get(name)
        if isinstance(row, _FloatRowAdapter):
            row.set_note(None)
        self.changed.emit(name, value)
        self.params_changed.emit(self.params())
