"""Python コンソール(出力ペイン + 入力 + 履歴)。

- コードは `edulite05.core.scripting.ScriptEngine` の**ワーカースレッド**で実行する。GUI スレッドは
  待たない(出力はキュー経由で 50 ms ごとにまとめて足す)。
- 入力: Enter で実行、Shift+Enter で改行、カーソルが先頭行/最終行のとき ↑ ↓ で履歴。
- 実行中は主ボタンが「中断」に変わる(位置は同じ。1 区画に主ボタン 1 つ)。
- 色はトークン経由。出力は kind ごとに文字色を変える(エラーだけ critical)。
"""

from __future__ import annotations

import queue
from collections import deque
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from edulite05.app.i18n import tr
from edulite05.app.ui.button import Button
from edulite05.app.ui.label import Label
from edulite05.app.ui.style import make_font, theme_manager, tokens
from edulite05.app.ui.tokens import FS_BODY, SP_GROUP, SP_PAD, SP_RELATED
from edulite05.app.ui.util import hbox
from edulite05.core.scripting import (
    ECHO,
    ERR,
    INFO,
    OUT,
    RESULT,
    ScriptEngine,
    default_namespace,
    stop_all_motion,
)
from edulite05.plugin_api import runtime

HISTORY_MAX = 100
ENTRIES_MAX = 3000
FLUSH_MS = 50
MAX_CHUNKS_PER_TICK = 500

# kind → トークン属性名(文字色)
_COLOR = {
    OUT: "text_primary",
    RESULT: "text_primary",
    ERR: "critical",
    ECHO: "text_secondary",
    INFO: "text_tertiary",
}


class InputEdit(QPlainTextEdit):
    """コード入力。Enter = 実行、Shift+Enter = 改行、↑ ↓ = 履歴(先頭行/最終行にいるとき)。"""

    submitted = Signal()
    history_prev = Signal()
    history_next = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(make_font(FS_BODY, mono=True))
        self.setTabChangesFocus(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        fm = self.fontMetrics()
        self.setFixedHeight(fm.lineSpacing() * 3 + SP_PAD)  # 3 行分

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        mods = event.modifiers()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if mods & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.submitted.emit()
            return
        if key == Qt.Key.Key_Up and not mods and self.textCursor().blockNumber() == 0:
            self.history_prev.emit()
            return
        if (
            key == Qt.Key.Key_Down
            and not mods
            and self.textCursor().blockNumber() == self.document().blockCount() - 1
        ):
            self.history_next.emit()
            return
        super().keyPressEvent(event)


class ConsoleView(QWidget):
    """コンソール全体。`engine` がワーカー実行を受け持つ。"""

    state_changed = Signal(bool)  # 実行中か(ページが他の区画の可否を決めるのに使う)

    def __init__(
        self,
        *,
        history: list[str] | None = None,
        save_history: Callable[[list[str]], None] | None = None,
        project_root: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._save_history = save_history
        self._q: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self._entries: deque[tuple[str, str]] = deque(maxlen=ENTRIES_MAX)
        self._history: list[str] = [h for h in (history or []) if h][-HISTORY_MAX:]
        self._hist_idx = len(self._history)
        self._draft = ""
        self._was_running = False
        self._styled = False
        self.engine = ScriptEngine(
            lambda: default_namespace(runtime.current_service(), project_root, self.engine),
            self._post,
            on_abort=lambda: stop_all_motion(runtime.current_service()),
            project_root=project_root,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SP_RELATED)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(make_font(FS_BODY, mono=True))
        self.output.setMaximumBlockCount(ENTRIES_MAX * 4)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        root.addWidget(self.output, 1)

        self.input = InputEdit()
        self.input.setPlaceholderText(tr("script.input_placeholder"))
        root.addWidget(self.input)

        bar = hbox(spacing=SP_GROUP)
        self.status_label = Label("", "caption")
        bar.addWidget(self.status_label, 1)
        self.clear_button = Button(tr("script.clear"), "subtle", icon="trash-2")
        bar.addWidget(self.clear_button)
        self.run_button = Button(tr("script.run"), "accent", icon="play")
        self.run_button.setMinimumWidth(112)
        bar.addWidget(self.run_button)
        root.addLayout(bar)

        self.names_label = Label(tr("script.console_names"), "caption-tertiary", wrap=True)
        root.addWidget(self.names_label)

        self.input.submitted.connect(self.submit)
        self.input.history_prev.connect(self._prev)
        self.input.history_next.connect(self._next)
        self.run_button.clicked.connect(self._on_run_button)
        self.clear_button.clicked.connect(self.clear_output)
        theme_manager().changed.connect(self._rerender)

        self._timer = QTimer(self)
        self._timer.setInterval(FLUSH_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._post(INFO, tr("script.welcome") + "\n")
        self._tick()
        self._update_state()

    # ============================================================ 公開
    @property
    def running(self) -> bool:
        return self.engine.running

    def submit(self) -> bool:
        """入力欄のコードを実行する。実行中・空なら何もしない。"""
        code = self.input.toPlainText().rstrip()
        if not code.strip() or self.engine.running:
            return False
        self._remember(code)
        self.input.clear()
        self._entries_add(ECHO, _echo(code))
        if not self.engine.execute(code):  # 競合(ほぼ起きない)
            return False
        self._update_state()
        return True

    def abort(self) -> None:
        self.engine.abort()

    def clear_output(self) -> None:
        self._entries.clear()
        self.output.clear()

    def output_text(self) -> str:
        return self.output.toPlainText()

    def history(self) -> list[str]:
        return list(self._history)

    def shutdown(self) -> None:
        """終了時: 実行中なら中断して待つ。"""
        self._timer.stop()
        if self.engine.running:
            self.engine.abort()
            self.engine.join(3.0)

    def refresh_status(self) -> None:
        self._update_state()

    # ============================================================ 内部
    def _post(self, kind: str, text: str) -> None:
        """どのスレッドからでも(ワーカーの print など)。GUI スレッドで `_tick` が取り出す。"""
        self._q.put((kind, text))

    def _tick(self) -> None:
        n = 0
        while n < MAX_CHUNKS_PER_TICK:
            try:
                kind, text = self._q.get_nowait()
            except queue.Empty:
                break
            self._entries_add(kind, text)
            n += 1
        self._update_state()

    def _entries_add(self, kind: str, text: str) -> None:
        self._entries.append((kind, text))
        self._append(kind, text)

    def _append(self, kind: str, text: str) -> None:
        sb = self.output.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        cur = QTextCursor(self.output.document())
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertText(text, self._format(kind))
        if at_bottom:
            sb.setValue(sb.maximum())

    @staticmethod
    def _format(kind: str) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(getattr(tokens(), _COLOR.get(kind, "text_primary"))))
        return fmt

    def _rerender(self, _name: str = "") -> None:
        """テーマが変わったら、保持している出力を新しい色で描き直す。"""
        self.output.clear()
        for kind, text in self._entries:
            self._append(kind, text)

    def _update_state(self) -> None:
        running = self.engine.running
        if running != self._was_running or not self._styled:
            self._styled = True
            if running:
                self.run_button.setText(tr("script.abort"))
                self.run_button.set_variant("standard")
                self.run_button.set_icon_name("square")
                self.run_button.set_base_tooltip(tr("script.abort_tip"))
            else:
                self.run_button.setText(tr("script.run"))
                self.run_button.set_variant("accent")
                self.run_button.set_icon_name("play")
                self.run_button.set_base_tooltip(tr("script.run_tip"))
        if running:
            self.status_label.setText(tr("script.status_running"))
            self.status_label.set_tone("accent")
        else:
            svc = runtime.current_service()
            if svc is None:
                self.status_label.setText(tr("script.status_offline"))
            else:
                self.status_label.setText(tr("script.status_online", n=len(svc.motor_ids())))
            self.status_label.set_tone("")
        if running != self._was_running:
            self._was_running = running
            self.state_changed.emit(running)

    def _on_run_button(self) -> None:
        if self.engine.running:
            self.abort()
        else:
            self.submit()

    # ---- 履歴 ----
    def _remember(self, code: str) -> None:
        if not self._history or self._history[-1] != code:
            self._history.append(code)
            del self._history[:-HISTORY_MAX]
            if self._save_history is not None:
                self._save_history(list(self._history))
        self._hist_idx = len(self._history)
        self._draft = ""

    def _prev(self) -> None:
        if not self._history or self._hist_idx == 0:
            return
        if self._hist_idx == len(self._history):
            self._draft = self.input.toPlainText()
        self._hist_idx -= 1
        self._set_input(self._history[self._hist_idx])

    def _next(self) -> None:
        if self._hist_idx >= len(self._history):
            return
        self._hist_idx += 1
        self._set_input(
            self._draft if self._hist_idx == len(self._history) else self._history[self._hist_idx]
        )

    def _set_input(self, text: str) -> None:
        self.input.setPlainText(text)
        self.input.moveCursor(QTextCursor.MoveOperation.End)


def _echo(code: str) -> str:
    first, nxt = tr("script.echo_first"), tr("script.echo_next")
    lines = code.splitlines() or [""]
    return "\n".join((first if i == 0 else nxt) + ln for i, ln in enumerate(lines)) + "\n"
