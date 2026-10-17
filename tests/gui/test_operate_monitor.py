"""運転ページのモニタ側(読み値ストリップ・グラフ・3D・記録・イベント行)のテスト。"""

from __future__ import annotations

import csv

import pytest
from operate_helpers import make_page, teardown
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QCheckBox

from edulite05.app.i18n import tr
from edulite05.app.pages.operate.graph_view import PRESETS, axis_label, fmt_value
from edulite05.app.pages.operate.readout_strip import physical_range, wrap_angle
from edulite05.app.pages.operate.recorder import Recorder, format_elapsed
from edulite05.app.ui.style import theme_manager, tokens
from edulite05.app.viewmodels import LogEntry


@pytest.fixture(autouse=True)
def _theme():
    theme_manager().apply("dark")
    yield
    theme_manager().apply("dark")


@pytest.fixture
def env(qtbot, tmp_path):
    page, bridge, shell = make_page(qtbot, tmp_path)
    yield page, bridge, shell
    teardown(page, bridge)


def num(text: str) -> float:
    return float(text.replace("−", "-"))


def run_velocity(page, v: float) -> None:
    insp = page.inspector
    insp.forms["velocity"].set_value("velocity", v)
    insp.primary.click()
    insp.primary.click()


# ------------------------------------------------------------------ 読み値ストリップ
def test_readout_strip_cells_and_gauge_ranges(env, qtbot) -> None:
    page, _bridge, _shell = env
    strip = page.strip
    cells = [strip.pos_cell, strip.vel_cell, strip.torque_cell]
    assert [c.label.text() for c in cells] == [
        tr("operate.readout.position"), tr("operate.readout.velocity"),
        tr("operate.readout.torque"),
    ]
    qtbot.waitUntil(lambda: strip.env_cell.value_texts()[1] == "24.0", timeout=3000)
    # ゲージの全幅 = 物理レンジ(Params の metadata から)、薄い帯 = 現在の出力段階の許容範囲
    assert (strip.vel_cell.gauge._lo, strip.vel_cell.gauge._hi) == physical_range("velocity")  # noqa: SLF001
    assert (strip.torque_cell.gauge._lo, strip.torque_cell.gauge._hi) == physical_range("torque_ff")  # noqa: SLF001
    assert strip.vel_cell.gauge._allowed == (-5.0, 5.0)  # noqa: SLF001  (低出力: 5 rad/s)
    assert strip.torque_cell.gauge._allowed == (-0.5, 0.5)  # noqa: SLF001
    # 4 セルは同じ高さ
    qtbot.waitUntil(
        lambda: len({c.height() for c in strip._cells}) == 1, timeout=3000  # noqa: SLF001
    )


def test_readout_values_follow_state_and_command(env, qtbot) -> None:
    page, _bridge, _shell = env
    run_velocity(page, 2.0)
    strip = page.strip
    qtbot.waitUntil(lambda: num(strip.vel_cell.value_text()) > 1.8, timeout=4000)
    assert strip.vel_cell.command_label.text() == tr("ui.command", value="+2.00")
    assert strip.vel_cell.gauge._command == pytest.approx(2.0)  # noqa: SLF001
    assert strip.pos_cell.meta_label.text().startswith("多回転")


def test_position_cell_unit_toggle(env, qtbot) -> None:
    page, bridge, _shell = env
    strip = page.strip
    assert strip.pos_cell.unit_label.text() == "rad"
    bridge._motors[1].theta = 1.0  # noqa: SLF001
    bridge._publish_states()  # noqa: SLF001
    qtbot.waitUntil(lambda: abs(num(strip.pos_cell.value_text()) - 1.0) < 0.2, timeout=3000)
    qtbot.mouseClick(strip.unit_toggle, Qt.MouseButton.LeftButton, pos=QPoint(60, 10))  # deg 側
    assert strip.use_degrees() and strip.pos_cell.unit_label.text() == "deg"
    assert abs(num(strip.pos_cell.value_text()) - 57.3) < 12
    strip.set_degrees(False)
    assert not strip.use_degrees() and strip.pos_cell.unit_label.text() == "rad"
    assert wrap_angle(3 * 3.141592653589793) == pytest.approx(3 * 3.141592653589793)
    assert wrap_angle(5 * 3.141592653589793) == pytest.approx(-3 * 3.141592653589793)


def test_readout_note_when_command_is_at_tier_limit(env, qtbot) -> None:
    page, _bridge, _shell = env
    run_velocity(page, 5.0)  # 低出力の上限
    strip = page.strip
    note = tr("operate.readout.limited", tier=tr("tier.low.short"))
    qtbot.waitUntil(lambda: strip.vel_cell.meta_label.text() == note, timeout=4000)
    assert strip.vel_cell.meta_label.property("tone") == "caution"
    page.inspector.forms["velocity"].set_value("velocity", 1.0)
    qtbot.waitUntil(lambda: strip.vel_cell.meta_label.text() != note, timeout=4000)


def test_readout_follows_selected_motor(env, qtbot) -> None:
    page, bridge, _shell = env
    bridge.select_motor(2)
    assert page.strip._last[0].id == 2  # noqa: SLF001
    assert page.inspector.motor_id() == 2


# ------------------------------------------------------------------ グラフ
def test_graph_one_plot_per_quantity_with_motor_colors(env) -> None:
    page, _bridge, _shell = env
    g = page.monitor.graph
    assert g.kinds() == list(PRESETS["standard"]) == ["pos", "vel", "torque"]
    assert g.motor_ids() == [1, 2]
    assert len(g._curves) == 3 * 2 * 2  # noqa: SLF001  (量 × モータ × {実測, 指令})
    t = tokens()
    for (kind, mid, is_cmd), curve in g._curves.items():  # noqa: SLF001
        pen = curve.opts["pen"]
        want = t.motor_color(_bridge.motor_color_index(mid))
        assert pen.color().name().lower() == want.lower(), (kind, mid)
        if is_cmd:  # 指令は同色の破線
            assert pen.style() == Qt.PenStyle.CustomDashLine
        else:
            assert pen.style() == Qt.PenStyle.SolidLine
    # 1 モータ 1 色(初登録順で固定)
    c1, c2 = (t.motor_color(_bridge.motor_color_index(i)) for i in (1, 2))
    assert c1 != c2
    # 横線のみのごく薄い格子
    p = g._plots["pos"]  # noqa: SLF001
    assert p.ctrl.yGridCheck.isChecked() and not p.ctrl.xGridCheck.isChecked()
    assert axis_label("pos") == tr("scope.axis.pos")


def test_graph_presets_replace_checkboxes(env) -> None:
    page, _bridge, _shell = env
    mon = page.monitor
    g = mon.graph
    assert not g.findChildren(QCheckBox)
    keys = [mon.preset_combo.itemData(i) for i in range(mon.preset_combo.count())]
    assert keys == ["standard", "mit", "env", "all", "fft"]  # FFT は表示プリセットの末尾
    for key, kinds in (
        ("mit", ["pos", "torque"]),
        ("env", ["temp", "vbus"]),
        ("all", ["pos", "vel", "torque", "temp", "vbus", "current", "power"]),
        ("standard", ["pos", "vel", "torque"]),
    ):
        mon.preset_combo.setCurrentIndex(mon.preset_combo.findData(key))
        assert g.preset() == key and g.kinds() == kinds
        assert [r.isValid() for r in [g.plot_rect(k) for k in kinds]]


def test_graph_legend_toggles_motor_visibility(env) -> None:
    page, _bridge, _shell = env
    g = page.monitor.graph
    tg = g.legend.toggle(2)
    assert tg.isChecked() and tg.toolTip() == tr("operate.graph.legend_tip", id=2)
    tg.click()
    assert g.hidden_motors() == {2} and not tg.isChecked()
    for (_k, mid, _c), curve in g._curves.items():  # noqa: SLF001
        assert curve.isVisible() == (mid != 2)
    tg.click()
    assert g.hidden_motors() == set()
    assert all(c.isVisible() for c in g._curves.values())  # noqa: SLF001
    # 凡例には線種の見本と各モータの■が並ぶ
    assert set(g.legend._toggles) == {1, 2}  # noqa: SLF001


def test_graph_latest_value_tags_and_window(env, qtbot) -> None:
    page, _bridge, _shell = env
    mon, g = page.monitor, page.monitor.graph
    run_velocity(page, 2.0)
    qtbot.waitUntil(lambda: 1 in g.latest("vel") and 2 in g.latest("vel"), timeout=4000)
    qtbot.waitUntil(lambda: g.latest("vel")[1] > 1.5, timeout=4000)
    assert fmt_value("vel", 1.94).startswith("+") and fmt_value("vel", -2.07).startswith("−")
    mon.window_combo.setCurrentIndex(mon.window_combo.findData(2.0))
    assert g.window_s() == 2.0
    assert g._plots["pos"].viewRange()[0] == pytest.approx([-2.0, 0.0])  # noqa: SLF001
    # 電圧は自前の履歴から
    mon.preset_combo.setCurrentIndex(mon.preset_combo.findData("env"))
    qtbot.waitUntil(lambda: 1 in g.latest("vbus"), timeout=4000)
    assert g.latest("vbus")[1] == pytest.approx(24.0, abs=0.5)


def test_graph_pause_freezes_data_and_shows_synced_cursor(env, qtbot) -> None:
    page, _bridge, _shell = env
    mon, g = page.monitor, page.monitor.graph
    qtbot.wait(1300)
    assert g.cursor_time() is None
    g.set_cursor_time(-0.5)
    assert g.cursor_time() is None  # 一時停止中だけ
    mon.pause_btn.click()
    assert g.is_paused() and mon.pause_btn.text() == tr("operate.monitor.resume")
    frozen = {k: v[0] for k, v in g._data.items()}  # noqa: SLF001
    qtbot.wait(200)
    assert all(g._data[k][0] is frozen[k] for k in frozen)  # noqa: SLF001
    g.set_cursor_time(-0.5)
    vals = g.cursor_values()
    assert set(vals) == {"pos", "vel", "torque"}  # 全グラフで同期
    assert [row[0] for row in vals["pos"]] == [1, 2]
    # Space で再開(グラフにフォーカスがあるとき)
    qtbot.keyClick(g.glw, Qt.Key.Key_Space)
    assert not g.is_paused() and mon.pause_btn.text() == tr("operate.monitor.pause")
    assert g.cursor_time() is None
    qtbot.keyClick(g.glw, Qt.Key.Key_Space)
    assert g.is_paused()
    g.set_paused(False)


def test_graph_rebuilds_when_motors_change(env, qtbot) -> None:
    page, bridge, _shell = env
    g = page.monitor.graph
    bridge.disconnect_adapter()
    assert g.motor_ids() == [] and g._curves == {}  # noqa: SLF001
    bridge.connect_adapter({"adapter": "simulator", "port": ""})
    qtbot.waitUntil(lambda: g.motor_ids() == [1, 2], timeout=5000)


# ------------------------------------------------------------------ 3D
def test_3d_view_falls_back_to_dial(env, qtbot) -> None:
    page, _bridge, _shell = env
    mon = page.monitor
    mon.set_view("3d")
    assert mon.view() == "3d" and mon.stack.currentWidget() is mon.view3d
    assert not mon.pause_btn.isVisible() and not mon._graph_controls.isVisible()  # noqa: SLF001
    v = mon.view3d
    assert v.mode == "dial"
    qtbot.waitUntil(lambda: v.dial.motor_count() == 2, timeout=3000)
    page.set_preload_failed("ImportError: pyvista")
    assert v.state == "failed" and "pyvista" in v.backend_label.text()
    page.set_3d_enabled(False)
    assert v.backend_label.text() == tr("operate.view3d.disabled") or v.state == "failed"
    v.set_preload_failed("x")
    v.set_3d_enabled(False)
    assert v.state == "failed"
    mon.set_view("graph")
    assert mon.pause_btn.isVisible() and mon.stack.currentWidget() is mon.graph
    assert v.dial.grab().width() > 0  # ダイヤルが描画できる(モータ色のリング)


def test_3d_hooks_do_not_import_pyvista_when_disabled(env) -> None:
    page, _bridge, _shell = env
    page.set_3d_enabled(False)
    assert page.monitor.view3d.state == "disabled"
    page.ensure_3d()  # 無効のときは何もしない(pyvista を読み込まない)
    assert page.monitor.view3d.state == "disabled" and page.monitor.view3d.mode == "dial"


# ------------------------------------------------------------------ 記録
def test_recorder_writes_csv(tmp_path, env) -> None:
    _page, bridge, _shell = env
    rec = Recorder(tmp_path / "rec")
    assert rec.stop() is None  # 開始前
    rec.start()
    assert rec.active
    for _ in range(3):
        rec.add(bridge.states())
    path = rec.stop()
    assert path is not None and path.parent == tmp_path / "rec" and not rec.active
    rows = list(csv.reader(path.open(encoding="utf-8")))
    assert rows[0][0] == "t_s" and "m1_pos_rad" in rows[0] and "m2_cmd_torque_Nm" in rows[0]
    assert len(rows) == 4 and len(rows[1]) == len(rows[0])
    assert rec.stop() is None
    assert format_elapsed(12.4) == "00:12" and format_elapsed(3725) == "1:02:05"


def test_record_button_and_shortcut(env, qtbot, tmp_path) -> None:
    page, _bridge, shell = env
    mon = page.monitor
    assert mon.record_btn.text() == tr("operate.monitor.record")
    mon.record_btn.click()
    assert page.recorder.active and mon.is_recording()
    assert mon.record_btn.text() == tr("operate.monitor.recording", time="00:00")
    qtbot.waitUntil(lambda: page.recorder.rows > 3, timeout=3000)
    page.toggle_recording()  # Ctrl+R
    assert not page.recorder.active and mon.record_btn.text() == tr("operate.monitor.record")
    files = list(tmp_path.glob("record_*.csv"))
    assert len(files) == 1
    assert shell.toasts[-1] == (tr("operate.monitor.saved", name=files[0].name), "success")
    # データが無いときは保存しない
    page.set_recording(True)
    page.set_recording(False)  # 状態更新が 1 回も来ないうちに止めた
    assert shell.toasts[-1] == (tr("operate.monitor.nothing_recorded"), "info")
    assert len(list(tmp_path.glob("record_*.csv"))) == 1


# ------------------------------------------------------------------ イベント行
def test_event_line_shows_latest_event_and_links_to_diagnostics(env, qtbot) -> None:
    page, bridge, shell = env
    line = page.event_line
    bridge.log_message.emit(LogEntry(1.0, "debug", "細かい情報"))
    assert line.text_value() != "細かい情報"
    bridge.log_message.emit(LogEntry(1700000000.0, "info", "ID 1: 速度モードで運転を開始しました"))
    assert line.text_value() == "ID 1: 速度モードで運転を開始しました"
    assert line.height() == 32
    bridge.log_message.emit(LogEntry(1700000001.0, "error", "ID 2: 通信エラー"))
    assert line.entry().level == "error"
    qtbot.mouseClick(line, Qt.MouseButton.LeftButton)
    assert shell.navigated == ["diagnostics"] and shell.navigated_views == ["log"]
    assert line.toolTip() == tr("operate.event.tip")


def test_light_theme_renders(env, qtbot) -> None:
    page, _bridge, _shell = env
    theme_manager().apply("light")
    qtbot.wait(100)
    img = page.grab()
    assert img.width() > 0
    assert page.monitor.graph.glw.backgroundBrush().color().name().lower() == "#ffffff"
