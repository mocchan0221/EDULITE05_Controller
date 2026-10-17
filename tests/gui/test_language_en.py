"""英語 UI のテスト(offscreen): 起動・最小サイズ・各ページに日本語や生のキーが残らないこと。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QWidget

from edulite05.app import i18n
from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.i18n import tr
from edulite05.app.main_window import MIN_SIZE, MainWindow
from edulite05.app.plugin_host import create_manager
from edulite05.app.ui.style import theme_manager

_JA = re.compile(r"[぀-ヿ一-鿿]")
_KEY = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z0-9_]+)+$")
ROOT = Path(__file__).resolve().parents[2]
PAGES = ("operate", "params", "diagnostics", "maintenance", "settings")


def make_window(qtbot, *, plugins=None) -> MainWindow:
    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0)
    win = MainWindow(bridge, settings_path=None, enable_3d=False, plugins=plugins)
    qtbot.addWidget(win)
    win.show()
    bridge.start()
    qtbot.waitExposed(win)
    return win


@pytest.fixture
def window_en(qtbot, english):
    win = make_window(qtbot)
    yield win
    win.close()


def test_shell_structure_in_english(qtbot, english) -> None:
    """ナビ・ヘッダー・最小サイズ(≤ 1280×720)が英語でも日本語と同じに成り立つ。

    ここの最小サイズは接続前(起動直後)の値。接続後・読み値が最大幅のときの最小サイズは
    `test_mock_window.py::test_minimum_size_after_connect_fits_1280x720`(ja / en)が測る。
    起動直後の値が接続の速さで揺れないよう、Bridge は後から start する。
    """
    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0)
    win = MainWindow(bridge, settings_path=None, enable_3d=False)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    try:
        assert win.minimumSize() == MIN_SIZE
        assert win.minimumSizeHint().width() <= MIN_SIZE.width()
        assert win.minimumSizeHint().height() <= MIN_SIZE.height()
        for bar in win.banners._bars.values():  # どのバナーが出ても 1 行に収まる
            assert bar.minimumSizeHint().width() <= MIN_SIZE.width()
        bridge.start()
        qtbot.waitUntil(lambda: set(win.header.chips()) == {1, 2}, timeout=5000)
        assert win.header.app_name.text() == "EL05 Controller"
        assert win.header.estop_btn._text_label.text() == "E-STOP"
        assert win.header.estop_btn.height() == 40 and win.header.estop_btn.width() >= 140
        assert [win.nav.item(i)._label for i in win.nav.item_ids()] == [
            "Operate", "Parameters", "Diagnosis", "Service", "Settings"
        ]
        assert win.kind_label.text() == "Simulator"
    finally:
        win.close()


def _texts(root: QWidget) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for w in [root, *root.findChildren(QWidget)]:
        if not w.isVisible():  # 表示中のものだけ(別のページ・畳まれた区画は対象外)
            continue
        if isinstance(w, QLabel | QAbstractButton):
            out.append((w.objectName() or type(w).__name__, w.text()))
        out.append((type(w).__name__ + ".tip", w.toolTip()))
    return [(n, t) for n, t in out if t]


def _problems(win: MainWindow) -> list[str]:
    bad: list[str] = []
    for name, text in _texts(win):
        # 言語名の「日本語」は訳さない(各言語の自称)。「・」は箇条書きの記号(コード側の定数)
        if _JA.search(text.replace("日本語", "").replace("・", "")):
            bad.append(f"JA {name}: {text!r}")
        for line in text.splitlines():
            if _KEY.match(line.strip()):
                bad.append(f"KEY {name}: {text!r}")
    return bad


@pytest.mark.parametrize("page", PAGES)
def test_no_japanese_or_raw_keys_on_pages(window_en, qtbot, page) -> None:
    window_en.navigate(page)
    qtbot.wait(50)
    assert _problems(window_en) == []


def test_diagnostics_subviews_in_english(window_en, qtbot) -> None:
    for view in ("faults", "log", "frames", "stats"):
        window_en.navigate("diagnostics", view)
        qtbot.wait(50)
    assert _problems(window_en) == []


def test_plugin_pages_in_english(qtbot, english) -> None:
    mgr = create_manager(None, project_root=ROOT)
    win = make_window(qtbot, plugins=mgr)
    try:
        ids = [p.id for p in mgr.ui.pages()]
        assert "scripting" in ids and "example_triangle" in ids
        names = {r.id: tr(r.name) for r in mgr.records}
        assert names["scripting"] == "Script"
        assert names["example_triangle"] == "Triangle test signal (sample)"
        # スクリプトはコンソール側だけ見る。シーケンスの手順名・検証メッセージは core が日本語で
        # 作る(core/sequence/schema.py)ため英語化していない(既知の残り)
        win.page("scripting").show_view("console")
        for pid in ("scripting", "example_triangle", "settings"):
            win.navigate(pid)
            qtbot.wait(50)
            assert _problems(win) == [], pid
        # 運転ページのモード選択にも訳が出る
        assert tr("三角波") == "Triangle"
    finally:
        win.close()
        mgr.shutdown()


def test_app_starts_in_english_with_lang_flag(qtbot, monkeypatch) -> None:
    """``--lang en`` で起動すると、カタログも画面も英語になる(保存値・環境変数より優先)。"""
    from edulite05.app.main import main

    monkeypatch.setenv("EDULITE05_AUTOCLOSE", "1")
    monkeypatch.setenv("EDULITE05_NO_STATE", "1")
    monkeypatch.setenv(i18n.ENV_VAR, "ja")
    try:
        assert main(["--demo", "--no-demo", "--no-3d", "--lang", "en"]) == 0
        assert i18n.current_language() == "en"
        wins = [w for w in QApplication.topLevelWidgets() if isinstance(w, MainWindow)]
        assert wins and wins[-1].windowTitle() == tr("app.title")
        assert wins[-1].nav.item("operate")._label == "Operate"
        assert wins[-1].header.estop_btn._text_label.text() == "E-STOP"
    finally:
        for w in QApplication.topLevelWidgets():
            if isinstance(w, MainWindow):
                w.close()
        i18n.load_catalog("ja")


def test_app_starts_in_english_from_saved_setting(qtbot, monkeypatch, tmp_path) -> None:
    """設定ページで保存した言語(prefs/language)は、次回の起動で使われる。"""
    from PySide6.QtCore import QSettings

    from edulite05.app.main import saved_language

    ini = tmp_path / "ui_state.ini"
    s = QSettings(str(ini), QSettings.Format.IniFormat)
    s.setValue("prefs/language", "en")
    s.sync()
    monkeypatch.delenv(i18n.ENV_VAR, raising=False)
    assert i18n.resolve_language(None, saved_language(ini)) == "en"
    monkeypatch.setenv(i18n.ENV_VAR, "ja")
    assert i18n.resolve_language(None, saved_language(ini)) == "ja"


def test_estop_banner_in_english(window_en, qtbot) -> None:
    window_en.estop()
    qtbot.wait(50)
    assert _problems(window_en) == []
    assert tr("banner.estop_title") == "E-STOP active"
