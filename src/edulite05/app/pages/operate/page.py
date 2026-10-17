"""運転ページ(ui_design.md §5.1)。既定のページ。

    ┌ モニタ(可変幅)────────────────────────────┬ インスペクタ 360px〜 ┐
    │ 読み値ストリップ(操作対象のモータ)              │ ID1 EL05 #1  ◉ 有効 │
    │ モニタ [グラフ|3D] 表示 時間幅 ⏸ ⏺               │ 制御モード / 目標    │
    │ イベント行                                       │ [ 主ボタン ]         │
    └──────────────────────────────────────────┴──────────────────┘

未接続・通信断・スキャン中・モータなしのときは、モニタ領域の中央に空状態(接続フォーム付き)を出す。
モニタとインスペクタの境(区画境界)はドラッグで動かせる(インスペクタ 360〜520 px)。幅は
`shell.settings()` の `layout/operate/inspector_w` に保存し、`reset_layout()` で既定に戻す。
設定ページの値(角度の単位・グラフの時間幅とプリセット・ホスト ID)は `shell.page("settings").prefs`
から読み、`prefs.changed` に追従する。

キーボード: F5 = 主ボタン(有効化/運転開始)、F6 = 運転停止、Ctrl+R = 記録、Space = 一時停止(グラフ)。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtGui import QShortcut as _QShortcut
from PySide6.QtWidgets import QHBoxLayout, QSplitter, QStackedWidget, QVBoxLayout, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.pages.base import PageBase, ShellApi
from edulite05.app.pages.operate.empty_states import ConnectionStateView
from edulite05.app.pages.operate.event_line import EventLine
from edulite05.app.pages.operate.inspector import Inspector
from edulite05.app.pages.operate.monitor import Monitor
from edulite05.app.pages.operate.readout_strip import ReadoutStrip
from edulite05.app.pages.operate.recorder import Recorder, format_elapsed
from edulite05.app.pages.operate.zero_origin import ZeroOriginFlow, ZeroPopover, zero_availability
from edulite05.app.ui.tokens import SP_GROUP, SP_PAD, W_INSPECTOR, W_INSPECTOR_MAX
from edulite05.app.ui.util import divider
from edulite05.app.viewmodels import ConnectionInfo, LogEntry, MotorInfo, MotorViewState
from edulite05.core import clock

UI_THROTTLE_S = 0.03  # 読み値・インスペクタの更新は約 30 Hz
PENDING_TIMEOUT_MS = 4000  # 接続・スキャンの「進行中」表示が居座らないための上限

CONTENT_MONITOR = 0
CONTENT_STATE = 1
LAYOUT_KEY = "layout/operate/inspector_w"  # QSettings のキー(インスペクタの幅 px)


class OperatePage(PageBase):
    """`OperatePage(bridge, shell)`。シェルが呼ぶフック: `ensure_3d` / `set_preload_failed` /
    `shutdown`。"""

    title_key = "nav.operate"

    def __init__(
        self,
        bridge: Bridge,
        shell: ShellApi | None = None,
        parent: QWidget | None = None,
        *,
        record_dir: Path | None = None,
    ) -> None:
        super().__init__(bridge, shell, parent)
        self._last_ui = 0.0
        self._sel_sig: tuple = ()
        self._connecting = False
        self._scanning = False
        self.recorder = Recorder(record_dir, bridge)
        self._prefs: object | None = None  # 設定ページの Prefs(シェルが組み終わってから結ぶ)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(4)
        lay.addWidget(self.splitter)
        self.content = QStackedWidget()
        self.splitter.addWidget(self.content)
        # 区画の境の線は、インスペクタの左端に置く(つまみの幅と線を分ける)
        right = QWidget()
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(divider(Qt.Orientation.Vertical))
        self.inspector = Inspector(bridge, shell)
        rl.addWidget(self.inspector, 1)
        right.setMinimumWidth(W_INSPECTOR + 1)
        right.setMaximumWidth(W_INSPECTOR_MAX + 1)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.splitterMoved.connect(self._save_layout)
        self._inspector_w = W_INSPECTOR
        self._restore_layout()

        # ---- モニタ列 ----
        col = QWidget()
        cl = QVBoxLayout(col)
        cl.setContentsMargins(SP_PAD, SP_PAD, SP_PAD, SP_GROUP)
        cl.setSpacing(SP_GROUP)
        self.strip = ReadoutStrip()
        cl.addWidget(self.strip)
        self.monitor = Monitor(bridge)
        cl.addWidget(self.monitor, 1)
        self.event_line = EventLine()
        cl.addWidget(self.event_line)
        self.content.addWidget(col)
        self.state_view = ConnectionStateView(bridge, host_id=self._host_id)
        self.content.addWidget(self.state_view)

        # 位置カードの [原点に設定]: 確認ポップオーバー → 停止・無効化 → set_zero → トースト
        self.zero_popover = ZeroPopover(self)
        self.zero_flow = ZeroOriginFlow(bridge, self._toast, self)
        self.strip.zero_requested.connect(self._on_zero_requested)
        self.strip.unit_toggle.changed.connect(
            lambda unit: self.inspector.set_angle_unit(unit == "deg")
        )

        self.monitor.record_toggled.connect(self.set_recording)
        self.event_line.clicked.connect(self._open_diagnostics)
        self.state_view.connect_requested.connect(self._on_connect_requested)
        self.state_view.reconnect_requested.connect(self._on_reconnect_requested)
        self.state_view.rescan_requested.connect(self._on_rescan_requested)

        self._record_timer = QTimer(self)
        self._record_timer.setInterval(500)
        self._record_timer.timeout.connect(self._tick_record)
        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.setInterval(PENDING_TIMEOUT_MS)
        self._pending_timer.timeout.connect(self._clear_pending)

        self._shortcuts: list[_QShortcut] = []
        for seq, slot, auto in (
            ("F5", self.inspector.trigger_primary, False),
            ("F6", self.inspector.trigger_stop, False),
            ("Ctrl+R", self.toggle_recording, False),
        ):
            sc = _QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.setAutoRepeat(auto)
            sc.activated.connect(slot)
            self._shortcuts.append(sc)

        bridge.connection_changed.connect(self._on_connection)
        bridge.motors_changed.connect(self._on_motors)
        bridge.selected_motor_changed.connect(self._on_selected)
        bridge.state_updated.connect(self._on_states)
        bridge.power_tier_changed.connect(self._on_tier)
        bridge.estop_changed.connect(self._on_estop)
        bridge.log_message.connect(self._on_log)

        self._on_motors(bridge.motors())
        self._refresh_content()
        self._bind_prefs()

    # ================================================================ シェルから呼ばれるフック
    def ensure_3d(self) -> None:
        self.monitor.view3d.ensure_3d()

    def set_preload_failed(self, reason: str) -> None:
        self.monitor.view3d.set_preload_failed(reason)

    def set_3d_enabled(self, enabled: bool) -> None:
        self.monitor.view3d.set_3d_enabled(enabled)

    def add_monitor_view(self, key: str, title: str, widget: QWidget) -> None:
        """モニタの `[グラフ | 3D | …]` に表示を足す(プラグインの `add_monitor_view`)。"""
        self.monitor.add_view(key, title, widget)

    def shutdown(self) -> None:
        self.inspector.deactivate()
        if self.recorder.active:
            self.recorder.discard()
        self.monitor.view3d.shutdown()

    def activated(self) -> None:
        self._bind_prefs()  # 設定ページはこのページより後に組まれるため、初回表示で結ぶ
        self._refresh_all()

    def deactivated(self) -> None:
        self.inspector.deactivate()  # 押下保持を解除

    # ================================================================ 設定ページの値
    def _bind_prefs(self) -> None:
        """設定ページの `Prefs` を探して値を反映し、変更に追従する(一度だけ)。"""
        if self._prefs is not None:
            return
        page = getattr(self.shell, "page", None)
        if not callable(page):
            return
        try:
            prefs = page("settings").prefs
        except (KeyError, AttributeError):
            return
        self._prefs = prefs
        prefs.changed.connect(self._on_pref)
        for key in ("angle_unit", "graph_window_s", "graph_preset"):
            self._on_pref(key, prefs.get(key))

    def _host_id(self) -> int:
        prefs = self._prefs
        return int(prefs.get("host_id")) if prefs is not None else 0xFD  # type: ignore[attr-defined]

    def _on_pref(self, key: str, value: object) -> None:
        if key == "angle_unit":
            self.strip.set_degrees(value == "deg")
        elif key == "graph_window_s":
            self.monitor.set_window(float(value))  # type: ignore[arg-type]
        elif key == "graph_preset":
            self.monitor.set_preset(str(value))

    # ================================================================ 区画の幅
    def _restore_layout(self) -> None:
        width = W_INSPECTOR
        settings = self._settings()
        if settings is not None:
            try:
                width = int(settings.value(LAYOUT_KEY, W_INSPECTOR))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                width = W_INSPECTOR
        self._set_inspector_width(width)

    def _set_inspector_width(self, width: int) -> None:
        """インスペクタを `width` px にする(範囲外は丸める)。表示前でも効くよう既定の比率も持つ。"""
        self._inspector_w = max(W_INSPECTOR, min(W_INSPECTOR_MAX, width))
        total = max(self.splitter.width(), self._inspector_w + 400)
        self.splitter.setSizes([total - self._inspector_w - 1, self._inspector_w + 1])

    def inspector_width(self) -> int:
        return self.inspector.width()

    def _save_layout(self, *_args: object) -> None:
        settings = self._settings()
        if settings is not None:
            settings.setValue(LAYOUT_KEY, self.inspector.width())
            settings.sync()

    def reset_layout(self) -> None:
        """区画の幅を既定に戻す(設定ページの「レイアウトの初期化」から)。保存値は呼び出し側が消す。"""
        self._set_inspector_width(W_INSPECTOR)

    def _settings(self):  # noqa: ANN202
        get = getattr(self.shell, "settings", None)
        return get() if callable(get) else None

    # ================================================================ 記録
    def toggle_recording(self) -> None:
        if self.content.currentIndex() != CONTENT_MONITOR:
            return
        self.set_recording(not self.recorder.active)

    def set_recording(self, on: bool) -> None:
        if on == self.recorder.active:
            return
        if on:
            self.recorder.start()
            self.monitor.set_recording(True, format_elapsed(0))
            self._record_timer.start()
            return
        self._record_timer.stop()
        self.monitor.set_recording(False)
        try:
            path = self.recorder.stop()
        except OSError as exc:
            self._toast(tr("operate.monitor.save_failed", reason=str(exc)), "caution")
            return
        if path is None:
            self._toast(tr("operate.monitor.nothing_recorded"), "info")
        else:
            self._toast(tr("operate.monitor.saved", name=path.name), "success")

    def _tick_record(self) -> None:
        self.monitor.set_recording(True, format_elapsed(self.recorder.elapsed()))

    def _toast(self, text: str, kind: str) -> None:
        if self.shell is not None:
            self.shell.toast(text, kind)

    # ================================================================ 接続の空状態
    def _on_connect_requested(self, cfg: dict) -> None:
        self._connecting = True
        self._pending_timer.start()
        self._refresh_content()
        self.bridge.connect_adapter(cfg)

    def _on_reconnect_requested(self) -> None:
        self._connecting = True
        self._pending_timer.start()
        self._refresh_content()
        self.bridge.reconnect()

    def _on_rescan_requested(self) -> None:
        self._scanning = True
        self._pending_timer.start()
        self._refresh_content()
        self.bridge.scan()

    def _clear_pending(self) -> None:
        self._connecting = False
        self._scanning = False
        self._refresh_content()

    def _refresh_content(self) -> None:
        info = self.bridge.connection_info()
        if info.connected and self.bridge.motors():
            self.content.setCurrentIndex(CONTENT_MONITOR)
            return
        if not info.connected:
            if self._connecting:
                mode = "connecting"
            else:
                mode = "lost" if info.lost else "offline"
        else:
            mode = "scanning" if self._scanning else "none"
        self.state_view.set_mode(mode)
        self.content.setCurrentIndex(CONTENT_STATE)

    # ================================================================ Bridge → 表示
    def _on_connection(self, info: ConnectionInfo) -> None:
        self._connecting = False
        if info.connected and not self.bridge.motors():
            self._scanning = True  # 接続直後は自動スキャンの結果を待つ
            self._pending_timer.start()
        elif not info.connected:
            self._scanning = False
        self._refresh_content()
        self.inspector.refresh()

    def _on_motors(self, motors: list[MotorInfo]) -> None:
        self._scanning = False
        sel = self.bridge.selected_motor()
        ids = [m.id for m in motors]
        self.inspector.set_motor(sel if sel in ids else (ids[0] if ids else None))
        self.inspector.refresh()
        self._refresh_content()
        self._refresh_all()

    def _on_selected(self, motor_id: int) -> None:
        if motor_id in [m.id for m in self.bridge.motors()]:
            self.inspector.set_motor(motor_id)
        self._sel_sig = ()
        self._refresh_all()

    def _on_states(self, states: dict[int, MotorViewState]) -> None:
        if self.recorder.active:
            self.recorder.add(states)
        sel = self.bridge.selected_motor()
        s = states.get(sel)
        sig = (
            s.ui_state if s is not None else "offline",
            s.faults if s is not None else (),
            s.controller if s is not None else None,
        )
        now = clock.now()
        if sig == self._sel_sig and now - self._last_ui < UI_THROTTLE_S:
            return
        self._sel_sig = sig
        self._last_ui = now
        self._set_strip(s)
        self.inspector.set_state(s)

    def _set_strip(self, s: MotorViewState | None) -> None:
        self.strip.set_state(s, self.bridge.power_limits())
        ok, reason = zero_availability(s, self.bridge.estop_latched())
        self.strip.set_zero_available(ok and not self.zero_flow.busy(), reason)

    def _on_tier(self, _tier: str) -> None:
        self._refresh_strip()

    def _on_estop(self, _latched: bool) -> None:
        self.inspector.refresh()
        self._refresh_strip()

    def _refresh_strip(self) -> None:
        self._set_strip(self.bridge.states().get(self.bridge.selected_motor()))

    def _refresh_all(self) -> None:
        s = self.bridge.states().get(self.bridge.selected_motor())
        self._set_strip(s)
        self.inspector.set_state(s)

    # ================================================================ 原点に設定
    def _on_zero_requested(self) -> None:
        """[原点に設定] → 確認ポップオーバー(有効・運転中なら「停止して無効化」も知らせる)。"""
        mid = self.bridge.selected_motor()
        ok, _reason = zero_availability(self.bridge.states().get(mid), self.bridge.estop_latched())
        if not ok or self.zero_flow.busy():
            return
        self.zero_popover.open_for(
            self.strip.zero_btn,
            will_stop=self.zero_flow.needs_stop(mid),
            on_confirm=lambda m=mid: self._confirm_zero(m),
        )

    def _confirm_zero(self, motor_id: int) -> None:
        if self.zero_flow.start(motor_id):
            self._refresh_strip()  # 進行中は押せない

    def _on_log(self, entry: LogEntry) -> None:
        if entry.level != "debug":
            self.event_line.set_entry(entry)

    def _open_diagnostics(self) -> None:
        if self.shell is not None:
            self.shell.navigate("diagnostics", "log")
