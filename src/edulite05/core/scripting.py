"""スクリプトコンソールの実行エンジン(phase4_5.md M)。Qt を import しない。

利用者が書いた Python をワーカースレッドで実行する(GUI をブロックしない)。

- 名前空間は実行のたびに最新の `service` / `telemetry` / `session` を入れ直す(接続し直しても追従)。
  変数は実行をまたいで残る(対話コンソールと同じ)。
- 式なら値を表示し、文なら実行する(`eval` → だめなら `exec`)。
- `print` は名前空間で差し替えて出力ペインへ流す。例外は `<console>` の行だけに絞った短い
  日本語の traceback にする。
- **中断**(`abort()`): 実行中のスレッドに `ScriptAborted` を注入し、`sleep()` / `wait()` を起こす。
  C の待ち(ブロッキング I/O など)の中では次に Python のコードへ戻るまで効かないので、待つ処理は
  `sleep()` / `wait()` を使う。中断時は `on_abort`(GUI では全モータの運転停止)も呼ぶ。
- 指令の経路は変えない: `service`(`MotorService`)のメソッドは SafetyGuard を通る。ここは
  生フレームを送る手段を名前空間に載せない(`service.session.bus` は読み取り用の参照として見えるが、
  使い方の責任は利用者)。

非常停止は `service.estop()` が同期で全モータを止めるため、スクリプトの実行中でも常に有効。
ラッチ後にスクリプトが指令を出しても `SafetyError` で拒否される。
"""

from __future__ import annotations

import ast
import ctypes
import logging
import threading
import traceback
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

log = logging.getLogger("edulite05.scripting")

FILENAME = "<console>"
POLL_S = 0.05
FUTURE_TIMEOUT_S = 60.0  # 式の値が Future のとき、結果を待つ上限

# 出力の種類
OUT = "out"  # print の出力
ERR = "err"  # 例外
RESULT = "result"  # 式の値
ECHO = "echo"  # 入力したコード(履歴の表示)
INFO = "info"  # 中断・終了などの案内


class ScriptAborted(BaseException):
    """実行中のスクリプトを中断する(`except Exception` で握りつぶされないよう BaseException)。"""


Output = Callable[[str, str], None]  # (kind, text)


class ScriptEngine:
    def __init__(
        self,
        namespace_factory: Callable[[], dict[str, Any]],
        on_output: Output,
        *,
        on_finished: Callable[[bool], None] | None = None,
        on_abort: Callable[[], None] | None = None,
        project_root: Path | None = None,
    ) -> None:
        self._factory = namespace_factory
        self._out = on_output
        self._on_finished = on_finished
        self._on_abort = on_abort
        self._root = project_root
        self.namespace: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._abort_evt = threading.Event()
        self._running = False
        self._done = threading.Event()
        self._done.set()
        self._code = ""

    # ============================================================ 公開
    @property
    def running(self) -> bool:
        return self._running

    def execute(self, code: str) -> bool:
        """`code` をワーカースレッドで実行する。実行中なら何もせず False。"""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._abort_evt.clear()
            self._done.clear()
        self._code = code
        self._thread = threading.Thread(
            target=self._worker, args=(code,), name="edulite05-script", daemon=True
        )
        self._thread.start()
        return True

    def abort(self) -> None:
        """実行中のスクリプトを中断する(冪等・どのスレッドからでも)。モータの運転停止も依頼する。"""
        with self._lock:
            th = self._thread if self._running else None
        if th is None:
            return
        self._abort_evt.set()
        if th.ident is not None and th.is_alive():
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(th.ident), ctypes.py_object(ScriptAborted)
            )
        if self._on_abort is not None:
            try:
                self._on_abort()
            except Exception:  # noqa: BLE001
                log.exception("中断時の運転停止に失敗")

    def join(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    def emit(self, kind: str, text: str) -> None:
        """出力ペインへ 1 件流す(どのスレッドからでも)。"""
        self._out(kind, text)

    # ============================================================ 名前空間の道具
    def sleep(self, seconds: float) -> None:
        """中断できる sleep。`time.sleep` の代わりに使う。"""
        if self._abort_evt.wait(max(0.0, float(seconds))):
            raise ScriptAborted

    def wait(self, fut: Future[Any], timeout: float | None = None) -> Any:
        """Future の結果を待つ(中断できる)。`service.enable(...)` などの戻り値に使う。"""
        end = None if timeout is None else _now() + timeout
        while True:
            if self._abort_evt.is_set():
                raise ScriptAborted
            try:
                return fut.result(timeout=POLL_S)
            except FutureTimeout:
                if end is not None and _now() >= end:
                    raise

    def _print(self, *args: object, sep: str = " ", end: str = "\n", **_kw: object) -> None:
        self._out(OUT, sep.join(str(a) for a in args) + end)

    # ============================================================ ワーカー
    def _worker(self, code: str) -> None:
        ok = True
        try:
            ns = self.namespace
            ns.update(self._factory())
            ns["print"] = self._print
            ns["sleep"] = self.sleep
            ns["wait"] = self.wait
            ns["__name__"] = "__console__"
            try:
                body, last = _compile(code)
            except SyntaxError as e:
                ok = False
                self._out(ERR, _format_syntax(e, code))
                return
            try:
                if body is not None:
                    exec(body, ns)  # noqa: S102 - 利用者が書いたコードを実行するのが目的
                if last is not None:
                    value = eval(last, ns)  # noqa: S307
                    if isinstance(value, Future):  # Future は完了を待って結果を出す
                        value = self.wait(value, FUTURE_TIMEOUT_S)
                    if value is not None:
                        ns["_"] = value
                        self._out(RESULT, repr(value) + "\n")
            except ScriptAborted:
                ok = False
                self._out(INFO, "中断しました\n")
            except SystemExit:
                self._out(INFO, "exit() は使えません(コンソールは終了しません)\n")
            except BaseException as e:  # noqa: BLE001
                ok = False
                self._out(ERR, _format_exception(e, code))
        except ScriptAborted:
            ok = False
        except BaseException:  # noqa: BLE001 - ワーカーは何があっても終わらせる
            ok = False
            log.exception("スクリプトのワーカーで例外")
        finally:
            with self._lock:
                self._running = False
            self._done.set()
            if self._on_finished is not None:
                try:
                    self._on_finished(ok)
                except Exception:  # noqa: BLE001
                    log.exception("on_finished で例外")


# ====================================================================== 補助
def _now() -> float:
    from edulite05.core import clock

    return clock.now()


def _compile(code: str) -> tuple[Any, Any]:
    """(本体, 最後の式)。最後の文が式ならその値を表示する(対話コンソールと同じ)。"""
    tree = ast.parse(code, FILENAME, "exec")
    last = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = compile(ast.Expression(tree.body.pop().value), FILENAME, "eval")
    body = compile(tree, FILENAME, "exec") if tree.body else None
    return body, last


def _format_syntax(e: SyntaxError, code: str) -> str:
    line = e.lineno or 1
    lines = code.splitlines()
    src = lines[line - 1].strip() if 0 < line <= len(lines) else ""
    return f"構文エラー(行 {line}): {e.msg}\n  {src}\n"


def _format_exception(e: BaseException, code: str) -> str:
    """`<console>` のフレームだけを「行 N: ソース」で示し、最後に 例外名: メッセージ。"""
    lines = code.splitlines()
    out: list[str] = []
    for fs in traceback.extract_tb(e.__traceback__):
        if fs.filename != FILENAME or fs.lineno is None:
            continue
        src = lines[fs.lineno - 1].strip() if 0 < fs.lineno <= len(lines) else ""
        out.append(f"  行 {fs.lineno}: {src}")
    msg = str(e)
    out.append(f"{type(e).__name__}: {msg}" if msg else type(e).__name__)
    return "\n".join(out) + "\n"


# ====================================================================== 既定の名前空間
def default_namespace(
    service: Any, project_root: Path | None = None, engine: ScriptEngine | None = None
) -> dict[str, Any]:
    """コンソールに公開する名前。`service` が None(未接続)でも他は使える。

    service     MotorService(enable / start / update / stop_motion / disable / estop ...)
    session     Session(bus / events / profile / motors。読み取り用)
    telemetry   TelemetryHub(`telemetry.latest("m127.vel")` など)
    controllers Controller 名 → クラス(`controllers["velocity"]`)
    params      Controller 名 → Params クラス
    make(name, **kw)  Controller を作る。`service.start(127, make("velocity", velocity=1.0))`
    ids()       接続中のモータ ID の一覧
    sleep(s) / wait(future)  中断できる待ち
    run_sequence(path, motor=None)  シーケンス(YAML)を実行して結果を返す
    """
    from edulite05.core.control.controllers import CONTROLLERS
    from edulite05.core.control.params import PARAMS_BY_CONTROLLER
    from edulite05.core.control.service import UiState
    from edulite05.core.protocol.types import RunMode
    from edulite05.core.safety import PowerTier

    def make(name: str, **kw: Any) -> Any:
        cls = CONTROLLERS.get(name)
        if cls is None:
            names = ", ".join(sorted(CONTROLLERS))
            raise KeyError(f"Controller {name!r} はありません。使えるもの: {names}")
        return cls(cls.Params(**kw))

    def ids() -> list[int]:
        return list(service.motor_ids()) if service is not None else []

    ns: dict[str, Any] = {
        "service": service,
        "session": getattr(service, "session", None),
        "telemetry": getattr(service, "telemetry", None),
        "controllers": CONTROLLERS,
        "params": PARAMS_BY_CONTROLLER,
        "make": make,
        "ids": ids,
        "RunMode": RunMode,
        "PowerTier": PowerTier,
        "UiState": UiState,
    }
    if engine is not None:
        ns["run_sequence"] = _make_run_sequence(service, project_root, engine)
    return ns


def _make_run_sequence(
    service: Any, project_root: Path | None, engine: ScriptEngine
) -> Callable[..., Any]:
    def run_sequence(path: str, motor: int | None = None) -> Any:
        from edulite05.core.sequence import SequenceRunner, load_sequence

        if service is None:
            raise RuntimeError("接続されていません(service が None です)")
        p = Path(path)
        if not p.is_absolute() and project_root is not None and not p.exists():
            p = project_root / "sequences" / p
        seq = load_sequence(p)
        mid = motor if motor is not None else seq.motor
        if mid is None:
            motor_ids = service.motor_ids()
            if len(motor_ids) != 1:
                raise ValueError("モータ ID を run_sequence(path, motor=ID) で指定してください")
            mid = motor_ids[0]

        def on_event(ev: Any) -> None:
            if ev.kind == "step":
                engine.emit(OUT, f"[{ev.index + 1}/{ev.total}] {ev.text}\n")
            elif ev.kind == "log":
                engine.emit(OUT, f"    {ev.text}\n")

        runner = SequenceRunner(service, seq, mid, on_event=on_event)
        runner.start()
        try:
            while not runner.join(POLL_S):
                pass
        except ScriptAborted:
            runner.abort("スクリプトの中断")
            runner.join(15.0)
            raise
        res = runner.result
        engine.emit(OUT, f"シーケンス: {res.state.value}({res.message})\n" if res else "")
        return res

    return run_sequence


def stop_all_motion(service: Any) -> None:
    """中断時の安全側の動作: 全モータを運転停止(有効のまま保持)。ラッチ中・未接続なら何もしない。"""
    if service is None or service.estop_latched:
        return
    ids = service.motor_ids()
    if ids:
        service.stop_motion(ids)


__all__ = [
    "ECHO", "ERR", "INFO", "OUT", "RESULT",
    "ScriptAborted", "ScriptEngine", "default_namespace", "stop_all_motion",
]
