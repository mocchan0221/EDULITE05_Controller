"""スクリプトコンソールの実行エンジン(Qt・モータなし)。ワーカー実行・出力・例外・中断。"""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future

import pytest

from edulite05.core.scripting import (
    ERR,
    INFO,
    OUT,
    RESULT,
    ScriptEngine,
    default_namespace,
)


class Sink:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []
        self.finished: list[bool] = []

    def out(self, kind: str, text: str) -> None:
        self.items.append((kind, text))

    def done(self, ok: bool) -> None:
        self.finished.append(ok)

    def text(self, kind: str | None = None) -> str:
        return "".join(t for k, t in self.items if kind is None or k == kind)


@pytest.fixture
def eng() -> tuple[ScriptEngine, Sink]:
    sink = Sink()
    e = ScriptEngine(lambda: {"x": 41}, sink.out, on_finished=sink.done)
    return e, sink


def run(e: ScriptEngine, code: str, timeout: float = 5.0) -> None:
    assert e.execute(code)
    assert e.join(timeout)


def test_expression_prints_result_and_persists_variables(eng) -> None:
    e, s = eng
    run(e, "x + 1")
    assert s.items == [(RESULT, "42\n")]
    run(e, "y = x * 2")
    run(e, "y")
    assert s.text(RESULT) == "42\n82\n"
    assert e.namespace["_"] == 82
    assert s.finished == [True, True, True]


def test_print_goes_to_output_and_multiline_exec(eng) -> None:
    e, s = eng
    run(e, "for i in range(3):\n    print('a', i, sep='-')\nprint('end', end='')")
    assert s.text(OUT) == "a-0\na-1\na-2\nend"


def test_runs_on_worker_thread_and_does_not_block(eng) -> None:
    e, s = eng
    t0 = time.perf_counter()
    assert e.execute("sleep(0.5)\nprint('done')")
    assert time.perf_counter() - t0 < 0.2  # execute() はすぐ戻る
    assert e.running
    assert not e.execute("1")  # 実行中は受け付けない
    assert e.join(3.0)
    assert not e.running and s.text(OUT) == "done\n"


def test_thread_is_not_main(eng) -> None:
    e, s = eng
    here = threading.get_ident()
    run(e, "import threading\nthreading.get_ident()")
    assert int(s.text(RESULT)) != here


def test_exception_is_short_japanese_traceback(eng) -> None:
    e, s = eng
    run(e, "a = 1\nb = 0\nprint(a / b)")
    err = s.text(ERR)
    assert "行 3: print(a / b)" in err and "ZeroDivisionError" in err
    assert s.finished[-1] is False
    run(e, "1 + 1")  # エラーの後も続けて使える
    assert s.text(RESULT) == "2\n"


def test_syntax_error(eng) -> None:
    e, s = eng
    run(e, "def (:")
    assert "構文エラー" in s.text(ERR)
    assert s.finished == [False]


def test_system_exit_does_not_kill(eng) -> None:
    e, s = eng
    run(e, "exit()")
    assert "終了しません" in s.text(INFO)
    run(e, "1")
    assert s.text(RESULT) == "1\n"


def test_abort_interrupts_python_loop(eng) -> None:
    e, s = eng
    assert e.execute("n = 0\nwhile True:\n    n += 1")
    time.sleep(0.2)
    t0 = time.perf_counter()
    e.abort()
    assert e.join(3.0) and time.perf_counter() - t0 < 1.0
    assert "中断" in s.text(INFO) and s.finished == [False]
    run(e, "1")  # 中断後も使える
    assert s.text(RESULT) == "1\n"


def test_abort_interrupts_sleep_and_wait_and_calls_on_abort() -> None:
    s = Sink()
    stopped = []
    e = ScriptEngine(lambda: {}, s.out, on_finished=s.done, on_abort=lambda: stopped.append(1))
    assert e.execute("sleep(30)")
    time.sleep(0.1)
    e.abort()
    assert e.join(3.0) and stopped == [1]
    fut: Future = Future()
    e.namespace["fut"] = fut
    assert e.execute("wait(fut)")
    time.sleep(0.1)
    e.abort()
    assert e.join(3.0) and stopped == [1, 1]
    assert s.finished == [False, False]


def test_abort_when_idle_is_noop() -> None:
    s = Sink()
    stopped = []
    e = ScriptEngine(lambda: {}, s.out, on_abort=lambda: stopped.append(1))
    e.abort()
    assert stopped == []


def test_except_exception_cannot_swallow_abort(eng) -> None:
    e, s = eng
    code = "while True:\n    try:\n        sleep(0.05)\n    except Exception:\n        pass"
    assert e.execute(code)
    time.sleep(0.2)
    e.abort()
    assert e.join(3.0)


def test_wait_returns_result_and_raises_timeout(eng) -> None:
    e, s = eng
    f: Future = Future()
    f.set_result(7)
    e.namespace["f"] = f
    run(e, "wait(f)")
    assert s.text(RESULT) == "7\n"
    g: Future = Future()
    e.namespace["g"] = g
    run(e, "wait(g, timeout=0.2)")
    assert "TimeoutError" in s.text(ERR)


def test_namespace_refreshed_each_run() -> None:
    s = Sink()
    box = {"v": 1}
    e = ScriptEngine(lambda: {"v": box["v"]}, s.out)
    run(e, "v")
    box["v"] = 2
    run(e, "v")
    assert s.text(RESULT) == "1\n2\n"


def test_default_namespace_without_service() -> None:
    s = Sink()
    e = ScriptEngine(lambda: {}, s.out)
    e._factory = lambda: default_namespace(None, None, e)
    run(e, "(service, telemetry, session, ids())")
    assert s.text(RESULT) == "(None, None, None, [])\n"
    run(e, "sorted(controllers)[:3]")
    assert "bilateral" in s.text(RESULT)
    run(e, "make('velocity', velocity=1.5).params.velocity")
    assert "1.5" in s.text(RESULT)
    run(e, "make('nope')")
    assert "KeyError" in s.text(ERR) and "使えるもの" in s.text(ERR)
    run(e, "run_sequence('x.yaml')")
    assert "接続されていません" in s.text(ERR)
    run(e, "[RunMode.VELOCITY, PowerTier.LOW.value, UiState.RUNNING.value, 'params' in dir()]")
    assert "'low'" in s.text(RESULT) and "'running'" in s.text(RESULT)
