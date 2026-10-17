"""実機初試験で見つかった「運転終了時にモータが止まらない」競合の回帰テスト(phase2-fix)。

実機(user_data/hw2_velocity.log)の事象: CLI の `velocity` が終了時に
`無効化を確認できませんでした` で失敗し、停止後も spd_ref=0.5 が流れ続けた。原因は
  1. フレームログ(共有フォルダ上のファイル)の同期書込みが 1 行 約 3 ms で、TX/RX スレッドを
     詰まらせた(TX キューに古い制御指令が滞留、RX は 0.7 s 遅れ)。
  2. 停止指令・保持指令が古い指令の後ろに並び、disable 後も古い指令が送られた。
  3. 無効化の確認が「次に処理した type 2」(= 古い応答)を見て Reset 以外と判断し、再送を使い切った。
ここでは遅延・ジッタ付き Sim(実 BusManager キュー経由)+ 遅い frame log で再現する。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from edulite05.cli import main as cli
from edulite05.cli.main import main
from edulite05.core.protocol.types import MotorMode
from edulite05.core.transport import framelog
from edulite05.core.transport.base import CanFrame, Direction
from edulite05.core.transport.framelog import read_framelog
from edulite05.sim.transport import SimTransport, make_motors

MID = 127
T = ["--timeout", "0.1"]
SLOW_WRITE_S = 0.003  # 共有フォルダ(SMB)での 1 行書込み(実測 約 3 ms)

# 通信タイプ / パラメータ index(el05_protocol.md)
TYPE_ENABLE, TYPE_STOP, TYPE_MIT, TYPE_WRITE = 3, 4, 1, 18
IDX_SPD_REF, IDX_IQ_REF, IDX_LOC_REF = 0x700A, 0x7006, 0x7016


class SlowFile:
    """write に固定の待ちを入れるファイルオブジェクト(SMB の遅さの模擬)。"""

    def __init__(self, fh: Any, delay: float) -> None:
        self._fh = fh
        self._delay = delay

    def write(self, s: str) -> int:
        time.sleep(self._delay)
        return self._fh.write(s)

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def make_log_slow(monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = open

    def slow_open(*a: Any, **k: Any) -> Any:
        fh = real_open(*a, **k)
        mode = a[1] if len(a) > 1 else k.get("mode", "r")
        return SlowFile(fh, SLOW_WRITE_S) if "w" in mode or "a" in mode else fh  # 書込みだけ遅く

    monkeypatch.setattr(framelog, "open", slow_open, raising=False)


def make_sim(monkeypatch: pytest.MonkeyPatch, latency: float, jitter: float) -> SimTransport:
    tr = SimTransport(make_motors(1), latency_s=latency, jitter_s=jitter, seed=1)
    monkeypatch.setattr(cli, "make_transport", lambda args: tr)
    return tr


def ctype(f: CanFrame) -> int:
    return (f.can_id >> 24) & 0x1F


def target(f: CanFrame) -> int:
    return f.can_id & 0xFF


def write_of(f: CanFrame) -> tuple[int, float] | None:
    """type 18 の (index, float 値)。"""
    if ctype(f) != TYPE_WRITE:
        return None
    import struct

    return int.from_bytes(f.data[:2], "little"), struct.unpack("<f", f.data[4:8])[0]


def tx_frames(path: Path) -> list[CanFrame]:
    return [f for f in read_framelog(path) if f.direction is Direction.TX and target(f) == MID]


CASES: list[tuple[str, list[str], int | None]] = [
    ("velocity", ["velocity", str(MID), "--speed", "0.5", "--seconds", "1.5"], IDX_SPD_REF),
    ("jog", ["jog", str(MID), "--speed", "0.5", "--seconds", "1.5"], IDX_SPD_REF),
    ("current", ["current", str(MID), "--iq", "0.2", "--seconds", "1.5"], IDX_IQ_REF),
    (
        "csp",
        ["csp", str(MID), "--position", "1.5", "--speed-limit", "0.5", "--seconds", "1.5"],
        None,
    ),
    ("mit", ["mit", str(MID), "--kp", "2", "--kd", "0.2", "--seconds", "1.5"], None),
    (
        "sine",
        ["sine", str(MID), "--amplitude", "0.5", "--frequency", "1", "--seconds", "1.5"],
        None,
    ),
]


@pytest.mark.parametrize("slow", [False, True], ids=["fastlog", "slowlog"])
@pytest.mark.parametrize("name,argv,ref_idx", CASES, ids=[c[0] for c in CASES])
def test_cli_cleanup_is_clean_under_latency(
    name: str,
    argv: list[str],
    ref_idx: int | None,
    slow: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    if slow:
        make_log_slow(monkeypatch)
    tr = make_sim(monkeypatch, latency=0.002, jitter=0.001)
    log = tmp_path / "run.log"
    code = main([*argv, "--yes", "--sim", "--log", str(log), *T])
    cap = capsys.readouterr()

    # 1. 終了コード 0・警告なし・Reset で終わる
    assert code == 0, cap.err
    assert "後始末に失敗" not in cap.err and "警告: 後始末" not in cap.out
    assert tr.motor(MID).mode is MotorMode.RESET

    frames = tx_frames(log)
    enable_i = max(i for i, f in enumerate(frames) if ctype(f) == TYPE_ENABLE)
    after = frames[enable_i + 1 :]
    stops = [i for i, f in enumerate(after) if ctype(f) == TYPE_STOP]
    assert stops, "運転後に stop(type 4)が送られていない"
    first_stop = stops[0]

    # 2. 最初の stop より後に、制御指令(type 1 / type 18)は 1 本も送られない
    late = [f for f in after[first_stop:] if ctype(f) in (TYPE_MIT, TYPE_WRITE)]
    assert late == [], f"disable 後に制御フレームが送られた: {late[:3]}"

    # 3. stop の直前の制御指令は on_stop / Hold(速度・電流は 0)
    if ref_idx is not None:
        refs = [
            (i, w[1])
            for i, f in enumerate(after[:first_stop])
            if (w := write_of(f)) is not None and w[0] == ref_idx
        ]
        assert refs, "指令値の書込みが無い"
        assert refs[-1][1] == 0.0, f"停止指令(0)が stop の前に送られていない: {refs[-3:]}"
        nz = [i for i, v in refs if v != 0.0]
        assert nz, "運転中の指令が無い"
        # 0 を送った後に、運転中の指令(非 0)が再び流れない
        first_zero_after_run = next(i for i, v in refs if i > nz[0] and v == 0.0)
        assert not [1 for i, v in refs if i > first_zero_after_run and v != 0.0]

    # 4. RX フレームのタイムスタンプは受信時刻(増加している)
    rx = [f.timestamp for f in read_framelog(log) if f.direction is Direction.RX]
    assert len(rx) > 100
    assert rx == sorted(rx)
    assert len(set(rx)) > len(rx) * 0.5, "RX タイムスタンプがほぼ同一(受信時刻になっていない)"


# ---------------------------------------------------------------- MotorService 単体
class ServiceRig:
    def __init__(self, latency: float = 0.002, jitter: float = 0.001) -> None:
        from edulite05.core.device.profile import MotorProfile
        from edulite05.core.session import Session

        profile = MotorProfile.load_builtin("el05")
        self.sim = SimTransport(
            make_motors(1, profile=profile), latency_s=latency, jitter_s=jitter, seed=3
        )
        self.session = Session(self.sim, profile)
        self.session.open()
        self.session.motor(MID)
        from edulite05.core.control.service import MotorService

        self.svc = MotorService(self.session)
        self.svc.open()

    def close(self) -> None:
        self.svc.close()
        self.session.close()


@pytest.fixture
def srig() -> Any:
    r = ServiceRig()
    yield r
    r.close()


def _run_velocity(r: ServiceRig, speed: float = 0.5) -> None:
    from edulite05.core.control.controllers import Velocity
    from edulite05.core.control.params import VelocityParams
    from edulite05.core.protocol.types import RunMode

    r.svc.enable(MID, RunMode.VELOCITY).result(5.0)
    r.svc.start([MID], Velocity(VelocityParams(velocity=speed)))
    time.sleep(0.3)


def test_disable_confirmation_ignores_stale_feedback(srig: ServiceRig) -> None:
    """片道 30 ms の遅延で、type 4 の後にも運転中の指令への古い応答(mode=MOTOR)が届く。
    それを Reset の確認と取り違えず(かつ失敗扱いにもせず)、Reset の応答で完了する。"""
    srig.sim.latency_s = 0.03
    _run_velocity(srig)
    srig.svc.stop_motion([MID]).result(2.0)
    srig.svc.disable([MID]).result(5.0)  # 例外なし
    assert srig.sim.motor(MID).mode is MotorMode.RESET
    m = srig.svc.motor(MID)
    assert m.state.mode is MotorMode.RESET


def test_loop_is_silent_for_motor_once_disable_returns(srig: ServiceRig) -> None:
    _run_velocity(srig)
    fut = srig.svc.disable([MID])  # 戻った時点でループは当該モータ宛てに何も積まない
    tx0 = srig.sim.stats().tx_frames
    q0 = srig.session.bus.queue_len()
    time.sleep(0.15)
    frames_after = srig.sim.stats().tx_frames - tx0
    fut.result(5.0)
    # 送られたのは type 4 の再送程度(制御指令は 30 周期分流れていたはず)
    assert frames_after <= q0 + 6


def test_stop_motion_is_idempotent_and_safe_in_any_state(srig: ServiceRig) -> None:
    from edulite05.core.control.service import UiState
    from edulite05.core.protocol.types import RunMode

    svc, loop = srig.svc, srig.svc.loop
    svc.stop_motion([MID]).result(1.0)  # DISABLED: 何も起きない(例外なし)
    assert svc.ui_state(MID) not in (UiState.ENABLED, UiState.RUNNING)
    assert loop.handle_of(MID) is None
    srig.svc.enable(MID, RunMode.VELOCITY).result(5.0)
    svc.stop_motion([MID]).result(1.0)  # ENABLED(コントローラなし)
    svc.stop_motion([MID]).result(1.0)
    assert svc.ui_state(MID) is UiState.ENABLED
    _run_velocity_started(srig)
    svc.stop_motion([MID]).result(1.0)
    svc.stop_motion([MID]).result(1.0)  # 二重でも安全
    assert svc.ui_state(MID) is UiState.ENABLED and loop.handle_of(MID) is None


def _run_velocity_started(r: ServiceRig) -> None:
    from edulite05.core.control.controllers import Velocity
    from edulite05.core.control.params import VelocityParams

    r.svc.start([MID], Velocity(VelocityParams(velocity=0.5)))
    time.sleep(0.1)


def test_stop_motion_drops_controller_even_if_state_is_not_running(srig: ServiceRig) -> None:
    """サービスの状態が RUNNING でなくても、コントローラが残っていれば外して保持へ戻す。"""
    from edulite05.core.control.controllers import Velocity
    from edulite05.core.control.params import VelocityParams
    from edulite05.core.control.service import UiState
    from edulite05.core.protocol.types import RunMode

    svc, loop = srig.svc, srig.svc.loop
    svc.enable(MID, RunMode.VELOCITY).result(5.0)
    # サービスを介さず直接 attach(状態は ENABLED のまま = 食い違い)
    loop.attach(Velocity(VelocityParams(velocity=0.5)), {"motor": srig.session.motor(MID)})
    assert svc.ui_state(MID) is UiState.ENABLED and loop.handle_of(MID) is not None
    time.sleep(0.1)
    assert srig.sim.motor(MID).velocity > 0.2
    svc.stop_motion([MID]).result(1.0)
    assert loop.handle_of(MID) is None and svc.ui_state(MID) is UiState.ENABLED
    time.sleep(0.5)
    assert abs(srig.sim.motor(MID).velocity) < 0.1  # 速度 0 の保持に切り替わった
    svc.disable([MID]).result(5.0)
    assert srig.sim.motor(MID).mode is MotorMode.RESET
