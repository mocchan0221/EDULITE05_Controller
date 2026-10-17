"""シーケンスの区画: ファイルを選ぶ → 手順の一覧 → 実行 / 中断 → 進捗(現在のステップ)と実行ログ。

- 検証(`load_sequence`)は選んだ時点で行い、問題は行番号つきで見せる(直すまで実行できない)。
- 実行は `SequenceRunner` のワーカースレッド。イベントはキュー経由で 50 ms ごとに画面へ反映する。
- 主ボタンは 1 つ。実行中は「中断」に変わる。押せない理由はツールチップで言う。
- 出力段階 LOW のときだけ実行できる(`SequenceRunner` も HIGH なら開始しない)。
"""

from __future__ import annotations

import queue
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from edulite05.app.i18n import tr
from edulite05.app.ui.button import Button
from edulite05.app.ui.icon import pixmap
from edulite05.app.ui.inputs import Combo
from edulite05.app.ui.label import Label
from edulite05.app.ui.style import make_font, theme_manager, tokens
from edulite05.app.ui.tokens import FS_CAPTION, ICON_S, SP_GROUP, SP_RELATED
from edulite05.app.ui.util import hbox
from edulite05.core import clock
from edulite05.core.control.service import UiState
from edulite05.core.sequence import (
    Sequence,
    SequenceError,
    SequenceEvent,
    SequenceRunner,
    SequenceState,
    list_sequences,
    load_sequence,
)
from edulite05.plugin_api import runtime

from .step_text import step_detail, step_line, step_title

FLUSH_MS = 50
STEPS_W = 360
LOG_MAX_BLOCKS = 4000

# 手順の行の見た目: 状態 → (アイコン, 色トークン)
_MARKS = {
    "pending": ("circle", "text_tertiary"),
    "current": ("play", "accent"),
    "done": ("circle-check", "text_secondary"),
    "failed": ("circle-x", "critical"),
}


class StepRow(QWidget):
    """手順 1 行: 状態アイコン + 名前(+ 補足 1 行)+ 待ち時間(あれば)。"""

    def __init__(
        self, title: str, detail: str, seconds: float, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._mark = "pending"
        lay = hbox(self, margins=(SP_RELATED, ROW_PAD, SP_RELATED, ROW_PAD), spacing=SP_RELATED)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(ICON_S, ICON_S)
        lay.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.text_label = Label(title, "body")
        col.addWidget(self.text_label)
        self.detail_label = Label(detail, "caption-tertiary", wrap=True)
        self.detail_label.setVisible(bool(detail))
        col.addWidget(self.detail_label)
        lay.addLayout(col, 1)
        self.time_label = Label(f"{seconds:g} s" if seconds > 0 else "", "caption-tertiary")
        lay.addWidget(self.time_label, 0, Qt.AlignmentFlag.AlignTop)
        self.set_mark("pending")
        theme_manager().changed.connect(self._retint)

    @property
    def mark(self) -> str:
        return self._mark

    def _retint(self, _name: str = "") -> None:
        self.set_mark(self._mark)

    def set_mark(self, mark: str) -> None:
        self._mark = mark
        name, color = _MARKS[mark]
        self.icon_label.setPixmap(pixmap(name, color, ICON_S))
        self.text_label.set_role("heading" if mark == "current" else "body")
        self.text_label.set_tone("critical" if mark == "failed" else "")


ROW_PAD = 6  # 行の上下余白


class SequenceView(QWidget):
    """シーケンスの選択・実行・進捗。"""

    state_changed = Signal(bool)  # 実行中か

    def __init__(
        self,
        bridge,  # noqa: ANN001 - Bridge(selected_motor / motors)
        *,
        project_root: Path,
        console_running=lambda: False,  # noqa: ANN001 - コンソールが実行中か
        on_toast=None,  # noqa: ANN001 - (text, kind)
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._root = project_root
        self._console_running = console_running
        self._toast = on_toast
        self._q: queue.SimpleQueue[SequenceEvent] = queue.SimpleQueue()
        self.sequence: Sequence | None = None
        self.problems: list[str] = []
        self.runner: SequenceRunner | None = None
        self.step_rows: list[StepRow] = []
        self._extra_files: list[Path] = []
        self._t_log0 = 0.0
        self._was_running = False
        self._status = ("", "")  # (text, tone)
        self._motor_ids: tuple[int, ...] = ()
        self._loaded: tuple[str, float] | None = ("", -1.0)  # 未読込(どの署名とも一致しない)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SP_GROUP)

        # ---- 選択(ファイル・モータ) ----
        sel = hbox(spacing=SP_RELATED)
        sel.addWidget(Label(tr("script.seq_file"), "secondary"))
        self.file_combo = Combo()
        self.file_combo.setMinimumWidth(260)
        sel.addWidget(self.file_combo, 1)
        self.browse_button = Button(tr("script.seq_browse"), "subtle", icon="folder-open")
        sel.addWidget(self.browse_button)
        sel.addSpacing(SP_GROUP)
        sel.addWidget(Label(tr("script.seq_motor"), "secondary"))
        self.motor_combo = Combo()
        self.motor_combo.setMinimumWidth(110)
        sel.addWidget(self.motor_combo)
        root.addLayout(sel)

        # ---- 概要(名前・説明・問題) ----
        self.summary_label = Label("", "secondary", wrap=True)
        root.addWidget(self.summary_label)
        self.problem_label = Label("", "caption", wrap=True)
        self.problem_label.setVisible(False)
        root.addWidget(self.problem_label)

        # ---- 手順 | ログ ----
        body = hbox(spacing=SP_GROUP)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(SP_RELATED)
        left.addWidget(Label(tr("script.seq_steps"), "heading"))
        self.steps_scroll = QScrollArea()
        self.steps_scroll.setWidgetResizable(True)
        self.steps_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.steps_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.steps_scroll.setFixedWidth(STEPS_W)
        self._steps_host = QFrame()
        self._steps_host.setProperty("card", True)
        self._steps_lay = QVBoxLayout(self._steps_host)
        self._steps_lay.setContentsMargins(SP_RELATED, SP_RELATED, SP_RELATED, SP_RELATED)
        self._steps_lay.setSpacing(0)
        self._steps_lay.addStretch(1)
        self.steps_scroll.setWidget(self._steps_host)
        left.addWidget(self.steps_scroll, 1)
        body.addLayout(left)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(SP_RELATED)
        right.addWidget(Label(tr("script.seq_log"), "heading"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(make_font(FS_CAPTION + 2, mono=True))
        self.log.setMaximumBlockCount(LOG_MAX_BLOCKS)
        right.addWidget(self.log, 1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        # ---- 進捗 + 主ボタン ----
        bar = hbox(spacing=SP_GROUP)
        self.progress_label = Label(tr("script.seq_idle"), "secondary")
        bar.addWidget(self.progress_label, 1)
        self.run_button = Button(tr("script.seq_run"), "accent", icon="play")
        self.run_button.setMinimumWidth(176)
        bar.addWidget(self.run_button)
        root.addLayout(bar)

        self.file_combo.currentIndexChanged.connect(self._on_file_changed)
        self.browse_button.clicked.connect(self._browse)
        self.run_button.clicked.connect(self._on_run_button)
        theme_manager().changed.connect(self._rerender_log)
        self._log_entries: list[tuple[str, str]] = []

        self._timer = QTimer(self)
        self._timer.setInterval(FLUSH_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self.refresh_files()
        self.refresh_motors()
        self._update_availability()

    # ============================================================ 公開
    @property
    def running(self) -> bool:
        return self.runner is not None and self.runner.running

    def sequences_dir(self) -> Path:
        return self._root / "sequences"

    def refresh_files(self) -> None:
        """`sequences/*.yaml`(と「ほかのファイル」で選んだもの)を並べ直す。選択は保つ。"""
        if self.running:
            return
        keep = self.file_combo.currentData()
        files = [*list_sequences(self.sequences_dir()), *self._extra_files]
        self.file_combo.blockSignals(True)
        self.file_combo.clear()
        for p in files:
            self.file_combo.addItem(p.name, str(p))
        idx = self.file_combo.findData(keep) if keep else -1
        self.file_combo.setCurrentIndex(idx if idx >= 0 else (0 if files else -1))
        self.file_combo.blockSignals(False)
        if self._signature() != self._loaded:  # 選択やファイルの中身が変わったときだけ読み直す
            self._load_current()

    def refresh_motors(self) -> None:
        svc = runtime.current_service()
        ids = tuple(svc.motor_ids()) if svc is not None else ()
        if ids == self._motor_ids and self.motor_combo.count() == len(ids):
            return
        keep = self.motor_combo.currentData()
        self._motor_ids = ids
        self.motor_combo.clear()
        for i in ids:
            self.motor_combo.addItem(tr("ui.motor_id", id=i), i)
        want = keep
        if want is None and self.sequence is not None and self.sequence.motor in ids:
            want = self.sequence.motor
        if want is None:
            try:
                want = self.bridge.selected_motor()
            except Exception:  # noqa: BLE001
                want = None
        idx = self.motor_combo.findData(want) if want is not None else -1
        self.motor_combo.setCurrentIndex(idx if idx >= 0 else (0 if ids else -1))

    def select_file(self, path: str | Path) -> None:
        """ファイルを選ぶ(「ほかのファイル」と同じ。一覧に無ければ足す)。"""
        p = Path(path)
        idx = self.file_combo.findData(str(p))
        if idx < 0:
            self._extra_files.append(p)
            self.file_combo.addItem(p.name, str(p))
            idx = self.file_combo.findData(str(p))
        self.file_combo.setCurrentIndex(idx)

    def start(self) -> bool:
        """実行を始める(ボタンと同じ)。押せない状態なら False。"""
        ok, _reason = self._availability()
        if not ok or self.sequence is None or self.running:
            return False
        svc = runtime.current_service()
        mid = self.motor_combo.currentData()
        self._reset_marks()
        self.log.clear()
        self._log_entries.clear()
        self._t_log0 = clock.now()
        self.runner = SequenceRunner(
            svc, self.sequence, int(mid), on_event=self._q.put,
            recordings_dir=self._root / "user_data" / "records",
        )
        self._set_status(tr("script.seq_state_running"), "accent")
        self.runner.start()
        self._update_availability()
        return True

    def abort(self) -> None:
        if self.runner is not None:
            self.runner.abort(tr("script.seq_state_aborted"))

    def log_text(self) -> str:
        return self.log.toPlainText()

    def status_text(self) -> str:
        return self._status[0]

    def shutdown(self) -> None:
        self._timer.stop()
        r = self.runner
        if r is not None and r.running:
            r.abort()
            r.join(10.0)

    # ============================================================ ファイル
    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("script.seq_browse_title"), str(self.sequences_dir()),
            tr("script.seq_browse_filter"),
        )
        if path:
            self.select_file(path)

    def _on_file_changed(self, _i: int = 0) -> None:
        if not self.running:
            self._load_current()
            self.log.clear()  # 別のファイルを選んだら、前の実行のログは消す
            self._log_entries.clear()

    def _signature(self) -> tuple[str, float] | None:
        path = self.file_combo.currentData()
        if not path:
            return None
        try:
            return (str(path), Path(path).stat().st_mtime)
        except OSError:
            return (str(path), 0.0)

    def _load_current(self) -> None:
        path = self.file_combo.currentData()
        self._loaded = self._signature()
        self.sequence = None
        self.problems = []
        self._clear_steps()
        self.problem_label.setVisible(False)
        if not path:
            self.summary_label.setText(tr("script.seq_no_files"))
            self._set_status(tr("script.seq_idle"), "")
            self._update_availability()
            return
        try:
            seq = load_sequence(path)
        except SequenceError as e:
            name = Path(path).name
            self.problems = [p.format(name) for p in e.problems]
            self.summary_label.setText(tr("script.seq_problems", n=len(e.problems)))
            self._show_problems(self.problems, "critical")
        else:
            self.sequence = seq
            head = seq.name + (f" — {seq.description}" if seq.description else "")
            summary = tr("script.seq_summary", n=len(seq.steps), sec=f"{seq.total_seconds:g}")
            self.summary_label.setText(f"{head}\n{summary}")
            if seq.warnings:
                name = Path(path).name
                self._show_problems([w.format(name) for w in seq.warnings], "caution")
            for st in seq.steps:
                self._add_step_row(step_title(st), step_detail(st), st.seconds)
            if seq.motor is not None and seq.motor in self._motor_ids:
                self.motor_combo.setCurrentIndex(self.motor_combo.findData(seq.motor))
        self._set_status(tr("script.seq_idle"), "")
        self._update_availability()

    def _show_problems(self, lines: list[str], tone: str) -> None:
        self.problem_label.setText("\n".join(lines))
        self.problem_label.set_tone(tone)
        self.problem_label.setVisible(True)

    # ============================================================ 手順の一覧
    def _clear_steps(self) -> None:
        for r in self.step_rows:
            self._steps_lay.removeWidget(r)
            r.hide()
            r.setParent(None)  # deleteLater だけだと、イベントループ外では消えるまで描かれ続ける
            r.deleteLater()
        self.step_rows.clear()

    def _add_step_row(self, title: str, detail: str, seconds: float) -> None:
        row = StepRow(title, detail, seconds)
        self._steps_lay.insertWidget(self._steps_lay.count() - 1, row)
        self.step_rows.append(row)

    def _scroll_to(self, row: StepRow) -> None:
        """縦方向だけ、行が見える位置までスクロールする(横には動かさない)。"""
        bar = self.steps_scroll.verticalScrollBar()
        top, bottom = row.y(), row.y() + row.height()
        view = self.steps_scroll.viewport().height()
        if top < bar.value():
            bar.setValue(top)
        elif bottom > bar.value() + view:
            bar.setValue(bottom - view)

    def _reset_marks(self) -> None:
        for r in self.step_rows:
            r.set_mark("pending")

    def _mark_current(self, index: int) -> None:
        for i, r in enumerate(self.step_rows):
            if i < index:
                r.set_mark("done")
            elif i == index:
                r.set_mark("current")
                self._scroll_to(r)
            elif r.mark != "pending":
                r.set_mark("pending")

    # ============================================================ 実行中の更新
    def _tick(self) -> None:
        n = 0
        while n < 400:
            try:
                ev = self._q.get_nowait()
            except queue.Empty:
                break
            self._on_event(ev)
            n += 1
        self._update_progress()
        self._update_availability()
        running = self.running
        if running != self._was_running:
            self._was_running = running
            self.state_changed.emit(running)

    def _step_text(self, index: int, fallback: str) -> str:
        """手順 `index` の説明(UI 言語)。範囲外なら core が作った文字列をそのまま使う。"""
        steps = self.sequence.steps if self.sequence is not None else ()
        return step_line(steps[index]) if 0 <= index < len(steps) else fallback

    def _on_event(self, ev: SequenceEvent) -> None:
        if ev.kind == "started" and self.sequence is not None:
            mid = self.runner.motor_id if self.runner else 0
            self._log(tr("script.seq_started", name=self.sequence.name, id=mid), "info")
        elif ev.kind == "step":
            self._mark_current(ev.index)
            self._log(
                tr("script.seq_step_line", i=ev.index + 1, n=ev.total,
                   text=self._step_text(ev.index, ev.text)),
                "info",
            )
        elif ev.kind == "step_done":
            if ev.index < len(self.step_rows):
                self.step_rows[ev.index].set_mark("done")
        elif ev.kind == "log":
            self._log(ev.text, "warn" if ev.level != "info" else "secondary", ev.t)
        elif ev.kind == "finished" and ev.result is not None:
            self._on_finished(ev)

    def _on_finished(self, ev: SequenceEvent) -> None:
        res = ev.result
        assert res is not None
        text, tone = {
            SequenceState.COMPLETED: (tr("script.seq_state_completed"), ""),
            SequenceState.ABORTED: (tr("script.seq_state_aborted"), "caution"),
            SequenceState.ESTOP: (tr("script.seq_state_estop"), "critical"),
            SequenceState.FAILED: (tr("script.seq_state_failed", msg=res.message), "critical"),
        }[res.state]
        if res.state is SequenceState.COMPLETED:
            for r in self.step_rows:
                r.set_mark("done")
        elif res.state is SequenceState.FAILED and self.runner is not None:
            idx = self.runner.progress().index
            if 0 <= idx < len(self.step_rows):
                self.step_rows[idx].set_mark("failed")
        for p in res.problems:
            self._log(tr("script.seq_cleanup_problem", text=p), "error", ev.t)
        level = {"": "info", "caution": "warn", "critical": "error"}[tone]
        self._log(text, level, ev.t)
        self._set_status(text, tone)
        if res.state is SequenceState.COMPLETED and not res.problems and self._toast is not None:
            self._toast(tr("script.seq_done_toast"), "success")

    def _update_progress(self) -> None:
        r = self.runner
        if r is None or not r.running:
            if self._status[0]:
                self.progress_label.setText(self._status[0])
                self.progress_label.set_tone(self._status[1])
            return
        pr = r.progress()
        if pr.index < 0:
            self.progress_label.setText(tr("script.seq_state_running"))
        else:
            self.progress_label.setText(
                tr("script.seq_progress_time", i=pr.index + 1, n=pr.total,
                   text=self._step_text(pr.index, pr.text),
                   elapsed=f"{pr.step_elapsed:.1f}")
            )
        self.progress_label.set_tone("accent")

    def _set_status(self, text: str, tone: str) -> None:
        self._status = (text, tone)
        self.progress_label.setText(text)
        self.progress_label.set_tone(tone)

    # ---- ログ ----
    def _log(self, text: str, level: str, t: float | None = None) -> None:
        if t is None:
            t = clock.now() - self._t_log0
        stamp = f"{int(t // 60):02d}:{t % 60:04.1f}"
        line = f"{stamp}  {text}"
        self._log_entries.append((level, line))
        self._append_log(level, line)

    def _append_log(self, level: str, line: str) -> None:
        from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor

        tk = tokens()
        color = {
            "info": tk.text_primary, "secondary": tk.text_secondary, "warn": tk.caution,
            "error": tk.critical,
        }.get(level, tk.text_primary)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        sb = self.log.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        cur = QTextCursor(self.log.document())
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertText(line + "\n", fmt)
        if at_bottom:
            sb.setValue(sb.maximum())

    def _rerender_log(self, _name: str = "") -> None:
        self.log.clear()
        for level, line in self._log_entries:
            self._append_log(level, line)

    # ============================================================ 主ボタン
    def _on_run_button(self) -> None:
        if self.running:
            self.abort()
        else:
            self.start()

    def _availability(self) -> tuple[bool, str]:
        """(押せるか, 押せない理由)。"""
        svc = runtime.current_service()
        if svc is None or not getattr(svc, "connected", False):
            return False, tr("script.reason_offline")
        if svc.estop_latched:
            return False, tr("script.reason_estop")
        if svc.tier.value != "low":
            return False, tr("script.reason_high")
        if self._console_running():
            return False, tr("script.reason_script")
        if self.file_combo.count() == 0 or self.file_combo.currentData() is None:
            return False, tr("script.reason_no_file")
        if self.sequence is None:
            return False, tr("script.reason_invalid")
        mid = self.motor_combo.currentData()
        if mid is None:
            return False, tr("script.reason_no_motor")
        if svc.ui_state(int(mid)) is not UiState.DISABLED or svc.is_busy(int(mid)):
            return False, tr("script.reason_motor_state")
        return True, ""

    def _update_availability(self) -> None:
        self.refresh_motors()
        running = self.running
        b = self.run_button
        self.set_controls_locked(running)  # 実行中は選択を変えられない
        if running:
            b.setText(tr("script.seq_abort"))
            b.set_variant("standard")
            b.set_icon_name("square")
            b.set_available(True)
            b.set_base_tooltip(tr("script.abort_tip"))
            return
        b.setText(tr("script.seq_run"))
        b.set_variant("accent")
        b.set_icon_name("play")
        ok, reason = self._availability()
        b.set_base_tooltip("")
        b.set_available(ok, reason)

    def set_controls_locked(self, locked: bool) -> None:
        """実行中はファイルとモータの選択を変えられない。"""
        for w in (self.file_combo, self.motor_combo, self.browse_button):
            w.setEnabled(not locked)
