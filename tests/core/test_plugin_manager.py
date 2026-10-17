"""PluginManager(Qt なし): 読込の順序・失敗の隔離・有効/無効の保存・取消(architecture.md §4.8)。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from edulite05.core.control.controllers import CONTROLLERS
from edulite05.core.control.params import PARAMS_BY_CONTROLLER
from edulite05.plugin_api import (
    JsonFileStateStore,
    MemoryStateStore,
    Plugin,
    PluginContext,
    PluginManager,
    PluginStatus,
    UiRegistry,
)
from edulite05.plugin_api import manager as manager_mod


def make_dropin(root: Path, name: str, body: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "plugin.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return d


def mgr_for(tmp_path: Path, **kw) -> PluginManager:
    kw.setdefault("builtin", [])
    kw.setdefault("entry_points", False)
    return PluginManager(project_root=tmp_path, **kw)


GOOD = """
    from edulite05.plugin_api import Plugin

    class P(Plugin):
        id = "{id}"; name = "{id} plugin"; version = "1.2.3"; requires_gui = {gui}
        def activate(self, ctx):
            ctx.log.info("hello")
            if ctx.ui is not None:
                ctx.ui.add_page("{id}", "{id}", "gauge", lambda b, s: None, order={order})
    PLUGIN = P()
"""


def good(id_: str, order: int = 50, gui: bool = True) -> str:
    return textwrap.dedent(GOOD.format(id=id_, order=order, gui=gui))


# ---------------------------------------------------------------- 組込み
def test_builtin_loaded_first() -> None:
    m = PluginManager(dropin=False, entry_points=False)
    m.load_all()
    assert [r.id for r in m.records] == [
        "edulite05.builtin.pages", "edulite05.builtin.operate_modes",
    ]
    assert all(r.builtin and r.active for r in m.records)
    assert m.ui is not None and len(m.ui.pages()) == 5
    m.shutdown()


def test_builtin_cannot_be_disabled() -> None:
    m = PluginManager(dropin=False, entry_points=False)
    m.load_all()
    with pytest.raises(ValueError):
        m.set_enabled("edulite05.builtin.pages", False)
    assert m.is_enabled("edulite05.builtin.pages")
    m.shutdown()


# ---------------------------------------------------------------- 順序
def test_dropin_order_is_builtin_then_by_name(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    make_dropin(root, "zz_last", good("zz_last"))
    make_dropin(root, "aa_first", good("aa_first"))
    make_dropin(root, "_ignored", good("ignored"))  # _ で始まるディレクトリは読まない
    (root / "no_plugin_py").mkdir()  # plugin.py の無いディレクトリは無視
    m = mgr_for(tmp_path, builtin=None)
    m.load_all()
    assert [r.id for r in m.records] == [
        "edulite05.builtin.pages", "edulite05.builtin.operate_modes", "aa_first", "zz_last",
    ]
    assert m.record("aa_first").source == "dropin" and m.record("aa_first").version == "1.2.3"
    m.shutdown()


def test_page_order_and_ties(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    make_dropin(root, "p1", good("p1", order=70))
    make_dropin(root, "p2", good("p2", order=25))
    make_dropin(root, "p3", good("p3", order=70))
    m = mgr_for(tmp_path, builtin=None)
    m.load_all()
    assert [p.id for p in m.ui.pages()] == [
        "operate", "params", "p2", "diagnostics", "maintenance", "p1", "p3", "settings",
    ]
    m.shutdown()


# ---------------------------------------------------------------- 失敗の隔離
def test_failure_isolation(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    root = tmp_path / "plugins"
    make_dropin(root, "a_ok", good("a_ok"))
    make_dropin(root, "b_import_error", "raise RuntimeError('import 失敗')\n")
    make_dropin(root, "c_syntax", "def (:\n")
    make_dropin(root, "d_no_plugin", "x = 1\n")
    make_dropin(root, "e_activate_error", """
        from edulite05.plugin_api import Plugin
        class P(Plugin):
            id = "e_activate_error"
            def activate(self, ctx):
                ctx.ui.add_page("half", "half", "gauge", lambda b, s: None)  # 登録してから失敗
                raise ValueError("activate 失敗")
    """)
    make_dropin(root, "f_bad_id", """
        from edulite05.plugin_api import Plugin
        class P(Plugin):
            id = "bad id!"
            def activate(self, ctx): ...
        PLUGIN = P()
    """)
    make_dropin(root, "g_dup", good("a_ok"))  # id 重複
    make_dropin(root, "h_ok", good("h_ok"))
    m = mgr_for(tmp_path)
    with caplog.at_level("ERROR", logger="edulite05.plugins"):
        m.load_all()
    st = {r.id: r for r in m.records}
    assert st["a_ok"].status is PluginStatus.ACTIVE and st["h_ok"].status is PluginStatus.ACTIVE
    assert st["dropin:b_import_error"].status is PluginStatus.FAILED
    assert "import 失敗" in st["dropin:b_import_error"].error
    assert "Traceback" in st["dropin:b_import_error"].detail
    assert st["dropin:c_syntax"].status is PluginStatus.FAILED
    assert st["dropin:d_no_plugin"].status is PluginStatus.FAILED
    assert st["e_activate_error"].status is PluginStatus.FAILED
    assert st["e_activate_error"].error == "ValueError: activate 失敗"
    assert st["dropin:f_bad_id"].status is PluginStatus.FAILED
    assert st["dropin:g_dup"].status is PluginStatus.FAILED and "重複" in st["dropin:g_dup"].error
    # 失敗の記録がログに出る
    assert "e_activate_error" in caplog.text and "activate 失敗" in caplog.text
    # 失敗したプラグインの登録(ページ)は取り消され、他は生きている
    ids = [p.id for p in m.ui.pages()]
    assert "half" not in ids and "a_ok" in ids and "h_ok" in ids
    assert [r.id for r in m.failed()] == [
        "dropin:b_import_error", "dropin:c_syntax", "dropin:d_no_plugin", "e_activate_error",
        "dropin:f_bad_id", "dropin:g_dup",
    ]
    m.shutdown()


def test_failed_activation_rolls_back_core_registrations(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    make_dropin(root, "bad", """
        from edulite05.core.control.controller import Controller
        from edulite05.core.control.controllers import CONTROLLERS
        from edulite05.core.control.params import VelocityParams
        from edulite05.core.protocol.types import RunMode
        from edulite05.plugin_api import Plugin

        class Dummy(Controller):
            name = "dummy_rollback"; label = "dummy"; run_mode = RunMode.VELOCITY
            Params = VelocityParams
            def step(self, t, dt, states): return {}
            def on_stop(self, states): return {}

        class P(Plugin):
            id = "bad"
            def activate(self, ctx):
                ctx.core.register_controller(Dummy)
                ctx.ui.add_mode(Dummy)
                assert "dummy_rollback" in CONTROLLERS
                raise RuntimeError("後で失敗")
        PLUGIN = P()
    """)
    m = mgr_for(tmp_path)
    m.load_all()
    assert m.record("bad").status is PluginStatus.FAILED
    assert "dummy_rollback" not in CONTROLLERS and "dummy_rollback" not in PARAMS_BY_CONTROLLER
    assert m.ui.modes() == []


def test_factory_failure_reported_isolates_plugin(tmp_path: Path) -> None:
    make_dropin(tmp_path / "plugins", "ok", good("ok"))
    m = mgr_for(tmp_path)
    m.load_all()
    m.report_failure("ok", RuntimeError("ページ生成の失敗"), "ページの生成")
    r = m.record("ok")
    assert r.status is PluginStatus.FAILED and "ページ生成の失敗" in r.error
    assert "ok" not in [p.id for p in m.ui.pages()]


# ---------------------------------------------------------------- 有効/無効の保存
def test_enable_disable_persists_and_applies_on_next_start(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    marker = tmp_path / "ran.txt"
    make_dropin(root, "x", good("x") + f"\nopen(r'{marker}', 'a').write('x')\n")
    state_path = tmp_path / "state.json"
    m = mgr_for(tmp_path, state=JsonFileStateStore(state_path))
    m.load_all()
    assert m.record("x").active and marker.exists()
    m.set_enabled("x", False)
    assert m.record("x").active  # 実行中は止めない(次回から)
    assert m.pending_restart() and not m.is_enabled("x")
    m.shutdown()
    # 次回の起動: id で無効 → activate しない
    m2 = mgr_for(tmp_path, state=JsonFileStateStore(state_path))
    m2.load_all()
    assert m2.record("x").status is PluginStatus.DISABLED and not m2.pending_restart()
    assert "x" not in [p.id for p in m2.ui.pages()]
    m2.set_enabled("x", True)
    m2.shutdown()
    m3 = mgr_for(tmp_path, state=JsonFileStateStore(state_path))
    m3.load_all()
    assert m3.record("x").active
    m3.shutdown()


def test_disabled_dropin_by_dirname_is_not_imported(tmp_path: Path) -> None:
    marker = tmp_path / "imported.txt"
    make_dropin(
        tmp_path / "plugins", "never", f"open(r'{marker}', 'w').write('x')\n" + good("never")
    )
    m = mgr_for(tmp_path, state=MemoryStateStore({"disabled": ["dropin:never"]}))
    m.load_all()
    assert m.record("dropin:never").status is PluginStatus.DISABLED
    assert not marker.exists()  # 無効なら import(= コードの実行)もしない


# ---------------------------------------------------------------- requires_gui
def test_requires_gui_skipped_without_gui(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    make_dropin(root, "gui_only", good("gui_only", gui=True))
    make_dropin(root, "core_only", """
        from edulite05.plugin_api import Plugin
        class P(Plugin):
            id = "core_only"; requires_gui = False
            def activate(self, ctx):
                assert ctx.ui is None
                ctx.core.register_channel("core_only_ch", "V")
        PLUGIN = P()
    """)
    m = mgr_for(tmp_path, gui=False)
    m.load_all()
    assert m.ui is None
    assert m.record("gui_only").status is PluginStatus.SKIPPED
    assert m.record("core_only").active
    from edulite05.core.telemetry import extra_channels

    assert any(c[0] == "core_only_ch" for c in extra_channels())
    m.shutdown()
    assert not any(c[0] == "core_only_ch" for c in extra_channels())


# ---------------------------------------------------------------- entry points
def test_entry_points_are_loaded_after_dropins(tmp_path: Path, monkeypatch) -> None:
    make_dropin(tmp_path / "plugins", "d1", good("d1"))

    class EP:
        def __init__(self, name: str, obj) -> None:
            self.name, self.value, self._obj = name, f"pkg:{name}", obj

        def load(self):
            if isinstance(self._obj, Exception):
                raise self._obj
            return self._obj

    class FromEp(Plugin):
        id = "from_ep"
        name = "EP"

        def activate(self, ctx: PluginContext) -> None:
            ctx.ui.add_more_action("ep", lambda: None)

    eps = [EP("zz", FromEp), EP("bad", ImportError("no module x"))]
    monkeypatch.setattr(
        manager_mod.metadata, "entry_points", lambda group=None: eps if group else {}
    )
    m = PluginManager(project_root=tmp_path, builtin=[], entry_points=True)
    m.load_all()
    assert [r.id for r in m.records] == ["d1", "entry_point:bad", "from_ep"]
    assert m.record("from_ep").source == "entry_point" and m.record("from_ep").active
    assert m.record("entry_point:bad").status is PluginStatus.FAILED
    assert [a.text for a in m.ui.more_actions()] == ["ep"]
    m.shutdown()


# ---------------------------------------------------------------- 終了・取消・検証
def test_shutdown_deactivates_in_reverse_and_rolls_back(tmp_path: Path) -> None:
    calls: list[str] = []

    def plug(pid: str) -> Plugin:
        class P(Plugin):
            id = pid

            def activate(self, ctx: PluginContext) -> None:
                ctx.ui.add_settings_section(pid, lambda b, s: None)
                calls.append(f"on {pid}")

            def deactivate(self) -> None:
                calls.append(f"off {pid}")

        return P()

    m = PluginManager(
        project_root=tmp_path, dropin=False, entry_points=False, builtin=[plug("a"), plug("b")]
    )
    m.load_all()
    assert len(m.ui.settings_sections()) == 2
    m.shutdown()
    m.shutdown()  # 2 回呼んでも安全
    assert calls == ["on a", "on b", "off b", "off a"]
    assert m.ui.settings_sections() == []


def test_core_registry_refuses_overriding_builtin_controller(tmp_path: Path) -> None:
    class Evil(Plugin):
        id = "evil"

        def activate(self, ctx: PluginContext) -> None:
            from edulite05.core.control.controllers.tests import SineTest

            class Fake(SineTest):
                name = "sine"  # 組込みの名前を奪う

            ctx.core.register_controller(Fake)

    m = PluginManager(project_root=tmp_path, dropin=False, entry_points=False, builtin=[Evil()])
    m.load_all()
    assert m.record("evil").status is PluginStatus.FAILED
    assert CONTROLLERS["sine"].__name__ == "SineTest"


def test_add_mode_requires_registered_controller() -> None:
    from edulite05.core.control.controllers.tests import SineTest

    class Unreg(SineTest):
        name = "unregistered_x"

    ui = UiRegistry()
    with pytest.raises(ValueError, match="未登録"):
        ui.add_mode(Unreg)


def test_ui_registry_validation() -> None:
    ui = UiRegistry()
    ui.add_page("p", "P", "gauge", lambda b, s: None)
    with pytest.raises(ValueError):
        ui.add_page("p", "P2", "gauge", lambda b, s: None)
    with pytest.raises(ValueError):
        ui.add_monitor_view("graph", "x", lambda b: None)
    with pytest.raises(ValueError):
        ui.add_maintenance_item("nope", "t", "d", (), lambda p, m: None)
    # プラグインはナビ最下部(bottom)・本体グループ(main)を使えない: 組込み専用
    scoped = ui.scoped("plug")
    scoped.add_page("q", "Q", "gauge", lambda b, s: None, bottom=True)
    assert next(p for p in ui.pages() if p.id == "q").bottom is False


def test_plugin_settings_roundtrip(tmp_path: Path) -> None:
    class S(Plugin):
        id = "s"

        def activate(self, ctx: PluginContext) -> None:
            n = ctx.settings.get("count", 0)
            ctx.settings.set("count", n + 1)

    state = JsonFileStateStore(tmp_path / "s.json")
    for expected in (1, 2):
        m = PluginManager(
            project_root=tmp_path, dropin=False, entry_points=False, builtin=[S()], state=state
        )
        m.load_all()
        assert state.get("settings/s/count") == expected
        m.shutdown()
