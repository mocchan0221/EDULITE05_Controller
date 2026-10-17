"""設定ページ(ui_design.md §5.5)のテスト(offscreen)。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.i18n import tr
from edulite05.app.main_window import MainWindow
from edulite05.app.pages import settings as settings_mod
from edulite05.app.pages.settings import PREF_DEFAULTS, HostIdSpin, Prefs, SettingsPage
from edulite05.app.ui.style import theme_manager


def make_window(qtbot, ini: Path | None) -> MainWindow:
    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0)
    win = MainWindow(bridge, settings_path=ini, enable_3d=False)
    qtbot.addWidget(win)
    win.show()
    bridge.start()
    qtbot.waitExposed(win)
    win.navigate("settings")
    return win


@pytest.fixture
def window(qtbot):
    win = make_window(qtbot, None)
    yield win
    win.close()


@pytest.fixture
def ini_window(qtbot, tmp_path):
    ini = tmp_path / "ui_state.ini"
    win = make_window(qtbot, ini)
    yield win, ini
    win.close()


def test_structure(window) -> None:
    page = window.page("settings")
    assert isinstance(page, SettingsPage)
    assert page.heading.text() == tr("nav.settings")
    assert [k for k, _ in (it for it in page.theme_seg._buttons.items())] == [
        "dark", "light", "system"
    ]
    assert page.theme_seg.current() == "dark"
    assert page.angle_seg.current() == "rad"
    assert page.window_combo.currentData() == 10
    assert page.preset_combo.currentData() == "standard"
    assert page.host_spin.value() == 0xFD and page.host_spin.text() == "0xFD"
    # 言語は日本語 / English(保存して次回の起動から反映)。プラグインは一覧に出る
    assert page.language_combo.isEnabled()
    assert [page.language_combo.itemData(i) for i in range(page.language_combo.count())] == [
        "ja", "en"
    ]
    assert [page.language_combo.itemText(i) for i in range(page.language_combo.count())] == [
        "日本語", "English"
    ]
    assert page.language_combo.currentData() == "ja"
    assert page.language_row.tags[0].isHidden()  # 実行中の言語と同じ → 「再起動で反映」は出さない
    assert set(page.plugin_rows) >= {"edulite05.builtin.pages", "edulite05.builtin.operate_modes"}
    assert not page.plugin_switches
    assert page.layout_button.isEnabled()


def test_theme_segment_switches_theme_via_shell(window) -> None:
    page = window.page("settings")
    page.theme_seg.selected.emit("light")  # クリックと同じ経路
    assert theme_manager().name == "light"
    page.theme_seg.selected.emit("dark")
    assert theme_manager().name == "dark"


def test_theme_system_follows_os(window, monkeypatch) -> None:
    page = window.page("settings")
    monkeypatch.setattr(settings_mod, "resolve_system_theme", lambda: "light")
    page.set_theme_mode("system")
    assert theme_manager().name == "light" and page.theme_mode() == "system"
    # OS の配色が変わったら追従
    monkeypatch.setattr(settings_mod, "resolve_system_theme", lambda: "dark")
    page._on_system_scheme()
    assert theme_manager().name == "dark"
    # システム以外を選べば追従しない
    page.set_theme_mode("light")
    page._on_system_scheme()
    assert theme_manager().name == "light"


def test_external_theme_change_updates_segment(window) -> None:
    page = window.page("settings")
    theme_manager().apply("light")  # ギャラリーなど別経路
    assert page.theme_seg.current() == "light"


def test_prefs_persist_to_settings_file(ini_window, qtbot) -> None:
    win, ini = ini_window
    page = win.page("settings")
    changes: list[tuple] = []
    page.prefs.changed.connect(lambda k, v: changes.append((k, v)))

    page.angle_seg.selected.emit("deg")
    page.window_combo.setCurrentIndex(page.window_combo.findData(60))
    page.window_combo.activated.emit(page.window_combo.currentIndex())
    page.preset_combo.setCurrentIndex(page.preset_combo.findData("mit"))
    page.preset_combo.activated.emit(page.preset_combo.currentIndex())
    page.host_spin.setValue(0xFE)
    page.set_theme_mode("light")
    assert ("angle_unit", "deg") in changes and ("graph_window_s", 60) in changes
    assert ("graph_preset", "mit") in changes and ("host_id", 0xFE) in changes

    # 同じファイルから読み直すと復元される(型も戻る)
    again = Prefs(ini)
    assert again.get("angle_unit") == "deg"
    assert again.get("graph_window_s") == 60
    assert again.get("graph_preset") == "mit"
    assert again.get("host_id") == 0xFE
    assert again.get("theme_mode") == "light"
    raw = QSettings(str(ini), QSettings.Format.IniFormat)
    assert raw.value("theme") == "light"  # 配色はシェルが従来のキーに保存する


def test_new_page_restores_saved_values(ini_window, qtbot) -> None:
    win, ini = ini_window
    s = QSettings(str(ini), QSettings.Format.IniFormat)
    s.setValue("prefs/angle_unit", "deg")
    s.setValue("prefs/graph_window_s", 30)
    s.setValue("prefs/host_id", 17)
    s.sync()
    page = SettingsPage(win.bridge, win)
    qtbot.addWidget(page)
    assert page.angle_seg.current() == "deg"
    assert page.window_combo.currentData() == 30
    assert page.host_spin.value() == 17


def test_prefs_memory_mode_and_unknown_key() -> None:
    p = Prefs(None)
    assert {k: p.get(k) for k in PREF_DEFAULTS} == PREF_DEFAULTS
    seen: list[tuple] = []
    p.changed.connect(lambda k, v: seen.append((k, v)))
    p.set("angle_unit", "deg")
    p.set("angle_unit", "deg")  # 同じ値は通知しない
    assert p.get("angle_unit") == "deg" and seen == [("angle_unit", "deg")]
    with pytest.raises(KeyError):
        p.set("nope", 1)


def test_host_id_spin_hex(qtbot) -> None:
    spin = HostIdSpin()
    qtbot.addWidget(spin)
    spin.setValue(0xFD)
    assert spin.text() == "0xFD"
    assert spin.valueFromText("0x0a") == 10 and spin.valueFromText("FF") == 255
    assert spin.maximum() == 255


def test_reset_layout_clears_saved_layout(ini_window, monkeypatch) -> None:
    win, ini = ini_window
    s = QSettings(str(ini), QSettings.Format.IniFormat)
    s.setValue("layout/operate/splitter", "x")
    s.setValue("shell/geometry", b"x")
    s.sync()
    page = win.page("settings")
    page.angle_seg.selected.emit("deg")
    toasts: list[str] = []
    monkeypatch.setattr(win, "toast", lambda t, k="success": toasts.append(t))
    resized: list[bool] = []
    monkeypatch.setattr(win, "reset_layout", lambda: resized.append(True))

    monkeypatch.setattr(page, "_confirm_reset", lambda: False)  # キャンセルなら何も消さない
    assert page.reset_layout() is False
    assert QSettings(str(ini), QSettings.Format.IniFormat).contains("shell/geometry")

    monkeypatch.setattr(page, "_confirm_reset", lambda: True)
    assert page.reset_layout() is True
    after = QSettings(str(ini), QSettings.Format.IniFormat)
    assert not after.contains("shell/geometry") and not after.contains("layout/operate/splitter")
    assert after.value("prefs/angle_unit") == "deg"  # ほかの設定は残る
    assert resized == [True] and toasts == [tr("settings.layout_done")]


def test_reset_layout_confirm_dialog(window) -> None:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    page = window.page("settings")

    def tick() -> None:
        dlg = QApplication.activeModalWidget()
        if dlg is None:
            QTimer.singleShot(20, tick)
        else:
            assert dlg.cancel_button.isDefault()  # 既定はキャンセル
            assert dlg.confirm_button.text() == tr("settings.layout_confirm_ok")
            dlg.cancel_button.click()

    QTimer.singleShot(20, tick)
    assert page.reset_layout() is False


def test_language_choice_persists_and_is_marked_for_restart(ini_window, qtbot) -> None:
    win, ini = ini_window
    page = win.page("settings")
    assert page.prefs.get("language") == "ja" and not page.prefs.has("language")
    page.language_combo.setCurrentIndex(1)  # English
    page.language_combo.activated.emit(1)  # 選択と同じ経路
    assert page.prefs.get("language") == "en"
    assert QSettings(str(ini), QSettings.Format.IniFormat).value("prefs/language") == "en"
    assert page.language_row.tags[0].isVisible()  # 実行中は ja のまま → 再起動で反映
    assert page.language_row.tags[0].text() == tr("settings.plugin_restart_tag")
    assert tr("nav.settings") == "設定"  # 実行中の言語は変わらない
    # 新しいウィンドウ(= 次回の起動)でも選択が残る。ja に戻せばタグは消える
    win2 = make_window(qtbot, ini)
    page2 = win2.page("settings")
    assert page2.language_combo.currentData() == "en"
    page2.set_language("ja")
    assert page2.prefs.get("language") == "ja" and page2.language_row.tags[0].isHidden()
    page2.set_language("fr")  # 非対応は無視
    assert page2.prefs.get("language") == "ja"
    win2.close()


def test_settings_page_in_english(qtbot, tmp_path, english) -> None:
    win = make_window(qtbot, tmp_path / "ui_state.ini")
    page = win.page("settings")
    assert page.heading.text() == "Settings"
    assert page.language_row.title_label.text() == "Language"
    assert page.language_combo.currentData() == "en"  # 保存値が無いときは実行中の言語を見せる
    assert page.language_row.tags[0].isHidden()
    page.set_language("ja")  # 実行中(en)と違う言語を選ぶ → 再起動で反映
    assert page.language_row.tags[0].isVisible()
    win.close()
