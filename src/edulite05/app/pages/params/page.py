"""パラメータページ本体(ui_design.md §5.2)。

- 検索 + グループ(すべて/制御ゲイン/制限/指令/状態/設定)で絞り込み。表は全行ぶんを一度だけ作り、
  絞り込みは行の表示/非表示で行う
- 「新しい値」は平文のセル(空 = 編集なし)。クリック・ダブルクリック・Enter・F2・文字キーで
  入力欄が開き(`ValueDelegate`)、確定した値は未書込として溜まり(モータごと)、
  [変更を書込] で一括送信する。
  書込と Flash 保存は、選択中のモータが `disabled` のときだけ(それ以外はボタン無効 + 理由)
- 状態列: ✎ = 編集中(未書込)/ ● = 書込済・Flash 未保存。行全体の塗りは使わない
- 入力の問題(範囲外など)は、そのセルを赤い印 + 赤字にし、理由を同じ行の右端の空き列に出す
- v1 の JSON 書き出し/読み込みは [⋯] メニューへ。読み込み・既定値に戻すは差分プレビューを見せてから
  編集値にする(モータへは [変更を書込] を押すまで送らない)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.pages.base import PageBase, ShellApi
from edulite05.app.pages.params.edit import (
    ROLE_DEF,
    ROLE_EDIT_TEXT,
    ROLE_PROBLEM,
    ROLE_REFERENCE,
    ROLE_SUB,
    NameDelegate,
    ValueDelegate,
    ValueEdit,
)
from edulite05.app.pages.params.groups import (
    GROUPS,
    TRANSIENT_GROUPS,
    fmt_cell,
    fmt_param,
    group_of,
    group_title,
    is_motion_locked,
    is_writable,
    key_text,
    range_cell,
    range_text,
    same_value,
    title_of,
)
from edulite05.app.ui import (
    Button,
    ConfirmDialog,
    EmptyState,
    Label,
    Menu,
    Segmented,
    icon,
    pixmap,
    theme_manager,
)
from edulite05.app.ui.dialogs import FlashItem, confirm_flash_save
from edulite05.app.ui.style import make_font, tokens
from edulite05.app.ui.tokens import (
    FS_CAPTION,
    H_CONTROL,
    ICON_S,
    SP_GROUP,
    SP_ICON,
    SP_PAD,
    SP_RELATED,
    SP_SECTION,
)
from edulite05.app.ui.util import hbox, vbox
from edulite05.app.viewmodels import ParamDef, ParamValue

# 列(フォームのようにコンパクトに: 値の列を名称の近くにまとめ、余白は右端の空列が受け持つ)
C_STATE, C_NAME, C_VALUE, C_NEW, C_UNIT, C_RANGE, C_DEFAULT, C_FILL = range(8)
_COL_KEYS = ("state", "name", "value", "new", "unit", "range", "default", "")
_COL_WIDTHS = {
    C_STATE: 40, C_NAME: 240, C_VALUE: 110, C_NEW: 120, C_UNIT: 72, C_RANGE: 124, C_DEFAULT: 90,
}

# 行の高さ(名称 2 行 + 入力欄 32 が収まる高さ)
ROW_H = H_CONTROL + SP_RELATED
ROW_H_GROUP = H_CONTROL + SP_ICON

MAX_PREVIEW_LINES = 10  # 確認ダイアログに並べる項目数の上限

Change = tuple[ParamDef, float | int | None, float]  # (定義, 現在の値, 新しい値)


@dataclass(slots=True)
class _Row:
    d: ParamDef
    row: int
    group: str
    editable: bool
    motion_locked: bool = False  # 運転系(値は見せるが編集欄は出さない。理由を右端に出す)


class _ParamTable(QTableWidget):
    """キー操作での編集開始(Enter / F2 / 文字キー)を `edit_requested(行, 最初の文字)` で通知。"""

    edit_requested = Signal(int, str)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self.state() != QAbstractItemView.State.EditingState and self.currentRow() >= 0:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2):
                self.edit_requested.emit(self.currentRow(), "")
                event.accept()
                return
            text = event.text()
            mods = (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            )
            if text.strip() and text.isprintable() and not (event.modifiers() & mods):
                self.edit_requested.emit(self.currentRow(), text)
                event.accept()
                return
        super().keyPressEvent(event)


class _Toolbar(QWidget):
    """左: 検索 + グループ / 右: 件数 + ボタン。幅が足りなければ右を 2 行目へ回す。"""

    def __init__(self, left: QWidget, right: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._left, self._right = left, right
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(SP_GROUP)
        self._grid.setVerticalSpacing(SP_RELATED)
        self._wrapped: bool | None = None
        self._layout_for(False)

    def needed_width(self) -> int:
        return self._left.sizeHint().width() + SP_GROUP + self._right.sizeHint().width()

    def wrapped(self) -> bool:
        return bool(self._wrapped)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """狭いときは 2 行に折り返せるので、最小幅は広い方のグループの幅(高さは折返し時)。"""
        w = max(self._left.minimumSizeHint().width(), self._right.minimumSizeHint().width())
        return QSize(w, 2 * H_CONTROL + SP_RELATED)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.needed_width(), H_CONTROL)

    def _layout_for(self, wrapped: bool) -> None:
        if wrapped == self._wrapped:
            return
        self._wrapped = wrapped
        for w in (self._left, self._right):
            self._grid.removeWidget(w)
        left_align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        right_align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 0)
        if wrapped:
            self._grid.addWidget(self._left, 0, 0, 1, 2, left_align)
            self._grid.addWidget(self._right, 1, 0, 1, 2, right_align)
            self.setFixedHeight(2 * H_CONTROL + SP_RELATED)
        else:
            self._grid.addWidget(self._left, 0, 0, 1, 1, left_align)
            self._grid.addWidget(self._right, 0, 1, 1, 1, right_align)
            self.setFixedHeight(H_CONTROL)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        self._layout_for(self.width() < self.needed_width())
        super().resizeEvent(event)


class ParamsPage(PageBase):
    """パラメータページ。操作対象はシェルの選択中モータに追従する。"""

    def __init__(
        self, bridge: Bridge, shell: ShellApi | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(bridge, shell, parent)
        self._defs: list[ParamDef] = bridge.param_definitions()
        self._motor = bridge.selected_motor()
        self._values: dict[int, dict[int, ParamValue]] = {}
        self._pending: dict[int, dict[int, float]] = {}  # モータ → index → 新しい値
        self._problems: dict[int, str] = {}  # 選択中モータの入力の問題(index → 理由)
        self._bad_text: dict[int, str] = {}  # 選択中モータの不正な入力文字列(index → 入力のまま)
        self._rows: dict[int, _Row] = {}
        self._by_table_row: dict[int, int] = {}  # 表の行 → index
        self._header_rows: dict[str, int] = {}
        self._active = False
        self._read_requested: set[int] = set()
        self._toast_on_read = False
        self._ui_state = "offline"

        outer = vbox(self, margins=(SP_SECTION, SP_PAD, SP_SECTION, SP_PAD), spacing=0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        self.content = QWidget()
        lay = vbox(self.content, spacing=SP_RELATED)
        lay.addWidget(self._build_toolbar())
        lay.addLayout(self._build_lock_row())
        # 0x70xx は運転用の値で、電源を切ると工場値に戻る(実測 HW-6)。控えめな一行で常に伝える
        self.runtime_hint = Label(tr("param_page.runtime_hint"), "caption", wrap=True)
        lay.addWidget(self.runtime_hint)
        self._build_table()
        lay.addWidget(self.table, 1)
        self.stack.addWidget(self.content)

        self.empty = EmptyState(
            "plug", tr("param_page.empty_title"), tr("param_page.empty_desc"),
            action=tr("param_page.empty_action"),
        )
        self.empty.action_clicked.connect(self._open_connection)
        self.stack.addWidget(self.empty)

        self._build_rows()
        self._connect()
        self._refresh_all()

    # ================================================================ 構築
    def _build_toolbar(self) -> QWidget:
        """左に検索とグループ、右に件数とボタン(件数はボタンのすぐ左)。"""
        left = QWidget()
        ll = hbox(left, spacing=SP_GROUP)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("param_page.search_placeholder"))
        self.search.setFixedWidth(220)
        self._search_action = self.search.addAction(
            icon("search", "text_tertiary"), QLineEdit.ActionPosition.LeadingPosition
        )
        ll.addWidget(self.search)
        items = [("all", tr("param_page.group.all"))] + [(g, group_title(g)) for g in GROUPS]
        self.seg = Segmented(items, current="all")
        ll.addWidget(self.seg)

        right = QWidget()
        rl = hbox(right, spacing=SP_RELATED)
        self.counter_label = Label("", "secondary")
        rl.addWidget(self.counter_label)
        rl.addSpacing(SP_RELATED)
        self.read_btn = Button(tr("param_page.read"), "standard", icon="refresh-cw")
        self.write_btn = Button(tr("param_page.write"), "standard", icon="upload")
        self.save_btn = Button(tr("param_page.save_flash"), "standard", icon="save")
        self.more_btn = Button(icon="ellipsis", variant="standard", tooltip=tr("param_page.more"))
        for b in (self.read_btn, self.write_btn, self.save_btn, self.more_btn):
            rl.addWidget(b)
        self.toolbar = _Toolbar(left, right)
        return self.toolbar

    def _build_lock_row(self) -> QHBoxLayout:
        """書込できない理由の常設の一行(ロック中だけ出る)。"""
        row = hbox(spacing=SP_RELATED)
        self.lock_icon = Label("")
        self.lock_icon.setFixedSize(ICON_S, ICON_S)
        self.lock_note = Label("", "caption")
        row.addWidget(self.lock_icon)
        row.addWidget(self.lock_note)
        row.addStretch(1)
        return row

    def _build_table(self) -> None:
        t = _ParamTable(0, len(_COL_KEYS))
        self.table = t
        t.setHorizontalHeaderLabels([tr(f"param_page.col_{k}") if k else "" for k in _COL_KEYS])
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(ROW_H)
        t.setShowGrid(False)
        t.setAlternatingRowColors(False)  # 行の塗り分けはしない(§5.2)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)  # 開始は自前(下の接続)
        t.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        t.setWordWrap(False)
        t.setMouseTracking(True)  # ホバーの鉛筆
        t.setStyleSheet(
            "QTableView::item:hover, QTableView::item:selected { background: transparent; }"
        )
        t.setIconSize(QSize(ICON_S, ICON_S))
        t.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hh = t.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setSectionsClickable(False)
        hh.setMinimumSectionSize(ICON_S * 2)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for c in range(len(_COL_KEYS)):
            if c in _COL_WIDTHS:
                hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
                t.setColumnWidth(c, _COL_WIDTHS[c])
            else:
                hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for c in (C_VALUE, C_NEW, C_DEFAULT):
            item = t.horizontalHeaderItem(c)
            if item is not None:
                item.setTextAlignment(right)
        self.value_delegate = ValueDelegate(self._on_edit_state, t)
        t.setItemDelegateForColumn(C_NEW, self.value_delegate)
        t.setItemDelegateForColumn(C_NAME, NameDelegate(t))
        t.cellClicked.connect(self._on_cell_clicked)
        t.cellDoubleClicked.connect(self._on_cell_double_clicked)
        t.currentCellChanged.connect(self._on_current_cell)
        t.edit_requested.connect(self._on_edit_requested)

    def _build_rows(self) -> None:
        """グループ順(制御ゲイン → 制限 → 指令 → 状態 → 設定)に、見出し行 + パラメータ行を作る。"""
        by_group: dict[str, list[ParamDef]] = {g: [] for g in GROUPS}
        for d in self._defs:
            by_group[group_of(d)].append(d)
        t = self.table
        for g in GROUPS:
            defs = sorted(by_group[g], key=lambda d: d.index)
            if not defs:
                continue
            r = t.rowCount()
            t.insertRow(r)
            head = QTableWidgetItem(tr("param_page.group_header", name=group_title(g), n=len(defs)))
            head.setFlags(Qt.ItemFlag.ItemIsEnabled)
            head.setFont(make_font(FS_CAPTION, semibold=True))
            t.setItem(r, 0, head)
            t.setSpan(r, 0, 1, len(_COL_KEYS))
            t.setRowHeight(r, ROW_H_GROUP)
            self._header_rows[g] = r
            for d in defs:
                self._add_param_row(d, g)

    def _item(self, r: int, c: int) -> QTableWidgetItem:
        it = self.table.item(r, c)
        assert it is not None
        return it

    def _add_param_row(self, d: ParamDef, group: str) -> None:
        t = self.table
        r = t.rowCount()
        t.insertRow(r)
        locked = is_motion_locked(d)
        row = _Row(d, r, group, is_writable(d) and not locked, locked)
        self._rows[d.index] = row
        self._by_table_row[r] = d.index
        t.setRowHeight(r, ROW_H)
        for c in range(len(_COL_KEYS)):
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            t.setItem(r, c, it)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        self._item(r, C_STATE).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._item(r, C_VALUE).setTextAlignment(right)
        self._item(r, C_UNIT).setTextAlignment(left)
        self._item(r, C_RANGE).setTextAlignment(left)
        self._item(r, C_DEFAULT).setTextAlignment(right)
        self._item(r, C_FILL).setTextAlignment(left)
        small_mono = make_font(FS_CAPTION, mono=True)
        self._item(r, C_VALUE).setFont(make_font(mono=True))
        self._item(r, C_RANGE).setFont(small_mono)
        self._item(r, C_DEFAULT).setFont(small_mono)
        self._item(r, C_FILL).setFont(make_font(FS_CAPTION))
        self._item(r, C_UNIT).setText(d.unit)
        self._item(r, C_RANGE).setText(range_cell(d))
        self._item(r, C_RANGE).setToolTip(range_text(d))
        self._item(r, C_DEFAULT).setText(fmt_cell(d.default, d))

        # 名称: 日本語名(14)+ 薄い 2 行目(key と index)。描画は NameDelegate
        name = self._item(r, C_NAME)
        name.setText(title_of(d))
        name.setData(ROLE_SUB, key_text(d))
        name.setToolTip(f"{title_of(d)}\n{key_text(d)} ({d.dtype})\n{d.description}")

        # 新しい値: 読み取り専用は何も出さない(編集不可・手がかりなし)
        if row.editable:
            new = self._item(r, C_NEW)
            new.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEditable
            )
            new.setData(ROLE_DEF, d)
            new.setData(ROLE_PROBLEM, "")
            new.setTextAlignment(right)
        elif locked:
            # 運転系: 値は見える。編集欄の代わりに理由を右端の空き列とツールチップに出す
            self._item(r, C_NEW).setToolTip(tr("param_page.motion_locked_tip"))
            fill = self._item(r, C_FILL)
            fill.setText(tr("param_page.motion_locked"))
            fill.setToolTip(tr("param_page.motion_locked_tip"))

    def _connect(self) -> None:
        b = self.bridge
        b.params_updated.connect(self._on_params)
        b.selected_motor_changed.connect(self._on_motor)
        b.state_updated.connect(self._on_states)
        b.motors_changed.connect(self._on_motors)
        b.connection_changed.connect(self._on_connection)
        self.search.textChanged.connect(self._apply_filter)
        self.seg.current_changed.connect(self._apply_filter)
        self.read_btn.clicked.connect(self.read_from_motor)
        self.write_btn.clicked.connect(self.write_pending)
        self.save_btn.clicked.connect(self.save_flash)
        self.more_btn.clicked.connect(self._open_more_menu)
        theme_manager().changed.connect(self._on_theme)

    # ================================================================ ページの出入り
    def activated(self) -> None:
        self._active = True
        self._ensure_read()

    def deactivated(self) -> None:
        self._active = False
        self.end_edit()

    # ================================================================ 状態の取得
    def motor_id(self) -> int:
        return self._motor

    def _has_motor(self) -> bool:
        return bool(self.bridge.motors()) and self.bridge.connection_info().connected

    def _current_ui_state(self) -> str:
        s = self.bridge.states().get(self._motor)
        return s.ui_state if s is not None else "offline"

    def motor_value(self, index: int) -> float | int | None:
        pv = self._values.get(self._motor, {}).get(index)
        return pv.value if pv is not None else None

    def pending(self) -> dict[int, float]:
        """選択中モータの未書込(index → 新しい値)。"""
        return dict(self._pending.get(self._motor, {}))

    def dirty_indices(self) -> list[int]:
        """選択中モータの「書込済・Flash 未保存」の index。"""
        return sorted(i for i, pv in self._values.get(self._motor, {}).items() if pv.dirty)

    def visible_indices(self) -> list[int]:
        return [i for i, r in self._rows.items() if not self.table.isRowHidden(r.row)]

    def has_problems(self) -> bool:
        return bool(self._problems)

    def problem_of(self, index: int) -> str:
        """その項目の入力の問題(無ければ空)。"""
        return self._problems.get(index, "")

    def cell_text(self, index: int) -> str:
        """「新しい値」セルの表示(空 = 編集なし)。"""
        return self._item(self._rows[index].row, C_NEW).text()

    def lock_reason(self) -> str:
        """書込・Flash 保存ができない理由(できるなら空)。"""
        if self.bridge.connection_info().read_only:  # ログ再生: 何も送らない
            return tr("param_page.lock_replay")
        return {
            "offline": tr("param_page.lock_offline"),
            "enabled": tr("param_page.lock_enabled"),
            "running": tr("param_page.lock_running"),
            "fault": tr("param_page.lock_fault"),
        }.get(self._ui_state, "")

    # ================================================================ 入力欄(デリゲートのエディタ)
    def _open_editors(self) -> list[ValueEdit]:
        # 閉じた入力欄は隠れたまま deleteLater を待つので、隠れているものは数えない
        return [e for e in self.table.viewport().findChildren(ValueEdit) if not e.isHidden()]

    def edit_widget(self, index: int) -> ValueEdit | None:
        """その項目の入力欄(開いているときだけ)。"""
        for ed in self._open_editors():
            if ed._def.index == index:
                return ed
        return None

    def begin_edit(self, index: int) -> ValueEdit | None:
        """その項目の入力欄を開く(読み取り専用なら None)。"""
        row = self._rows.get(index)
        if row is None or not row.editable:
            return None
        ed = self.edit_widget(index)
        if ed is not None:
            return ed
        self.end_edit()
        self.table.scrollToItem(self._item(row.row, C_NEW))
        self.table.setCurrentCell(row.row, C_NEW)
        self.table.editItem(self._item(row.row, C_NEW))
        return self.edit_widget(index)

    def end_edit(self) -> None:
        """開いている入力欄を確定して閉じる(入力は未書込・問題として残る)。"""
        for ed in self._open_editors():
            ed.commit()
            self.value_delegate.closeEditor.emit(ed, QAbstractItemDelegate.EndEditHint.NoHint)

    def _index_at_row(self, r: int) -> int | None:
        return self._by_table_row.get(r)

    def _on_cell_clicked(self, r: int, c: int) -> None:
        idx = self._index_at_row(r)
        if idx is not None and c == C_NEW:
            self.begin_edit(idx)

    def _on_cell_double_clicked(self, r: int, _c: int) -> None:
        idx = self._index_at_row(r)
        if idx is not None:
            self.begin_edit(idx)

    def _on_current_cell(self, r: int, c: int, _pr: int, _pc: int) -> None:
        """カーソルは編集できる行では常に「新しい値」のセルに置く(キー操作の目印)。"""
        idx = self._index_at_row(r)
        if idx is not None and c != C_NEW and self._rows[idx].editable:
            self.table.setCurrentCell(r, C_NEW)

    def _on_edit_requested(self, r: int, text: str) -> None:
        idx = self._index_at_row(r)
        if idx is None:
            return
        ed = self.begin_edit(idx)
        if ed is not None and text:
            ed.type_text(text)

    # ================================================================ Bridge → 表示
    def _on_motor(self, motor_id: int) -> None:
        self.end_edit()
        self._motor = motor_id
        self._problems.clear()
        self._bad_text.clear()
        self._refresh_all()
        if self._active:
            self._ensure_read()

    def _on_motors(self, motors: list) -> None:
        ids = {m.id for m in motors}
        for cache in (self._values, self._pending):  # いなくなったモータの値と編集は捨てる
            for mid in [m for m in cache if m not in ids]:
                del cache[mid]
        self._read_requested &= ids
        self._refresh_all()
        if self._active:
            self._ensure_read()

    def _on_connection(self, _info: object) -> None:
        self._refresh_all()
        if self._active:
            self._ensure_read()

    def _on_theme(self, _name: str = "") -> None:
        self._refresh_all()

    def _on_states(self, _states: object) -> None:
        state = self._current_ui_state()
        if state != self._ui_state:
            self._ui_state = state
            self._update_actions()

    def _on_params(self, motor_id: int, values: list[ParamValue]) -> None:
        self._values[motor_id] = {v.index: v for v in values}
        pend = self._pending.get(motor_id, {})
        for idx in [
            i for i, v in pend.items()
            if i in self._values[motor_id] and same_value(self._values[motor_id][i].value, v)
        ]:
            pend.pop(idx, None)  # 読んだ値と同じになった編集は不要
        if motor_id == self._motor:
            self._refresh_all()
            if self._toast_on_read:
                self._toast_on_read = False
                self._toast(tr("param_page.read_done", n=len(values)), "info")

    def _ensure_read(self) -> None:
        """このモータの値をまだ読んでいなければ読む(ページを初めて開いたとき)。"""
        if (
            self._has_motor()
            and self._motor not in self._values
            and self._motor not in self._read_requested
        ):
            self._read_requested.add(self._motor)
            self.bridge.read_all_params(self._motor)

    # ================================================================ 描画の更新
    def _brushes(self) -> tuple[QBrush, QBrush, QBrush]:
        t = tokens()
        return (
            QBrush(QColor(t.text_primary)),
            QBrush(QColor(t.text_secondary)),
            QBrush(QColor(t.text_tertiary)),
        )

    def _refresh_all(self) -> None:
        has = self._has_motor()
        self.stack.setCurrentWidget(self.content if has else self.empty)
        self._ui_state = self._current_ui_state()
        self._search_action.setIcon(icon("search", "text_tertiary"))
        brushes = self._brushes()
        for row in self._rows.values():
            self._refresh_row(row, brushes)
        for r in self._header_rows.values():
            self._item(r, 0).setForeground(brushes[1])
        self._apply_filter()
        self._update_actions()

    def _refresh_row(self, row: _Row, brushes: tuple[QBrush, QBrush, QBrush]) -> None:
        primary, secondary, tertiary = brushes
        d, r = row.d, row.row
        pv = self._values.get(self._motor, {}).get(d.index)
        pend = self._pending.get(self._motor, {})
        value_item = self._item(r, C_VALUE)
        value_item.setText(fmt_cell(pv.value if pv is not None else None, d))
        value_item.setForeground(primary)  # 読み取り専用も通常色(読めることが重要)
        self._item(r, C_UNIT).setForeground(secondary)
        range_item = self._item(r, C_RANGE)
        range_item.setForeground(tertiary if range_item.text() == "—" else secondary)
        self._item(r, C_DEFAULT).setForeground(secondary)
        state_item = self._item(r, C_STATE)
        if d.index in pend:
            state_item.setIcon(icon("pencil", "accent", ICON_S))
            state_item.setToolTip(tr("param_page.state_editing"))
        elif pv is not None and pv.dirty:
            state_item.setIcon(icon("circle-dot", "caution", ICON_S))
            state_item.setToolTip(tr("param_page.state_unsaved"))
        else:
            state_item.setIcon(QIcon())
            state_item.setToolTip("")
        if row.motion_locked:
            self._item(r, C_FILL).setForeground(tertiary)  # 理由は薄く(操作できないことの説明)
        if not row.editable:
            return
        # 「新しい値」セル: 未書込 → 固定桁の値 / 不正な入力 → 入力のまま(赤)/ 無し → 空
        problem = self._problems.get(d.index, "")
        new = self._item(r, C_NEW)
        if problem:
            text, edit_text = self._bad_text.get(d.index, ""), self._bad_text.get(d.index, "")
        elif d.index in pend:
            text, edit_text = fmt_cell(pend[d.index], d), fmt_param(pend[d.index], d)
        else:
            text = edit_text = ""
        new.setText(text)
        new.setData(ROLE_EDIT_TEXT, edit_text)
        new.setData(ROLE_PROBLEM, problem)
        new.setData(ROLE_REFERENCE, pv.value if pv is not None else None)
        new.setToolTip(problem or tr("param_page.edit_tip"))
        # 理由は同じ行の右端の空き列に(行の高さは変えない)
        fill = self._item(r, C_FILL)
        fill.setText(problem)
        fill.setToolTip(problem)
        fill.setForeground(QBrush(QColor(tokens().critical)))

    def _apply_filter(self, *_args: object) -> None:
        group = self.seg.current() or "all"
        needle = self.search.text().strip().lower()
        shown: dict[str, int] = dict.fromkeys(GROUPS, 0)
        for row in self._rows.values():
            d = row.d
            ok = group in ("all", row.group)
            if ok and needle:
                hay = " ".join(
                    (title_of(d), d.name, f"0x{d.index:04x}", f"{d.index:04x}", d.description)
                ).lower()
                ok = needle in hay
            self.table.setRowHidden(row.row, not ok)
            if ok:
                shown[row.group] += 1
        for g, r in self._header_rows.items():
            self.table.setRowHidden(r, group != "all" or shown[g] == 0)

    def _update_actions(self) -> None:
        n_pend = len(self._pending.get(self._motor, {}))
        n_dirty = len(self.dirty_indices())
        self.counter_label.setText(tr("param_page.counter", pending=n_pend, dirty=n_dirty))
        lock = self.lock_reason()
        # ロックの理由はツールチップだけでなく常設の一行でも言う(§2 原則 6)
        self.lock_note.setText(lock)
        self.lock_icon.setVisible(bool(lock))
        self.lock_note.setVisible(bool(lock))
        if lock:
            self.lock_icon.setPixmap(pixmap("lock", "text_secondary", ICON_S))

        if lock:
            write_reason = lock
        elif self._problems:
            write_reason = tr("param_page.reason_problem")
        elif not n_pend:
            write_reason = tr("param_page.reason_nothing")
        else:
            write_reason = ""
        can_write = not write_reason
        self.write_btn.set_available(can_write, write_reason)
        # 強調色は「今すぐ書き込める変更があるとき」だけ
        self.write_btn.set_variant("accent" if can_write else "standard")

        if lock:
            save_reason = lock
        else:
            save_reason = ""
        self.save_btn.set_available(not save_reason, save_reason)
        self.read_btn.set_available(self._has_motor())
        self.toolbar.updateGeometry()

    # ================================================================ 入力
    def _on_edit_state(
        self, index: int, value: float | None, problem: str, raw: str = ""
    ) -> None:
        """入力欄の状態が変わった(入力のたび / 確定 / Esc で戻したとき)。"""
        pend = self._pending.setdefault(self._motor, {})
        row = self._rows[index]
        if problem:
            # 入力の途中や範囲外の値を未書込として残さない(表示と書込内容を食い違わせない)
            pend.pop(index, None)
            self._problems[index] = problem
            self._bad_text[index] = raw
        else:
            self._problems.pop(index, None)
            self._bad_text.pop(index, None)
            if value is None or same_value(self.motor_value(index), value):
                pend.pop(index, None)
            else:
                pend[index] = value
        # 入力のたびに全行を作り直さず、その行の状態列とボタンだけ更新する
        self._refresh_row(row, self._brushes())
        self._update_actions()

    def set_pending(self, index: int, value: float | None) -> None:
        """プログラムから新しい値を入れる(取り込み・テスト用)。開いている入力欄にも反映する。"""
        row = self._rows[index]
        if not row.editable:
            return
        ed = self.edit_widget(index)
        if ed is not None:
            ed.set_pending(value)
        self._on_edit_state(index, value, "")

    # ================================================================ 操作
    def read_from_motor(self) -> None:
        if not self._has_motor():
            return
        self._toast_on_read = True
        self._read_requested.add(self._motor)
        self.bridge.read_all_params(self._motor)

    def write_pending(self) -> int:
        """未書込をすべて書き込む。書き込んだ件数を返す(書込できないときは 0)。"""
        self.end_edit()
        if self.lock_reason() or self._problems:
            return 0
        pend = self._pending.get(self._motor, {})
        items = sorted(pend.items())
        if not items:
            return 0
        for idx, v in items:
            d = self._rows[idx].d
            self.bridge.write_param(self._motor, idx, v if d.dtype == "float" else int(v))
        pend.clear()
        self._refresh_all()
        self._toast(tr("param_page.written", n=len(items)), "success")
        return len(items)

    def save_flash(self, *, confirm: bool = True) -> bool:
        """モータに保存する(type 22)。書込済みの項目をアプリ共通の確認ダイアログに出す。"""
        if self.lock_reason():
            return False
        dirty = self.dirty_indices()  # 0x70xx の書込済み項目(保存しても電源断で戻る。表示用)
        if confirm:
            vals = self._values.get(self._motor, {})
            items = [
                FlashItem(title_of(self._rows[i].d),
                          self._fmt_cell_with_unit(self._rows[i].d, vals[i].value))
                for i in dirty
            ]
            if not confirm_flash_save(self, self._motor, items):
                return False
        self.bridge.save_params(self._motor)
        self._toast(tr("param_page.saved"), "success")
        return True

    # ---- ファイル入出力・既定値(差分プレビュー付き) ----
    def export_json(self, path: Path) -> int:
        vals = self._values.get(self._motor, {})
        params = {f"0x{i:04X}": v.value for i, v in sorted(vals.items()) if v.value is not None}
        payload = {"motor_id": self._motor, "params": params}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(params)

    def diff_from_file(self, path: Path) -> list[Change]:
        """ファイルの値のうち、書込可能で現在のモータの値と異なるもの(指令・状態は除く)。"""
        data = json.loads(path.read_text(encoding="utf-8"))
        out: list[Change] = []
        for key, value in data.get("params", {}).items():
            idx = int(key, 16) if isinstance(key, str) else int(key)
            row = self._rows.get(idx)
            if row is None or not row.editable or value is None or row.group in TRANSIENT_GROUPS:
                continue
            cur = self.motor_value(idx)
            if cur is None or not same_value(cur, value):
                out.append((row.d, cur, float(value)))
        return sorted(out, key=lambda x: x[0].index)

    def diff_to_defaults(self) -> list[Change]:
        out: list[Change] = []
        for idx, row in sorted(self._rows.items()):
            d = row.d
            if not row.editable or row.group in TRANSIENT_GROUPS or d.default is None:
                continue
            cur = self.motor_value(idx)
            if cur is None or not same_value(cur, d.default):
                out.append((d, cur, float(d.default)))
        return out

    def stage(self, changes: list[Change], *, title: str, body: str, confirm: bool = True) -> int:
        """差分を確認してから新しい値として取り込む(モータへは書き込まない)。"""
        if not changes:
            self._toast(tr("param_page.no_diff"), "info")
            return 0
        if confirm:
            lines = [
                tr("param_page.line_diff", name=title_of(d),
                   old=self._fmt_with_unit(d, old), new=self._fmt_with_unit(d, new))
                for d, old, new in changes
            ]
            ok = ConfirmDialog.ask(
                self, title=title, body=body + "\n\n" + self._preview_lines(lines),
                confirm_text=tr("param_page.stage_confirm", n=len(changes)),
                tone="neutral", confirm_variant="accent",
            )
            if not ok:
                return 0
        for d, _old, new in changes:
            self.set_pending(d.index, new)
        self._toast(tr("param_page.staged", n=len(changes)), "info")
        return len(changes)

    def import_json(self, path: Path, *, confirm: bool = True) -> int:
        return self.stage(
            self.diff_from_file(path), title=tr("param_page.import_title", name=path.name),
            body=tr("param_page.import_body"), confirm=confirm,
        )

    def reset_to_defaults(self, *, confirm: bool = True) -> int:
        return self.stage(
            self.diff_to_defaults(), title=tr("param_page.reset_title"),
            body=tr("param_page.reset_body"), confirm=confirm,
        )

    def _open_more_menu(self) -> None:
        menu = Menu(self)
        menu.addAction(icon("download", "text_primary"), tr("param_page.menu_export"),
                       self._on_export)
        menu.addAction(icon("upload", "text_primary"), tr("param_page.menu_import"),
                       self._on_import)
        menu.addSeparator()
        menu.addAction(icon("rotate-ccw", "text_primary"), tr("param_page.menu_reset"),
                       self.reset_to_defaults)
        menu.exec(self.more_btn.mapToGlobal(self.more_btn.rect().bottomRight()))

    def _on_export(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("param_page.menu_export"), f"user_data/params_id{self._motor}_{stamp}.json",
            "JSON (*.json)",
        )
        if path:
            n = self.export_json(Path(path))
            self._toast(tr("param_page.exported", n=n), "success")

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("param_page.menu_import"), "user_data", "JSON (*.json)"
        )
        if path:
            self.import_json(Path(path))

    # ================================================================ 補助
    @staticmethod
    def _fmt_with_unit(d: ParamDef, v: float | int | None) -> str:
        text = fmt_param(v, d)
        return f"{text} {d.unit}" if d.unit and v is not None else text

    @staticmethod
    def _fmt_cell_with_unit(d: ParamDef, v: float | int | None) -> str:
        """表と同じ桁数 + 単位(Flash 保存の確認用)。"""
        text = fmt_cell(v, d)
        return f"{text} {d.unit}" if d.unit and v is not None else text

    @staticmethod
    def _preview_lines(lines: list[str]) -> str:
        shown = lines[:MAX_PREVIEW_LINES]
        if len(lines) > len(shown):
            shown.append(tr("param_page.more_lines", n=len(lines) - len(shown)))
        return "\n".join(shown)

    def _toast(self, text: str, kind: str) -> None:
        if self.shell is not None:
            self.shell.toast(text, kind)

    def _open_connection(self) -> None:
        if self.shell is not None:
            self.shell.open_connection()
