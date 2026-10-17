"""グラフ強化(phase4_5.md §K)の画面テスト: トリガ・Δ カーソル・FFT・ステップ評価・状態の帯。

DemoBridge(実時間で動く)で、試験信号(StepTest / SineTest)を流して確認する。
計算そのものの正しさは tests/test_graph_analysis.py(Qt なし)。ここは結線と表示。
"""

from __future__ import annotations

import pytest
from operate_helpers import make_page, teardown
from PySide6.QtCore import QPoint, Qt

from edulite05.app.i18n import tr
from edulite05.app.pages.operate import analysis as an
from edulite05.app.pages.operate.graph_view import FFT_KEY
from edulite05.app.pages.operate.timeline_band import LANES
from edulite05.app.ui.style import theme_manager
from edulite05.core.control.params import MitParams, SineParams, StepParams

WAIT = 20000  # ms(実時間で流すので余裕を持つ)


@pytest.fixture(autouse=True)
def _theme():
    theme_manager().apply("dark")
    yield
    theme_manager().apply("dark")


@pytest.fixture
def env(qtbot, tmp_path):
    page, bridge, shell = make_page(qtbot, tmp_path)
    qtbot.waitUntil(lambda: len(bridge.motors()) == 2, timeout=WAIT)
    yield page, bridge, shell
    teardown(page, bridge)


def ui(bridge, mid: int = 1) -> str:
    s = bridge.states().get(mid)
    return s.ui_state if s else "none"


def run_test_signal(qtbot, bridge, name: str, params, run_mode: int = 2) -> None:
    bridge.enable([1], run_mode)
    qtbot.waitUntil(lambda: ui(bridge) == "enabled", timeout=WAIT)
    bridge.start_controller(name, {"motor": 1}, params)
    qtbot.waitUntil(lambda: ui(bridge) == "running", timeout=WAIT)


# ------------------------------------------------------------------ トリガ
def test_trigger_captures_span_around_the_crossing_and_resume_releases(env, qtbot) -> None:
    page, bridge, _shell = env
    g = page.monitor.graph
    states: list[str] = []
    g.trigger_changed.connect(states.append)
    run_test_signal(
        qtbot, bridge, "step", StepParams(target="velocity", low=0.0, high=2.0, period=1.0)
    )
    g.arm_trigger(an.TriggerSpec("m1.vel", 1.0, True, 1.0))
    assert g.trigger_state() == "armed" and "armed" in states
    assert page.monitor.analysis_btn.property("trigger") == "armed"
    assert page.monitor.analysis_btn.toolTip() == tr("operate.analysis.button_armed")
    assert not g.is_paused()
    qtbot.waitUntil(lambda: g.trigger_state() == "captured", timeout=WAIT)
    assert g.is_paused()  # 条件成立の 1 s 後に止まる
    rel = g.trigger_rel()
    assert rel is not None
    lo, hi = g.x_range()
    assert lo == pytest.approx(rel - 1.0, abs=0.05) and hi == pytest.approx(rel + 1.0, abs=0.05)
    assert page.monitor.analysis_btn.property("trigger") == "captured"
    assert page.monitor.analysis_btn.toolTip() == tr("operate.analysis.button_fired")
    assert tr("operate.analysis.fired_status", **g._trigger_text(g.trigger_spec())) in (  # noqa: SLF001
        g.legend.status.text()
    )
    # 速度は成立の時刻に 1.0 rad/s を立上りで横切っている
    t, y = g._data[("vel", 1, False)]  # noqa: SLF001
    tc = an.find_crossing(t, y, 1.0, True, after=rel - 0.2)
    # トリガ時刻は「更新した時刻 + 相対秒」の絶対時刻で覚え、DemoBridge の相対秒は最後のサンプルを 0 に
    # する。この 2 つの原点は DemoBridge の tick の遅れ(負荷が高いと数十〜百 ms)の分だけずれるので、
    # 許容は 0.05 s でなく 0.25 s とする(以前は負荷時に 0.07 s ずれて不定期に失敗した)。
    # DemoBridge の原点を「現在」に変えると解消するが、ステップ応答の検出(test_plot_height_is_stable…)
    # の失敗率が上がったため採らなかった
    assert tc is not None and tc == pytest.approx(rel, abs=0.25)
    states.clear()
    g.set_paused(False)  # 再開でトリガ成立の表示を解除し、時間幅を元に戻す
    assert g.trigger_state() == "idle" and "idle" in states
    assert g.x_range()[0] == pytest.approx(-g.window_s(), abs=0.01)
    assert page.monitor.analysis_btn.property("trigger") == "idle"
    assert page.monitor.analysis_btn.toolTip() == tr("operate.analysis.button_tip")


def test_trigger_popover_arms_and_disarms(env, qtbot) -> None:
    page, _bridge, _shell = env
    mon, g = page.monitor, page.monitor.graph
    pop = mon.analysis
    pop.sync()
    names = [pop.channel_combo.itemData(i) for i in range(pop.channel_combo.count())]
    assert "m1.vel" in names and "m1.pos_cmd" not in names  # 実測値だけ
    pop.channel_combo.setCurrentIndex(pop.channel_combo.findData("m2.torque"))
    assert pop.unit_label.text() == "N·m"
    pop.edge_seg.set_current("falling")
    pop.level_edit.set_value(0.25)
    pop.arm_btn.click()
    spec = g.trigger_spec()
    assert g.trigger_state() == "armed"
    assert spec == an.TriggerSpec("m2.torque", 0.25, False, 2.0)
    assert mon.analysis_btn.property("trigger") == "armed"
    pop.sync()  # 待機中は設定を固定し、ボタンは「待機を解除」
    assert not pop.channel_combo.isEnabled()
    assert pop.arm_btn.text() == tr("operate.analysis.trigger_disarm")
    pop.arm_btn.click()
    assert g.trigger_state() == "idle"
    assert mon.analysis_btn.property("trigger") == "idle"


def test_armed_trigger_does_not_fire_on_old_data(env, qtbot) -> None:
    """待機を始める前のデータでは成立しない(過去の交差を拾わない)。"""
    page, bridge, _shell = env
    g = page.monitor.graph
    run_test_signal(
        qtbot, bridge, "step", StepParams(target="velocity", low=0.0, high=2.0, period=20.0)
    )
    qtbot.wait(1500)  # 立上りは待機の前にすでに起きている(次の立上りは 20 s 後)
    g.arm_trigger(an.TriggerSpec("m1.vel", 1.0, True, 1.0))
    qtbot.wait(1000)
    assert g.trigger_state() == "armed" and not g.is_paused()


# ------------------------------------------------------------------ Δ カーソル
def test_delta_cursors_measure_dt_dv_slope_while_paused(env, qtbot) -> None:
    page, bridge, _shell = env
    g = page.monitor.graph
    sine = SineParams(target="velocity", amplitude=2.0, frequency=0.5)
    run_test_signal(qtbot, bridge, "sine", sine)
    qtbot.wait(2500)
    g.add_mark(-1.0)  # 一時停止中でなければ無視される
    assert g.marks() == []
    g.set_paused(True)
    assert g.legend.status.text() == tr("operate.graph_delta.paused_hint")
    # 実際のクリック(ビューポート上)で A・B を置く
    vp = g.glw.viewport()
    rect = g.plot_rect("vel")
    ya = int(rect.center().y())
    xa, xb = int(rect.left() + rect.width() * 0.3), int(rect.left() + rect.width() * 0.6)
    qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=QPoint(xa, ya))
    assert len(g.marks()) == 1
    assert g.legend.status.text() == tr("operate.graph_delta.paused_hint_one")
    qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=QPoint(xb, ya))
    a, b = g.marks()
    assert a < b
    assert b - a == pytest.approx((xb - xa) / rect.width() * g.window_s(), rel=0.05)
    rows = g.delta_rows()
    assert set(rows) == {"pos", "vel", "torque"}
    (m1, d), *_ = [r for r in rows["vel"] if r[0] == 1]
    t, y = g._data[("vel", 1, False)]  # noqa: SLF001
    va, vb = an.sample_at(t, y, a), an.sample_at(t, y, b)
    assert d.dt == pytest.approx(b - a) and d.dv == pytest.approx(vb - va)
    assert d.slope == pytest.approx((vb - va) / (b - a))
    # 3 回目のクリックは A からやり直し、右クリックで解除
    qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=QPoint(xa, ya))
    assert len(g.marks()) == 1
    qtbot.mouseClick(vp, Qt.MouseButton.RightButton, pos=QPoint(xa, ya))
    assert g.marks() == [] and g.delta_rows() == {}
    # 再開で消える
    g.add_mark(a)
    g.add_mark(b)
    g.set_paused(False)
    assert g.marks() == []


def test_delta_uses_hover_as_provisional_b(env, qtbot) -> None:
    page, _bridge, _shell = env
    g = page.monitor.graph
    qtbot.wait(800)
    g.set_paused(True)
    g.add_mark(-2.0)
    assert g.delta_points() is None  # ホバーがまだ無い
    g.set_cursor_time(-1.0)
    assert g.delta_points() == (-2.0, -1.0)
    assert g.cursor_time() == -1.0


# ------------------------------------------------------------------ FFT
def test_fft_preset_shows_spectrum_peak_of_selected_channel(env, qtbot) -> None:
    page, bridge, _shell = env
    mon, g = page.monitor, page.monitor.graph
    assert mon.fft_combo.isHidden() and mon.fft_label.isHidden()
    sine = SineParams(target="velocity", amplitude=2.0, frequency=3.0)
    run_test_signal(qtbot, bridge, "sine", sine)
    g.set_window(5)
    qtbot.wait(5500)
    mon.preset_combo.setCurrentIndex(mon.preset_combo.findData("fft"))
    assert g.preset() == FFT_KEY and g.kinds() == [FFT_KEY] and g.fft_active()
    assert not mon.fft_combo.isHidden()
    mon.fft_combo.setCurrentIndex(mon.fft_combo.findData("m1.vel"))
    assert g.fft_channel() == "m1.vel" and g.fft_unit() == "rad/s"
    qtbot.waitUntil(lambda: g.fft_spectrum() is not None, timeout=WAIT)
    sp = g.fft_spectrum()
    assert sp.peak_freq == pytest.approx(3.0, abs=0.35)
    assert sp.peak_amp > 0.2
    assert g.axis_text(FFT_KEY) == tr("operate.analysis.fft_axis", unit="rad/s")
    assert g.cursor_time() is None and not g.band.isVisible() and g.step_summary.isHidden()
    g.set_paused(True)  # FFT 表示ではクリックで Δ を置かない
    g.add_mark(-1.0)
    assert g.marks() == []
    g.set_paused(False)
    # 別のチャネルに切り替え
    mon.fft_combo.setCurrentIndex(mon.fft_combo.findData("m2.torque"))
    assert g.fft_channel() == "m2.torque" and g.fft_unit() == "N·m"
    # 標準へ戻すと選択は隠れる
    mon.preset_combo.setCurrentIndex(mon.preset_combo.findData("standard"))
    assert not g.fft_active() and mon.fft_combo.isHidden()
    assert g.kinds() == ["pos", "vel", "torque"]


# ------------------------------------------------------------------ ステップ応答
def test_step_response_metrics_during_step_test(env, qtbot) -> None:
    page, bridge, _shell = env
    g = page.monitor.graph
    # 結果が無いときも行は残す(淡いプレースホルダ。グラフの高さが跳ばない)
    assert not g.step_summary.isHidden() and g.step_result() is None
    assert g.step_summary.title.text() == tr("operate.step.title_none")
    assert g.step_summary.title.property("role") == "caption-tertiary"
    h_empty = g.glw.height()
    run_test_signal(
        qtbot, bridge, "step", StepParams(target="velocity", low=0.0, high=2.0, period=2.0)
    )
    qtbot.waitUntil(lambda: g.step_result() is not None, timeout=WAIT)
    kind, mid, m = g.step_result()
    assert (kind, mid) == ("vel", 1)
    assert abs(m.amplitude) == pytest.approx(2.0, rel=0.1)
    assert m.rise_time is not None and 0 < m.rise_time < 0.5
    assert m.overshoot_pct >= 0.0
    assert not g.step_summary.isHidden()
    texts = g.step_summary.texts()
    assert texts["rise"].endswith(" s") and texts["overshoot"].endswith(" %")
    assert g.step_summary.title.text() == tr("operate.step.title", id=1, quantity="速度")
    # 結果が出てもプロット領域の高さは変わらない(UI の枠が動かない)
    qtbot.wait(100)
    assert g.glw.height() == h_empty
    assert g.step_summary.height() == 28
    # 試験信号が無いときは出さない(クリアして停止中にする)
    bridge.stop_controller("step", {"motor": 1})
    bridge.disable([1])
    qtbot.waitUntil(lambda: ui(bridge) == "disabled", timeout=WAIT)
    g.set_window(2)
    qtbot.waitUntil(lambda: g.step_result() is None, timeout=WAIT)
    assert not g.step_summary.isHidden()
    assert g.step_summary.title.text() == tr("operate.step.title_none")
    assert g.glw.height() == h_empty


# ------------------------------------------------------------------ 状態の帯
def test_timeline_records_state_mode_and_clamp(env, qtbot) -> None:
    page, bridge, _shell = env
    g = page.monitor.graph
    band = g.band
    assert band.isVisible() and g.timeline_motor() == 1
    lanes = g.timeline_lanes(1)
    assert set(lanes) == set(LANES)
    assert [s.key for s in lanes["state"].segments][-1] == "disabled"
    bridge.enable([1], 0)
    qtbot.waitUntil(lambda: ui(bridge) == "enabled", timeout=WAIT)
    assert lanes["state"].segments[-1].key == "enabled"
    assert lanes["mode"].segments[-1].key == "mit"  # 有効(待機)中は run_mode のモード
    s = bridge.states()[1]
    bridge.start_controller(
        "mit", {"motor": 1},
        MitParams(position=s.position_multi, velocity=30.0, kp=0.0, kd=0.5, torque_ff=0.0),
    )
    qtbot.waitUntil(lambda: ui(bridge) == "running", timeout=WAIT)
    qtbot.waitUntil(
        lambda: bool(lanes["clamp"].segments) and lanes["clamp"].segments[-1].end is None,
        timeout=WAIT,
    )
    assert lanes["state"].segments[-1].key == "running"
    assert lanes["mode"].segments[-1].key == "mit"
    assert lanes["clamp"].segments[-1].key == "clamp"
    assert "velocity" in bridge.clamp_note(1)
    # 帯に出る区間(横軸は現在 = 0 の相対秒)と、書く文字・意味色
    vis = band.visible_segments()
    assert [seg.key for seg, _s, _e in vis["state"]][-3:] == ["disabled", "enabled", "running"]
    assert vis["clamp"] and vis["clamp"][-1][2] == pytest.approx(0.0, abs=0.2)
    from edulite05.app.pages.operate.timeline_band import lane_label, lane_tone
    assert lane_label("state", "running") == tr("motor_state.running")
    assert lane_label("mode", "mit") == tr("controller.mit")
    assert lane_label("clamp", "clamp") == tr("operate.timeline.clamp")
    assert lane_tone("state", "running") == "success" and lane_tone("state", "fault") == "critical"
    assert lane_tone("mode", "mit") == "neutral" and lane_tone("clamp", "clamp") == "caution"
    # 上限内に戻すと制限の帯が閉じる
    bridge.update_controller_params(
        "mit", {"motor": 1},
        MitParams(position=s.position_multi, velocity=1.0, kp=0.0, kd=0.5, torque_ff=0.0),
    )
    qtbot.waitUntil(lambda: lanes["clamp"].segments[-1].end is not None, timeout=WAIT)
    # 切替: 帯を隠す
    g.set_timeline_visible(False)
    assert not band.isVisible() and not g.timeline_visible()
    g.set_timeline_visible(True)
    assert band.isVisible()
    # 切断で帯の履歴は消える
    bridge.stop_controller("mit", {"motor": 1})
    bridge.disable([1])
    bridge.disconnect_adapter()
    qtbot.waitUntil(lambda: g.timeline_motor() is None, timeout=WAIT)
    assert g.timeline_lanes(1) == {}
