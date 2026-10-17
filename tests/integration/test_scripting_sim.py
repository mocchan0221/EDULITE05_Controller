"""スクリプトコンソールのエンジンを実コア(MotorService + SimTransport)に接続して試す。

確認すること: コンソールから service を動かせる / 指令は SafetyGuard で制限される /
中断で運転停止 / 非常停止はスクリプト実行中でも効き、以後の指令は拒否される / run_sequence。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

import pytest

from edulite05.core import clock
from edulite05.core.control.service import MotorService, UiState
from edulite05.core.device.profile import MotorProfile
from edulite05.core.protocol.types import MotorMode
from edulite05.core.scripting import (
    ERR,
    OUT,
    RESULT,
    ScriptEngine,
    default_namespace,
    stop_all_motion,
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
    def __init__(self) -> None:
        self.sim = SimTransport(make_motors(1, profile=PROFILE))
        self.session = Session(self.sim, PROFILE)
        self.session.open()
        self.svc = MotorService(self.session)
        self.session.motor(A)
        self.svc.open()
        assert wait_until(lambda: self.svc.ui_state(A) is UiState.DISABLED)
        self.out: list[tuple[str, str]] = []
        self.engine = ScriptEngine(
            lambda: default_namespace(self.svc, None, self.engine),
            lambda k, t: self.out.append((k, t)),
            on_abort=lambda: stop_all_motion(self.svc),
        )

    def text(self, kind: str) -> str:
        return "".join(t for k, t in self.out if k == kind)

    def run(self, code: str, timeout: float = 10.0) -> None:
        assert self.engine.execute(code)
        assert self.engine.join(timeout)

    def close(self) -> None:
        self.engine.abort()
        self.svc.close()
        self.session.close()


@pytest.fixture
def rig() -> Iterator[Rig]:
    r = Rig()
    yield r
    r.close()


START = (
    "wait(service.enable(127, RunMode.VELOCITY), 5)\n"
    "service.start(127, make('velocity', velocity={v}, accel=100))\n"
)


def test_console_drives_motor_through_service(rig: Rig) -> None:
    rig.run(START.format(v=1.5) + "sleep(0.6)\nservice.ui_state(127).value")
    assert rig.text(RESULT) == "'running'\n"
    assert abs(rig.sim.motor(A).velocity - 1.5) < 0.3
    rig.run("service.stop_motion(127)\nsleep(0.3)\nwait(service.disable(127), 5)\n"
            "service.ui_state(127).value")
    assert "'disabled'" in rig.text(RESULT)
    assert rig.sim.motor(A).mode is MotorMode.RESET


def test_safetyguard_still_limits_commands_from_console(rig: Rig) -> None:
    rig.run(START.format(v=40.0) + "sleep(0.8)\nmax_v = telemetry.latest('m127.vel')[1]\nmax_v")
    assert wait_until(lambda: abs(rig.sim.motor(A).velocity) > 3.0)
    assert abs(rig.sim.motor(A).velocity) <= 5.2  # LOW の速度上限 5 rad/s
    assert rig.engine.namespace["max_v"] <= 5.2


def test_abort_stops_motion_and_keeps_enabled(rig: Rig) -> None:
    assert rig.engine.execute(START.format(v=2.0) + "sleep(30)")
    assert wait_until(lambda: abs(rig.sim.motor(A).velocity) > 1.0)
    rig.engine.abort()
    assert rig.engine.join(3.0)
    assert wait_until(lambda: abs(rig.sim.motor(A).velocity) < 0.3)
    assert rig.svc.ui_state(A) is UiState.ENABLED  # 運転停止(無効化はしない)
    assert rig.svc.controller_of(A) is None
    assert "中断" in "".join(t for k, t in rig.out)


def test_estop_during_script_always_works(rig: Rig) -> None:
    assert rig.engine.execute(START.format(v=2.0) + "sleep(30)")
    assert wait_until(lambda: abs(rig.sim.motor(A).velocity) > 1.0)
    t0 = time.perf_counter()
    rig.svc.estop()  # ヘッダーのボタン / Pause / Esc と同じ呼び出し(GUI スレッドから)
    assert wait_until(lambda: rig.sim.motor(A).mode is MotorMode.RESET)
    assert time.perf_counter() - t0 < 1.0
    assert rig.svc.estop_latched and rig.svc.ui_state(A) is UiState.DISABLED
    # スクリプトが止まっていなくても、以後の指令は SafetyGuard / ラッチで拒否される
    rig.engine.abort()
    assert rig.engine.join(3.0)
    rig.run("service.start(127, make('velocity', velocity=3.0))")
    assert "SafetyError" in rig.text(ERR) or "ProtocolError" in rig.text(ERR)
    assert rig.sim.motor(A).mode is MotorMode.RESET
    rig.run("service.enable(127, RunMode.VELOCITY)")
    assert "非常停止" in rig.text(ERR)
    assert rig.sim.motor(A).mode is MotorMode.RESET
    rig.svc.release_estop()


def test_script_loop_that_keeps_commanding_cannot_override_estop(rig: Rig) -> None:
    code = START.format(v=1.0) + (
        "n = 0\nwhile True:\n    try:\n"
        "        service.update(127, make('velocity', velocity=3.0).params)\n"
        "        n += 1\n    except Exception as e:\n        err = str(e)\n    sleep(0.01)\n"
    )
    assert rig.engine.execute(code)
    assert wait_until(lambda: rig.engine.namespace.get("n", 0) > 5)
    rig.svc.estop()
    assert wait_until(lambda: "err" in rig.engine.namespace)
    time.sleep(0.3)
    assert rig.sim.motor(A).mode is MotorMode.RESET  # 再び動き出さない
    assert abs(rig.sim.motor(A).velocity) < 1.0  # 3 rad/s へ加速していない(惰走で減衰中)
    assert wait_until(lambda: abs(rig.sim.motor(A).velocity) < 0.3)
    rig.engine.abort()
    assert rig.engine.join(3.0)


def test_run_sequence_from_console(rig: Rig, tmp_path) -> None:
    f = tmp_path / "s.yaml"
    f.write_text(
        "steps:\n  - enable: velocity\n"
        "  - run: {controller: velocity, params: {velocity: 1.0}, seconds: 0.4}\n"
        "  - disable\n",
        encoding="utf-8",
    )
    rig.run(f"r = run_sequence({str(f)!r}, 127)\nr.state.value")
    assert "'completed'" in rig.text(RESULT)
    assert "[2/3]" in rig.text(OUT)
    assert rig.sim.motor(A).mode is MotorMode.RESET


def test_abort_script_aborts_running_sequence_and_cleans_up(rig: Rig, tmp_path) -> None:
    f = tmp_path / "long.yaml"
    f.write_text(
        "steps:\n  - enable: velocity\n"
        "  - run: {controller: velocity, params: {velocity: 2.0}, seconds: 30}\n  - disable\n",
        encoding="utf-8",
    )
    assert rig.engine.execute(f"run_sequence({str(f)!r}, 127)")
    assert wait_until(lambda: abs(rig.sim.motor(A).velocity) > 1.0)
    rig.engine.abort()
    assert rig.engine.join(10.0)
    assert wait_until(lambda: rig.sim.motor(A).mode is MotorMode.RESET)  # 後始末で無効化された
    assert rig.svc.ui_state(A) is UiState.DISABLED
