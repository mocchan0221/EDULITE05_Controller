"""シーケンスの実行(phase4_5.md M)。Qt を import しない。

`SequenceRunner` は検証済みの `Sequence` を、**ワーカースレッド**で `MotorService` へ実行する。

- モータへの指令はすべて `MotorService`(→ ControlLoop → SafetyGuard)のメソッド経由。
  生フレームは送らない。出力段階の上限は SafetyGuard が掛ける(クランプはログに出す)。
- **LOW 段階のみ**。`allow_high=True`(明示)でなければ、出力段階が HIGH なら開始しない。
- 開始前の確認(接続・非常停止・出力段階・モータの存在と無効状態)に通らなければ、
  **モータには何も送らず**に失敗で終わる(他の人が動かしているモータを止めない)。
- 開始後は、**終わり方に関わらず(完了・中断・エラー・非常停止)必ず** 記録を止めて
  `stop_and_disable`(運転停止 → 減速を待つ → 無効化)で終える。非常停止のラッチ中は
  `estop()` が全モータを無効化済みなので何も送らない。
- 中断は `abort()`(どのスレッドからでも、いつでも)。直ちに `stop_motion` を送り、
  ワーカーは次の周期(`tick_s`)で気づいて後始末へ進む。
- 実行中は毎周期、非常停止ラッチ・通信断・故障・モータ状態(予期しない無効化など)を見て、
  異常なら中断して後始末する。
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from edulite05.core import clock
from edulite05.core.control.service import SERVICE_CLAMPED, MotorService, UiState
from edulite05.core.errors import EduliteError
from edulite05.core.safety import ClampNote, PowerTier
from edulite05.core.sequence.schema import (
    DisableStep,
    EnableStep,
    LogStep,
    RecordStep,
    RunStep,
    Sequence,
    SetParamsStep,
    StartStep,
    Step,
    StopStep,
    WaitStep,
)
from edulite05.core.telemetry import Recorder

log = logging.getLogger("edulite05.sequence")

TICK_S = 0.02
ENABLE_TIMEOUT_S = 8.0
DISABLE_TIMEOUT_S = 5.0
ONLINE_WAIT_S = 1.5  # 開始前: モータの応答を待つ上限
SETTLE_TIMEOUT_S = 0.5  # 終了時: 減速を待つ上限(cli/motion.py と同じ)
SETTLE_SPEED_EPS = 0.05

_REUSE = "このシーケンスは実行済み / 実行中です(新しい Runner を作ってください)"
_FIELD_NAMES = {"value": "指令値", "velocity": "速度", "torque": "トルク", "position": "位置"}


class SequenceState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"  # 利用者による中断
    ESTOP = "estop"  # 非常停止で中断
    FAILED = "failed"  # エラー・故障・通信断・開始前の確認に失敗


@dataclass(frozen=True, slots=True)
class SequenceResult:
    state: SequenceState
    message: str
    steps_done: int  # 最後まで終えたステップ数
    problems: tuple[str, ...] = ()  # 後始末の失敗(あれば)
    recording: Path | None = None  # 記録したファイル(あれば)
    stopped: bool = False  # モータに触れた(開始前の確認を通った)ので、運転停止・無効化で終えた

    @property
    def ok(self) -> bool:
        return self.state is SequenceState.COMPLETED and not self.problems


@dataclass(frozen=True, slots=True)
class SequenceEvent:
    """進行の通知。kind: started / step / step_done / log / finished。"""

    kind: str
    t: float  # 開始からの経過秒
    index: int = -1  # step / step_done のステップ番号(0 始まり)
    total: int = 0
    text: str = ""
    level: str = "info"  # log のとき info / warn / error
    result: SequenceResult | None = None  # finished のとき


@dataclass(slots=True)
class Progress:
    """GUI が定期的に読む進捗の断面。"""

    state: SequenceState = SequenceState.IDLE
    index: int = -1
    total: int = 0
    step_elapsed: float = 0.0
    step_seconds: float = 0.0
    text: str = ""


class _Stop(Exception):
    """実行を打ち切る(後始末へ)。"""

    def __init__(self, state: SequenceState, message: str) -> None:
        super().__init__(message)
        self.state = state
        self.message = message


class SequenceRunner:
    def __init__(
        self,
        service: MotorService,
        sequence: Sequence,
        motor_id: int,
        *,
        allow_high: bool = False,
        on_event: Callable[[SequenceEvent], None] | None = None,
        recordings_dir: str | Path | None = None,
        tick_s: float = TICK_S,
        enable_timeout_s: float = ENABLE_TIMEOUT_S,
        settle_timeout_s: float = SETTLE_TIMEOUT_S,
    ) -> None:
        self._svc = service
        self.sequence = sequence
        self.motor_id = motor_id
        self.allow_high = allow_high
        self._on_event = on_event
        self._rec_dir = Path(recordings_dir) if recordings_dir is not None else None
        self._tick = tick_s
        self._enable_timeout = enable_timeout_s
        self._settle = settle_timeout_s

        self._lock = threading.Lock()
        self._abort = threading.Event()
        self._abort_reason = ""
        self._thread: threading.Thread | None = None
        self._state = SequenceState.IDLE
        self._result: SequenceResult | None = None
        self._index = -1
        self._step_t0 = 0.0
        self._step_len = 0.0
        self._t0 = 0.0
        self._expect: set[UiState] = {UiState.DISABLED}
        self._recorder: Recorder | None = None
        self._rec_path: Path | None = None
        self._done = threading.Event()
        self._notes_seen = ""

    # ============================================================ 公開
    @property
    def state(self) -> SequenceState:
        return self._state

    @property
    def running(self) -> bool:
        return self._state is SequenceState.RUNNING

    @property
    def result(self) -> SequenceResult | None:
        return self._result

    def progress(self) -> Progress:
        """現在の進捗(どのスレッドからでも読める)。"""
        idx = self._index
        steps = self.sequence.steps
        el = max(0.0, clock.now() - self._step_t0) if self._state is SequenceState.RUNNING else 0.0
        return Progress(
            self._state, idx, len(steps), min(el, self._step_len) if self._step_len else el,
            self._step_len, steps[idx].title() if 0 <= idx < len(steps) else "",
        )

    def start(self) -> None:
        """ワーカースレッドで実行を始める(すぐ戻る)。結果は `result` / `finished` イベント。"""
        with self._lock:
            if self._state is not SequenceState.IDLE:
                raise RuntimeError(_REUSE)
            self._state = SequenceState.RUNNING  # run() の入口の検査より前に確定させる
        self._thread = threading.Thread(
            target=self._run, name="edulite05-sequence", daemon=True
        )
        self._thread.start()

    def run(self) -> SequenceResult:
        """呼んだスレッドで実行する(CLI・テスト用)。終わると結果を返す。"""
        with self._lock:
            if self._state is not SequenceState.IDLE:
                raise RuntimeError(_REUSE)
            self._state = SequenceState.RUNNING
        return self._run()

    def abort(self, reason: str = "") -> None:
        """中断する(冪等・どのスレッドからでも)。直ちに運転停止を送り、後始末はワーカーが行う。"""
        if self._state is not SequenceState.RUNNING:
            return
        if not self._abort.is_set():
            self._abort_reason = reason
            self._abort.set()
        if self._index >= 0 and not self._svc.estop_latched:  # 最初の指令の前なら送らない
            try:
                self._svc.stop_motion([self.motor_id])
            except Exception:  # noqa: BLE001 - 後始末で再度行う
                log.debug("abort 中の stop_motion に失敗", exc_info=True)

    def join(self, timeout: float | None = None) -> bool:
        """終わるまで待つ。終わったら True。"""
        return self._done.wait(timeout)

    # ============================================================ 本体
    def _run(self) -> SequenceResult:
        seq = self.sequence
        self._t0 = clock.now()
        total = len(seq.steps)
        self._emit("started", total=total, text=seq.name)
        owned = False
        state, message = SequenceState.COMPLETED, "完了しました"
        done = 0
        unsub = self._subscribe_clamped()
        try:
            self._precheck()
            owned = True
            for i, step in enumerate(seq.steps):
                self._check_alive()
                self._index = i
                self._step_t0 = clock.now()
                self._step_len = step.seconds
                self._emit("step", index=i, total=total, text=step.describe())
                self._exec(step)
                done = i + 1
                self._emit("step_done", index=i, total=total, text=step.describe())
        except _Stop as s:
            state, message = s.state, s.message
        except Exception as e:  # noqa: BLE001 - どんなエラーでも後始末へ
            log.exception("シーケンスの実行中にエラー")
            state, message = SequenceState.FAILED, _error_text(e)
        except BaseException as e:  # KeyboardInterrupt など: 後始末してから再送出
            self._finish(owned, done, SequenceState.ABORTED, "中断されました", unsub)
            raise e
        return self._finish(owned, done, state, message, unsub)

    def _finish(
        self, owned: bool, done: int, state: SequenceState, message: str, unsub: Callable[[], None]
    ) -> SequenceResult:
        try:
            unsub()
        except Exception:  # noqa: BLE001
            pass
        problems = self._cleanup(owned)
        if problems and state is SequenceState.COMPLETED:
            message = "完了しましたが、後始末に失敗しました"
        result = SequenceResult(
            state, message, done, tuple(problems), self._rec_path, owned
        )
        self._result = result
        self._state = state
        level = "info" if result.ok else ("warn" if state is not SequenceState.FAILED else "error")
        self._emit("finished", total=len(self.sequence.steps), text=message, level=level,
                   result=result)
        self._done.set()
        return result

    # ------------------------------------------------------------ 開始前の確認
    def _precheck(self) -> None:
        svc, mid = self._svc, self.motor_id
        if not svc.connected:
            raise _Stop(SequenceState.FAILED, "アダプタに接続されていません")
        if svc.estop_latched:
            raise _Stop(SequenceState.FAILED, "非常停止中です(解除してから実行してください)")
        if svc.tier is not PowerTier.LOW and not self.allow_high:
            raise _Stop(
                SequenceState.FAILED,
                "出力段階が高出力です。シーケンスは低出力でだけ実行できます(低出力に戻してください)",
            )
        if mid not in svc.motor_ids():
            try:
                svc.motor(mid)  # スキャンしていない CLI など: 取り込んで応答を待つ(enable と同じ)
            except EduliteError as e:
                raise _Stop(SequenceState.FAILED, f"ID {mid} のモータを使えません: {e}") from e
        ui = svc.ui_state(mid)
        end = clock.now() + ONLINE_WAIT_S
        while ui is UiState.OFFLINE and clock.now() < end:
            if self._abort.wait(self._tick):
                raise _Stop(SequenceState.ABORTED, self._abort_reason or "中断しました")
            ui = svc.ui_state(mid)
        if ui is UiState.OFFLINE:
            raise _Stop(SequenceState.FAILED, f"ID {mid} から応答がありません(電源・配線を確認)")
        if ui is not UiState.DISABLED or svc.is_busy(mid):
            raise _Stop(
                SequenceState.FAILED,
                f"ID {mid} は停止中(無効)の状態から始めてください(現在: {_ui_text(ui)})",
            )

    # ------------------------------------------------------------ 周期の検査
    def _check_alive(self, check_state: bool = True) -> None:
        """中断・非常停止・通信断・故障・予期しない状態なら `_Stop`。"""
        svc, mid = self._svc, self.motor_id
        if self._abort.is_set():
            raise _Stop(SequenceState.ABORTED, self._abort_reason or "中断しました")
        if svc.estop_latched:
            raise _Stop(SequenceState.ESTOP, "非常停止で中断しました")
        if not svc.connected:
            raise _Stop(SequenceState.FAILED, "アダプタとの通信が途絶えました")
        ui = svc.ui_state(mid)
        if ui is UiState.FAULT:
            names = ", ".join(f.name for f in svc.fault_details(mid))
            detail = f"({names})" if names else ""
            raise _Stop(SequenceState.FAILED, f"ID {mid} が故障しました{detail}")
        if ui is UiState.OFFLINE:
            raise _Stop(SequenceState.FAILED, f"ID {mid} との通信が途絶えました")
        if not check_state:
            return
        want = self._expect
        if want and ui not in want and not svc.is_busy(mid):
            raise _Stop(
                SequenceState.FAILED,
                f"ID {mid} が予期せず「{_ui_text(ui)}」になりました(期待: "
                + " / ".join(_ui_text(u) for u in sorted(want, key=lambda u: u.value)) + ")",
            )

    def _hold(self, seconds: float) -> None:
        """`seconds` 秒待つ。毎周期、中断・異常を見る。"""
        end = clock.now() + seconds
        while True:
            self._check_alive()
            remaining = end - clock.now()
            if remaining <= 0:
                return
            self._abort.wait(min(self._tick, remaining))

    def _await(self, fut: Future[Any], timeout: float, what: str) -> Any:
        """Future を待つ。待つ間も中断・非常停止・故障を見る(状態の期待は問わない)。"""
        end = clock.now() + timeout
        while True:
            self._check_alive(False)
            try:
                return fut.result(timeout=self._tick)
            except FutureTimeout:
                if clock.now() >= end:
                    raise _Stop(SequenceState.FAILED, f"{what}がタイムアウトしました") from None

    # ------------------------------------------------------------ ステップ
    def _exec(self, step: Step) -> None:
        svc, mid = self._svc, self.motor_id
        try:
            if isinstance(step, EnableStep):
                self._await(svc.enable(mid, step.mode), self._enable_timeout, "有効化")
                self._expect = {UiState.ENABLED, UiState.RUNNING}
                self._check_alive()
            elif isinstance(step, (StartStep, RunStep)):
                svc.start([mid], step.controller(step.params))
                self._expect = {UiState.RUNNING}
                if isinstance(step, RunStep):
                    self._hold(step.duration)
                    svc.stop_motion([mid])
                    self._expect = {UiState.ENABLED}
            elif isinstance(step, SetParamsStep):
                ctrl = svc.controller_of(mid)
                if ctrl is None:
                    raise _Stop(SequenceState.FAILED, "運転中のコントローラがありません")
                svc.update([mid], dataclasses.replace(ctrl.params, **dict(step.changes)))
            elif isinstance(step, WaitStep):
                self._hold(step.duration)
            elif isinstance(step, StopStep):
                svc.stop_motion([mid])
                self._expect = {UiState.ENABLED}
            elif isinstance(step, DisableStep):
                self._await(svc.disable([mid]), DISABLE_TIMEOUT_S, "無効化")
                self._expect = {UiState.DISABLED}
            elif isinstance(step, RecordStep):
                self._record(step)
            elif isinstance(step, LogStep):
                self._emit("log", text=step.text)
            else:  # pragma: no cover
                raise _Stop(SequenceState.FAILED, f"未対応のステップです: {step!r}")
        except _Stop:
            raise
        except EduliteError as e:
            if svc.estop_latched:
                raise _Stop(SequenceState.ESTOP, "非常停止で中断しました") from e
            raise _Stop(SequenceState.FAILED, f"{step.describe()}: {_error_text(e)}") from e

    # ------------------------------------------------------------ 記録
    def _record(self, step: RecordStep) -> None:
        if step.action == "stop":
            self._stop_recording()
            return
        if self._recorder is not None:
            return
        hub = self._svc.telemetry
        names = [c.name for c in hub.channels() if c.motor_id == self.motor_id]
        if not names:
            raise _Stop(SequenceState.FAILED, "記録できるチャネルがありません")
        path = self._record_path(step.path)
        rec = Recorder(hub)
        rec.start(
            path, names, profile_name=self._svc.session.profile.name,
            tier=self._svc.tier.value, extra={"sequence": self.sequence.name},
        )
        self._recorder, self._rec_path = rec, path
        self._emit("log", text=f"記録を開始しました: {path.name}")

    def _record_path(self, given: str | None) -> Path:
        base = self._rec_dir if self._rec_dir is not None else Path("user_data") / "records"
        if given:
            p = Path(given)
            return p if p.is_absolute() else base / p
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.sequence.name)
        safe = "_".join(part for part in safe.split("_") if part) or "sequence"
        return base / f"seq_{safe}_{stamp}.csv"

    def _stop_recording(self) -> None:
        rec, self._recorder = self._recorder, None
        if rec is None:
            return
        rows = rec.stop()
        name = self._rec_path.name if self._rec_path else ""
        self._emit("log", text=f"記録を保存しました: {name}({rows} 行)")
        if rec.error is not None:
            self._emit("log", text=f"記録の書込みでエラー: {rec.error}", level="error")

    # ------------------------------------------------------------ 後始末
    def _cleanup(self, owned: bool) -> list[str]:
        """記録を止め、運転停止 → 減速を待つ → 無効化。失敗しても次の手へ進む。"""
        problems: list[str] = []
        try:
            self._stop_recording()
        except Exception as e:  # noqa: BLE001
            problems.append(f"記録の停止: {e!r}")
        if not owned:
            return problems
        svc, mid = self._svc, self.motor_id
        if svc.estop_latched:
            return problems  # estop() が全モータへ type 4 を送って無効化済み
        ok = _retry(
            lambda: svc.stop_and_disable(
                [mid], self._settle, SETTLE_SPEED_EPS
            ).result(self._settle + DISABLE_TIMEOUT_S + 3.0),
            "運転停止・無効化", problems,
        )
        if not ok and not svc.estop_latched:
            _retry(
                lambda: svc.disable([mid]).result(DISABLE_TIMEOUT_S + 3.0),
                "無効化(再試行)", problems,
            )
        if svc.estop_latched:
            problems.clear()  # 途中で非常停止が入った: estop() が全モータを無効化済み
        return problems

    # ------------------------------------------------------------ 通知
    def _emit(self, kind: str, **kw: Any) -> None:
        cb = self._on_event
        if cb is None:
            return
        try:
            cb(SequenceEvent(kind, clock.now() - self._t0, **kw))
        except Exception:  # noqa: BLE001 - 購読側の例外で実行を止めない
            log.exception("シーケンスのイベント通知で例外")

    def _subscribe_clamped(self) -> Callable[[], None]:
        mid = self.motor_id

        def on_clamped(p: Any) -> None:
            if p.get("motor_id") != mid:
                return
            notes: list[ClampNote] = list(p.get("notes") or [])
            text = ", ".join(
                f"{_FIELD_NAMES.get(n.field, n.field)} {n.requested:g}→{n.applied:g}"
                for n in notes
            )
            if text and text != self._notes_seen:
                self._emit("log", text=f"出力段階の上限で制限しました: {text}", level="warn")
            self._notes_seen = text

        return self._svc.events.subscribe(SERVICE_CLAMPED, on_clamped)


# ====================================================================== 補助
def _retry(fn: Callable[[], Any], what: str, problems: list[str], tries: int = 3) -> bool:
    for _ in range(tries):
        try:
            fn()
            return True
        except KeyboardInterrupt:
            continue
        except Exception as e:  # noqa: BLE001
            problems.append(f"{what}: {_error_text(e)}")
            return False
    problems.append(f"{what}: 中断されました")
    return False


def _error_text(e: BaseException) -> str:
    msg = str(e)
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


_UI_TEXT = {
    UiState.OFFLINE: "未接続",
    UiState.DISABLED: "停止中",
    UiState.ENABLED: "有効",
    UiState.RUNNING: "運転中",
    UiState.FAULT: "故障",
}


def _ui_text(ui: UiState) -> str:
    return _UI_TEXT.get(ui, ui.value)


__all__ = [
    "Progress",
    "SequenceEvent",
    "SequenceResult",
    "SequenceRunner",
    "SequenceState",
]
