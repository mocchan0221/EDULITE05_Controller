"""診断ページのテーブルモデル(イベントログ・CAN フレーム)。

行数上限つきの追記型。描画負荷を抑えるため、呼び出し側が 100 ms ごとにまとめて `append` する。
色はトークン経由(`data()` のたびに現在のテーマから取るので、テーマ切替は再描画だけで済む)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from edulite05.app.i18n import tr
from edulite05.app.ui import icon
from edulite05.app.ui.style import make_font, tokens
from edulite05.app.ui.tokens import FS_BODY, ICON_S
from edulite05.app.viewmodels import FrameEntry, LogEntry

LEVEL_RANK = {"debug": 0, "info": 1, "warn": 2, "error": 3}
# レベル → (アイコン, 色トークン, 文字色トークン)。info/debug は無彩色(平常時は静かに)
LEVEL_STYLE: dict[str, tuple[str | None, str]] = {
    "debug": (None, "text_tertiary"),
    "info": ("info", "text_secondary"),
    "warn": ("triangle-alert", "caution"),
    "error": ("circle-alert", "critical"),
}


def fmt_time(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-3]


class RowsModel(QAbstractTableModel):
    """行数上限つきの追記型モデルの共通部分。サブクラスは `headers` と `cell()` を実装する。"""

    headers: tuple[str, ...] = ()

    def __init__(self, max_rows: int) -> None:
        super().__init__()
        self._max = max_rows
        self._rows: list[Any] = []

    # ---- QAbstractTableModel ----
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self.headers)

    def headerData(  # noqa: N802
        self, section: int, orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        return self.cell(self._rows[index.row()], index.column(), role)

    def cell(self, row: Any, col: int, role: int) -> Any:
        raise NotImplementedError

    # ---- 追記・入替 ----
    def row_at(self, i: int) -> Any:
        return self._rows[i]

    def rows(self) -> list[Any]:
        return list(self._rows)

    def append(self, rows: list[Any]) -> None:
        if not rows:
            return
        rows = rows[-self._max:]
        overflow = len(self._rows) + len(rows) - self._max
        if overflow > 0:
            self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
            del self._rows[:overflow]
            self.endRemoveRows()
        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(rows) - 1)
        self._rows.extend(rows)
        self.endInsertRows()

    def reset(self, rows: list[Any]) -> None:
        self.beginResetModel()
        self._rows = rows[-self._max:]
        self.endResetModel()

    def clear(self) -> None:
        self.reset([])


class LogModel(RowsModel):
    """イベントログ: 時刻 / レベル(アイコン + 文字)/ メッセージ。"""

    def __init__(self, max_rows: int) -> None:
        super().__init__(max_rows)
        self.headers = (tr("diag.col_time"), tr("diag.col_level"), tr("diag.col_message"))

    def cell(self, e: LogEntry, col: int, role: int) -> Any:  # type: ignore[override]
        t = tokens()
        style = LEVEL_STYLE.get(e.level, LEVEL_STYLE["info"])
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return fmt_time(e.t)
            if col == 1:
                return tr(f"diag.level_{e.level}")
            return e.text
        if role == Qt.ItemDataRole.DecorationRole and col == 1 and style[0] is not None:
            return icon(style[0], style[1], ICON_S)
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 0:
                return QColor(t.text_secondary)
            if col == 1:
                return QColor(getattr(t, style[1]))
            return QColor(t.text_primary)
        if role == Qt.ItemDataRole.FontRole and col == 0:
            return make_font(FS_BODY, mono=True)
        return None


def comm_type(f: FrameEntry) -> int:
    """29bit ID の bit28..24(通信タイプ)。el05_protocol.md §1.2。"""
    return (f.can_id >> 24) & 0x1F


def motor_of(f: FrameEntry) -> int:
    """フレームが指すモータ ID。ホスト→モータは bit7..0、モータ→ホストは bit15..8(§1.2)。"""
    if f.direction == "TX":
        return f.can_id & 0xFF
    return (f.can_id >> 8) & 0xFF


class FrameModel(RowsModel):
    """CAN フレーム: 時刻 / 方向 / CAN ID / type / モータ / データ / デコード結果(等幅)。"""

    def __init__(self, max_rows: int) -> None:
        super().__init__(max_rows)
        self.headers = (
            tr("diag.col_time"), tr("diag.col_dir"), tr("diag.col_id"), tr("diag.col_type"),
            tr("diag.col_motor"), tr("diag.col_data"), tr("diag.col_decoded"),
        )

    def cell(self, f: FrameEntry, col: int, role: int) -> Any:  # type: ignore[override]
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return fmt_time(f.t)
            if col == 1:
                return f.direction
            if col == 2:
                return f"0x{f.can_id:08X}"
            if col == 3:
                return str(comm_type(f))
            if col == 4:
                return str(motor_of(f))
            if col == 5:
                return " ".join(f"{b:02X}" for b in f.data)
            return f.decoded_text
        if role == Qt.ItemDataRole.ForegroundRole:
            t = tokens()
            if col in (0, 3, 4):
                return QColor(t.text_secondary)
            if col == 1:  # 方向は色ではなく濃淡で区別(TX = 二次色 / RX = 本文色)
                return QColor(t.text_secondary if f.direction == "TX" else t.text_primary)
            return QColor(t.text_primary)
        if role == Qt.ItemDataRole.FontRole:
            return make_font(FS_BODY, mono=True)
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (1, 3, 4):
            return int(Qt.AlignmentFlag.AlignCenter)
        return None
