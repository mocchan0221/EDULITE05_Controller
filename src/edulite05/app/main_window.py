"""メインウィンドウ(シェル)。ui_design.md §4 の構成。

    ヘッダー(接続・モータチップ・出力段階・非常停止・⋯)
    バナー領域(優先順位付き)
    ナビレール + ページ(QStackedWidget)
    ステータスバー

ページは `PluginManager.ui.pages()`(組込み + プラグインの `add_page`)から組み立てる。
シェルは `Bridge` の signal を購読して
ヘッダー・バナー・ナビの表示を更新し、操作(接続・出力段階・非常停止)を `Bridge` に流す。
ページがシェルに頼めること(ページ移動・トースト・接続ポップオーバー)は `ShellApi`(pages/base.py)。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QGuiApplication,
    QKeyEvent,
    QKeySequence,
    QShowEvent,
)
from PySide6.QtGui import QShortcut as _QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.pages import PageBase
from edulite05.app.pages.operate import modes as operate_modes
from edulite05.app.plugin_host import QSettingsStateStore
from edulite05.app.ui.header import Header
from edulite05.app.ui.infobar import BannerStack, InfoBar
from edulite05.app.ui.navrail import NavRail
from edulite05.app.ui.popover import Menu, Popover
from edulite05.app.ui.style import resolve_system_theme, theme_manager
from edulite05.app.ui.toast import ToastHost
from edulite05.app.ui.tokens import H_STATUSBAR, SP_RELATED
from edulite05.app.ui.util import divider
from edulite05.app.viewmodels import (
    FAULT_KEYS,
    BusStats,
    ConnectionInfo,
    MotorInfo,
    MotorViewState,
)
from edulite05.plugin_api import PageReg, PluginManager

VERSION = "0.1.0"
FRAME_GUESS = QSize(16, 39)  # ウィンドウ枠(左右 + タイトルバー・下枠)の見積り。表示後は実測値を使う
MIN_SIZE = QSize(1280, 720)  # この大きさでも主要操作(非常停止・出力段階・チップ・主ボタン)が見える
DEFAULT_SIZE = QSize(1600, 1000)


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_settings_path() -> Path:
    return project_root() / "user_data" / "ui_state.ini"


def confirm_high_tier(parent: QWidget | None) -> bool:
    """高出力への切替確認(ui_design.md §5.7)。前提 3 つが必須。テストはこの関数を差し替える。"""
    from edulite05.app.ui.dialogs import confirm_high_tier as _confirm

    return _confirm(parent)


def confirm_release_estop(parent: QWidget | None) -> bool:
    """非常停止の解除確認(「安全を確認しました」必須。解除後もモータは停止中のまま)。"""
    from edulite05.app.ui.dialogs import confirm_release_estop as _confirm

    return _confirm(parent)


class _Preloader(QObject):
    """pyvista / pyvistaqt を別スレッドで先読みする(共有フォルダ上では 40〜60 秒かかる)。"""

    finished = Signal(bool, str)

    def start(self) -> None:
        threading.Thread(target=self._run, name="preload-3d", daemon=True).start()

    def _run(self) -> None:
        try:
            import pyvista  # noqa: F401
            import pyvistaqt  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.finished.emit(False, f"{type(exc).__name__}: {exc}")
        else:
            self.finished.emit(True, "")


class _EscEStopFilter(QObject):
    """アプリ全体の Esc を横取りする。モータが有効/運転中なら非常停止して Esc を消費する。

    停止中・未接続なら何もしない(= 通常の Esc: 編集の取消・ダイアログ/ポップオーバーを閉じる)。
    """

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)  # 破棄時は Qt が自動で外す

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.KeyPress or not isinstance(event, QKeyEvent):
            return False
        if event.key() != Qt.Key.Key_Escape or not self._window.motors_live():
            return False
        self._window.estop()
        return True  # Esc を消費(取消・ダイアログ閉じには使わせない)


class MainWindow(QMainWindow):
    """シェル。`MainWindow(bridge, settings_path=None, enable_3d=True, plugins=None)`。

    `plugins` は起動時に作った `PluginManager`(組込み + `plugins/` + entry point)。省略すると
    組込みだけのマネージャを作る(テスト・ギャラリー用)。どちらもここで `load_all()` し、
    閉じるときに `shutdown()` する。
    """

    def __init__(
        self,
        bridge: Bridge,
        *,
        settings_path: Path | None = None,
        enable_3d: bool = True,
        plugins: PluginManager | None = None,
    ) -> None:
        super().__init__()
        self.bridge = bridge
        self._settings_path = settings_path
        self.plugins: PluginManager = plugins or PluginManager(
            gui=True, dropin=False, entry_points=False,
            state=QSettingsStateStore(self.settings),
        )
        self.plugins.load_all()  # 登録だけ(ページの生成は _build_pages)
        assert self.plugins.ui is not None
        operate_modes.install(self.plugins.ui.modes())  # 運転モード一覧(組込み + プラグイン)
        self._enable_3d = enable_3d
        self._fitted = False
        self._saved_geometry = False
        self._last_sig: tuple = ()
        self._info_dismissed = False
        self._more_actions: list[tuple[str, object]] = []
        self._preloader: _Preloader | None = None
        self._conn_popover: Popover | None = None
        self.setWindowTitle(tr("app.title"))
        self.setMinimumSize(MIN_SIZE)
        self.resize(self._initial_size())

        self._build_shell()
        self._build_pages()
        self._build_statusbar()
        self._build_shortcuts()
        self.toast_host = ToastHost(self)
        self._restore_settings()
        self._connect_bridge()
        theme_manager().changed.connect(self._save_theme)
        self._theme_connected = True

    # ================================================================ ShellApi
    def navigate(self, page_id: str, view: str | None = None) -> None:
        """ページを切り替える。`view` を渡すと、そのページの `show_view(view)` で表示を指定する。"""
        self.nav.set_current(page_id)
        if view is not None:
            show = getattr(self._pages.get(page_id), "show_view", None)
            if callable(show):
                show(view)

    def settings(self) -> QSettings | None:
        """UI 状態の保存先(`user_data/ui_state.ini`)。保存しない設定(テスト等)なら None。

        呼ぶたびに新しい `QSettings` を返す(ページは `layout/` 以下に区画の幅などを保存する)。
        """
        if self._settings_path is None:
            return None
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        return QSettings(str(self._settings_path), QSettings.Format.IniFormat)

    def toast(self, text: str, kind: str = "success") -> None:
        self.toast_host.show_message(text, kind)

    def open_connection(self) -> None:
        self._open_connection_popover()

    def page(self, page_id: str) -> PageBase:
        return self._pages[page_id]

    def current_page_id(self) -> str | None:
        return self.nav.current()

    def add_more_action(self, text: str, callback: object) -> None:
        """⋯ メニューに項目を足す(プラグイン用。ui_design.md §10)。"""
        self._more_actions.append((text, callback))

    # ================================================================ 構築
    def _build_shell(self) -> None:
        central = QWidget()
        # 起動直後にボタンへフォーカスリングを出さない
        central.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._central = central
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = Header(tr("app.title"))
        self.header.connection_clicked.connect(self._open_connection_popover)
        self.header.chip_selected.connect(self.bridge.select_motor)
        self.header.scan_clicked.connect(self.bridge.scan)
        self.header.tier_selected.connect(self._on_tier_selected)
        self.header.estop_clicked.connect(self.estop)
        self.header.more_clicked.connect(self._open_more_menu)
        root.addWidget(self.header)

        # バナー: 非常停止 > 故障 > 通信断 > HIGH > モック/シミュレータ
        self.banners = BannerStack()
        self.banners.add(
            "estop",
            InfoBar("estop", tr("banner.estop_title"), tr("banner.estop_text"),
                    action=tr("banner.estop_action")),
        )
        self.banners.add("fault", InfoBar("critical", "", "", action=tr("banner.fault_action")))
        self.banners.add(
            "comm",
            InfoBar("critical", tr("banner.comm_title"), tr("banner.comm_text"),
                    action=tr("banner.comm_action")),
        )
        self.banners.add(
            "high",
            InfoBar("caution", tr("banner.high_title"), tr("banner.high_text"),
                    action=tr("banner.high_action")),
        )
        # ログ再生(読み出し専用)。何も送らないことを常に見せる(閉じられない)
        self.banners.add(
            "replay",
            InfoBar("info", tr("banner.replay_title"), tr("banner.replay_text")),
        )
        kind = self.bridge.backend_kind
        has_info = kind != "hardware"
        self.banners.add(
            "info",
            InfoBar(
                "info",
                tr(f"banner.{kind}_title") if has_info else "",
                tr(f"banner.{kind}_text") if has_info else "",
                closable=True,
            ),
        )
        self.banners.bar("estop").action_clicked.connect(self._release_estop)
        self.banners.bar("fault").action_clicked.connect(
            lambda: self.navigate("diagnostics", "faults")
        )
        self.banners.bar("comm").action_clicked.connect(self.bridge.reconnect)
        self.banners.bar("high").action_clicked.connect(self._set_low)
        self.banners.bar("info").closed.connect(self._dismiss_info)
        root.addWidget(self.banners)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.nav = NavRail()
        body.addWidget(self.nav)
        self.page_host = QWidget()
        self.page_host.setObjectName("PageHost")
        self.page_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        host_lay = QVBoxLayout(self.page_host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        host_lay.addWidget(self.stack)
        body.addWidget(self.page_host, 1)
        root.addLayout(body, 1)
        self.setCentralWidget(central)

    def _build_pages(self) -> None:
        assert self.plugins.ui is not None
        self._pages: dict[str, PageBase] = {}
        self._specs: list[PageReg] = []
        for spec in self.plugins.ui.pages():
            try:
                page = spec.factory(self.bridge, self)
            except Exception as exc:  # noqa: BLE001 - プラグインのページだけ諦める
                if spec.owner.startswith("edulite05.builtin."):
                    raise
                self.plugins.report_failure(spec.owner, exc, "ページの生成")
                continue
            i = len(self._specs)
            self._specs.append(spec)
            self._pages[spec.id] = page
            self.stack.addWidget(page)
            title = tr(spec.title)
            self.nav.add_item(
                spec.id, title, spec.icon, bottom=spec.bottom,
                tooltip=tr("nav.tip", name=title, keys=f"Ctrl+{i + 1}") if i < 9 else title,
            )
        self._apply_extensions()
        self.nav.current_changed.connect(self._on_nav)
        # 診断の赤い点は、診断ページが決める(故障ビューで見たかどうか。ui_design.md §5.3)
        diag = self._pages.get("diagnostics")
        unseen = getattr(diag, "unseen_changed", None)
        if unseen is not None:
            unseen.connect(self._on_diag_unseen)
            self._on_diag_unseen(bool(getattr(diag, "has_unseen_faults", lambda: False)()))
        first = self._specs[0].id
        self.stack.setCurrentWidget(self._pages[first])
        self._pages[first].activated()

    def _apply_extensions(self) -> None:
        """プラグインが登録した保守項目・モニタ表示をページへ足す(ページの生成後)。

        factory の例外はそのプラグインだけ失敗扱いにする(アプリは続行)。
        """
        assert self.plugins.ui is not None
        maint = self._pages.get("maintenance")
        add_item = getattr(maint, "add_item", None)
        if callable(add_item):
            from edulite05.app.pages.maintenance import MaintenanceItem

            for reg in self.plugins.ui.maintenance_items():
                try:
                    add_item(MaintenanceItem(
                        reg.key, reg.group, tr(reg.title), tr(reg.description),
                        tuple(tr(t) for t in reg.tags), reg.dialog_factory, reg.needs_motor,
                    ))
                except Exception as exc:  # noqa: BLE001
                    self.plugins.report_failure(reg.owner, exc, "保守項目の追加")
        add_view = getattr(self._pages.get("operate"), "add_monitor_view", None)
        if callable(add_view):
            for view in self.plugins.ui.monitor_views():
                try:
                    add_view(view.id, tr(view.title), view.factory(self.bridge))
                except Exception as exc:  # noqa: BLE001
                    self.plugins.report_failure(view.owner, exc, "モニタ表示の追加")

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        sb.setContentsMargins(SP_RELATED, 0, SP_RELATED, 0)
        sb.setFixedHeight(H_STATUSBAR)
        self.fps_label = QLabel(tr("status_bar.fps", tx="0", rx="0"))
        self.loop_label = QLabel(tr("status_bar.loop", hz="0", jitter="0.0"))
        self.kind_label = QLabel(tr(f"status_bar.kind_{self.bridge.backend_kind}"))
        sb.addWidget(self.fps_label)
        sb.addWidget(self._bar_divider())
        sb.addWidget(self.loop_label)
        sb.addPermanentWidget(self.kind_label)

    @staticmethod
    def _bar_divider() -> QWidget:
        d = divider(Qt.Orientation.Vertical)
        d.setFixedHeight(12)
        return d

    def _build_shortcuts(self) -> None:
        """Pause = 非常停止(常時)、Ctrl+1.. = ページ切替。Esc の非常停止は `_EscEStopFilter`。"""
        self.estop_shortcuts: list[_QShortcut] = []
        sc = _QShortcut(QKeySequence(Qt.Key.Key_Pause), self)
        sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc.activated.connect(self.estop)
        self.estop_shortcuts.append(sc)
        # Esc: いずれかのモータが有効/運転中のときだけ非常停止(ui_design.md §9)。
        # 入力欄・表のエディタ・コンボのポップアップ・ポップオーバー・モーダルより先に受けるため、
        # QApplication にイベントフィルタを掛ける(ショートカットでは先に消費される)。
        self._esc_filter = _EscEStopFilter(self)
        self.page_shortcuts: list[_QShortcut] = []
        for i, spec in enumerate(self._specs[:9]):  # Ctrl+1..9
            sc = _QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(lambda pid=spec.id: self.navigate(pid))
            self.page_shortcuts.append(sc)

    def _connect_bridge(self) -> None:
        b = self.bridge
        b.connection_changed.connect(self._on_connection)
        b.motors_changed.connect(self._on_motors)
        b.selected_motor_changed.connect(self._on_selected)
        b.state_updated.connect(self._on_states)
        b.power_tier_changed.connect(self._on_tier)
        b.stats_updated.connect(self._on_stats)
        b.estop_changed.connect(self._on_estop)
        self._on_connection(b.connection_info())
        self._on_motors(b.motors())
        self._on_tier(b.power_tier())
        self._on_estop(b.estop_latched())
        self._on_states(b.states(), force=True)

    # ================================================================ 操作
    def motors_live(self) -> bool:
        """いずれかのモータが有効/運転中か(= Esc を非常停止として扱う条件)。"""
        return any(s.ui_state in ("enabled", "running") for s in self.bridge.states().values())

    def estop(self) -> None:
        """非常停止(常に全台)。Pause / Esc(運転中) / ボタン。開いているダイアログは閉じる。"""
        self.bridge.estop()
        modal = QApplication.activeModalWidget()
        if isinstance(modal, QDialog):
            modal.reject()

    def _release_estop(self) -> None:
        if confirm_release_estop(self):
            self.bridge.release_estop()

    def _set_low(self) -> None:
        self.bridge.set_power_tier("low")

    def _on_tier_selected(self, tier: str) -> None:
        """低出力 → 高出力は確認ダイアログ承認後のみ。高出力 → 低出力は即時。"""
        if tier == self.bridge.power_tier():
            return
        if tier == "high" and not confirm_high_tier(self):
            self.header.set_tier(self.bridge.power_tier())
            return
        if not self.bridge.set_power_tier(tier):
            self.toast(tr("header.tier_refused"), "caution")
        self.header.set_tier(self.bridge.power_tier())

    def _host_id(self) -> int:
        """設定ページのホスト ID(接続の既定値)。"""
        prefs = getattr(self._pages.get("settings"), "prefs", None)
        return int(prefs.get("host_id")) if prefs is not None else 0xFD

    def _open_connection_popover(self) -> None:
        from edulite05.app.pages.operate.connection_popover import ConnectionPopover

        if self._conn_popover is None:
            self._conn_popover = ConnectionPopover(self.bridge, self, host_id=self._host_id)
        self._conn_popover.show_below(self.header.connection_btn)

    def _open_more_menu(self) -> None:
        menu = Menu(self)
        menu.addAction(tr("more_menu.docs")).triggered.connect(self._open_docs)
        menu.addAction(tr("more_menu.about")).triggered.connect(self._about)
        extra = [*self._more_actions]
        if self.plugins.ui is not None:
            extra += [(r.text, r.callback) for r in self.plugins.ui.more_actions()]
        if extra:
            menu.addSeparator()
            for text, cb in extra:
                menu.addAction(tr(text)).triggered.connect(cb)  # type: ignore[arg-type]
        if self.bridge.backend_kind != "hardware":
            menu.addSeparator()
            demo = menu.addMenu(tr("more_menu.demo"))
            sub = demo.addMenu(tr("more_menu.inject_fault"))
            for key in FAULT_KEYS:
                sub.addAction(tr(f"fault.{key}")).triggered.connect(
                    lambda _c=False, k=key: self._inject_fault(k)
                )
            demo.addAction(tr("more_menu.inject_comm_loss")).triggered.connect(
                self.bridge.inject_comm_loss
            )
        self._more_menu = menu
        btn = self.header.more_btn
        menu.popup(btn.mapToGlobal(QPoint(btn.width() - menu.sizeHint().width(), btn.height() + 4)))

    def _inject_fault(self, key: str) -> None:
        self.bridge.inject_fault(self.bridge.selected_motor(), key)

    def _open_docs(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(project_root() / "docs")))

    def _about(self) -> None:
        kind = tr(f"status_bar.kind_{self.bridge.backend_kind}")
        QMessageBox.about(
            self, tr("more_menu.about"), tr("more_menu.about_text", version=VERSION, kind=kind)
        )

    def _dismiss_info(self) -> None:
        self._info_dismissed = True
        self.banners.set_active("info", False)

    def _on_nav(self, page_id: str) -> None:
        prev = self.stack.currentWidget()
        if isinstance(prev, PageBase):
            prev.deactivated()
        page = self._pages[page_id]
        self.stack.setCurrentWidget(page)
        page.activated()

    # ================================================================ Bridge → 表示
    def _on_connection(self, info: ConnectionInfo) -> None:
        if info.connected:
            self.header.set_connection(tr("header.connected", name=info.port or info.adapter),
                                       "connected")
        elif info.lost:
            self.header.set_connection(tr("header.disconnected"), "lost")
        else:
            self.header.set_connection(tr("header.disconnected"), "offline")
        self.banners.set_active("comm", info.lost)
        self.banners.set_active("replay", info.connected and info.read_only)
        self.kind_label.setText(
            tr("status_bar.kind_replay") if info.read_only
            else tr(f"status_bar.kind_{self.bridge.backend_kind}")
        )
        self._refresh_motor_hint()
        self._refresh_info_banner(info)
        self._refresh_availability()

    def _refresh_info_banner(self, info: ConnectionInfo) -> None:
        simulated = self.bridge.backend_kind != "hardware"
        show = simulated and not self._info_dismissed and info.connected
        self.banners.set_active("info", show)

    def _on_motors(self, motors: list[MotorInfo]) -> None:
        self.header.set_motors(
            [(m.id, m.name, m.color_index) for m in motors], self.bridge.selected_motor()
        )
        states = self.bridge.states()
        for m in motors:
            s = states.get(m.id)
            self.header.set_motor_state(m.id, s.ui_state if s else "offline")
        self._refresh_motor_hint()

    def _refresh_motor_hint(self) -> None:
        info = self.bridge.connection_info()
        self.header.set_no_motors_hint(info.connected and not self.bridge.motors())
        self.header.set_scan_enabled(info.connected, tr("header.scan_need_connection"))

    def _on_selected(self, motor_id: int) -> None:
        self.header.set_selected(motor_id)

    def _on_states(self, states: dict[int, MotorViewState], *, force: bool = False) -> None:
        sig = tuple((i, s.ui_state, s.faults) for i, s in sorted(states.items()))
        if sig == self._last_sig and not force:
            return
        self._last_sig = sig
        for mid, s in states.items():
            self.header.set_motor_state(mid, s.ui_state)
        faulted = sorted((s for s in states.values() if s.faults), key=lambda x: x.id)
        if faulted:
            ids = "・".join(tr("ui.motor_id", id=s.id) for s in faulted)
            names = "、".join(dict.fromkeys(tr(f"fault.{k}") for s in faulted for k in s.faults))
            self.banners.bar("fault").set_texts(tr("banner.fault_title", ids=ids), names)
        self.banners.set_active("fault", bool(faulted))
        self._refresh_availability()

    def _on_diag_unseen(self, on: bool) -> None:
        self.nav.set_badge("diagnostics", on)

    def _on_tier(self, tier: str) -> None:
        self.header.set_tier(tier)
        self.banners.set_active("high", tier == "high")
        self._refresh_availability()

    def _on_estop(self, latched: bool) -> None:
        self.banners.set_active("estop", latched)
        self._refresh_availability()

    def _on_stats(self, st: BusStats) -> None:
        self.fps_label.setText(tr("status_bar.fps", tx=f"{st.tx_fps:.0f}", rx=f"{st.rx_fps:.0f}"))
        self.loop_label.setText(
            tr("status_bar.loop", hz=f"{st.loop_hz:.0f}", jitter=f"{st.jitter_ms:.1f}")
        )

    def _refresh_availability(self) -> None:
        """できないことは押せなくして理由を言う(ui_design.md 原則 6)。出力段階の選択可否。"""
        seg = self.header.tier
        if not self.bridge.connection_info().connected:
            seg.set_all_enabled(False, tr("header.tier_need_connection"))
            return
        seg.set_item_enabled("low", True)
        if self.bridge.power_tier() == "high":
            seg.set_item_enabled("high", True)
        elif self.bridge.estop_latched():
            seg.set_item_enabled("high", False, tr("header.tier_estop_latched"))
        elif any(s.ui_state in ("enabled", "running") for s in self.bridge.states().values()):
            seg.set_item_enabled("high", False, tr("header.tier_need_disabled"))
        else:
            seg.set_item_enabled("high", True)

    # ================================================================ テーマ・サイズ・保存
    def set_theme(self, name: str) -> None:
        theme_manager().apply(name)

    def _save_theme(self, name: str) -> None:
        s = self.settings()
        if s is not None:
            s.setValue("theme", name)
            s.sync()

    @staticmethod
    def _initial_size() -> QSize:
        """既定 1600x1000。画面が小さければ作業領域に収まる大きさにする。"""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return DEFAULT_SIZE
        avail = screen.availableGeometry()
        return QSize(
            min(DEFAULT_SIZE.width(), avail.width() - FRAME_GUESS.width()),
            min(DEFAULT_SIZE.height(), avail.height() - FRAME_GUESS.height()),
        )

    def _fit_to_screen(self) -> None:
        """フレーム込みで作業領域(availableGeometry)に収める。最大化・全画面のときは何もしない。"""
        if self.isMaximized() or self.isFullScreen():
            return
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        frame = self.frameGeometry()
        geo = self.geometry()
        extra_w = max(frame.width() - geo.width(), 0)
        extra_h = max(frame.height() - geo.height(), 0)
        if extra_w == 0 and extra_h == 0:
            extra_w, extra_h = FRAME_GUESS.width(), FRAME_GUESS.height()
        max_w = avail.width() - extra_w
        max_h = avail.height() - extra_h
        if self._saved_geometry:
            w, h = min(geo.width(), max_w), min(geo.height(), max_h)
        else:
            w, h = min(DEFAULT_SIZE.width(), max_w), min(DEFAULT_SIZE.height(), max_h)
        if (w, h) != (geo.width(), geo.height()):
            self.resize(max(w, 1), max(h, 1))
        frame = self.frameGeometry()
        x = min(max(frame.x(), avail.left()), avail.right() + 1 - frame.width())
        y = min(max(frame.y(), avail.top()), avail.bottom() + 1 - frame.height())
        x = max(x, avail.left())
        y = max(y, avail.top())
        if (x, y) != (frame.x(), frame.y()):
            self.move(self.x() + (x - frame.x()), self.y() + (y - frame.y()))

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._fitted:
            self._fitted = True
            self._fit_to_screen()
            QTimer.singleShot(0, self._fit_to_screen)
            self._central.setFocus(Qt.FocusReason.OtherFocusReason)

    def _restore_settings(self) -> None:
        s = self.settings()
        if s is None:
            return
        # 設定ページで選んだ「システム」も保存してあり、起動時は OS の配色に合わせる
        mode = str(s.value("prefs/theme_mode", s.value("theme", theme_manager().name)))
        name = resolve_system_theme() if mode == "system" else mode
        if name in ("dark", "light") and name != theme_manager().name:
            theme_manager().apply(name)
        geo = s.value("shell/geometry")
        if geo is not None:
            self._saved_geometry = bool(self.restoreGeometry(geo))

    def save_layout(self) -> None:
        s = self.settings()
        if s is None:
            return
        s.setValue("shell/geometry", self.saveGeometry())
        s.setValue("theme", theme_manager().name)
        s.sync()

    def reset_layout(self) -> None:
        """ウィンドウの大きさを既定に戻す(設定ページの「レイアウトの初期化」用)。"""
        self._saved_geometry = False
        self.resize(self._initial_size())

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.save_layout()
        if self._theme_connected:
            self._theme_connected = False
            theme_manager().changed.disconnect(self._save_theme)
        for page in self._pages.values():
            shutdown = getattr(page, "shutdown", None)
            if callable(shutdown):
                shutdown()
        self.plugins.shutdown()  # プラグインの deactivate と登録の取消(bridge より先)
        shutdown = getattr(self.bridge, "shutdown", None)
        if callable(shutdown):
            shutdown()
        super().closeEvent(event)

    # ================================================================ 3D 先読み(運転ページが使う)
    def start_3d_preload(self) -> None:
        """表示後に pyvista / pyvistaqt を別スレッドで先読みし、運転ページの 3D ビューへ知らせる。

        運転ページ(P1)は `ensure_3d()` と `set_preload_failed(reason)` を実装する。
        """
        operate = self._pages.get("operate")
        # --no-3d でも 2D ダイヤルへ切り替える(「3D を読み込み中」のまま残さない)
        enable = getattr(operate, "set_3d_enabled", None)
        if callable(enable):
            enable(self._enable_3d)
        if not self._enable_3d or self._preloader is not None:
            return
        hook = getattr(operate, "ensure_3d", None)
        if "pyvistaqt" in sys.modules:
            if callable(hook):
                QTimer.singleShot(0, hook)
            return
        self._preloader = _Preloader(self)
        self._preloader.finished.connect(self._on_preload_done)
        self._preloader.start()

    def _on_preload_done(self, ok: bool, reason: str) -> None:
        operate = self._pages.get("operate")
        if ok:
            hook = getattr(operate, "ensure_3d", None)
            if callable(hook):
                hook()
        else:
            fail = getattr(operate, "set_preload_failed", None)
            if callable(fail):
                fail(reason)
