"""パラメータ表の「新しい値」の編集(ui_design.md §5.2)。

- セルは普段は平文(空 = 編集なし / 編集済みは Semibold の値)。ホバーで鉛筆、
  クリック・ダブルクリック・Enter・F2・文字キーで入力欄(`ValueEdit`)が開く(`ValueDelegate`)
- 入力欄は `NumberEdit`(アプリ共通の入力スタイル・範囲外は赤枠)を土台にする:
  - 空欄は「編集なし」(エラーにしない)
  - 入力のたびに `state_changed(値 or None, 問題)` を出す
    ([変更を書込] が入力途中の値を取りこぼさないため)
  - 範囲は ParamDef の最小/最大(片側だけの定義もある)と整数型の判定
- Esc は編集前の状態に戻して入力欄を閉じる。ただしモータが有効/運転中のときの Esc は
  アプリ全体の非常停止で、MainWindow のイベントフィルタが先に消費する(ui_design.md §9)
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QModelIndex,
    QObject,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from edulite05.app.i18n import tr
from edulite05.app.pages.params.groups import check_value, fmt_param, is_integer
from edulite05.app.ui import NumberEdit, icon
from edulite05.app.ui.style import make_font, tokens
from edulite05.app.ui.tokens import (
    FS_BODY,
    FS_CAPTION,
    H_CONTROL,
    ICON_S,
    R_CONTROL,
    SP_ICON,
    SP_RELATED,
    mix,
)
from edulite05.app.viewmodels import ParamDef

# セルのデータ役割
ROLE_DEF = Qt.ItemDataRole.UserRole + 1  # ParamDef(編集できるセルだけ)
ROLE_PROBLEM = Qt.ItemDataRole.UserRole + 2  # 入力の問題の理由("" で問題なし)
ROLE_EDIT_TEXT = Qt.ItemDataRole.UserRole + 3  # 入力欄を開くときの初期文字列
ROLE_REFERENCE = Qt.ItemDataRole.UserRole + 4  # モータの値(↑↓ の起点)
ROLE_SUB = Qt.ItemDataRole.UserRole + 5  # 名称セルの 2 行目(key と index)

StateCallback = Callable[[int, "float | None", str, str], None]  # (index, 値, 問題, 入力文字列)


class ValueEdit(NumberEdit):
    """`ValueEdit(def)`。`load(text, problem)` で外から内容を入れる(信号は出さない)。"""

    state_changed = Signal(object, str)  # float | None, 問題の理由("" で問題なし)

    def __init__(self, d: ParamDef, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._def = d
        self._reference: float | None = None  # モータの値(↑↓ の起点)
        # 大きさはセル(デリゲート)が決める
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setToolTip(tr("param_page.edit_tip"))
        self.textEdited.connect(self._emit_state)

    # ---- NumberEdit の差し替え ----
    def _check(self, text: str) -> tuple[float | None, str]:  # type: ignore[override]
        return check_value(self._def, text)

    def _commit(self) -> None:
        """Enter / フォーカス外れ: 有効な値は表示を整える。"""
        self.commit()

    def commit(self) -> None:
        self._emit_state(normalise=True)

    def _emit_state(self, _text: str = "", *, normalise: bool = False) -> None:
        v, msg = self._check(self.text())
        self._set_problem(msg)
        if v is not None and normalise:
            self.setText(fmt_param(v, self._def))
        self.state_changed.emit(v, msg)

    # ---- 外からの操作 ----
    def set_reference(self, v: float | int | None) -> None:
        self._reference = None if v is None else float(v)

    def load(self, text: str, problem: str = "") -> None:
        """内容を入れる(信号は出さない)。問題があれば赤枠にする。"""
        self.setText(text)
        self._set_problem(problem)

    def set_pending(self, v: float | None) -> None:
        """編集値を表示する(None で空欄)。入力の問題表示も消す。"""
        self.load("" if v is None else fmt_param(v, self._def))

    def type_text(self, text: str) -> None:
        """ユーザーが入力したのと同じ経路で文字列を入れる(キー入力での編集開始・テスト用)。"""
        self.setText(text)
        self._emit_state()

    def problem_text(self) -> str:
        return self._problem

    # ---- ↑↓ で桁に応じた刻み(Shift で 10 倍) ----
    def _step(self, base: float) -> float:
        if is_integer(self._def):
            return 1.0
        mag = abs(base)
        return 10.0 ** (math.floor(math.log10(mag)) - 1) if mag > 0 else 0.01

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            v, _msg = self._check(self.text())
            base = v if v is not None else (self._reference or 0.0)
            mult = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            sign = 1 if event.key() == Qt.Key.Key_Up else -1
            nv = base + sign * self._step(base) * mult
            lo, hi = self._def.minimum, self._def.maximum
            if lo is not None:
                nv = max(nv, lo)
            if hi is not None:
                nv = min(nv, hi)
            self.setText(fmt_param(round(nv, 9), self._def))
            self._emit_state(normalise=True)
            event.accept()
            return
        super().keyPressEvent(event)


class ValueDelegate(QStyledItemDelegate):
    """「新しい値」列のデリゲート。表示は平文、編集は `ValueEdit`。

    入力欄の状態(値・問題・入力文字列)は `on_state` でページへ渡す。ページが未書込と問題を持ち、
    セルのデータ(表示・ROLE_*)を更新する。
    """

    def __init__(self, on_state: StateCallback, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._on_state = on_state
        self._initial: dict[int, tuple[float | None, str, str]] = {}  # 入力欄 id → 開いた時点

    # ---- 描画 ----
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        d = index.data(ROLE_DEF)
        if d is None:  # 読み取り専用: 何も出さない(操作の手がかりも出さない)
            return
        t = tokens()
        r = option.rect
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        problem = str(index.data(ROLE_PROBLEM) or "")
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focus = bool(option.state & QStyle.StateFlag.State_HasFocus)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRect(r.left() + SP_ICON, r.top() + SP_ICON, r.width() - SP_ICON,
                    r.height() - 2 * SP_ICON)
        if hover or focus:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(mix(t.text_primary, t.bg_card, 0.08)))
            painter.drawRoundedRect(box, R_CONTROL, R_CONTROL)
        if focus:
            painter.setPen(QColor(t.accent))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(0, 0, -1, -1), R_CONTROL, R_CONTROL)
        # 左端の手がかり: 問題があれば赤い印、無ければホバー時だけ鉛筆
        mark = None
        if problem:
            mark = icon("circle-alert", "critical", ICON_S)
        elif hover:
            mark = icon("pencil", "text_tertiary", ICON_S)
        if mark is not None:
            mark.paint(painter, QRect(box.left() + SP_ICON, r.center().y() - ICON_S // 2 + 1,
                                      ICON_S, ICON_S))
        if text:
            painter.setFont(make_font(FS_BODY, semibold=True, mono=True))
            painter.setPen(QColor(t.critical if problem else t.text_primary))
            tr_rect = r.adjusted(ICON_S + SP_RELATED, 0, -SP_RELATED, 0)
            align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            painter.drawText(tr_rect, int(align), text)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), H_CONTROL)

    # ---- 編集 ----
    def createEditor(  # noqa: N802
        self, parent: QWidget, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QWidget | None:
        d: ParamDef | None = index.data(ROLE_DEF)
        if d is None:
            return None
        ed = ValueEdit(d, parent)
        ref = index.data(ROLE_REFERENCE)
        ed.set_reference(ref)
        ed.state_changed.connect(
            lambda v, msg, e=ed, i=d.index: self._on_state(i, v, msg, e.text())
        )
        return ed

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:  # noqa: N802
        if not isinstance(editor, ValueEdit) or id(editor) in self._initial:
            return  # 入力中にモデルが変わっても、入力欄は書き換えない
        problem = str(index.data(ROLE_PROBLEM) or "")
        text = str(index.data(ROLE_EDIT_TEXT) or "")
        editor.load(text, problem)
        v, _msg = check_value(index.data(ROLE_DEF), text)
        self._initial[id(editor)] = (v, problem, text)

    def setModelData(  # noqa: N802
        self, editor: QWidget, model: QAbstractItemModel, index: QModelIndex
    ) -> None:
        if isinstance(editor, ValueEdit):
            editor.commit()  # 表示を整えて、状態をページへ渡す

    def updateEditorGeometry(  # noqa: N802
        self, editor: QWidget, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        r = option.rect
        h = min(H_CONTROL, r.height())
        editor.setGeometry(QRect(r.left() + SP_ICON, r.top() + (r.height() - h) // 2,
                                 r.width() - SP_ICON, h))

    def destroyEditor(self, editor: QWidget, index: QModelIndex) -> None:  # noqa: N802
        self._initial.pop(id(editor), None)
        super().destroyEditor(editor, index)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            isinstance(obj, ValueEdit)
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Escape
        ):
            self.revert(obj)
            return True
        return super().eventFilter(obj, event)

    def revert(self, editor: ValueEdit) -> None:
        """開いた時点の状態に戻して閉じる(コミットしない)。"""
        v, problem, text = self._initial.get(id(editor), (None, "", ""))
        d = editor._def
        editor.load(text, problem)
        self._on_state(d.index, v, problem, text)
        self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.RevertModelCache)


class NameDelegate(QStyledItemDelegate):
    """名称列: 日本語名(14)+ 薄い 2 行目(key と index。12)。"""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        t = tokens()
        r = option.rect.adjusted(SP_RELATED, 0, -SP_RELATED, 0)
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        sub = str(index.data(ROLE_SUB) or "")
        painter.save()
        fm_t = make_font(FS_BODY)
        fm_s = make_font(FS_CAPTION)
        painter.setFont(fm_t)
        h_t = painter.fontMetrics().height()
        painter.setFont(fm_s)
        h_s = painter.fontMetrics().height()
        top = r.top() + (r.height() - h_t - h_s) // 2
        painter.setFont(fm_t)
        painter.setPen(QColor(t.text_primary))
        elide = Qt.TextElideMode.ElideRight
        align = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        painter.drawText(QRect(r.left(), top, r.width(), h_t), align,
                         painter.fontMetrics().elidedText(title, elide, r.width()))
        painter.setFont(fm_s)
        painter.setPen(QColor(t.text_tertiary))
        painter.drawText(QRect(r.left(), top + h_t, r.width(), h_s), align,
                         painter.fontMetrics().elidedText(sub, elide, r.width()))
        painter.restore()


__all__ = ["NameDelegate", "ValueDelegate", "ValueEdit"]
