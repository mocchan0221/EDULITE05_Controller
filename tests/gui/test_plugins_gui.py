"""プラグイン基盤の GUI テスト(offscreen): 組込み登録の等価性・サンプル・設定ページ。

組込みのページ・運転モードは、プラグイン API 経由の登録に置き換えた(以前は `PAGES` と modes.py の
定数)。見た目(順序・ラベル・アイコン)を変えないことを、置換前の値と突き合わせて確認する。
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

from PySide6.QtCore import QSettings

from edulite05.app.core_bridge import CoreBridge
from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.i18n import tr
from edulite05.app.main_window import MainWindow
from edulite05.app.pages.operate import modes as md
from edulite05.app.pages.settings import SettingsPage
from edulite05.app.plugin_host import QSettingsStateStore, create_manager
from edulite05.app.ui.style import theme_manager
from edulite05.core.control.controllers import CONTROLLERS
from edulite05.core.control.params import PARAMS_BY_CONTROLLER
from edulite05.plugin_api import PluginManager, PluginStatus

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- 置換前の値(等価性の基準)
OLD_PAGES = [  # (id, title_key, icon, module, cls, order, bottom)
    ("operate", "nav.operate", "gauge", "edulite05.app.pages.operate", "OperatePage", 10, False),
    ("params", "nav.params", "sliders-horizontal", "edulite05.app.pages.params", "ParamsPage",
     20, False),
    ("diagnostics", "nav.diagnostics", "stethoscope", "edulite05.app.pages.diagnostics",
     "DiagnosticsPage", 30, False),
    ("maintenance", "nav.maintenance", "wrench", "edulite05.app.pages.maintenance",
     "MaintenancePage", 40, False),
    ("settings", "nav.settings", "settings", "edulite05.app.pages.settings", "SettingsPage",
     90, True),
]
OLD_MAIN = ("jog", "velocity", "position", "mit")
OLD_OTHER = ("current", "test", "link")
OLD_TARGET = {
    "jog": ("speed",), "velocity": ("velocity",), "csp": ("position",), "pp": ("position",),
    "mit": ("position", "kp", "kd"), "current": ("iq",),
    "sine": ("target", "amplitude", "frequency", "offset"),
    "step": ("target", "low", "high", "period"),
}
OLD_LIMIT = {
    "jog": ("current_limit", "accel"), "velocity": ("current_limit", "accel"),
    "csp": ("speed_limit",), "pp": ("velocity", "accel"),
    "mit": ("velocity", "torque_ff", "friction_comp"),
    "current": (), "sine": (), "step": (),
}
OLD_OTHER_SHORT = {  # 選択中に「その他 ▾」の位置へ出る名前(旧 inspector.OTHER_SHORT)
    "current": "operate.mode.current_short", "test": "operate.mode.test",
    "link": "operate.mode.link",
}


def test_builtin_pages_registration_equivalent() -> None:
    m = PluginManager(dropin=False, entry_points=False)
    m.load_all()
    got = [
        (p.id, p.title, p.icon, p.factory.module, p.factory.cls, p.order, p.bottom)
        for p in m.ui.pages()
    ]
    assert got == OLD_PAGES
    m.shutdown()


def test_builtin_modes_registration_equivalent() -> None:
    m = PluginManager(dropin=False, entry_points=False)
    m.load_all()
    md.install(m.ui.modes())
    try:
        assert md.MAIN_MODES == OLD_MAIN and md.OTHER_MODES == OLD_OTHER
        assert md.ALL_MODES == (*OLD_MAIN, *OLD_OTHER)
        assert md.TARGET_FIELDS == OLD_TARGET and md.LIMIT_FIELDS == OLD_LIMIT
        assert md.LABEL_OVERRIDES == {("mit", "velocity"): "operate.target.mit_velocity"}
        # 選択肢の名前・説明・「その他 ▾」のメニュー(2 台未満では 2台連動を出さない)
        assert [md.MODE_SPECS[k].title for k in OLD_MAIN] == [f"operate.mode.{k}" for k in OLD_MAIN]
        assert md.other_menu(1) == [("その他", [("current", "operate.mode.current"),
                                                ("test", "operate.mode.test")])]
        assert md.other_menu(2) == [("その他", [("current", "operate.mode.current"),
                                                ("test", "operate.mode.test"),
                                                ("link", "operate.mode.link")])]
        for k, key in OLD_OTHER_SHORT.items():
            assert md.short_title(k) == key
        for k in (*OLD_MAIN, *OLD_OTHER):
            mm = md.MotorModel(mode=k)
            if k == "position":
                continue
            assert md.mode_desc_key(mm) == f"operate.mode_desc.{k}"
            assert md.mode_info_key(mm) == f"operate.mode_info.{k}"
    finally:
        m.shutdown()
        _reinstall_builtin()


def _reinstall_builtin() -> None:
    from edulite05.builtin.operate_modes import builtin_mode_specs

    md.install(builtin_mode_specs())


# ---------------------------------------------------------------- シェル
def _window(qtbot, plugins: PluginManager | None, *, ini: Path | None = None, bridge=None):
    theme_manager().apply("dark")
    bridge = bridge or DemoBridge(kind="simulator", demo_motion=False, seed=0)
    win = MainWindow(bridge, settings_path=ini, enable_3d=False, plugins=plugins)
    qtbot.addWidget(win)
    win.show()
    bridge.start()
    qtbot.waitExposed(win)
    return win


def test_shell_nav_unchanged_with_builtin_only(qtbot) -> None:
    win = _window(qtbot, None)
    try:
        assert win.nav.item_ids() == ["operate", "params", "diagnostics", "maintenance", "settings"]
        assert len(win.page_shortcuts) == 5
        assert md.MAIN_MODES == OLD_MAIN
    finally:
        win.close()


def _example_manager(tmp_path: Path, ini: Path | None = None) -> PluginManager:
    shutil.copytree(
        ROOT / "plugins" / "example_triangle", tmp_path / "plugins" / "example_triangle",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return create_manager(ini, project_root=tmp_path)


def test_example_plugin_page_and_mode(qtbot, tmp_path: Path) -> None:
    mgr = _example_manager(tmp_path)
    win = _window(qtbot, mgr)
    try:
        rec = mgr.record("example_triangle")
        assert rec is not None and rec.status is PluginStatus.ACTIVE
        # ナビ: 保守の下・設定の上。Ctrl+N も増える
        assert win.nav.item_ids() == [
            "operate", "params", "diagnostics", "maintenance", "example_triangle", "settings",
        ]
        assert len(win.page_shortcuts) == 6
        win.navigate("example_triangle")
        page = win.page("example_triangle")
        assert page.heading.text() == "三角波の試験信号"
        # 運転ページ: 「その他 ▾」に三角波が増え、フォームが自動生成される
        assert "triangle" in md.OTHER_MODES
        assert md.TARGET_FIELDS["triangle"] == ("target", "amplitude", "frequency", "offset")
        assert "triangle" in CONTROLLERS and "triangle" in PARAMS_BY_CONTROLLER
        win.navigate("operate")
        insp = win.page("operate").inspector
        insp.set_motor(1)
        assert "triangle" in insp.forms
        insp.pick_other("triangle")
        assert insp.mode() == "triangle" and insp.controller_name() == "triangle"
        assert insp.mode_seg.button("other").text() == "三角波 ▾"
        assert insp.desc_label.text() == "目標の量に三角波を出します"
        assert insp._target_boxes["triangle"].isVisibleTo(insp)
        assert not insp._target_boxes["velocity"].isVisibleTo(insp)
        assert [v for v in md.other_menu(2)[0][1] if v[0] == "triangle"] == [("triangle", "三角波")]
    finally:
        win.close()
    # 閉じるとコア側の登録も取り消される(他のテストへ残さない)
    assert "triangle" not in CONTROLLERS and "triangle" not in PARAMS_BY_CONTROLLER
    _reinstall_builtin()


def test_example_plugin_runs_through_motorservice_with_sim(qtbot, tmp_path: Path) -> None:
    """モード選択 → 有効化 → 開始 → 三角波で動く(CoreBridge + SimTransport)。LOW の上限内。"""
    mgr = _example_manager(tmp_path)
    bridge = CoreBridge(kind="simulator", sim_motors=2)
    win = _window(qtbot, mgr, bridge=bridge)
    try:
        qtbot.waitUntil(lambda: len(bridge.motors()) == 2, timeout=8000)
        qtbot.waitUntil(
            lambda: bridge.states().get(1) is not None
            and bridge.states()[1].ui_state == "disabled", timeout=8000,
        )
        insp = win.page("operate").inspector
        insp.set_motor(1)
        insp.pick_other("triangle")
        form = insp.forms["triangle"]
        form.set_value("amplitude", 20.0)  # LOW の上限(5 rad/s)を超える要求
        form.set_value("frequency", 1.0)
        insp._do_enable()
        qtbot.waitUntil(lambda: bridge.states()[1].ui_state == "enabled", timeout=8000)
        insp._do_start()
        qtbot.waitUntil(lambda: bridge.states()[1].ui_state == "running", timeout=8000)
        assert bridge.states()[1].controller == "triangle"
        vs: list[float] = []
        for _ in range(60):
            qtbot.wait(25)
            vs.append(bridge.states()[1].velocity)
        assert max(vs) > 1.0 and min(vs) < -1.0  # 三角波で正負に動く
        assert max(abs(v) for v in vs) <= 5.5  # SafetyGuard(LOW: 5 rad/s)を超えない
        insp._do_stop()
        qtbot.waitUntil(lambda: bridge.states()[1].ui_state == "enabled", timeout=8000)
        bridge.disable([1])
    finally:
        win.close()
    _reinstall_builtin()


# ---------------------------------------------------------------- 設定ページ
GOOD = '''
    from edulite05.plugin_api import Plugin

    class P(Plugin):
        id = "demo_ok"; name = "動作するプラグイン"; version = "2.0"
        description = "テスト用"
        def activate(self, ctx):
            ctx.ui.add_settings_section("追加の設定", section)
    def section(bridge, shell):
        from PySide6.QtWidgets import QLabel
        return QLabel("section body")
    PLUGIN = P()
'''
BAD = '''
    raise RuntimeError("読み込みに失敗するプラグイン")
'''


def _make(tmp_path: Path, name: str, body: str) -> None:
    d = tmp_path / "plugins" / name
    d.mkdir(parents=True)
    (d / "plugin.py").write_text(textwrap.dedent(body), encoding="utf-8")


def test_settings_plugins_list_and_errors(qtbot, tmp_path: Path) -> None:
    _make(tmp_path, "demo_ok", GOOD)
    _make(tmp_path, "demo_bad", BAD)
    mgr = create_manager(None, project_root=tmp_path)
    win = _window(qtbot, mgr)
    try:
        win.navigate("settings")
        page = win.page("settings")
        assert isinstance(page, SettingsPage)
        # 一覧: 組込み(スイッチ無し・常に有効)+ 追加 + 読込失敗
        assert set(page.plugin_rows) == {
            "edulite05.builtin.pages", "edulite05.builtin.operate_modes", "demo_ok",
            "dropin:demo_bad",
        }
        assert set(page.plugin_switches) == {"demo_ok", "dropin:demo_bad"}
        ok_row = page.plugin_rows["demo_ok"]
        assert ok_row.title_label.text() == "動作するプラグイン"
        assert tr("settings.plugin_dropin") in ok_row.tag_texts()
        assert page.plugin_switches["demo_ok"].isChecked()
        builtin_row = page.plugin_rows["edulite05.builtin.pages"]
        assert tr("settings.plugin_builtin") in builtin_row.tag_texts()
        # 読込エラー: 行のタグ・理由 + 一覧の上の注意バナー。アプリは動いている
        bad = page.plugin_rows["dropin:demo_bad"]
        assert tr("settings.plugin_failed_tag") in bad.tag_texts()
        assert "読み込みに失敗するプラグイン" in bad.description_label.text()
        assert len(page.plugin_alerts) == 1
        # 再起動で反映する旨を画面に書く
        assert page.plugins_note.text() == tr("settings.plugins_note")
        # プラグインの設定項目(add_settings_section)が表示される
        from PySide6.QtWidgets import QLabel

        assert any(w.text() == "section body" for w in page.findChildren(QLabel))
    finally:
        win.close()


def test_settings_enable_disable_persists(qtbot, tmp_path: Path) -> None:
    _make(tmp_path, "demo_ok", GOOD)
    ini = tmp_path / "ui_state.ini"
    mgr = create_manager(ini, project_root=tmp_path)
    win = _window(qtbot, mgr, ini=ini)
    try:
        win.navigate("settings")
        page = win.page("settings")
        sw = page.plugin_switches["demo_ok"]
        restart_tag = page.plugin_rows["demo_ok"].tags[-1]
        assert not restart_tag.isVisibleTo(page)
        sw.click()  # 無効にする
        assert not sw.isChecked()
        assert mgr.record("demo_ok").status is PluginStatus.ACTIVE  # 実行中は止めない
        assert restart_tag.isVisibleTo(page)
        assert restart_tag.text() == tr("settings.plugin_restart_tag")
        assert mgr.pending_restart()
        # ini に保存されている
        s = QSettings(str(ini), QSettings.Format.IniFormat)
        assert "demo_ok" in str(s.value("plugins/disabled"))
        sw.click()  # 有効に戻す → 保存も戻る
        assert sw.isChecked() and not restart_tag.isVisibleTo(page)
        sw.click()
    finally:
        win.close()
    # 次回の起動: 保存した設定どおり、読み込まれるが有効化されない
    mgr2 = create_manager(ini, project_root=tmp_path)
    win2 = _window(qtbot, mgr2, ini=ini)
    try:
        assert mgr2.record("demo_ok").status is PluginStatus.DISABLED
        win2.navigate("settings")
        page2 = win2.page("settings")
        assert not page2.plugin_switches["demo_ok"].isChecked()
        assert tr("settings.plugin_off_tag") in page2.plugin_rows["demo_ok"].tag_texts()
        assert not page2.plugin_rows["demo_ok"].tags[-1].isVisibleTo(page2)
        assert not [s for s in mgr2.ui.settings_sections() if s.owner == "demo_ok"]
        page2.plugin_switches["demo_ok"].click()  # 有効に戻す
    finally:
        win2.close()
    mgr3 = create_manager(ini, project_root=tmp_path)
    mgr3.load_all()
    assert mgr3.record("demo_ok").status is PluginStatus.ACTIVE
    mgr3.shutdown()


def test_plugin_page_factory_failure_does_not_stop_app(qtbot, tmp_path: Path) -> None:
    _make(tmp_path, "boom", '''
        from edulite05.plugin_api import Plugin
        def make(bridge, shell):
            raise RuntimeError("ページの生成で失敗")
        class P(Plugin):
            id = "boom"
            def activate(self, ctx):
                ctx.ui.add_page("boom", "boom", "gauge", make)
        PLUGIN = P()
    ''')
    mgr = create_manager(None, project_root=tmp_path)
    win = _window(qtbot, mgr)
    try:
        assert "boom" not in win.nav.item_ids()
        assert mgr.record("boom").status is PluginStatus.FAILED
        win.navigate("settings")
        page = win.page("settings")
        assert "ページの生成で失敗" in page.plugin_rows["boom"].description_label.text()
    finally:
        win.close()


def test_state_store_roundtrip_in_qsettings(tmp_path: Path) -> None:
    ini = tmp_path / "s.ini"
    st = QSettingsStateStore(ini)
    assert st.get("disabled", []) == []
    st.set("disabled", ["a", "b"])
    st.set("settings/x/n", 3)
    assert QSettingsStateStore(ini).get("disabled") == ["a", "b"]
    assert QSettingsStateStore(ini).get("settings/x/n") == 3
    st.set("disabled", [])
    assert QSettingsStateStore(ini).get("disabled") == []
    mem = QSettingsStateStore(None)
    mem.set("k", 1)
    assert mem.get("k") == 1
