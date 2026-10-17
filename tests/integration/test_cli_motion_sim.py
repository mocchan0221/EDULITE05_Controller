"""CLI の運動コマンド(jog / velocity / current / mit / csp / sine / estop)を Sim で実行するテスト。

どの終わり方(正常・Ctrl+C・例外)でも、モータが必ず無効化されることを確認する。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from edulite05.cli import main as cli
from edulite05.cli import motion
from edulite05.cli.main import main
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.sim.transport import SimTransport, make_motors

T = ["--timeout", "0.1"]


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


@pytest.fixture(params=[0.0, 0.05], ids=["clean", "reply-loss-5pct"])
def sim(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> SimTransport:
    """全テストを「応答欠落なし」と「応答の 5 % が欠落(実機の主動上報中の実測)」で実行する。"""
    # 欠落の並びは固定の種で決める。種なしだと、応答が続けて欠落する稀な並び(タイムアウト 0.1 s で
    # 再送しきれず終了コード 2)に当たって、全体を走らせたとき数百回に 1 回失敗した
    tr = SimTransport(make_motors(3), reply_drop_rate=request.param, seed=20261003)  # ID 127, 1, 2
    monkeypatch.setattr(cli, "make_transport", lambda args: tr)
    return tr


def hook_sleep(
    monkeypatch: pytest.MonkeyPatch, at: int, action: Callable[[int], None]
) -> list[int]:
    """表示ループの `_sleep` を `at` 回目に `action` へ差し替える(それ以外は実際に待つ)。"""
    calls: list[int] = []
    real = motion._sleep

    def fake(seconds: float) -> None:
        calls.append(1)
        n = len(calls)
        if n == at:
            action(n)
        real(seconds)

    monkeypatch.setattr(motion, "_sleep", fake)
    return calls


def raise_(exc: BaseException) -> Callable[[int], None]:
    def f(_n: int) -> None:
        raise exc

    return f


# ---------------------------------------------------------------- --yes / --tier
@pytest.mark.parametrize(
    "argv",
    [
        ["jog", "127"],
        ["velocity", "127"],
        ["current", "127", "--iq", "0.2"],
        ["mit", "127"],
        ["csp", "127", "--position", "0.5"],
        ["sine", "127"],
    ],
)
def test_requires_yes_and_sends_nothing(sim: SimTransport, capsys, argv) -> None:
    code, out, _ = run(capsys, *argv, "--sim", *T)
    assert code == 1 and "--yes" in out
    assert sim.motor(127).mode is MotorMode.RESET
    assert sim.stats().tx_frames == 0  # 何も送信していない


def test_tier_high_is_rejected(sim: SimTransport, capsys) -> None:
    code, _, err = run(capsys, "velocity", "127", "--yes", "--tier", "high", "--sim", *T)
    assert code == 1 and "--tier" in err
    assert sim.stats().tx_frames == 0


def test_bad_values_are_rejected_before_connecting(sim: SimTransport, capsys) -> None:
    code, _, err = run(capsys, "velocity", "127", "--speed", "nan", "--yes", "--sim", *T)
    assert code == 1 and "--speed" in err
    code, _, err = run(capsys, "sine", "127", "--frequency", "0", "--yes", "--sim", *T)
    assert code == 1 and "--frequency" in err
    assert sim.stats().tx_frames == 0


# ---------------------------------------------------------------- 各コマンド(正常終了)
def test_velocity_runs_and_ends_disabled(sim: SimTransport, capsys, monkeypatch) -> None:
    seen: dict[str, float] = {}

    def probe(_n: int) -> None:
        m = sim.motor(127)
        seen["mode"] = float(m.mode is MotorMode.MOTOR)
        seen["run_mode"] = float(m.run_mode)
        seen["limit_cur"] = float(m.get_param("limit_cur"))

    hook_sleep(monkeypatch, 4, probe)
    code, out, _ = run(
        capsys, "velocity", "127", "--speed", "2", "--seconds", "0.8", "--yes", "--sim", *T
    )
    assert code == 0, out
    assert seen == {"mode": 1.0, "run_mode": float(RunMode.VELOCITY), "limit_cur": 1.0}
    assert "運転開始" in out and "速度" in out and "運転停止 → 無効化しました" in out
    assert "出力段階 LOW" in out
    assert sim.motor(127).mode is MotorMode.RESET


def test_velocity_clamp_note_is_shown(sim: SimTransport, capsys) -> None:
    code, out, _ = run(
        capsys, "velocity", "127", "--speed", "20", "--seconds", "0.6", "--yes", "--sim", *T
    )
    assert code == 0
    assert "制限中" in out and "指令値 20→5" in out
    assert "出力段階 LOW の上限で制限しました" in out
    assert sim.motor(127).mode is MotorMode.RESET


def test_jog_forward_and_reverse(sim: SimTransport, capsys, monkeypatch) -> None:
    vel: list[float] = []
    hook_sleep(monkeypatch, 5, lambda _n: vel.append(sim.motor(1).velocity))
    code, out, _ = run(capsys, "jog", "1", "--speed", "1", "--seconds", "0.9", "--yes", "--sim", *T)
    assert code == 0 and vel[0] > 0.7
    vel.clear()
    hook_sleep(monkeypatch, 5, lambda _n: vel.append(sim.motor(1).velocity))
    code, out, _ = run(
        capsys, "jog", "1", "--speed", "1", "--reverse", "--seconds", "0.9", "--yes", "--sim", *T
    )
    assert code == 0 and vel[0] < -0.7 and "逆転" in out
    assert sim.motor(1).mode is MotorMode.RESET


def test_current_mode(sim: SimTransport, capsys, monkeypatch) -> None:
    iq: list[float] = []
    hook_sleep(monkeypatch, 4, lambda _n: iq.append(sim.motor(2).iq))
    code, out, _ = run(
        capsys, "current", "2", "--iq", "0.3", "--seconds", "0.7", "--yes", "--sim", *T
    )
    # 無負荷のシミュレータは 0.3 A で数十 ms のうちに LOW の 0.8·5 rad/s を超える。暴走ガードが
    # iq を制動方向へ置き換えるので、符号は押す側/制動側のどちらでもよい(大きさは 0.3 A)
    assert code == 0 and iq and abs(abs(iq[0]) - 0.3) < 0.05
    assert sim.motor(2).mode is MotorMode.RESET
    # 上限 1 A を超える要求はクランプされて表示される
    code, out, _ = run(
        capsys, "current", "2", "--iq", "3", "--seconds", "0.5", "--yes", "--sim", *T
    )
    assert code == 0 and "指令値 3→1" in out and sim.motor(2).mode is MotorMode.RESET


def test_mit_position_and_damping(sim: SimTransport, capsys, monkeypatch) -> None:
    mit: list[tuple[float, float, float]] = []
    hook_sleep(
        monkeypatch,
        4,
        lambda _n: mit.append(
            (sim.motor(127)._mit.kp, sim.motor(127)._mit.kd, sim.motor(127)._mit.position)
        ),
    )
    code, out, _ = run(
        capsys, "mit", "127", "--kp", "5", "--kd", "0.3", "--position", "0.4", "--seconds", "1.5",
        "--yes", "--sim", *T,
    )  # fmt: skip
    assert code == 0 and mit and mit[0][0] == pytest.approx(5.0, abs=0.1)
    assert abs(sim.motor(127).position - 0.4) < 0.1
    assert sim.motor(127).mode is MotorMode.RESET
    # --damping: Kp = 0、Kd だけ
    mit.clear()
    sim.motor(127).ext_torque = 0.2  # 手で回している想定
    hook_sleep(
        monkeypatch,
        4,
        lambda _n: mit.append(
            (sim.motor(127)._mit.kp, sim.motor(127)._mit.kd, sim.motor(127).velocity)
        ),
    )
    code, out, _ = run(
        capsys, "mit", "127", "--damping", "--kd", "0.5", "--seconds", "0.8", "--yes", "--sim", *T
    )
    sim.motor(127).ext_torque = 0.0
    assert code == 0 and "ダンピング" in out
    kp, kd, vel = mit[0]
    assert kp == 0.0 and kd == pytest.approx(0.5, abs=0.01) and vel > 0.05
    assert sim.motor(127).mode is MotorMode.RESET


def test_mit_defaults_to_holding_current_position(sim: SimTransport, capsys) -> None:
    sim.motor(127).position = 1.0
    code, out, _ = run(capsys, "mit", "127", "--kp", "5", "--seconds", "0.8", "--yes", "--sim", *T)
    assert code == 0 and abs(sim.motor(127).position - 1.0) < 0.1  # 0 へ飛ばない


def test_csp_relative_and_absolute(sim: SimTransport, capsys) -> None:
    m = sim.motor(127)
    m.position = 1.0
    m._sync_loc_ref()
    code, out, _ = run(
        capsys, "csp", "127", "--position", "0.5", "--relative", "--speed-limit", "1.0",
        "--seconds", "4", "--yes", "--sim", *T,
    )  # fmt: skip
    assert code == 0 and "目標位置に到達しました" in out and "相対" in out
    assert m.position == pytest.approx(1.5, abs=0.03)
    assert m.mode is MotorMode.RESET
    code, out, _ = run(
        capsys, "csp", "127", "--position", "1.0", "--speed-limit", "2.0", "--seconds", "4",
        "--yes", "--sim", *T,
    )  # fmt: skip
    assert code == 0 and m.position == pytest.approx(1.0, abs=0.03)


def test_sine_velocity_and_position(sim: SimTransport, capsys, monkeypatch) -> None:
    vs: list[float] = []

    def sample(_n: int) -> None:
        vs.append(sim.motor(127).velocity)

    real = motion._sleep

    def fake(s: float) -> None:
        sample(0)
        real(s)

    monkeypatch.setattr(motion, "_sleep", fake)
    code, out, _ = run(
        capsys, "sine", "127", "--target", "velocity", "--amplitude", "1", "--frequency", "1",
        "--seconds", "1.5", "--yes", "--sim", *T,
    )  # fmt: skip
    assert code == 0 and max(vs) > 0.6 and min(vs) < -0.6
    assert sim.motor(127).mode is MotorMode.RESET
    code, out, _ = run(
        capsys, "sine", "127", "--target", "position", "--amplitude", "0.3", "--frequency", "0.5",
        "--seconds", "1.0", "--yes", "--sim", *T,
    )  # fmt: skip
    assert code == 0 and sim.motor(127).mode is MotorMode.RESET


# ---------------------------------------------------------------- 後始末(Ctrl+C・例外・失敗)
@pytest.mark.parametrize(
    "argv",
    [
        ["velocity", "127", "--speed", "2"],
        ["jog", "127"],
        ["current", "127", "--iq", "0.3"],
        ["mit", "127", "--damping", "--kd", "0.5"],
        ["csp", "127", "--position", "1.0"],
        ["sine", "127", "--target", "velocity"],
    ],
)
def test_ctrl_c_stops_and_disables(sim: SimTransport, capsys, monkeypatch, argv) -> None:
    hook_sleep(monkeypatch, 4, raise_(KeyboardInterrupt()))
    code, out, err = run(capsys, *argv, "--seconds", "30", "--yes", "--sim", *T)
    assert code == 0 and "Ctrl+C" in out and "運転停止 → 無効化しました" in out
    assert sim.motor(127).mode is MotorMode.RESET
    assert sim.motor(127).get_param("canTimeout") == 5000  # 有効化手順を通った


def test_exception_during_run_still_disables(sim: SimTransport, capsys, monkeypatch) -> None:
    hook_sleep(monkeypatch, 3, raise_(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        main(["velocity", "127", "--speed", "2", "--seconds", "30", "--yes", "--sim", *T])
    assert sim.motor(127).mode is MotorMode.RESET
    capsys.readouterr()


def test_cleanup_waits_for_deceleration_before_disable(
    sim: SimTransport, capsys, monkeypatch
) -> None:
    """終了時は速度 0 指令の後、減速してから無効化する(直後の無効化で惰走した実測の対策)。"""
    m = sim.motor(127)
    at_disable: list[float] = []
    real = m.handle

    def handle(frame):  # type: ignore[no-untyped-def]
        if (frame.can_id >> 24) & 0x1F == 4 and m.mode is MotorMode.MOTOR:
            at_disable.append(abs(m.velocity))
        return real(frame)

    m.handle = handle  # type: ignore[method-assign]
    code, out, _ = run(
        capsys, "velocity", "127", "--speed", "3", "--accel", "10", "--seconds", "1.0", "--yes",
        "--sim", *T,
    )  # fmt: skip
    assert code == 0, out
    assert at_disable and at_disable[0] < 0.2  # 3 rad/s から減速してから type 4
    assert m.mode is MotorMode.RESET


def test_second_ctrl_c_during_cleanup_still_disables(
    sim: SimTransport, capsys, monkeypatch
) -> None:
    from edulite05.core.control.service import MotorService

    hook_sleep(monkeypatch, 3, raise_(KeyboardInterrupt()))
    real = MotorService.stop_motion
    state = {"n": 0}

    def flaky(self, ids):  # type: ignore[no-untyped-def]
        state["n"] += 1
        if state["n"] == 1:
            raise KeyboardInterrupt  # 後始末の最中の 2 回目の Ctrl+C
        return real(self, ids)

    monkeypatch.setattr(MotorService, "stop_motion", flaky)
    code, out, _ = run(
        capsys, "velocity", "127", "--speed", "2", "--seconds", "30", "--yes", "--sim", *T
    )
    assert code == 0 and state["n"] == 2
    assert sim.motor(127).mode is MotorMode.RESET


def test_stop_motion_failure_still_disables(sim: SimTransport, capsys, monkeypatch) -> None:
    from edulite05.core.control.service import MotorService

    def broken(self, ids):  # type: ignore[no-untyped-def]
        raise RuntimeError("stop_motion failed")

    monkeypatch.setattr(MotorService, "stop_motion", broken)
    hook_sleep(monkeypatch, 3, raise_(KeyboardInterrupt()))
    code, out, err = run(
        capsys, "velocity", "127", "--speed", "2", "--seconds", "30", "--yes", "--sim", *T
    )
    assert code == 1 and "stop_motion failed" in err
    assert sim.motor(127).mode is MotorMode.RESET  # 運転停止に失敗しても無効化する


def test_missing_motor_times_out_and_is_safe(sim: SimTransport, capsys) -> None:
    code, out, err = run(capsys, "velocity", "99", "--seconds", "1", "--yes", "--sim", *T)
    assert code == 2 and "タイムアウト" in err
    assert all(m.mode is MotorMode.RESET for m in sim.motors)


def test_fault_during_run_ends_with_error(sim: SimTransport, capsys, monkeypatch) -> None:
    hook_sleep(monkeypatch, 4, lambda _n: sim.inject_fault(127, 2))
    code, out, err = run(
        capsys, "velocity", "127", "--speed", "2", "--seconds", "5", "--yes", "--sim", *T
    )
    assert code == 1 and "運転が止まりました" in err and "fault" in err
    assert sim.motor(127).mode is MotorMode.RESET


# ---------------------------------------------------------------- estop
def test_estop_sends_stop_to_every_motor(sim: SimTransport, capsys) -> None:
    for i in (127, 1, 2):
        m = sim.motor(i)
        m.set_param("run_mode", 2)
        m.mode = MotorMode.MOTOR
    code, out, _ = run(capsys, "estop", "--sim", "3", *T)
    assert code == 0
    assert all(m.mode is MotorMode.RESET for m in sim.motors)
    assert "ID 127" in out and "ID 1:" in out and "ID 2:" in out
    assert "type 4" in out


def test_estop_with_ids_and_no_motor(sim: SimTransport, capsys) -> None:
    code, out, _ = run(capsys, "estop", "--ids", "10-12", "--sim", *T)
    assert code == 0 and "応答したモータはありません" in out
