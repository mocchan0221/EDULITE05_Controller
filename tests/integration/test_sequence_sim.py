"""シーケンスの実行(SequenceRunner)と CLI `run-sequence` を SimTransport に対して試す統合テスト。

実時間で動くので判定は緩め。確認するのは「どの終わり方でも必ず停止・無効化される」こと:
完了・途中の中断・エラー(故障 / 開始前の確認 / 後始末の失敗)・非常停止。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from edulite05.cli import main as cli
from edulite05.cli import sequence_cmd
from edulite05.cli.main import main
from edulite05.core import clock
from edulite05.core.control.service import MotorService, UiState
from edulite05.core.device.profile import MotorProfile
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.safety import PowerTier
from edulite05.core.sequence import (
    SequenceEvent,
    SequenceRunner,
    SequenceState,
    parse_sequence,
)
from edulite05.core.session import Session
from edulite05.sim.transport import SimTransport, make_motors

PROFILE = MotorProfile.load_builtin("el05")
A = 127


def wait_until(pred: Callable[[], object], timeout: float = 4.0) -> bool:
    end = clock.now() + timeout
    while clock.now() < end:
        if pred():
            return True
        time.sleep(0.005)
    return bool(pred())


class Rig:
    def __init__(self, n: int = 1, **sim_kwargs) -> None:
        self.sim = SimTransport(make_motors(n, profile=PROFILE), **sim_kwargs)
        self.session = Session(self.sim, PROFILE)
        self.session.open()
        self.svc = MotorService(self.session)
        for m in self.sim.motors:
            self.session.motor(m.motor_id)
        self.svc.open()
        assert wait_until(lambda: self.svc.ui_state(A) is UiState.DISABLED)
        self.events: list[SequenceEvent] = []

    def runner(self, text: str, motor: int = A, **kw) -> SequenceRunner:
        seq = parse_sequence(text, source="t.yaml")
        kw.setdefault("on_event", self.events.append)
        return SequenceRunner(self.svc, seq, motor, **kw)

    def motor(self, mid: int = A):
        return self.sim.motor(mid)

    def assert_safe(self, mid: int = A) -> None:
        assert wait_until(lambda: self.motor(mid).mode is MotorMode.RESET), "無効化されていない"
        assert self.svc.ui_state(mid) in (UiState.DISABLED, UiState.FAULT)
        assert self.svc.controller_of(mid) is None

    def close(self) -> None:
        self.svc.close()
        self.session.close()


@pytest.fixture
def rig() -> Iterator[Rig]:
    r = Rig()
    yield r
    r.close()


SHORT = """\
steps:
  - enable: velocity
  - run: {controller: velocity, params: {velocity: 2.0, accel: 50}, seconds: 0.6}
  - wait: 0.1
  - disable
"""
LONG = """\
steps:
  - enable: velocity
  - run: {controller: velocity, params: {velocity: 2.0, accel: 50}, seconds: 30}
  - disable
"""


# ---------------------------------------------------------------- 正常
def test_normal_run_completes_and_leaves_motor_disabled(rig: Rig) -> None:
    peak = []
    r = rig.runner(
        SHORT,
        on_event=lambda e: (rig.events.append(e), peak.append(abs(rig.motor().velocity))),
    )
    res = r.run()
    assert res.state is SequenceState.COMPLETED and res.ok and res.steps_done == 4
    assert res.stopped and not res.problems
    kinds = [e.kind for e in rig.events]
    assert kinds[0] == "started" and kinds[-1] == "finished"
    assert [e.index for e in rig.events if e.kind == "step"] == [0, 1, 2, 3]
    assert [e.index for e in rig.events if e.kind == "step_done"] == [0, 1, 2, 3]
    assert rig.events[-1].result is res
    rig.assert_safe()
    assert r.state is SequenceState.COMPLETED and not r.running
    # 実際に回った(run 中に速度が出ている)ことは state 側の記録で確認
    assert rig.motor().get_param("canTimeout") == 5000


def test_runs_in_worker_thread_and_reports_progress(rig: Rig) -> None:
    r = rig.runner(SHORT)
    t0 = time.perf_counter()
    r.start()
    assert time.perf_counter() - t0 < 0.5  # start() は待たない
    assert wait_until(lambda: r.progress().index == 1 and r.running)
    pr = r.progress()
    assert pr.total == 4 and pr.step_seconds == pytest.approx(0.6) and "運転" in pr.text
    assert wait_until(lambda: abs(rig.motor().velocity) > 0.5)  # 回っている
    assert r.join(10.0)
    assert r.result is not None and r.result.ok
    with pytest.raises(RuntimeError):
        r.start()  # 再利用は不可
    rig.assert_safe()


def test_set_params_updates_running_target(rig: Rig) -> None:
    r = rig.runner(
        "steps:\n  - enable: velocity\n"
        "  - start: {controller: velocity, params: {velocity: 1.0, accel: 100}}\n"
        "  - wait: 0.5\n  - set_params: {velocity: -2.0}\n  - wait: 0.7\n  - disable\n"
    )
    seen: list[float] = []
    r._on_event = lambda e: seen.append(rig.motor().velocity) if e.kind == "step" else None
    res = r.run()
    assert res.ok
    assert any(v > 0.5 for v in seen[2:4]) and seen[-1] < -1.0  # 切替前は正・切替後は負
    rig.assert_safe()


def test_record_start_stop_writes_csv(rig: Rig, tmp_path: Path) -> None:
    r = rig.runner(
        "steps:\n  - enable: velocity\n  - record: {action: start, path: a.csv}\n"
        "  - run: {controller: velocity, params: {velocity: 1.0}, seconds: 0.5}\n"
        "  - record: stop\n  - disable\n",
        recordings_dir=tmp_path,
    )
    res = r.run()
    assert res.ok and res.recording == tmp_path / "a.csv"
    lines = (tmp_path / "a.csv").read_text(encoding="utf-8").splitlines()
    assert any(ln.startswith("time_s,") for ln in lines)
    assert len([ln for ln in lines if ln and not ln.startswith("#")]) > 20
    rig.assert_safe()


def test_clamp_is_logged_and_speed_limited(rig: Rig) -> None:
    r = rig.runner(
        "steps:\n  - enable: velocity\n"
        "  - run: {controller: velocity, params: {velocity: 9.0, accel: 100}, seconds: 0.8}\n"
        "  - disable\n"
    )
    peak = [0.0]
    r._on_event = lambda e: (
        rig.events.append(e), peak.__setitem__(0, max(peak[0], abs(rig.motor().velocity)))
    )
    res = r.run()
    assert res.ok
    assert any(e.kind == "log" and "制限" in e.text for e in rig.events)
    assert peak[0] <= 5.2  # LOW の速度上限(5 rad/s)
    assert any(w.warning for w in r.sequence.warnings)


# ---------------------------------------------------------------- 中断
def test_abort_mid_run_stops_and_disables(rig: Rig) -> None:
    r = rig.runner(LONG)
    r.start()
    assert wait_until(lambda: r.progress().index == 1 and abs(rig.motor().velocity) > 0.5)
    t0 = time.perf_counter()
    r.abort("テスト")
    assert r.join(5.0)
    assert time.perf_counter() - t0 < 2.0  # 30 秒を待たずに止まる
    res = r.result
    assert res is not None and res.state is SequenceState.ABORTED and res.stopped
    assert "テスト" in res.message and res.steps_done == 1
    rig.assert_safe()
    assert abs(rig.motor().velocity) < 0.5
    assert not res.ok


def test_abort_is_idempotent_and_safe_before_and_after(rig: Rig) -> None:
    r = rig.runner(SHORT)
    r.abort()  # 開始前: 何もしない
    r.start()
    r.abort()
    r.abort()
    assert r.join(8.0)
    assert r.result is not None and r.result.state is SequenceState.ABORTED
    r.abort()  # 終了後: 何もしない
    rig.assert_safe()


def test_abort_during_enable_wait(rig: Rig) -> None:
    r = rig.runner(LONG)
    r.start()
    assert wait_until(lambda: r.progress().index == 0)
    r.abort()
    assert r.join(8.0)
    assert r.result is not None and r.result.state is SequenceState.ABORTED
    rig.assert_safe()


def test_abort_while_recording_closes_file(rig: Rig, tmp_path: Path) -> None:
    r = rig.runner(
        "steps:\n  - enable: velocity\n  - record: {action: start, path: b.csv}\n"
        "  - wait: 30\n  - disable\n",
        recordings_dir=tmp_path,
    )
    r.start()
    assert wait_until(lambda: r.progress().index == 2)
    time.sleep(0.2)
    r.abort()
    assert r.join(8.0)
    assert (tmp_path / "b.csv").stat().st_size > 0
    (tmp_path / "b.csv").unlink()  # 閉じられている(Windows で削除できる)
    rig.assert_safe()


# ---------------------------------------------------------------- エラー
def test_fault_during_run_fails_and_cleans_up(rig: Rig) -> None:
    r = rig.runner(LONG)
    r.start()
    assert wait_until(lambda: r.progress().index == 1 and abs(rig.motor().velocity) > 0.5)
    rig.sim.inject_fault(A, 2)  # 低電圧
    assert r.join(8.0)
    res = r.result
    assert res is not None and res.state is SequenceState.FAILED and "故障" in res.message
    assert wait_until(lambda: rig.motor().mode is MotorMode.RESET)
    assert rig.svc.controller_of(A) is None


def test_runtime_error_in_step_is_failure_not_crash(rig: Rig) -> None:
    # sine の target は運転中に変えられない(検証をすり抜けた場合の実行時エラー)
    seq = parse_sequence(
        "steps:\n  - enable: velocity\n"
        "  - start: {controller: sine, params: {target: velocity, amplitude: 0.2}}\n"
        "  - wait: 0.2\n  - disable\n",
        source="t",
    )
    from edulite05.core.sequence.schema import SetParamsStep

    steps = list(seq.steps)
    steps.insert(3, SetParamsStep(99, {"target": "current"}))
    import dataclasses

    seq2 = dataclasses.replace(seq, steps=tuple(steps))
    r = SequenceRunner(rig.svc, seq2, A, on_event=rig.events.append)
    res = r.run()
    assert res.state is SequenceState.FAILED and "target" in res.message
    rig.assert_safe()


def test_precheck_failures_send_nothing(rig: Rig) -> None:
    # 高出力のとき
    assert rig.svc.set_tier(PowerTier.HIGH)
    res = rig.runner(SHORT).run()
    assert res.state is SequenceState.FAILED and "高出力" in res.message and not res.stopped
    # allow_high=True なら実行できる(明示)
    res = rig.runner(SHORT, allow_high=True).run()
    assert res.state is SequenceState.COMPLETED
    assert rig.svc.set_tier(PowerTier.LOW)
    # すでに有効なモータ(誰かが運転中)は止めない
    rig.svc.enable(A, RunMode.VELOCITY).result(5)
    res = rig.runner(SHORT).run()
    assert res.state is SequenceState.FAILED and "停止中" in res.message and not res.stopped
    assert rig.svc.ui_state(A) is UiState.ENABLED  # 触っていない
    rig.svc.disable([A]).result(5)


def test_precheck_unknown_motor_id(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    from edulite05.core.sequence import runner as rn

    monkeypatch.setattr(rn, "ONLINE_WAIT_S", 0.3)
    res = rig.runner(SHORT, motor=5).run()
    assert res.state is SequenceState.FAILED and "応答がありません" in res.message
    assert not res.stopped


def test_precheck_estop_latched(rig: Rig) -> None:
    rig.svc.estop()
    res = rig.runner(SHORT).run()
    assert res.state is SequenceState.FAILED and "非常停止" in res.message


def test_cleanup_failure_falls_back_to_disable(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("stop_and_disable が壊れた")

    monkeypatch.setattr(rig.svc, "stop_and_disable", boom)
    res = rig.runner(
        "steps:\n  - enable: velocity\n"
        "  - run: {controller: velocity, params: {velocity: 1.0}, seconds: 0.3}\n"
    ).run()
    # 正常終了だが後始末の 1 手目が失敗 → 2 手目(disable)で無効化される
    assert res.state is SequenceState.COMPLETED and res.problems and not res.ok
    rig.assert_safe()


# ---------------------------------------------------------------- 非常停止
def test_estop_during_run_aborts_and_latch_stays(rig: Rig) -> None:
    r = rig.runner(LONG)
    r.start()
    assert wait_until(lambda: r.progress().index == 1 and abs(rig.motor().velocity) > 0.5)
    t0 = time.perf_counter()
    rig.svc.estop()
    assert r.join(5.0)
    assert time.perf_counter() - t0 < 2.0
    res = r.result
    assert res is not None and res.state is SequenceState.ESTOP and "非常停止" in res.message
    assert wait_until(lambda: rig.motor().mode is MotorMode.RESET)
    assert rig.svc.estop_latched  # ラッチは解除されない
    assert rig.svc.ui_state(A) is UiState.DISABLED
    assert not res.problems  # ラッチ中の後始末は何も送らない(失敗扱いにしない)
    rig.svc.release_estop()


def test_estop_from_other_thread_while_waiting(rig: Rig) -> None:
    r = rig.runner("steps:\n  - enable: velocity\n  - wait: 30\n  - disable\n")
    r.start()
    assert wait_until(lambda: r.progress().index == 1)
    threading.Timer(0.1, rig.svc.estop).start()
    assert r.join(5.0)
    assert r.result is not None and r.result.state is SequenceState.ESTOP
    assert wait_until(lambda: rig.motor().mode is MotorMode.RESET)


def test_comm_loss_is_failure_and_nothing_hangs(rig: Rig) -> None:
    r = rig.runner(LONG)
    r.start()
    assert wait_until(lambda: r.progress().index == 1)
    rig.svc.close()  # アダプタ喪失相当(サービスを閉じる)
    assert r.join(8.0)
    assert r.result is not None and r.result.state in (SequenceState.FAILED, SequenceState.ABORTED)


# ---------------------------------------------------------------- CLI
@pytest.fixture
def sim_cli(monkeypatch: pytest.MonkeyPatch) -> SimTransport:
    tr = SimTransport(make_motors(2))  # ID 127, 1
    monkeypatch.setattr(cli, "make_transport", lambda args: tr)
    return tr


def run_cli(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


T = ["--timeout", "0.1"]


def write(tmp_path: Path, text: str, name: str = "s.yaml") -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_cli_requires_yes_and_sends_nothing(sim_cli, capsys, tmp_path) -> None:
    f = write(tmp_path, SHORT)
    code, out, _ = run_cli(capsys, "run-sequence", f, "--id", "127", "--sim", *T)
    assert code == 1 and "--yes" in out
    assert sim_cli.stats().tx_frames == 0
    assert sim_cli.motor(127).mode is MotorMode.RESET


def test_cli_runs_to_completion(sim_cli, capsys, tmp_path) -> None:
    f = write(tmp_path, SHORT)
    code, out, err = run_cli(capsys, "run-sequence", f, "--id", "127", "--yes", "--sim", *T)
    assert code == 0, (out, err)
    assert "[1/4]" in out and "完了しました" in out and "無効化" in out
    assert sim_cli.motor(127).mode is MotorMode.RESET


def test_cli_validation_error_shows_line_and_sends_nothing(sim_cli, capsys, tmp_path) -> None:
    f = write(tmp_path, "steps:\n  - enable: velocity\n  - wait: abc\n")
    code, out, _ = run_cli(capsys, "run-sequence", f, "--id", "127", "--yes", "--sim", *T)
    assert code == 1 and "行 3" in out and "秒数" in out
    assert sim_cli.stats().tx_frames == 0


def test_cli_needs_motor_id(sim_cli, capsys, tmp_path) -> None:
    f = write(tmp_path, SHORT)
    code, _, err = run_cli(capsys, "run-sequence", f, "--yes", "--sim", *T)
    assert code == 1 and "ID" in err
    # YAML の motor が使われる
    f = write(tmp_path, "motor: 127\n" + SHORT, "m.yaml")
    code, out, _ = run_cli(capsys, "run-sequence", f, "--yes", "--sim", *T)
    assert code == 0


def test_cli_tier_high_is_rejected(sim_cli, capsys, tmp_path) -> None:
    f = write(tmp_path, SHORT)
    code, _, err = run_cli(
        capsys, "run-sequence", f, "--id", "127", "--yes", "--tier", "high", "--sim", *T
    )
    assert code == 1 and "tier" in err
    assert sim_cli.stats().tx_frames == 0


def test_cli_ctrl_c_aborts_and_disables(sim_cli, capsys, tmp_path, monkeypatch) -> None:
    f = write(tmp_path, LONG)
    real_join = SequenceRunner.join
    state = {"n": 0}

    def join(self, timeout=None):
        state["n"] += 1
        if state["n"] == 15 and self.progress().index >= 1:
            raise KeyboardInterrupt
        return real_join(self, timeout)

    monkeypatch.setattr(SequenceRunner, "join", join)
    code, out, err = run_cli(capsys, "run-sequence", f, "--id", "127", "--yes", "--sim", *T)
    assert code == 1 and "Ctrl+C" in out and "中断" in err
    assert sim_cli.motor(127).mode is MotorMode.RESET


def test_cli_missing_file(sim_cli, capsys, tmp_path) -> None:
    code, out, _ = run_cli(
        capsys, "run-sequence", str(tmp_path / "no.yaml"), "--id", "127", "--yes", "--sim", *T
    )
    assert code == 1 and "読めません" in out
    assert sim_cli.stats().tx_frames == 0


def test_sample_sequence_runs_on_sim(sim_cli, capsys, tmp_path) -> None:
    root = Path(__file__).resolve().parents[2]
    code, out, err = run_cli(
        capsys, "run-sequence", str(root / "sequences" / "velocity_steps.yaml"),
        "--id", "127", "--yes", "--records-dir", str(tmp_path), "--sim", *T,
    )
    assert code == 0, (out, err)
    assert list(tmp_path.glob("seq_*.csv"))
    assert sim_cli.motor(127).mode is MotorMode.RESET
    assert sequence_cmd.COMMANDS
