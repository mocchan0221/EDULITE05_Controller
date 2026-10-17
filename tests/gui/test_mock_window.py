"""UI v2 シェル(MainWindow)のテスト(offscreen)。ヘッダー・バナー・ナビ・非常停止を確認する。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt

import edulite05.app.main_window as mw
from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.i18n import tr
from edulite05.app.main_window import MIN_SIZE, MainWindow
from edulite05.app.pages import PageBase
from edulite05.app.ui.style import theme_manager
from edulite05.core.control.params import VelocityParams


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


def test_shell_structure(window) -> None:
    win = window
    specs = win.plugins.ui.pages()
    ids = [p.id for p in specs]
    assert ids == ["operate", "params", "diagnostics", "maintenance", "settings"]
    assert win.nav.item_ids() == ids
    assert win.stack.count() == 5
    assert win.nav.current() == "operate" and win.stack.currentWidget() is win.page("operate")
    # 各ページは登録(builtin プラグイン)どおりのクラス(スタブは残っていない)
    for spec in specs:
        page = win.page(spec.id)
        assert isinstance(page, PageBase) and type(page) is spec.factory.load()
        assert win.stack.indexOf(page) >= 0
    # ヘッダー: アプリ名・接続・チップ 2 台・出力段階・非常停止
    h = win.header
    assert h.app_name.text() == tr("app.title")
    assert h.connection_btn.state == "connected"
    assert set(h.chips()) == {1, 2}
    assert h.estop_btn.isVisible() and h.tier.isVisible()
    assert h.estop_btn.height() == 40 and h.estop_btn.width() >= 140
    # 設定はナビの最下部(保守との間に隙間)
    assert win.nav.item("settings").y() > win.nav.item("maintenance").y() + 100
    # ステータスバー: バックエンド種別
    assert win.kind_label.text() == tr("status_bar.kind_simulator")
    # 最小サイズ(接続後の幅は test_minimum_size_after_connect_fits_1280x720 が測る)
    assert win.minimumSize() == MIN_SIZE
    assert win.minimumSizeHint().width() <= MIN_SIZE.width()
    assert win.minimumSizeHint().height() <= MIN_SIZE.height()


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_minimum_size_after_connect_fits_1280x720(qtbot, real_fonts, lang) -> None:
    """接続後(モータ 2 台・読み値カードに消費電力の行と [原点に設定])の最小サイズが 1280×720 に収まる。

    起動直後ではなく、接続して 2 台のチップ・読み値・バナーが画面へ流れ込んだ後で測る(接続で
    ヘッダーのチップ・ステータスバー・読み値が増えるため、起動直後より幅が出る)。読み値は最も
    幅を取る内容(多回転の補足・「低出力上限で制限中」・消費電力の値)にして測る。
    実機の最小幅は ja 約 1202 / en 約 1200(運転ページ: ナビ 72 + モニタ 765 + インスペクタ 360)。
    offscreen はフォントが無く全文字が 1 em 幅になるので、conftest が OS のフォントを読み込む
    (`real_fonts`)。フォントが無い環境では測っても意味がないので skip する。
    """
    if not real_fonts:
        pytest.skip("UI フォント(Yu Gothic UI)が無く、幅を測れない")
    from edulite05.app import i18n

    i18n.load_catalog(lang)
    try:
        theme_manager().apply("dark")
        bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0)
        win = MainWindow(bridge, settings_path=None, enable_3d=False)
        qtbot.addWidget(win)
        win.show()
        bridge.start()
        qtbot.waitExposed(win)
        qtbot.waitUntil(lambda: set(win.header.chips()) == {1, 2}, timeout=5000)
        bridge.enable([1], 2)  # 有効にすると消費電力の値が出る
        qtbot.waitUntil(
            lambda: win.page("operate").strip.env_cell.extra_texts()[1] != "—", timeout=5000
        )
        strip = win.page("operate").strip
        limited = tr("operate.readout.limited", tier=tr("tier.low.short"))
        strip.pos_cell.set_value(-12.345)
        strip.pos_cell.set_meta(tr("operate.readout.multi_turn", value="−12.57 rad"))
        strip.vel_cell.set_value(-20.0)
        strip.vel_cell.set_note(limited)
        strip.torque_cell.set_value(-1.234)
        strip.torque_cell.set_note(limited)
        strip.env_cell.set_extra(tr("operate.readout.power_regen"), "88.8 W")
        qtbot.wait(100)
        # 窓の最小サイズ = 全ページの最小の最大(QStackedLayout)。運転ページが最大
        hint = win.minimumSizeHint()
        assert hint.width() <= MIN_SIZE.width(), (lang, hint, "運転ページ", strip.minimumSizeHint())
        assert hint.height() <= MIN_SIZE.height(), (lang, hint)
        for spec in win.plugins.ui.pages():
            w = win.page(spec.id).minimumSizeHint().width()
            assert w + win.nav.width() <= MIN_SIZE.width(), (lang, spec.id, w)
        # 1280×720 まで縮めても主要操作(非常停止・出力段階・チップ・原点に設定)が窓の中に見える
        win.resize(MIN_SIZE)
        qtbot.wait(100)
        assert win.size().width() <= MIN_SIZE.width() + 2  # 最小サイズで止まる(はみ出して広がらない)
        inner = win.rect()
        for w in (win.header.estop_btn, win.header.tier, strip.zero_btn, strip.unit_toggle,
                  *win.header.chips().values()):
            r = w.rect()
            r.moveTopLeft(w.mapTo(win, r.topLeft()))
            assert inner.contains(r), (lang, type(w).__name__, r, inner)
    finally:
        i18n.load_catalog("ja")


def test_chips_follow_state_and_select(window, qtbot) -> None:
    win, b = window, window.bridge
    chips = win.header.chips()
    assert chips[1].ui_state == "disabled" and chips[2].ui_state == "disabled"
    assert chips[1].selected and not chips[2].selected
    b.enable([2], 2)
    assert win.header.chips()[2].ui_state == "enabled"
    b.start_controller("velocity", {"motor": 2}, VelocityParams(velocity=1.0))
    assert win.header.chips()[2].ui_state == "running"
    assert win.header.chips()[2].state_text() == tr("motor_state.running")
    # クリックで操作対象を切替
    qtbot.mouseClick(chips[2], Qt.MouseButton.LeftButton)
    assert b.selected_motor() == 2
    assert win.header.chips()[2].selected and not win.header.chips()[1].selected


def test_page_switch_by_nav_and_shortcuts(window, qtbot) -> None:
    win = window
    win.activateWindow()
    qtbot.mouseClick(win.nav.item("params"), Qt.MouseButton.LeftButton)
    assert win.nav.current() == "params" and win.stack.currentWidget() is win.page("params")
    for i, pid in enumerate(win.nav.item_ids()):
        qtbot.keyClick(win, Qt.Key(Qt.Key.Key_1.value + i), Qt.KeyboardModifier.ControlModifier)
        assert win.nav.current() == pid, pid
        assert win.stack.currentWidget() is win.page(pid)
    win.navigate("operate")  # ShellApi
    assert win.current_page_id() == "operate"


@pytest.fixture
def estop_spy(window, monkeypatch) -> list[int]:
    calls: list[int] = []
    real = window.bridge.estop

    def spy() -> None:
        calls.append(1)
        real()

    monkeypatch.setattr(window.bridge, "estop", spy)
    return calls


def test_escape_and_pause_trigger_estop(window, qtbot, estop_spy) -> None:
    win, calls = window, estop_spy
    win.bridge.enable([1], 2)
    win.bridge.start_controller("velocity", {"motor": 1}, VelocityParams(velocity=2.0))
    win.activateWindow()
    qtbot.keyClick(win, Qt.Key.Key_Escape)
    assert calls, "運転中の Esc で estop が呼ばれていない"
    assert win.bridge.states()[1].ui_state == "disabled"
    calls.clear()
    qtbot.keyClick(win, Qt.Key.Key_Pause)
    assert calls == [1]
    calls.clear()
    win.header.estop_btn.click()  # ボタンも同じ経路
    assert calls == [1]
    assert len(win.estop_shortcuts) == 1  # Pause のみ(Esc はイベントフィルタ)


def test_pause_always_estops(window, qtbot, estop_spy) -> None:
    win = window  # 全モータ停止中でも Pause は非常停止
    assert not win.motors_live()
    qtbot.keyClick(win, Qt.Key.Key_Pause)
    assert estop_spy == [1]


def test_escape_with_enabled_motor_beats_line_edit(window, qtbot, estop_spy) -> None:
    from PySide6.QtWidgets import QLineEdit

    win = window
    win.bridge.enable([1], 2)  # 有効(運転はしていない)
    win.navigate("params")
    edit = win.page("params").search
    assert isinstance(edit, QLineEdit)
    edit.setFocus()
    edit.setText("kp")
    qtbot.keyClick(edit, Qt.Key.Key_Escape)
    assert estop_spy == [1]
    assert win.bridge.estop_latched() and edit.text() == "kp"  # Esc は入力欄に渡らない


def test_escape_with_enabled_motor_beats_modal_dialog(window, qtbot, estop_spy) -> None:
    from PySide6.QtWidgets import QApplication

    from edulite05.app.ui.dialogs import MotorTarget, SetZeroDialog

    win = window
    win.bridge.enable([1], 2)
    dlg = SetZeroDialog(win, MotorTarget(1, "EL05 #1", "enabled"))
    qtbot.addWidget(dlg)
    dlg.open()
    assert QApplication.activeModalWidget() is dlg
    qtbot.keyClick(dlg, Qt.Key.Key_Escape)
    assert estop_spy == [1]
    assert not dlg.isVisible()  # 非常停止はダイアログも閉じる


def test_escape_with_all_motors_stopped_is_normal_escape(window, qtbot, estop_spy) -> None:
    from edulite05.app.ui.dialogs import MotorTarget, SetZeroDialog

    win = window
    assert not win.motors_live()
    # 表の編集の取消
    win.navigate("params")
    page = win.page("params")
    qtbot.waitUntil(lambda: page.motor_value(0x701F) is not None, timeout=3000)
    ed = page.begin_edit(0x701F)
    assert ed is not None
    ed.type_text("8")
    qtbot.keyClick(ed, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: page.edit_widget(0x701F) is None, timeout=2000)
    assert page.pending() == {}
    assert estop_spy == [] and not win.bridge.estop_latched()
    # ダイアログは普通に閉じる
    dlg = SetZeroDialog(win, MotorTarget(1, "EL05 #1", "disabled"))
    qtbot.addWidget(dlg)
    dlg.open()
    qtbot.keyClick(dlg, Qt.Key.Key_Escape)
    assert not dlg.isVisible()
    assert estop_spy == [] and not win.bridge.estop_latched()


def test_estop_latch_banner_and_release(window, qtbot, monkeypatch) -> None:
    win, b = window, window.bridge
    b.set_power_tier("high")
    assert "high" in win.banners.active_keys()
    win.header.estop_btn.click()
    assert b.estop_latched()
    keys = win.banners.active_keys()
    assert keys[0] == "estop" and "high" not in keys  # 非常停止で出力段階は低出力へ戻る
    bar = win.banners.bar("estop")
    assert bar.isVisible() and bar.title() == tr("banner.estop_title")
    assert bar.action_button is not None and bar.action_button.text() == tr("banner.estop_action")
    # 全操作不可: 有効化できず、高出力は選べない(理由つき)
    b.enable([1], 2)
    assert win.header.chips()[1].ui_state == "disabled"
    assert not win.header.tier.item_enabled("high")
    assert win.header.tier.item_tooltip("high") == tr("header.tier_estop_latched")
    # 解除は確認ダイアログ経由(キャンセルなら解除しない)
    monkeypatch.setattr(mw, "confirm_release_estop", lambda _p: False)
    bar.action_button.click()
    assert b.estop_latched() and bar.isVisible()
    monkeypatch.setattr(mw, "confirm_release_estop", lambda _p: True)
    bar.action_button.click()
    assert not b.estop_latched() and "estop" not in win.banners.active_keys()
    assert {c.ui_state for c in win.header.chips().values()} == {"disabled"}  # 自動再開しない


def test_high_tier_flow_and_banner(window, qtbot, monkeypatch) -> None:
    win, b = window, window.bridge
    asked: list[bool] = []

    def fake_confirm(_parent) -> bool:
        asked.append(True)
        return True

    monkeypatch.setattr(mw, "confirm_high_tier", fake_confirm)
    seg = win.header.tier
    assert seg.current() == "low" and "high" not in win.banners.active_keys()
    seg.button("high").click()
    assert asked == [True] and b.power_tier() == "high" and seg.current() == "high"
    assert "high" in win.banners.active_keys()
    bar = win.banners.bar("high")
    assert bar.severity == "caution" and bar.title() == tr("banner.high_title")
    # 高出力 → 低出力は確認なし。バナーの [低出力に戻す] でも戻る
    bar.action_button.click()
    assert b.power_tier() == "low" and seg.current() == "low"
    assert "high" not in win.banners.active_keys() and asked == [True]
    # 承認しなければ切り替わらない
    monkeypatch.setattr(mw, "confirm_high_tier", lambda _p: False)
    seg.button("high").click()
    assert b.power_tier() == "low" and seg.current() == "low"


def test_high_tier_disabled_with_reason_while_motor_enabled(window, qtbot) -> None:
    win, b = window, window.bridge
    seg = win.header.tier
    assert seg.item_enabled("high")
    b.enable([1], 2)
    assert not seg.item_enabled("high")
    assert seg.item_tooltip("high") == tr("header.tier_need_disabled")
    assert seg.item_enabled("low")
    b.disable([1])
    assert seg.item_enabled("high")


def test_fault_banner_and_nav_badge(window, qtbot) -> None:
    win, b = window, window.bridge
    b.inject_fault(2, "overtemp")
    assert "fault" in win.banners.active_keys()
    bar = win.banners.bar("fault")
    assert "ID2" in bar.title() and tr("fault.overtemp") in bar.text()
    assert win.nav.item("diagnostics").badge()
    assert win.header.chips()[2].ui_state == "fault"
    qtbot.mouseClick(bar.action_button, Qt.MouseButton.LeftButton)  # [原因を確認] → 診断ページ
    assert win.nav.current() == "diagnostics"
    b.clear_fault([2])
    assert "fault" not in win.banners.active_keys() and not win.nav.item("diagnostics").badge()


def test_comm_loss_banner_and_reconnect(window, qtbot) -> None:
    win, b = window, window.bridge
    b.inject_comm_loss()
    assert "comm" in win.banners.active_keys()
    assert win.header.connection_btn.state == "lost"
    assert {c.ui_state for c in win.header.chips().values()} == {"offline"}
    assert not win.header.tier.item_enabled("high")
    win.banners.bar("comm").action_button.click()  # [再接続]
    qtbot.wait(1500)
    assert "comm" not in win.banners.active_keys()
    assert win.header.connection_btn.state == "connected"


def test_banner_priority_order(window) -> None:
    """非常停止 > 故障 > 通信断 > 高出力 > モック/シミュレータ。重ねず上から積む。"""
    win = window
    b = win.bridge
    b.set_power_tier("high")
    b.inject_fault(1, "encoder")
    assert win.banners.active_keys() == ["fault", "high", "info"]
    b.estop()
    assert win.banners.active_keys() == ["estop", "fault", "info"]


def test_info_banner_dismiss(window, qtbot) -> None:
    win = window
    assert "info" in win.banners.active_keys()
    win.banners.bar("info").close_button.click()  # 閉じる(×)
    assert "info" not in win.banners.active_keys()


def test_start_offline_shell_and_connect(qtbot) -> None:
    theme_manager().apply("dark")
    b = DemoBridge(kind="simulator", demo_motion=False, seed=0, start_offline=True)
    win = MainWindow(b, settings_path=None, enable_3d=False)
    qtbot.addWidget(win)
    win.show()
    b.start()
    try:
        h = win.header
        assert h.connection_btn.state == "offline"
        assert h.connection_btn.text_value() == tr("header.disconnected")
        assert h.chips() == {} and not h.scan_btn.isEnabled()
        assert not h.tier.item_enabled("low") and not h.tier.item_enabled("high")
        assert win.banners.active_keys() == []
        assert h.estop_btn.isEnabled()  # 非常停止は常に押せる
        b.connect_adapter({"adapter": "simulator"})
        qtbot.wait(1500)  # 接続 → 自動スキャン
        assert h.connection_btn.state == "connected" and set(h.chips()) == {1, 2}
        assert h.tier.item_enabled("low") and h.tier.item_enabled("high")
        assert win.banners.active_keys() == ["info"]
    finally:
        win.close()


def test_connection_popover_opens_and_connects(qtbot) -> None:
    theme_manager().apply("dark")
    b = DemoBridge(kind="simulator", demo_motion=False, seed=0, start_offline=True)
    win = MainWindow(b, settings_path=None, enable_3d=False)
    qtbot.addWidget(win)
    win.show()
    b.start()
    try:
        win.header.connection_btn.click()
        pop = win._conn_popover
        assert pop is not None and pop.isVisible()
        assert pop.adapter_combo.currentData() == "simulator"  # 非実機では既定がシミュレータ
        pop.connect_btn.click()
        qtbot.wait(1500)
        assert b.connection_info().connected and not pop.isVisible()
    finally:
        win.close()


def test_toast_via_shell_api(window, qtbot) -> None:
    win = window
    win.toast(tr("gallery.toast_text"))
    assert win.toast_host.toast.isVisible()
    assert win.toast_host.toast.message() == tr("gallery.toast_text")


def test_theme_switch_and_persistence(qtbot, tmp_path: Path) -> None:
    ini = tmp_path / "ui_state.ini"
    theme_manager().apply("dark")
    w1 = MainWindow(DemoBridge(kind="mock", demo_motion=False), settings_path=ini, enable_3d=False)
    qtbot.addWidget(w1)
    w1.show()
    w1.set_theme("light")
    assert theme_manager().name == "light"
    w1.close()
    assert ini.exists()
    theme_manager().apply("dark")
    w2 = MainWindow(DemoBridge(kind="mock", demo_motion=False), settings_path=ini, enable_3d=False)
    qtbot.addWidget(w2)
    assert theme_manager().name == "light"  # 保存したテーマが復元される
    w2.close()
    theme_manager().apply("dark")


def test_shrunk_to_min_keeps_main_controls_visible(window, qtbot) -> None:
    """1280x720 でも非常停止・出力段階・モータチップが見える(はみ出さない)。"""
    win = window
    win.resize(MIN_SIZE)
    qtbot.wait(300)
    assert win.width() == MIN_SIZE.width() and win.height() == MIN_SIZE.height()
    widgets = [win.header.estop_btn, win.header.tier, *win.header.chips().values(),
               win.header.more_btn, win.header.connection_btn]
    for w in widgets:
        assert w.isVisible()
        right = w.mapTo(win, w.rect().bottomRight()).x()
        assert right <= win.width(), w


def test_fits_available_geometry(qtbot, tmp_path) -> None:
    """実画面(作業領域)に収まる。保存済みの巨大なジオメトリを復元しても超えない。"""
    from PySide6.QtWidgets import QApplication

    screen = QApplication.primaryScreen()
    avail = screen.availableGeometry()
    if avail.width() < MIN_SIZE.width() or avail.height() < MIN_SIZE.height():
        pytest.skip(f"画面が小さい(offscreen 等): {avail.width()}x{avail.height()}")
    theme_manager().apply("dark")
    ini = tmp_path / "ui_state.ini"

    def open_window() -> MainWindow:
        win = MainWindow(DemoBridge(kind="simulator", demo_motion=False, seed=0),
                         settings_path=ini, enable_3d=False)
        qtbot.addWidget(win)
        win.show()
        qtbot.waitExposed(win)
        qtbot.wait(300)
        return win

    win = open_window()
    assert avail.contains(win.frameGeometry())
    win.resize(avail.width() + 400, avail.height() + 300)
    win.save_layout()
    win.close()
    win2 = open_window()
    assert avail.contains(win2.frameGeometry())
    win2.close()


def _drive_modal(fn):
    """モーダルダイアログが開いたら `fn(dialog)` を呼ぶ(exec() を抜けさせるのは fn の役目)。"""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    def tick() -> None:
        dlg = QApplication.activeModalWidget()
        if dlg is None:
            QTimer.singleShot(20, tick)
        else:
            fn(dlg)

    QTimer.singleShot(20, tick)


def test_real_high_tier_dialog_requires_all_checks(window, qtbot) -> None:
    seen: dict[str, object] = {}

    def accept(dlg) -> None:
        seen["checks"] = len(dlg.checkboxes)
        seen["enabled_before"] = dlg.confirm_button.isEnabled()
        for cb in dlg.checkboxes:
            cb.setChecked(True)
        seen["enabled_after"] = dlg.confirm_button.isEnabled()
        dlg.confirm_button.click()

    _drive_modal(accept)
    assert mw.confirm_high_tier(window) is True
    assert seen == {"checks": 3, "enabled_before": False, "enabled_after": True}


def test_real_estop_release_dialog_needs_safety_check(window, qtbot) -> None:
    def cancel(dlg) -> None:
        assert not dlg.confirm_button.isEnabled()  # 「安全を確認しました」が必須
        dlg.cancel_button.click()

    _drive_modal(cancel)
    assert mw.confirm_release_estop(window) is False


def test_estop_dismisses_open_dialog(window, qtbot) -> None:
    """ダイアログを開いている最中の Esc / 非常停止でも全モータが止まり、ダイアログは閉じる。"""
    window.bridge.enable([1], 2)
    _drive_modal(lambda dlg: window.estop())
    assert mw.confirm_high_tier(window) is False
    assert window.bridge.estop_latched()
    assert window.header.chips()[1].ui_state == "disabled"


def test_more_menu_contents(window, qtbot) -> None:
    window._open_more_menu()
    menu = window._more_menu
    texts = [a.text() for a in menu.actions() if a.text()]
    assert tr("more_menu.docs") in texts and tr("more_menu.about") in texts
    assert tr("more_menu.demo") in texts  # シミュレータ/モックでは故障注入などを出す
    menu.close()
    window.add_more_action("プラグインの項目", lambda: None)
    window._open_more_menu()
    assert "プラグインの項目" in [a.text() for a in window._more_menu.actions()]
    window._more_menu.close()


def test_navigate_with_view_and_diag_dot(window, qtbot) -> None:
    """ShellApi.navigate(page, view) は show_view に渡す。診断の赤い点は診断ページが決める。"""
    win, b = window, window.bridge
    diag = win.page("diagnostics")
    win.navigate("diagnostics", "log")
    assert win.current_page_id() == "diagnostics" and diag.current_view() == "log"
    win.navigate("operate")
    win.navigate("diagnostics", "stats")
    assert diag.current_view() == "stats"
    win.navigate("params", "no-such-view")  # show_view を持たないページでも落ちない
    assert win.current_page_id() == "params"
    # 故障 → 点が付く(別ページにいる間)。バナーの [原因を確認] は故障ビューで開き、点が消える
    win.navigate("operate")
    b.inject_fault(2, "overtemp")
    assert win.nav.item("diagnostics").badge() and diag.has_unseen_faults()
    qtbot.mouseClick(win.banners.bar("fault").action_button, Qt.MouseButton.LeftButton)
    assert win.current_page_id() == "diagnostics" and diag.current_view() == "faults"
    assert not win.nav.item("diagnostics").badge()


def test_event_line_opens_log_view(window, qtbot) -> None:
    win = window
    win.page("operate").event_line.clicked.emit()
    assert win.current_page_id() == "diagnostics"
    assert win.page("diagnostics").current_view() == "log"


def test_settings_accessor(qtbot, tmp_path: Path) -> None:
    theme_manager().apply("dark")
    assert MainWindow(DemoBridge(kind="simulator", demo_motion=False), settings_path=None,
                      enable_3d=False).settings() is None
    ini = tmp_path / "s.ini"
    win = MainWindow(DemoBridge(kind="simulator", demo_motion=False), settings_path=ini,
                     enable_3d=False)
    qtbot.addWidget(win)
    s = win.settings()
    assert s is not None and Path(s.fileName()) == ini
    win.close()


def test_theme_system_persists_and_applies_on_startup(qtbot, tmp_path: Path, monkeypatch) -> None:
    from PySide6.QtCore import QSettings

    ini = tmp_path / "ui_state.ini"
    theme_manager().apply("dark")
    win = MainWindow(DemoBridge(kind="simulator", demo_motion=False), settings_path=ini,
                     enable_3d=False)
    qtbot.addWidget(win)
    monkeypatch.setattr("edulite05.app.pages.settings.resolve_system_theme", lambda: "light")
    win.page("settings").set_theme_mode("system")
    win.close()
    assert QSettings(str(ini), QSettings.Format.IniFormat).value("prefs/theme_mode") == "system"
    # 次の起動: 「システム」を覚えていて、OS が暗くなっていればダークで始まる
    monkeypatch.setattr("edulite05.app.main_window.resolve_system_theme", lambda: "dark")
    theme_manager().apply("light")
    win2 = MainWindow(DemoBridge(kind="simulator", demo_motion=False), settings_path=ini,
                      enable_3d=False)
    qtbot.addWidget(win2)
    assert theme_manager().name == "dark"
    assert win2.page("settings").theme_mode() == "system"
    win2.close()


def test_no_3d_preload_disables_3d_label(window) -> None:
    """enable_3d=False(--no-3d)でも start_3d_preload は 3D ビューを無効にする。"""
    view3d = window.page("operate").monitor.view3d
    window.start_3d_preload()
    assert view3d.state == "disabled"
    assert view3d.backend_label.text() == tr("operate.view3d.disabled")
