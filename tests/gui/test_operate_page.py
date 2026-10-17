"""運転ページの空状態(未接続・通信断・モータなし)・接続ポップオーバー・シェルとの結線のテスト。"""

from __future__ import annotations

import pytest
from operate_helpers import make_page, teardown
from PySide6.QtCore import Qt

from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.i18n import tr
from edulite05.app.main_window import MainWindow
from edulite05.app.pages.operate import OperatePage
from edulite05.app.pages.operate.connection_popover import ConnectionForm, ConnectionPopover
from edulite05.app.pages.operate.page import CONTENT_MONITOR, CONTENT_STATE
from edulite05.app.ui.style import theme_manager


@pytest.fixture(autouse=True)
def _theme():
    theme_manager().apply("dark")
    yield
    theme_manager().apply("dark")


@pytest.fixture
def offline(qtbot, tmp_path):
    page, bridge, shell = make_page(qtbot, tmp_path, start_offline=True)
    yield page, bridge, shell
    teardown(page, bridge)


@pytest.fixture
def online(qtbot, tmp_path):
    page, bridge, shell = make_page(qtbot, tmp_path)
    yield page, bridge, shell
    teardown(page, bridge)


# ------------------------------------------------------------------ 未接続の空状態
def test_offline_empty_state_with_inline_connect_form(offline, qtbot) -> None:
    page, bridge, _shell = offline
    assert page.content.currentIndex() == CONTENT_STATE
    sv = page.state_view
    assert sv.mode == "offline"
    assert sv.empty.title_label.text() == tr("operate.empty.offline_title")
    assert sv.form.isVisible() and sv.action_button().text() == tr("operate.empty.connect")
    assert sv.action_button().property("variant") == "accent"
    assert sv.form.config()["adapter"] == "simulator"  # DemoBridge(simulator)の既定
    # 接続 → 接続中 → スキャン中 → モニタ
    sv.action_button().click()
    assert sv.mode == "connecting" and not sv.action_button().isVisible()
    assert not sv.form.isVisible()
    qtbot.waitUntil(lambda: sv.mode == "scanning" or page.content.currentIndex() == 0, timeout=3000)
    qtbot.waitUntil(lambda: page.content.currentIndex() == CONTENT_MONITOR, timeout=5000)
    assert bridge.connection_info().connected
    assert page.inspector.motor_id() == 1 and page.inspector.heading.text() == "ID1 EL05 #1"
    assert page.monitor.graph.motor_ids() == [1, 2]


def test_no_motors_found_shows_checklist_and_rescan(offline, qtbot) -> None:
    page, bridge, _shell = offline
    bridge._create_motors = lambda: None  # noqa: SLF001  (モータが 1 台も見つからない)
    bridge.connect_adapter({"adapter": "simulator", "port": ""})
    sv = page.state_view
    qtbot.waitUntil(lambda: sv.mode == "none", timeout=6000)
    assert page.content.currentIndex() == CONTENT_STATE
    assert sv.empty.title_label.text() == tr("operate.empty.none_title")
    assert sv.checklist.isVisible()
    texts = [c.text() for c in sv.checklist.findChildren(type(sv.empty.title_label))]
    assert any("終端抵抗" in t for t in texts) and any("電源" in t for t in texts)
    assert any("配線" in t for t in texts) and any("ID" in t for t in texts)
    assert sv.action_button().text() == tr("operate.empty.rescan")
    sv.action_button().click()
    assert sv.mode == "scanning"
    qtbot.waitUntil(lambda: sv.mode == "none", timeout=6000)
    # 今度は見つかる
    del bridge._create_motors  # noqa: SLF001
    sv.action_button().click()
    qtbot.waitUntil(lambda: page.content.currentIndex() == CONTENT_MONITOR, timeout=6000)


def test_comm_loss_and_disconnect(online, qtbot) -> None:
    page, bridge, _shell = online
    assert page.content.currentIndex() == CONTENT_MONITOR
    bridge.inject_comm_loss()
    sv = page.state_view
    assert page.content.currentIndex() == CONTENT_STATE and sv.mode == "lost"
    assert sv.action_button().text() == tr("operate.empty.reconnect")
    assert not page.inspector.primary.isVisible()
    sv.action_button().click()
    assert sv.mode == "connecting"
    qtbot.waitUntil(lambda: page.content.currentIndex() == CONTENT_MONITOR, timeout=6000)
    bridge.disconnect_adapter()
    assert sv.mode == "offline" and page.content.currentIndex() == CONTENT_STATE


def test_page_hooks_and_shutdown(online) -> None:
    page, _bridge, _shell = online
    for name in ("ensure_3d", "set_preload_failed", "shutdown", "activated", "deactivated"):
        assert callable(getattr(page, name))
    page.set_recording(True)
    page.shutdown()  # 記録中でも安全に終了できる
    assert not page.recorder.active


# ------------------------------------------------------------------ 接続ポップオーバー
def test_connection_popover_states(online, qtbot, monkeypatch) -> None:
    _page, bridge, _shell = online
    # 実機の COM ポートの有無(開発機は COM7 がある)に依存しない
    target = "edulite05.app.pages.operate.connection_popover.list_serial_ports"
    monkeypatch.setattr(target, lambda: [])
    pop = ConnectionPopover(bridge)
    qtbot.addWidget(pop)
    # 接続中: 要約 + 再スキャン + 切断
    assert pop._connected.isVisibleTo(pop) and not pop._form_host.isVisibleTo(pop)  # noqa: SLF001
    assert "シミュレータ" in pop.summary_label.text()
    assert pop.motors_label.text() == tr("operate.conn.motors", n=2)
    scans = []
    bridge.scan = lambda: scans.append(1)  # type: ignore[method-assign]
    pop.rescan_btn.click()
    assert scans == [1]
    pop.disconnect_btn.click()
    assert not bridge.connection_info().connected
    # 未接続: フォーム + 接続(強調)
    assert pop._form_host.isVisibleTo(pop) and not pop._connected.isVisibleTo(pop)  # noqa: SLF001
    assert pop.connect_btn.property("variant") == "accent"
    assert pop.config() == {"adapter": "simulator", "port": "", "bitrate": 1_000_000,
                            "host_id": 0xFD}
    assert not pop.port_combo.isEnabled()  # シミュレータにポートは要らない
    pop.adapter_combo.setCurrentIndex(pop.adapter_combo.findData("usbcan"))
    assert pop.port_combo.isEnabled() and pop.refresh_btn.isEnabled()
    pop.adapter_combo.setCurrentIndex(pop.adapter_combo.findData("simulator"))
    pop.connect_btn.click()
    qtbot.waitUntil(lambda: bridge.connection_info().connected, timeout=3000)


def test_connection_form_lists_ports_with_official_adapter_first(online, monkeypatch) -> None:
    from edulite05.app import ports
    from edulite05.app.pages.operate import connection_popover as cp

    fake = [
        ports.PortInfo("COM3", "Other", False),
        ports.PortInfo("COM7", "USB-SERIAL CH340", True),
    ]
    ordered = sorted(fake, key=lambda p: not p.preferred)
    monkeypatch.setattr(cp, "list_serial_ports", lambda: ordered)
    _page, bridge, _shell = online
    form = ConnectionForm(bridge)
    assert form.port_combo.itemText(0).startswith("★ COM7")
    assert form.port_combo.itemData(1) == "COM3"
    form.adapter_combo.setCurrentIndex(form.adapter_combo.findData("usbcan"))
    assert form.config()["port"] == "COM7"


# ------------------------------------------------------------------ シェルとの結線
@pytest.fixture
def window(qtbot):
    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0)
    win = MainWindow(bridge, settings_path=None, enable_3d=False)
    qtbot.addWidget(win)
    win.show()
    bridge.start()
    qtbot.waitExposed(win)
    yield win
    win.close()


def test_shell_builds_operate_page_and_popover(window, qtbot) -> None:
    win = window
    page = win.page("operate")
    assert isinstance(page, OperatePage)
    assert win.nav.current() == "operate" and win.stack.currentWidget() is page
    # イベント行 → 診断ページ
    qtbot.mouseClick(page.event_line, Qt.MouseButton.LeftButton)
    assert win.current_page_id() == "diagnostics"
    win.navigate("operate")
    # ヘッダーの接続ボタン → ポップオーバー(この部品)
    win.open_connection()
    assert isinstance(win._conn_popover, ConnectionPopover)  # noqa: SLF001
    win._conn_popover.hide()  # noqa: SLF001
    # 先読みのフック(シェルが呼ぶ)
    assert callable(getattr(page, "ensure_3d", None))
    assert callable(getattr(page, "set_preload_failed", None))


def test_shell_estop_and_f5_f6_keys(window, qtbot) -> None:
    win = window
    page = win.page("operate")
    bridge = win.bridge
    win.activateWindow()
    qtbot.keyClick(page.inspector, Qt.Key.Key_F5)
    qtbot.waitUntil(lambda: bridge.states()[1].ui_state == "enabled", timeout=2000)
    qtbot.keyClick(page.inspector, Qt.Key.Key_F5)
    qtbot.waitUntil(lambda: bridge.states()[1].ui_state == "running", timeout=2000)
    qtbot.keyClick(page.inspector, Qt.Key.Key_F6)
    qtbot.waitUntil(lambda: bridge.states()[1].ui_state == "enabled", timeout=2000)
    # 他のページでは F5 を無視する
    win.navigate("diagnostics")
    qtbot.keyClick(win, Qt.Key.Key_F5)
    qtbot.wait(100)
    assert bridge.states()[1].ui_state == "enabled"
    win.navigate("operate")
    # 非常停止(Esc)後は有効化できない
    win.estop()
    assert not page.inspector.primary.isEnabled()
    assert page.inspector.primary.toolTip() == tr("operate.action.estop_latched")


# ------------------------------------------------------------------ 設定ページの値・区画の幅
def make_window_with_ini(qtbot, ini):
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0)
    win = MainWindow(bridge, settings_path=ini, enable_3d=False)
    qtbot.addWidget(win)
    win.show()
    bridge.start()
    qtbot.waitExposed(win)
    return win


def test_operate_reads_prefs_at_start_and_follows_changes(qtbot, tmp_path) -> None:
    from PySide6.QtCore import QSettings

    ini = tmp_path / "ui_state.ini"
    s = QSettings(str(ini), QSettings.Format.IniFormat)
    s.setValue("prefs/angle_unit", "deg")
    s.setValue("prefs/graph_window_s", 60)
    s.setValue("prefs/graph_preset", "mit")
    s.setValue("prefs/host_id", 0x10)
    s.sync()
    win = make_window_with_ini(qtbot, ini)
    page = win.page("operate")
    assert page.strip.use_degrees()
    assert page.monitor.window_combo.currentData() == 60.0
    assert page.monitor.preset_combo.currentData() == "mit"
    assert page.state_view.form.config()["host_id"] == 0x10  # 接続フォームのホスト ID
    # 設定ページでの変更に追従する
    prefs = win.page("settings").prefs
    prefs.set("angle_unit", "rad")
    prefs.set("graph_window_s", 120)
    prefs.set("graph_preset", "env")
    assert not page.strip.use_degrees()
    assert page.monitor.window_combo.currentData() == 120.0
    assert page.monitor.preset_combo.currentData() == "env"
    prefs.set("host_id", 0x20)
    assert win._host_id() == 0x20  # noqa: SLF001  (接続ポップオーバーも同じ値)
    win.close()


def test_inspector_width_persists_under_layout_group_and_resets(qtbot, tmp_path) -> None:
    from PySide6.QtCore import QSettings

    from edulite05.app.ui.tokens import W_INSPECTOR, W_INSPECTOR_MAX

    ini = tmp_path / "ui_state.ini"
    win = make_window_with_ini(qtbot, ini)
    page = win.page("operate")
    qtbot.waitUntil(lambda: page.inspector.width() == W_INSPECTOR, timeout=2000)
    total = page.splitter.width()
    page.splitter.setSizes([total - 401, 401])  # ドラッグ相当(モニタの最小幅があるので控えめに)
    page.splitter.splitterMoved.emit(total - 401, 1)
    assert page.inspector.width() == 400
    raw = QSettings(str(ini), QSettings.Format.IniFormat)
    assert raw.value("layout/operate/inspector_w") == 400
    win.close()
    # 次の起動で復元される
    win2 = make_window_with_ini(qtbot, ini)
    page2 = win2.page("operate")
    qtbot.waitUntil(lambda: page2.inspector.width() == 400, timeout=2000)
    # 範囲外は丸める。reset_layout() で既定に戻る
    page2._set_inspector_width(10_000)  # noqa: SLF001
    assert page2.inspector.width() <= W_INSPECTOR_MAX
    page2.reset_layout()
    assert page2.inspector.width() == W_INSPECTOR
    win2.close()
