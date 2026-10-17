"""CoreBridge を SimTransport(本物のコア = MotorService)につないで動かす GUI テスト(phase3.md §2)。

pytest-qt(offscreen)。実時間で動くので判定は緩め。シミュレータのモータは ID 127 と 1(2 台)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edulite05.app.core_bridge import CoreBridge
from edulite05.app.i18n import tr
from edulite05.app.pages.operate import modes as md
from edulite05.app.viewmodels import BusStats
from edulite05.core.control.params import (
    CspParams,
    CurrentParams,
    JogParams,
    MitParams,
    PpParams,
    SineParams,
    StepParams,
    VelocityParams,
)
from edulite05.core.device.profile import MotorProfile

M1, M127 = 1, 127
WAIT = 8000  # ms


class Rig:
    """CoreBridge と、出た signal の記録。"""

    def __init__(self, bridge: CoreBridge) -> None:
        self.b = bridge
        self.logs: list = []
        self.frames: list = []
        self.stats: list[BusStats] = []
        self.params: list = []
        self.estops: list[bool] = []
        self.tiers: list[str] = []
        self.conns: list = []
        bridge.log_message.connect(self.logs.append)
        bridge.frame_logged.connect(self.frames.append)
        bridge.stats_updated.connect(self.stats.append)
        bridge.params_updated.connect(lambda mid, v: self.params.append((mid, v)))
        bridge.estop_changed.connect(self.estops.append)
        bridge.power_tier_changed.connect(self.tiers.append)
        bridge.connection_changed.connect(self.conns.append)

    def st(self, mid: int = M1):
        return self.b.states().get(mid)

    def ui(self, mid: int = M1) -> str:
        s = self.st(mid)
        return s.ui_state if s is not None else "none"

    def text(self) -> str:
        return "\n".join(e.text for e in self.logs)


@pytest.fixture
def rig(qtbot):
    b = CoreBridge(kind="simulator", sim_motors=2, start_offline=True)
    r = Rig(b)
    b.start()
    b.connect_adapter({"adapter": "sim", "n": 2})
    qtbot.waitUntil(lambda: len(b.motors()) == 2, timeout=WAIT)
    qtbot.waitUntil(lambda: r.ui(M1) == "disabled" and r.ui(M127) == "disabled", timeout=WAIT)
    yield r
    b.shutdown()


def go(qtbot, rig: Rig, cond, timeout: int = WAIT) -> None:
    qtbot.waitUntil(cond, timeout=timeout)


def enable(qtbot, rig: Rig, mid: int, run_mode: int) -> None:
    rig.b.enable([mid], run_mode)
    go(qtbot, rig, lambda: rig.ui(mid) == "enabled")


def disable(qtbot, rig: Rig, mid: int) -> None:
    rig.b.disable([mid])
    go(qtbot, rig, lambda: rig.ui(mid) == "disabled")


# ------------------------------------------------------------------ 接続・スキャン
def test_connect_scan_and_static_info(rig: Rig) -> None:
    b = rig.b
    info = b.connection_info()
    assert info.connected and info.simulated and not info.lost
    assert b.backend_kind == "simulator"
    assert [m.id for m in b.motors()] == [M1, M127]
    assert b.selected_motor() == M1
    assert b.power_tier() == "low" and not b.estop_latched()
    lim = b.power_limits()
    prof = MotorProfile.load_builtin("el05").tier("low")
    assert (lim.tier, lim.torque, lim.speed) == ("low", prof.torque, prof.speed)
    s = rig.st(M1)
    assert s.online and s.mode == "reset" and s.faults == () and s.controller is None
    defs = b.param_definitions()
    assert len(defs) == len(MotorProfile.load_builtin("el05").params)
    assert any(d.index == 0x7005 and d.name == "run_mode" and d.rw == "W/R" for d in defs)
    assert any(d.index == 0x7019 and d.rw == "R" for d in defs)
    chans = b.available_channels()
    assert {c.motor_id for c in chans} == {M1, M127}
    assert {"m1.pos", "m127.cmd_vel"} <= {c.name for c in chans}
    assert "スキャン完了" in rig.text()


def test_select_motor_and_disconnect(qtbot, rig: Rig) -> None:
    b = rig.b
    seen: list[int] = []
    b.selected_motor_changed.connect(seen.append)
    b.select_motor(M127)
    assert b.selected_motor() == M127 and seen == [M127]
    b.select_motor(99)  # 未知の ID は無視
    assert b.selected_motor() == M127
    b.disconnect_adapter()
    assert not b.connection_info().connected and b.motors() == [] and b.states() == {}
    assert not rig.conns[-1].connected and not rig.conns[-1].lost


# ------------------------------------------------------------------ 各モードの運転
def test_jog_velocity_stop_disable(qtbot, rig: Rig) -> None:
    b = rig.b
    enable(qtbot, rig, M1, 2)
    # ui_state(有効)は状態変更イベントで、mode(Motor)はモータのフィードバック(約 100 Hz)で届く。
    # 別経路なので ui_state が enabled になった直後に mode を見ると reset のことがある → 届くまで待つ
    go(qtbot, rig, lambda: rig.st().mode == "motor")
    assert rig.st().run_mode == 2
    # JOG: UI は符号付きの速度で渡す。逆方向
    b.start_controller("jog", {"motor": M1}, JogParams(speed=-1.0))
    go(qtbot, rig, lambda: rig.ui() == "running" and rig.st().velocity < -0.5)
    assert rig.st().controller == "jog"
    b.stop_controller("jog", {"motor": M1})
    go(qtbot, rig, lambda: rig.ui() == "enabled" and abs(rig.st().velocity) < 0.3)
    assert rig.st().controller is None
    # 速度モードへ。目標の更新
    b.start_controller("velocity", {"motor": M1}, VelocityParams(velocity=1.5))
    go(qtbot, rig, lambda: rig.st().velocity > 1.0)
    assert rig.st().controller == "velocity"
    b.update_controller_params("velocity", {"motor": M1}, VelocityParams(velocity=-1.5))
    go(qtbot, rig, lambda: rig.st().velocity < -1.0)
    b.stop_controller("velocity", {"motor": M1})
    go(qtbot, rig, lambda: rig.ui() == "enabled")
    disable(qtbot, rig, M1)
    go(qtbot, rig, lambda: rig.st().mode == "reset")  # 同上(フィードバック経由)
    assert "ID 1: 有効化しました" in rig.text() and "ID 1: 無効化しました" in rig.text()


def test_current_mode(qtbot, rig: Rig) -> None:
    enable(qtbot, rig, M1, 3)
    rig.b.start_controller("current", {"motor": M1}, CurrentParams(iq=0.5))
    go(qtbot, rig, lambda: rig.ui() == "running" and rig.st().velocity > 0.5)
    rig.b.stop_controller("current", {"motor": M1})
    go(qtbot, rig, lambda: rig.ui() == "enabled")
    disable(qtbot, rig, M1)


@pytest.mark.parametrize("ctrl", ["csp", "pp"])
def test_position_modes(qtbot, rig: Rig, ctrl: str) -> None:
    run_mode = md.RUN_MODE_OF[ctrl]
    assert run_mode == (5 if ctrl == "csp" else 1)
    enable(qtbot, rig, M1, run_mode)
    p0 = rig.st().position_multi
    params = CspParams(position=p0 + 0.8) if ctrl == "csp" else PpParams(position=p0 + 0.8)
    rig.b.start_controller(ctrl, {"motor": M1}, params)
    go(qtbot, rig, lambda: rig.ui() == "running" and rig.st().position_multi > p0 + 0.6)
    assert rig.st().controller == ctrl
    assert rig.st().cmd_position == pytest.approx(p0 + 0.8, abs=0.05)
    rig.b.stop_controller(ctrl, {"motor": M1})
    go(qtbot, rig, lambda: rig.ui() == "enabled")
    disable(qtbot, rig, M1)


def test_mit_mode(qtbot, rig: Rig) -> None:
    enable(qtbot, rig, M1, 0)
    p0 = rig.st().position_multi
    rig.b.start_controller("mit", {"motor": M1},
                           MitParams(position=p0 + 0.3, velocity=0.0, kp=8.0, kd=0.3))
    go(qtbot, rig, lambda: rig.ui() == "running" and rig.st().position_multi > p0 + 0.1)
    assert rig.st().controller == "mit"
    rig.b.stop_controller("mit", {"motor": M1})
    go(qtbot, rig, lambda: rig.ui() == "enabled")
    disable(qtbot, rig, M1)


@pytest.mark.parametrize(
    "wave, params",
    [
        ("sine", SineParams(target="velocity", amplitude=1.0, frequency=1.0)),
        ("step", StepParams(target="current", low=0.0, high=0.4, period=1.0)),
        ("sine", SineParams(target="torque", amplitude=0.1, frequency=1.0)),
    ],
)
def test_signal_tests_use_core_run_mode(qtbot, rig: Rig, wave: str, params) -> None:
    """試験信号の run_mode は core の Controller.run_mode が正(torque は運控 = 0)。"""
    m = md.MotorModel(mode="test", wave=wave)
    m.params[wave] = params
    run_mode = md.run_mode_for(m)
    expect = {"velocity": 2, "current": 3, "torque": 0, "position": 5}[params.target]
    assert run_mode == expect
    enable(qtbot, rig, M1, run_mode)
    rig.b.start_controller(wave, {"motor": M1}, params)
    go(qtbot, rig, lambda: rig.ui() == "running")
    assert rig.st().controller == wave
    rig.b.stop_controller(wave, {"motor": M1})
    go(qtbot, rig, lambda: rig.ui() == "enabled")
    disable(qtbot, rig, M1)


def test_start_refused_when_not_enabled(qtbot, rig: Rig) -> None:
    rig.b.start_controller("velocity", {"motor": M1}, VelocityParams(velocity=1.0))
    go(qtbot, rig, lambda: "運転開始" in rig.text() and "失敗" in rig.text())
    assert rig.ui() == "disabled"
    rig.b.start_controller("nope", {"motor": M1}, VelocityParams())
    assert "未知のコントローラ" in rig.text()


def test_two_motors_independent(qtbot, rig: Rig) -> None:
    enable(qtbot, rig, M1, 2)
    enable(qtbot, rig, M127, 2)
    rig.b.start_controller("velocity", {"motor": M1}, VelocityParams(velocity=1.0))
    rig.b.start_controller("velocity", {"motor": M127}, VelocityParams(velocity=-1.0))
    go(qtbot, rig, lambda: rig.st(M1).velocity > 0.5 and rig.st(M127).velocity < -0.5)
    rig.b.estop()
    go(qtbot, rig, lambda: rig.ui(M1) == "disabled" and rig.ui(M127) == "disabled")


# ------------------------------------------------------------------ 出力段階・制限
def test_tier_and_clamp_note(qtbot, rig: Rig) -> None:
    b = rig.b
    enable(qtbot, rig, M1, 2)
    assert b.set_power_tier("high") is False  # 有効なモータがあるうちは拒否
    assert b.power_tier() == "low"
    # LOW の上限(5 rad/s)を超える目標 → 指令値は上限に制限され、ログにも出る
    lim = b.power_limits().speed
    b.start_controller("velocity", {"motor": M1}, VelocityParams(velocity=20.0))
    go(qtbot, rig, lambda: rig.st().cmd_velocity is not None)
    assert rig.st().cmd_velocity == pytest.approx(lim, abs=1e-6)  # 読み値の「制限中」表示の条件
    go(qtbot, rig, lambda: "指令を制限しました" in rig.text())
    b.stop_controller("velocity", {"motor": M1})
    disable(qtbot, rig, M1)
    assert b.set_power_tier("high") is True
    go(qtbot, rig, lambda: rig.tiers and rig.tiers[-1] == "high")
    assert b.power_limits().speed > lim
    b.estop()  # E-STOP は LOW へ戻す
    assert b.power_tier() == "low"
    go(qtbot, rig, lambda: rig.tiers[-1] == "low")


# ------------------------------------------------------------------ E-STOP
def test_estop_latch_and_release(qtbot, rig: Rig) -> None:
    b = rig.b
    enable(qtbot, rig, M1, 2)
    b.start_controller("velocity", {"motor": M1}, VelocityParams(velocity=2.0))
    go(qtbot, rig, lambda: rig.ui() == "running" and rig.st().velocity > 1.0)
    b.estop()
    assert b.estop_latched()
    go(qtbot, rig, lambda: rig.ui() == "disabled")
    assert rig.estops == [True]
    go(qtbot, rig, lambda: abs(rig.st().velocity) < 0.5)
    # ラッチ中は有効化できない
    b.enable([M1], 2)
    go(qtbot, rig, lambda: "有効化に失敗" in rig.text())
    assert rig.ui() == "disabled"
    assert b.set_power_tier("high") is False
    b.release_estop()
    assert not b.estop_latched() and rig.estops == [True, False]
    assert rig.ui() in ("disabled",)  # 解除しても自動再開しない
    enable(qtbot, rig, M1, 2)  # 解除後は有効化できる
    disable(qtbot, rig, M1)


# ------------------------------------------------------------------ パラメータ
def test_params_read_write_save(qtbot, rig: Rig) -> None:
    b = rig.b
    b.read_all_params(M1)
    go(qtbot, rig, lambda: bool(rig.params))
    mid, vals = rig.params[-1]
    assert mid == M1
    by = {v.index: v for v in vals}
    assert by[0x7005].value is not None and by[0x700B].value is not None
    assert not any(v.dirty for v in vals)
    n_read = sum(1 for v in vals if v.value is not None)
    assert n_read >= 20
    # 書込み(未保存の印が付く)
    rig.params.clear()
    b.write_param(M1, 0x7010, 0.25)  # cur_kp
    go(qtbot, rig, lambda: bool(rig.params))
    by = {v.index: v for v in rig.params[-1][1]}
    assert by[0x7010].value == pytest.approx(0.25) and by[0x7010].dirty
    # 運転系パラメータ(limit_spd など)は MotorService が拒否する。画面側は失敗をログに出すだけ
    rig.params.clear()
    b.write_param(M1, 0x7017, 3.0)
    go(qtbot, rig, lambda: "パラメータ書込に失敗" in rig.text())
    go(qtbot, rig, lambda: bool(rig.params))
    assert {v.index: v for v in rig.params[-1][1]}[0x7017].dirty is False
    # 読出専用は拒否
    b.write_param(M1, 0x7019, 1.0)
    assert "書込みできません" in rig.text()
    # type 22 の保存でも 0x70xx の「変更済み」は消えない(電源を切ると戻る。HW-6)
    rig.params.clear()
    b.save_params(M1)
    go(qtbot, rig, lambda: "モータに保存しました(type 22)" in rig.text())
    assert "Flash" not in rig.text().split("パラメータをモータに保存")[-1]


# ------------------------------------------------------------------ 故障
def test_fault_injection_display_and_clear(qtbot, rig: Rig) -> None:
    b = rig.b
    b.inject_fault(M1, "overtemp")
    go(qtbot, rig, lambda: rig.ui() == "fault")
    assert "overtemp" in rig.st().faults
    det = b.fault_details(M1)
    assert [d.key for d in det] == ["overtemp"]
    d = det[0]
    assert d.name == tr("fault.overtemp") and d.count == 1 and d.remedy and d.causes
    assert d.threshold == pytest.approx(135.0) and d.unit == "℃"
    assert rig.ui(M127) == "disabled"  # 他のモータには影響しない
    assert "故障を検出しました" in rig.text()
    # 故障中は有効化できない
    b.enable([M1], 2)
    go(qtbot, rig, lambda: "有効化に失敗" in rig.text())
    b.clear_fault([M1])
    go(qtbot, rig, lambda: rig.ui() == "disabled")
    assert rig.st().faults == () and b.fault_details(M1) == []
    # 再発すると件数が増える
    b.inject_fault(M1, "overtemp")
    go(qtbot, rig, lambda: rig.ui() == "fault")
    assert b.fault_details(M1)[0].count == 1  # クリアで履歴は消えている(同じ発生の 1 件目)
    b.clear_fault([M1])
    go(qtbot, rig, lambda: rig.ui() == "disabled")


def test_fault_while_running(qtbot, rig: Rig) -> None:
    enable(qtbot, rig, M1, 2)
    rig.b.start_controller("velocity", {"motor": M1}, VelocityParams(velocity=2.0))
    go(qtbot, rig, lambda: rig.ui() == "running")
    rig.b.inject_fault(M1, "overload")
    go(qtbot, rig, lambda: rig.ui() == "fault")
    assert "overload" in rig.st().faults and rig.st().controller is None
    rig.b.clear_fault([M1])
    go(qtbot, rig, lambda: rig.ui() == "disabled")


# ------------------------------------------------------------------ 通信断
def test_comm_loss_banner_and_reconnect(qtbot, rig: Rig) -> None:
    b = rig.b
    enable(qtbot, rig, M1, 2)
    rig.b.start_controller("velocity", {"motor": M1}, VelocityParams(velocity=1.0))
    go(qtbot, rig, lambda: rig.ui() == "running")
    b.inject_comm_loss()
    go(qtbot, rig, lambda: b.connection_info().lost, timeout=10000)
    info = b.connection_info()
    assert not info.connected and info.lost
    go(qtbot, rig, lambda: rig.ui() == "offline")
    assert rig.conns[-1].lost
    assert b.power_tier() == "low" and not b.estop_latched()
    assert "通信が途絶えました" in rig.text()
    # 再接続
    b.reconnect()
    go(qtbot, rig, lambda: b.connection_info().connected and len(b.motors()) == 2, timeout=10000)
    assert not b.connection_info().lost
    go(qtbot, rig, lambda: rig.ui() == "disabled" and rig.ui(M127) == "disabled")


# ------------------------------------------------------------------ 記録・グラフ・フレーム・統計
def test_recording_file(qtbot, rig: Rig, tmp_path: Path) -> None:
    b = rig.b
    enable(qtbot, rig, M1, 2)
    b.start_controller("velocity", {"motor": M1}, VelocityParams(velocity=1.0))
    go(qtbot, rig, lambda: rig.ui() == "running")
    path = tmp_path / "rec" / "rec.csv"
    b.start_recording(path, None)
    qtbot.waitUntil(lambda: b.recording_rows() > 30, timeout=WAIT)
    out = b.stop_recording()
    assert out == path and path.exists() and b.recording_rows() == 0
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# edulite05 telemetry recording")
    header = next(ln for ln in lines if not ln.startswith("#"))
    assert header.startswith("time_s,") and "m1.vel" in header and "m1.cmd.vel" in header
    data = [ln for ln in lines if not ln.startswith("#")][1:]
    assert len(data) > 30
    b.stop_controller("velocity", {"motor": M1})
    disable(qtbot, rig, M1)
    assert "記録を保存しました" in rig.text()
    # 行が 1 つも無ければファイルを残さない
    path2 = tmp_path / "rec" / "empty.csv"
    b.start_recording(path2, ["m127.vel"])
    assert b.stop_recording() in (None, path2)
    assert b.stop_recording() is None


def test_current_mode_recorded_plotted_speed_limited(qtbot, rig: Rig, tmp_path: Path) -> None:
    """電流モード: cmd.cur / iqf が記録・グラフのチャネルに載り、暴走ガードで速度が上限付近に収まる
    (ファームの limit_spd は電流モードに効かない。実機 LOW で 23.9 rad/s まで加速した)。"""
    b = rig.b
    names = {c.name for c in b.available_channels()}
    assert {"m1.cmd_current", "m1.current"} <= names
    assert {c.kind for c in b.available_channels() if c.name == "m1.cmd_current"} == {"current"}
    enable(qtbot, rig, M1, 3)
    path = tmp_path / "cur.csv"
    b.start_recording(path, None)
    b.start_controller("current", {"motor": M1}, CurrentParams(iq=0.3))
    go(qtbot, rig, lambda: rig.ui() == "running")
    peak = 0.0
    for _ in range(40):
        qtbot.wait(50)
        peak = max(peak, abs(rig.st().velocity))
    assert 1.0 < peak < 1.5 * b.power_limits().speed, peak
    t, v = b.scope_data(["m1.cmd_current"], 5.0)["m1.cmd_current"]
    assert len(t) > 20 and float(v.min()) < 0.0 < float(v.max())  # 押す向きと制動の向き
    assert b.stop_recording() == path
    rows = path.read_text(encoding="utf-8").splitlines()
    header = next(ln for ln in rows if not ln.startswith("#"))
    assert "m1.cmd.cur" in header and "m1.iqf" in header
    b.stop_controller("current", {"motor": M1})
    disable(qtbot, rig, M1)


def test_power_estimate_in_state_scope_and_recording(qtbot, rig: Rig, tmp_path: Path) -> None:
    """消費電力の概算(未較正): 状態の power、チャネル m1.power、記録 CSV の m1.power_est。"""
    b = rig.b
    assert any(c.name == "m1.power" and c.unit == "W" and c.kind == "power"
               for c in b.available_channels())
    go(qtbot, rig, lambda: rig.st().power is not None)
    assert rig.st().power.p_base == pytest.approx(1.0)  # 停止中は待機消費だけ
    path = tmp_path / "pw.csv"
    b.start_recording(path, None)
    enable(qtbot, rig, M1, 2)
    b.start_controller("velocity", {"motor": M1}, VelocityParams(velocity=3.0, accel=50.0))
    go(qtbot, rig, lambda: rig.ui() == "running" and rig.st().velocity > 2.0)
    qtbot.wait(400)
    t, v = b.scope_data(["m1.power"], 5.0)["m1.power"]
    assert len(t) > 20 and float(v.min()) >= 0.9 and float(v.max()) > 1.0
    assert b.stop_recording() == path
    rows = path.read_text(encoding="utf-8").splitlines()
    header = next(ln for ln in rows if not ln.startswith("#"))
    assert "m1.power_est" in header
    b.stop_controller("velocity", {"motor": M1})
    disable(qtbot, rig, M1)


def test_scope_data_relative_times(qtbot, rig: Rig) -> None:
    b = rig.b
    enable(qtbot, rig, M1, 2)
    b.start_controller("velocity", {"motor": M1}, VelocityParams(velocity=1.0))
    go(qtbot, rig, lambda: rig.ui() == "running" and rig.st().velocity > 0.5)
    qtbot.wait(400)
    data = b.scope_data(["m1.pos", "m1.vel", "m1.torque", "m1.cmd_vel", "m127.vel", "nope"], 5.0)
    assert set(data) == {"m1.pos", "m1.vel", "m1.torque", "m1.cmd_vel", "m127.vel"}
    t, v = data["m1.vel"]
    assert len(t) == len(v) > 20 and t[-1] <= 0.0 and t[0] >= -5.0 and t[0] < -0.2
    assert float(v[-1]) > 0.3
    tc, vc = data["m1.cmd_vel"]
    assert len(tc) > 10 and float(vc[-1]) == pytest.approx(1.0, abs=0.05)
    b.clear_scope()
    assert b.scope_data(["m1.vel"], 5.0)["m1.vel"][0].size == 0
    b.stop_controller("velocity", {"motor": M1})
    disable(qtbot, rig, M1)


def test_frames_rate_limited_and_stats(qtbot, rig: Rig) -> None:
    b = rig.b
    enable(qtbot, rig, M1, 2)
    enable(qtbot, rig, M127, 2)
    b.start_controller("velocity", {"motor": M1}, VelocityParams(velocity=1.0))
    b.start_controller("velocity", {"motor": M127}, VelocityParams(velocity=1.0))
    go(qtbot, rig, lambda: rig.ui() == "running" and rig.ui(M127) == "running")
    qtbot.wait(300)
    n0 = len(rig.frames)
    qtbot.wait(2000)
    n = len(rig.frames) - n0
    assert 50 < n <= 200 * 2.0 + 80  # 毎秒 200 件まで(バーストの余裕)
    e = rig.frames[-1]
    assert e.direction in ("TX", "RX") and len(e.data) == 8
    # 間引きは件数制限で、最後の 40 件が RX だけになることがある(以前は不定期に失敗)。
    # 2 秒の窓全体で TX と RX の両方が出ていることを見る
    window = rig.frames[n0:]
    assert {f.direction for f in window} == {"TX", "RX"}
    assert any(f.decoded_text for f in window)
    go(qtbot, rig, lambda: len(rig.stats) >= 2)
    s = rig.stats[-1]
    assert s.tx_fps > 100 and s.rx_fps > 100  # 間引く前の実レート
    assert s.loop_hz > 100 and s.retries is not None and s.parse_errors == 0
    assert s.frames_dropped > 0  # 間引いた件数
    b.estop()


def test_run_tools(qtbot, rig: Rig) -> None:
    """保守操作は MotorService の保守 API へ。結果の日本語メッセージがログに出る。"""
    b = rig.b
    b.run_tool("set_zero", M1, {})
    go(qtbot, rig, lambda: tr("tool.set_zero.title") in rig.text().split("実行します")[-1])
    assert "失敗" not in rig.text()
    b.run_tool("can_timeout", M1, {"seconds": 1.0, "seconds_text": "1 s"})
    go(qtbot, rig, lambda: rig.text().count(tr("tool.can_timeout.title")) >= 2)
    b.run_tool("active_report", M1, {"on": True, "period_n": 1})
    go(qtbot, rig, lambda: rig.text().count(tr("tool.active_report.title")) >= 2)
    assert "未実装" not in rig.text()
    # 有効なモータには実行できない(core が拒否 → ログ)
    enable(qtbot, rig, M1, 2)
    b.run_tool("set_zero", M1, {})
    go(qtbot, rig, lambda: "に失敗しました" in rig.text())
    disable(qtbot, rig, M1)
    b.run_tool("nope", M1, {})


def test_set_zero_reports_result_and_breaks_the_position_line(qtbot, rig: Rig) -> None:
    """tool_finished で結果を知らせ、履歴の位置チャネルに NaN を挟んで 0 への跳びを線で結ばない。"""
    import numpy as np

    b = rig.b
    done: list[tuple] = []
    b.tool_finished.connect(lambda *a: done.append(a))
    # 少し回してから停止(位置が 0 でない状態にする)
    enable(qtbot, rig, M1, 2)
    b.start_controller("velocity", {"motor": M1}, VelocityParams(velocity=3.0, accel=50.0))
    go(qtbot, rig, lambda: rig.st().position_multi > 1.0)
    b.stop_controller("velocity", {"motor": M1})
    disable(qtbot, rig, M1)
    before = rig.st().position_multi
    assert before > 1.0
    b.run_tool("set_zero", M1, {})
    go(qtbot, rig, lambda: bool(done))
    tool, mid, ok, _msg = done[-1]
    assert (tool, mid, ok) == ("set_zero", M1, True)
    go(qtbot, rig, lambda: abs(rig.st().position_multi) < 0.1)
    tail_finite = lambda: bool(np.isfinite(b.scope_data(["m1.pos"], 10.0)["m1.pos"][1][-3:]).all())  # noqa: E731
    go(qtbot, rig, tail_finite)
    _t, v = b.scope_data(["m1.pos"], 10.0)["m1.pos"]
    bad = np.flatnonzero(~np.isfinite(v))
    assert bad.size >= 1, "位置の履歴に NaN の切れ目が無い"
    assert v[int(bad[0]) - 1] > 0.5 and abs(v[np.isfinite(v)][-1]) < 0.1  # 前は旧、後は新座標
    assert abs(v[int(bad[-1]) + 1]) < 0.1  # 最後の NaN の直後はもう新しい座標
    fin = np.flatnonzero(np.isfinite(v))
    adjacent = np.diff(fin) == 1  # 隣り合う有限なサンプルの間に、0 への跳びの線が残っていない
    assert np.all(np.abs(np.diff(v[fin]))[adjacent] < 0.5)
    # 失敗(有効なモータ)も tool_finished(ok=False)で知らせる
    enable(qtbot, rig, M1, 2)
    n = len(done)
    b.run_tool("set_zero", M1, {})
    go(qtbot, rig, lambda: len(done) > n)
    assert done[-1][:2] == ("set_zero", M1) and done[-1][2] is False and done[-1][3]
    disable(qtbot, rig, M1)


def test_set_zero_far_from_origin_leaves_no_jump_line(qtbot, monkeypatch) -> None:
    """多回転 +400 rad からの原点設定: 応答の type 2 は旧い展開オフセットのまま(~+400)だが、
    NaN で孤立させるので、400 → 0 の縦線は残らない。読み値も 0 になる。"""
    import numpy as np

    import edulite05.sim.transport as st

    orig = st.make_motors
    monkeypatch.setattr(
        st, "make_motors", lambda n=1, ids=None, profile=None, **kw: orig(
            n, ids, profile, initial_position=400.0, **kw
        ),
    )
    b = CoreBridge(kind="simulator", sim_motors=2, start_offline=True)
    r = Rig(b)
    b.start()
    try:
        b.connect_adapter({"adapter": "sim", "n": 2})
        qtbot.waitUntil(lambda: len(b.motors()) == 2 and r.ui(M1) == "disabled", timeout=WAIT)
        qtbot.waitUntil(lambda: r.st().position_multi > 399.0, timeout=WAIT)
        done: list[tuple] = []
        b.tool_finished.connect(lambda *a: done.append(a))
        qtbot.wait(300)
        b.run_tool("set_zero", M1, {})
        qtbot.waitUntil(lambda: bool(done), timeout=WAIT)
        assert done[-1][:3] == ("set_zero", M1, True)
        qtbot.waitUntil(lambda: abs(r.st().position_multi) < 0.1, timeout=WAIT)
        qtbot.wait(400)
        _t, v = b.scope_data(["m1.pos"], 10.0)["m1.pos"]
        assert np.isnan(v).any()
        v = v[int(np.flatnonzero(np.isnan(v))[0]) - 2:]  # 接続直後の較正(0 → 400)の跳びは対象外
        fin = np.flatnonzero(np.isfinite(v))
        adjacent = np.diff(fin) == 1
        assert np.all(np.abs(np.diff(v[fin]))[adjacent] < 5.0), "400 → 0 の跳びが線で結ばれている"
        assert v[fin][0] > 300 and abs(v[fin][-1]) < 0.1
    finally:
        b.shutdown()


def test_power_limits_expose_mit_host_side_caps(rig: Rig) -> None:
    lim = rig.b.power_limits()
    assert (lim.kd_negative_max, lim.friction_comp_max) == (0.05, 0.1)


def test_set_can_id_updates_motor_list(qtbot, rig: Rig) -> None:
    b = rig.b
    b.run_tool("set_can_id", M1, {"new_id": 5})
    go(qtbot, rig, lambda: [m.id for m in b.motors()] == [5, M127])
    assert b.selected_motor() == 5
    go(qtbot, rig, lambda: rig.ui(5) == "disabled" and 1 not in b.states())
    enable(qtbot, rig, 5, 2)  # 新 ID で運転できる
    disable(qtbot, rig, 5)


def test_actions_when_offline(qtbot) -> None:
    b = CoreBridge(kind="hardware", start_offline=True)
    r = Rig(b)
    b.start()
    assert b.backend_kind == "hardware" and not b.connection_info().connected
    b.enable([1], 2)
    b.scan()
    b.estop()
    assert not b.set_power_tier("high")
    assert r.ui() == "none" and b.power_limits().tier == "low"
    assert b.start_recording("x.csv", None) is None
    assert b.stop_recording() is None
    assert b.scope_data(["m1.vel"], 5.0) == {}
    assert "未接続です" in r.text()
    b.shutdown()


def test_connect_failure_returns_to_offline(qtbot) -> None:
    """存在しない COM ポートへの接続は失敗して未接続へ戻る(実機の COM7 には触れない)。"""
    b = CoreBridge(kind="hardware", start_offline=True)
    r = Rig(b)
    b.start()
    b.connect_adapter({"adapter": "usbcan", "port": "COM_DOES_NOT_EXIST"})
    qtbot.waitUntil(lambda: "接続できませんでした" in r.text(), timeout=WAIT)
    assert not b.connection_info().connected and not b.connection_info().lost
    b.connect_adapter({"adapter": "sim", "n": 1})  # 失敗の後でもつなげる
    qtbot.waitUntil(lambda: b.connection_info().connected and len(b.motors()) == 1, timeout=WAIT)
    assert b.backend_kind == "simulator"
    b.disconnect_adapter()
    assert b.backend_kind == "hardware"
    b.shutdown()


@pytest.mark.parametrize(
    "argv, sim, demo",
    [([], None, False), (["--sim"], 2, False), (["--sim", "3"], 3, False),
     (["--demo"], None, True), (["--demo", "--sim"], 2, True),
     (["--sim", "--start-offline"], 2, False)],
)
def test_main_argument_parsing(argv, sim, demo) -> None:
    from edulite05.app.main import _parse

    args, rest = _parse(argv)
    assert args.sim == sim and args.demo is demo and rest == []


def test_auto_connect_simulator(qtbot) -> None:
    """kind="simulator"(--sim)は start() で自動接続する。"""
    b = CoreBridge(kind="simulator", sim_motors=1)
    r = Rig(b)
    b.start()
    qtbot.waitUntil(lambda: len(b.motors()) == 1 and r.ui(127) == "disabled", timeout=WAIT)
    assert b.motors()[0].id == 127 and b.selected_motor() == 127
    b.shutdown()


# ---------------------------------------------------------------- 画面全体(MainWindow + 運転ページ)
def test_main_window_runs_on_core(qtbot, tmp_path: Path) -> None:
    from edulite05.app.main_window import MainWindow
    from edulite05.app.ui.style import theme_manager

    theme_manager().apply("dark")
    b = CoreBridge(kind="simulator", sim_motors=2, start_offline=False)
    r = Rig(b)
    win = MainWindow(b, settings_path=None, enable_3d=False)
    qtbot.addWidget(win)
    win.resize(1536, 912)
    win.show()
    b.start()
    qtbot.waitUntil(lambda: len(b.motors()) == 2 and r.ui(M1) == "disabled", timeout=WAIT)
    b.enable([M1], 2)
    qtbot.waitUntil(lambda: r.ui() == "enabled", timeout=WAIT)
    b.start_controller("velocity", {"motor": M1}, VelocityParams(velocity=2.0))
    qtbot.waitUntil(lambda: r.ui() == "running" and r.st().velocity > 1.0, timeout=WAIT)
    qtbot.wait(1200)  # グラフ・読み値・インスペクタが実データで更新される
    for page in ("params", "diagnostics", "maintenance", "operate"):
        win.navigate(page)
        qtbot.wait(300)
    b.estop()
    qtbot.waitUntil(lambda: r.ui() == "disabled", timeout=WAIT)
    win.close()
    b.shutdown()
