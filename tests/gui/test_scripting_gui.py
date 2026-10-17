"""「スクリプト」プラグイン(plugins/scripting)の GUI テスト(offscreen、CoreBridge + SimTransport)。

確認すること: ページが add_page で登録される / コンソールがワーカースレッドで実行し GUI を止めない /
出力・履歴 / SafetyGuard の注記 / 非常停止(Pause・Esc・ヘッダーのボタン)がスクリプト実行中に効く /
シーケンスの選択・実行・中断・進捗・検証エラー / プラグインを無効にするとページが消える。
"""

from __future__ import annotations

import re
import shutil
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from edulite05.app.core_bridge import CoreBridge
from edulite05.app.i18n import catalog, tr
from edulite05.app.main_window import MainWindow
from edulite05.app.plugin_host import create_manager
from edulite05.app.ui.style import theme_manager
from edulite05.core.control.controllers import CONTROLLERS
from edulite05.core.protocol.types import MotorMode
from edulite05.plugin_api import MemoryStateStore, PluginStatus

ROOT = Path(__file__).resolve().parents[2]
WAIT = 10000
M1 = 1

SHORT_SEQ = """\
name: 短い試験
description: テスト用
steps:
  - enable: velocity
  - run: {controller: velocity, params: {velocity: 1.0, accel: 50}, seconds: 0.6}
  - disable
"""
LONG_SEQ = """\
name: 長い試験
steps:
  - enable: velocity
  - run: {controller: velocity, params: {velocity: 2.0, accel: 50}, seconds: 60}
  - disable
"""
BAD_SEQ = """\
steps:
  - enable: velocity
  - run: {controller: velocity, params: {velocity: abc}, seconds: 2}
"""


def make_project(tmp_path: Path) -> Path:
    shutil.copytree(
        ROOT / "plugins" / "scripting", tmp_path / "plugins" / "scripting",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    seq = tmp_path / "sequences"
    seq.mkdir()
    shutil.copy(ROOT / "sequences" / "velocity_steps.yaml", seq / "velocity_steps.yaml")
    (seq / "short.yaml").write_text(SHORT_SEQ, encoding="utf-8")
    (seq / "long.yaml").write_text(LONG_SEQ, encoding="utf-8")
    (seq / "bad.yaml").write_text(BAD_SEQ, encoding="utf-8")
    return tmp_path


class Env:
    def __init__(self, win: MainWindow, bridge: CoreBridge, mgr, root: Path) -> None:
        self.win, self.bridge, self.mgr, self.root = win, bridge, mgr, root

    @property
    def page(self):
        return self.win.page("scripting")

    @property
    def sim(self):
        return self.bridge._conn.transport  # noqa: SLF001 - SimTransport

    def ui(self, mid: int = M1) -> str:
        s = self.bridge.states().get(mid)
        return s.ui_state if s is not None else "none"


@pytest.fixture
def env(qtbot, tmp_path):
    root = make_project(tmp_path)
    mgr = create_manager(None, project_root=root)
    theme_manager().apply("dark")
    bridge = CoreBridge(kind="simulator", sim_motors=2)
    win = MainWindow(bridge, settings_path=None, enable_3d=False, plugins=mgr)
    qtbot.addWidget(win)
    win.resize(1280, 800)
    win.show()
    bridge.start()
    qtbot.waitExposed(win)
    qtbot.waitUntil(lambda: len(bridge.motors()) == 2, timeout=WAIT)
    qtbot.waitUntil(lambda: bridge.states().get(M1) is not None
                    and bridge.states()[M1].ui_state == "disabled", timeout=WAIT)
    win.navigate("scripting")
    win.activateWindow()  # Pause のショートカット(ApplicationShortcut)はアクティブな窓が必要
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=3000)
    e = Env(win, bridge, mgr, root)
    yield e
    win.page("scripting").shutdown()
    win.close()
    bridge.shutdown()


def press(qtbot, env: Env, widget, key) -> None:
    """窓をアクティブにして部品にフォーカスを置き、キーを押す(実際のキー入力と同じ経路)。"""
    env.win.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is env.win, timeout=3000)
    widget.setFocus()
    qtbot.keyClick(widget, key)


def run_code(qtbot, env: Env, code: str, *, wait: bool = True) -> None:
    c = env.page.console
    c.input.setPlainText(code)
    assert c.submit()
    if wait:
        qtbot.waitUntil(lambda: not c.running, timeout=WAIT)
        qtbot.wait(120)  # 出力のフラッシュ(50 ms)待ち


# ---------------------------------------------------------------- 登録・構造
def test_page_registered_through_plugin_api(env: Env) -> None:
    rec = env.mgr.record("scripting")
    assert rec is not None and rec.status is PluginStatus.ACTIVE and not rec.builtin
    assert env.win.nav.item_ids() == [
        "operate", "params", "diagnostics", "maintenance", "scripting", "settings",
    ]
    assert len(env.win.page_shortcuts) == 6
    assert env.page.heading.text() == tr("script.title")
    assert env.page.current_view() == "console"
    assert env.page.view_switch.keys() == ["console", "sequence"]


def test_safety_note_is_shown(env: Env) -> None:
    note = env.page.safety_note
    assert note.isVisibleTo(env.page)
    texts = " ".join(lb.text() for lb in note.findChildren(type(env.page.heading)))
    assert "SafetyGuard" in texts and "非常停止" in texts


def test_one_accent_button_per_region(env: Env) -> None:
    c = env.page.console
    assert c.run_button.variant == "accent"
    env.page.show_view("sequence")
    s = env.page.sequence
    assert s.run_button.variant == "accent"
    # 非表示の区画は画面に出ない(強調ボタンは常に 1 つ)
    assert not c.isVisibleTo(env.page) and s.isVisibleTo(env.page)


# ---------------------------------------------------------------- コンソール
def test_console_runs_on_worker_and_shows_output(qtbot, env: Env) -> None:
    here = threading.get_ident()
    c = env.page.console
    run_code(qtbot, env, "import threading\nprint('tid', threading.get_ident())\n1 + 1")
    text = c.output_text()
    assert ">>> import threading" in text and "... print(" in text  # 入力のエコー
    m = re.search(r"tid (\d+)", text)
    assert m and int(m.group(1)) != here  # GUI スレッドではない
    assert text.rstrip().endswith("2")


def test_console_does_not_block_gui_while_running(qtbot, env: Env) -> None:
    c = env.page.console
    c.input.setPlainText("sleep(1.5)\nprint('done')")
    c.submit()
    assert c.running and c.run_button.text() == tr("script.abort")
    assert c.run_button.variant == "standard"
    ticks = []
    from PySide6.QtCore import QTimer

    t = QTimer()
    t.timeout.connect(lambda: ticks.append(1))
    t.start(20)
    qtbot.wait(400)
    t.stop()
    assert len(ticks) >= 8  # スクリプト実行中も GUI のイベントループが回る
    qtbot.waitUntil(lambda: not c.running, timeout=WAIT)
    qtbot.wait(150)
    assert "done" in c.output_text() and c.run_button.text() == tr("script.run")


def test_console_error_is_shown_in_output(qtbot, env: Env) -> None:
    run_code(qtbot, env, "1/0")
    assert "ZeroDivisionError" in env.page.console.output_text()
    run_code(qtbot, env, "'ok'")  # エラー後も続けて使える
    assert "'ok'" in env.page.console.output_text()


def test_console_namespace_exposes_service_telemetry_controllers(qtbot, env: Env) -> None:
    run_code(qtbot, env, "(type(service).__name__, type(telemetry).__name__, sorted(ids()))")
    out = env.page.console.output_text()
    assert "MotorService" in out and "TelemetryHub" in out and "[1, 127]" in out
    run_code(qtbot, env, "'velocity' in controllers and 'velocity' in params")
    assert out.count("True") + env.page.console.output_text().count("True") >= 1
    assert "velocity" in CONTROLLERS


def test_history_up_down_and_enter_keys(qtbot, env: Env) -> None:
    c = env.page.console
    for code in ("a = 1", "b = 2", "a + b"):
        run_code(qtbot, env, code)
    assert c.history() == ["a = 1", "b = 2", "a + b"]
    c.input.setFocus()
    qtbot.keyClick(c.input, Qt.Key.Key_Up)
    assert c.input.toPlainText() == "a + b"
    qtbot.keyClick(c.input, Qt.Key.Key_Up)
    assert c.input.toPlainText() == "b = 2"
    qtbot.keyClick(c.input, Qt.Key.Key_Down)
    assert c.input.toPlainText() == "a + b"
    qtbot.keyClick(c.input, Qt.Key.Key_Down)
    assert c.input.toPlainText() == ""  # 履歴の先(書きかけの下書きに戻る)
    # Shift+Enter は改行、Enter は実行
    c.input.setPlainText("x = 5")
    qtbot.keyClick(c.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert "\n" in c.input.toPlainText()
    c.input.setPlainText("a * 10")
    qtbot.keyClick(c.input, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: not c.running and c.input.toPlainText() == "", timeout=WAIT)
    qtbot.wait(150)
    assert c.output_text().rstrip().endswith("10")


def test_history_is_persisted_in_plugin_settings(qtbot, env: Env) -> None:
    run_code(qtbot, env, "z = 3")
    ctx = env.mgr.record("scripting").context
    assert ctx.settings.get("history") == ["z = 3"]


def test_clear_button(qtbot, env: Env) -> None:
    run_code(qtbot, env, "print('hello')")
    assert "hello" in env.page.console.output_text()
    env.page.console.clear_button.click()
    assert env.page.console.output_text() == ""


def test_console_drives_motor_through_service_and_safetyguard(qtbot, env: Env) -> None:
    run_code(
        qtbot, env,
        "wait(service.enable(1, RunMode.VELOCITY), 8)\n"
        "service.start(1, make('velocity', velocity=30.0, accel=100))\nsleep(0.8)",
    )
    st = env.bridge.states()[M1]
    assert st.ui_state == "running"
    assert abs(st.velocity) <= 5.5  # 30 rad/s を指令しても LOW の上限(5)
    run_code(qtbot, env, "service.stop_motion(1)\nsleep(0.3)\nwait(service.disable(1), 8)")
    qtbot.waitUntil(lambda: env.ui() == "disabled", timeout=WAIT)


# ---------------------------------------------------------------- 非常停止
START_AND_SLEEP = (
    "wait(service.enable(1, RunMode.VELOCITY), 8)\n"
    "service.start(1, make('velocity', velocity=2.0, accel=100))\nsleep(60)"
)


def _start_long(qtbot, env: Env) -> None:
    run_code(qtbot, env, START_AND_SLEEP, wait=False)
    qtbot.waitUntil(lambda: env.ui() == "running", timeout=WAIT)
    qtbot.waitUntil(lambda: abs(env.bridge.states()[M1].velocity) > 1.0, timeout=WAIT)


def _assert_estopped(qtbot, env: Env) -> None:
    c = env.page.console
    qtbot.waitUntil(lambda: env.ui() == "disabled", timeout=3000)
    assert env.bridge.estop_latched()
    qtbot.waitUntil(lambda: not c.running, timeout=3000)  # スクリプトも止まる
    qtbot.waitUntil(lambda: env.sim.motor(M1).mode is MotorMode.RESET, timeout=3000)


def test_estop_header_button_during_script(qtbot, env: Env) -> None:
    _start_long(qtbot, env)
    env.win.header.estop_btn.click()
    _assert_estopped(qtbot, env)


def test_estop_pause_key_during_script(qtbot, env: Env) -> None:
    _start_long(qtbot, env)
    press(qtbot, env, env.page.console.input, Qt.Key.Key_Pause)
    _assert_estopped(qtbot, env)


def test_estop_esc_key_in_input_during_script(qtbot, env: Env) -> None:
    _start_long(qtbot, env)
    press(qtbot, env, env.page.console.input, Qt.Key.Key_Escape)
    _assert_estopped(qtbot, env)


def test_script_after_estop_cannot_move_motor(qtbot, env: Env) -> None:
    _start_long(qtbot, env)
    env.win.estop()
    _assert_estopped(qtbot, env)
    run_code(qtbot, env, "service.enable(1, RunMode.VELOCITY)")
    assert "非常停止" in env.page.console.output_text()
    assert env.ui() == "disabled"


def test_abort_button_stops_script_and_motion(qtbot, env: Env) -> None:
    _start_long(qtbot, env)
    c = env.page.console
    c.run_button.click()  # 実行中は「中断」
    qtbot.waitUntil(lambda: not c.running, timeout=3000)
    qtbot.waitUntil(lambda: abs(env.bridge.states()[M1].velocity) < 0.5, timeout=5000)
    # 状態も出力も非同期に届く(状態は CoreBridge の tick、出力は 50 ms ごとのフラッシュ)ので、
    # 「中断」ボタンの直後に即時に見るのではなく届くまで待つ(以前は出力の「中断」が間に合わず
    # 6 回中 4 回失敗した)
    qtbot.waitUntil(lambda: env.ui() == "enabled", timeout=3000)  # 運転停止(有効のまま保持)
    assert not env.bridge.estop_latched()
    qtbot.waitUntil(lambda: "中断" in c.output_text(), timeout=3000)


# ---------------------------------------------------------------- シーケンス
def test_sequence_lists_files_and_validates(qtbot, env: Env) -> None:
    env.page.show_view("sequence")
    s = env.page.sequence
    names = [s.file_combo.itemText(i) for i in range(s.file_combo.count())]
    assert names == ["bad.yaml", "long.yaml", "short.yaml", "velocity_steps.yaml"]
    # 検証エラー: 行番号つき・実行不可・理由はツールチップ
    s.file_combo.setCurrentIndex(names.index("bad.yaml"))
    assert s.sequence is None and s.problems
    assert "行 3" in s.problem_label.text() and s.problem_label.isVisibleTo(s)
    assert not s.run_button.isEnabled()
    assert s.run_button.toolTip() == tr("script.reason_invalid")
    # 正常なファイル
    s.file_combo.setCurrentIndex(names.index("velocity_steps.yaml"))
    assert s.sequence is not None and len(s.step_rows) == len(s.sequence.steps)
    assert "ステップ" in s.summary_label.text()
    qtbot.waitUntil(lambda: s.run_button.isEnabled(), timeout=3000)
    assert s.motor_combo.count() == 2


def test_sequence_runs_to_completion_with_progress(qtbot, env: Env) -> None:
    env.page.show_view("sequence")
    s = env.page.sequence
    s.select_file(env.root / "sequences" / "short.yaml")
    qtbot.waitUntil(lambda: s.run_button.isEnabled(), timeout=3000)
    s.motor_combo.setCurrentIndex(s.motor_combo.findData(M1))
    assert s.start()
    assert s.running and s.run_button.text() == tr("script.seq_abort")
    qtbot.waitUntil(lambda: s.step_rows[1].mark == "current", timeout=WAIT)
    assert tr("script.seq_progress", i=2, n=3) in s.progress_label.text()
    assert s.step_rows[0].mark == "done"
    qtbot.waitUntil(lambda: not s.running, timeout=WAIT)
    qtbot.wait(150)
    assert [r.mark for r in s.step_rows] == ["done"] * 3
    assert s.status_text() == tr("script.seq_state_completed")
    assert "[2/3]" in s.log_text()
    assert env.ui() == "disabled"
    assert s.run_button.text() == tr("script.seq_run")


def test_sequence_abort_button(qtbot, env: Env) -> None:
    env.page.show_view("sequence")
    s = env.page.sequence
    s.select_file(env.root / "sequences" / "long.yaml")
    qtbot.waitUntil(lambda: s.run_button.isEnabled(), timeout=3000)
    s.motor_combo.setCurrentIndex(s.motor_combo.findData(M1))
    assert s.start()
    qtbot.waitUntil(lambda: env.ui() == "running", timeout=WAIT)
    s.run_button.click()  # 中断
    qtbot.waitUntil(lambda: not s.running, timeout=8000)
    qtbot.wait(150)
    assert s.status_text() == tr("script.seq_state_aborted")
    assert env.ui() == "disabled"  # 後始末で無効化
    assert s.step_rows[1].mark == "current"  # 中断した位置が分かる


def test_sequence_estop_during_run(qtbot, env: Env) -> None:
    env.page.show_view("sequence")
    s = env.page.sequence
    s.select_file(env.root / "sequences" / "long.yaml")
    qtbot.waitUntil(lambda: s.run_button.isEnabled(), timeout=3000)
    s.motor_combo.setCurrentIndex(s.motor_combo.findData(M1))
    s.start()
    qtbot.waitUntil(lambda: env.ui() == "running", timeout=WAIT)
    press(qtbot, env, s.log, Qt.Key.Key_Pause)
    qtbot.waitUntil(lambda: not s.running, timeout=5000)
    qtbot.wait(150)
    assert env.bridge.estop_latched() and env.ui() == "disabled"
    assert s.status_text() in (tr("script.seq_state_estop"), tr("script.seq_state_aborted"))
    assert not s.run_button.isEnabled()  # ラッチ中は実行できない
    assert s.run_button.toolTip() == tr("script.reason_estop")


def test_sequence_blocked_in_high_tier_and_when_script_running(qtbot, env: Env) -> None:
    env.page.show_view("sequence")
    s = env.page.sequence
    s.select_file(env.root / "sequences" / "short.yaml")
    qtbot.waitUntil(lambda: s.run_button.isEnabled(), timeout=3000)
    # コンソールのスクリプトが走っている間は実行できない
    env.page.console.input.setPlainText("sleep(1.0)")
    env.page.console.submit()
    qtbot.waitUntil(lambda: not s.run_button.isEnabled(), timeout=2000)
    assert s.run_button.toolTip() == tr("script.reason_script")
    qtbot.waitUntil(lambda: s.run_button.isEnabled(), timeout=4000)
    # 高出力のときは実行できない(低出力でだけ)
    assert env.bridge.set_power_tier("high")
    qtbot.waitUntil(lambda: not s.run_button.isEnabled(), timeout=2000)
    assert s.run_button.toolTip() == tr("script.reason_high")
    env.bridge.set_power_tier("low")
    qtbot.waitUntil(lambda: s.run_button.isEnabled(), timeout=2000)


def test_sequence_not_available_when_offline(qtbot, env: Env) -> None:
    env.page.show_view("sequence")
    s = env.page.sequence
    s.select_file(env.root / "sequences" / "short.yaml")
    qtbot.waitUntil(lambda: s.run_button.isEnabled(), timeout=3000)
    env.bridge.disconnect_adapter()
    qtbot.waitUntil(lambda: not s.run_button.isEnabled(), timeout=5000)
    assert s.run_button.toolTip() == tr("script.reason_offline")


# ---------------------------------------------------------------- プラグイン
def test_disabled_plugin_removes_page(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    state = MemoryStateStore({"disabled": ["dropin:scripting"]})
    mgr = create_manager(None, project_root=root, state=state)
    mgr.load_all()
    try:
        rec = mgr.record("dropin:scripting")
        assert rec is not None and rec.status is PluginStatus.DISABLED
        assert "scripting" not in [p.id for p in mgr.ui.pages()]
    finally:
        mgr.shutdown()


def test_plugin_metadata_and_tr_keys() -> None:
    mgr = create_manager(None, project_root=ROOT)
    mgr.load_all()
    try:
        rec = mgr.record("scripting")
        assert rec is not None and rec.status is PluginStatus.ACTIVE
        page = [p for p in mgr.ui.pages() if p.id == "scripting"][0]
        assert page.title == "script.nav" and page.icon == "terminal" and page.order == 50
    finally:
        mgr.shutdown()
    # プラグインのソースで使う tr キーはすべてカタログにある
    cat = catalog()
    missing: set[str] = set()
    for p in (ROOT / "plugins" / "scripting").glob("*.py"):
        for m in re.finditer(r'\btr\(\s*"([a-z_0-9.]+)"', p.read_text(encoding="utf-8")):
            if m.group(1) not in cat:
                missing.add(f"{p.name}: {m.group(1)}")
        for m in re.finditer(r'"(script\.[a-z_0-9]+)"', p.read_text(encoding="utf-8")):
            if m.group(1) not in cat:
                missing.add(f"{p.name}: {m.group(1)}")
    assert not missing, sorted(missing)


# ---------------------------------------------------------------- デモ(コア無し)
def test_page_works_without_core_in_demo_mode(qtbot, tmp_path: Path) -> None:
    """`--demo`(DemoBridge)ではサービスが無い。ページは開け、シーケンスは理由つきで押せない。"""
    from edulite05.app.demo_bridge import DemoBridge

    root = make_project(tmp_path)
    mgr = create_manager(None, project_root=root)
    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0)
    win = MainWindow(bridge, settings_path=None, enable_3d=False, plugins=mgr)
    qtbot.addWidget(win)
    win.show()
    bridge.start()
    qtbot.waitExposed(win)
    try:
        win.navigate("scripting")
        page = win.page("scripting")
        c = page.console
        c.input.setPlainText("(service, ids(), 1 + 1)")
        assert c.submit()
        qtbot.waitUntil(lambda: not c.running, timeout=WAIT)
        qtbot.wait(150)
        assert "(None, [], 2)" in c.output_text()
        assert tr("script.status_offline") == c.status_label.text()
        page.show_view("sequence")
        s = page.sequence
        s.select_file(root / "sequences" / "short.yaml")
        assert not s.run_button.isEnabled()
        assert s.run_button.toolTip() == tr("script.reason_offline")
    finally:
        win.page("scripting").shutdown()
        win.close()
