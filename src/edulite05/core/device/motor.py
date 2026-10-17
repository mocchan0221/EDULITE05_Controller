"""Motor: 1 台の EL05 に対する非同期 API と状態保持(phase1.md §1.8)。

- **状態**: `Motor.state`(`MotorState`)は Feedback(type 2 / 24)を受けるたびに置換する。
  読み出しは参照取得のみ(ロック不要)。書き込みは RX スレッド 1 本(+ 内部ロック)。
- **要求の直列化**: 応答が type 2(送信先モータ ID だけで照合する)の要求が同時に走ると
  取り違えるため、1 台のモータへの要求は内部の FIFO で 1 件ずつ実行する。呼び出し側は
  Future を受け取るだけでブロックしない。`disable()` は待ち行列の先頭へ入る(実行中の 1 件
  の応答 or タイムアウト後に即送信)。
- **バージョン応答**: type 2 の ID で返るが Feedback ではない(`try_parse_version`)。
  `state` を更新しない(el05_protocol.md §2.6、実機確認済み)。
- **再送(冪等な要求のみ)**: 応答が欠落する実機の挙動(主動上報中に約 5 %、el05_protocol.md /
  hw_bench.md)に備え、`read_param` / `write_param` / `enable` / `disable` / `set_run_mode` /
  `set_active_report` / `get_uid` は応答なし(`RequestTimeout`)のとき最大 `retries` 回再送する
  (既定 2 = 計 3 回)。試行ごとに別の `BusManager.request`(= 試行ごとに TX を再スタンプ)なので、
  受理するのは「その試行の送信より後に受信した応答」だけ。試行 k の応答が遅れて試行 k+1 の
  送信後に届いたら、k+1 の応答として受理する(同じ要求の応答なので内容は同じ)。
  `set_zero` / `save_params` / `set_can_id` / `set_baud` / `switch_protocol` は再送しない
  (重複実行すると意味が変わる・ID が変わると同じ宛先に届かない)。
- **多回転角の較正**: type 2 の位置は ±4π で折り返し、`position_multi` はセッション開始時の位置を
  基準に展開するため、起動時に ±4π の外にいると k·8π ずれる(el05_protocol.md §8.0)。
  `sync_multi()` が mechPos(0x7019、折り返さない多回転値)を読んで展開オフセットを合わせ、
  `MotorState.multi_valid` を True にする(冪等な要求なので再送する)。
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import CancelledError, Future
from dataclasses import replace
from typing import Any

from edulite05.core import clock
from edulite05.core.bus import BusManager, Priority, RateCounter
from edulite05.core.device.profile import MotorProfile, ParamDef
from edulite05.core.device.state import MotorState, PositionUnwrapper
from edulite05.core.errors import ProtocolError, RequestTimeout
from edulite05.core.events import MOTOR_FAULT, MOTOR_MODE_CHANGED, MOTOR_RETRY, PARAM_UPDATED
from edulite05.core.protocol import private
from edulite05.core.protocol.ids import CommType
from edulite05.core.protocol.private import (
    FaultReport,
    Feedback,
    Message,
    ParamResponse,
    UidResponse,
    Unknown,
    VersionInfo,
    try_parse_version,
)
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.transport.base import CanFrame

ONLINE_TIMEOUT_S = 1.0  # この時間フレームが来なければ online=False
DEFAULT_REQUEST_TIMEOUT_S = 0.2
DEFAULT_RETRIES = 2  # 冪等な要求の再送回数(計 1 + 2 = 3 回まで送る)
# 再送で成功した type 2 系の要求は、遅れて届く前の試行の応答が次の要求の応答に化けないよう、
# 完了をこの時間だけ遅らせる(type 2 は送信先モータ ID だけで照合するため)
RETRY_SETTLE_S = 0.01
READ_ALL_ABORT_AFTER = 3  # read_all_params: 連続タイムアウトがこの回数で打ち切り

_RUN_MODE_KEY = "run_mode"
_REPORT_PERIOD_KEY = "EPScan_time"
# 主動上報の周期 = 10 ms + N × 5 ms(N = EPScan_time。実機測定 el05_protocol.md §8.0。
# マニュアルの「1 = 10 ms」は誤り: N=0 → 10 ms、N=1 → 15 ms)
_REPORT_BASE_MS = 10
_REPORT_STEP_MS = 5


def _pipe(src: Future[Any], dst: Future[Any], fn: Callable[[Any], Any] | None = None) -> None:
    """src の結果(または例外)を dst へ写す。"""

    def done(f: Future[Any]) -> None:
        if f.cancelled():
            dst.set_exception(CancelledError())
            return
        exc = f.exception()
        if exc is not None:
            dst.set_exception(exc)
            return
        try:
            dst.set_result(f.result() if fn is None else fn(f.result()))
        except Exception as e:
            dst.set_exception(e)

    src.add_done_callback(done)


class Motor:
    def __init__(
        self,
        bus: BusManager,
        motor_id: int,
        profile: MotorProfile,
        host_id: int,
        *,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_S,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self.bus = bus
        self.motor_id = motor_id
        self.profile = profile
        self.host_id = host_id
        self.request_timeout = request_timeout
        self.retries = max(0, int(retries))  # 冪等な要求の再送回数(0 で再送なし)
        self.retry_count = 0  # このモータで再送した累計
        self.uid: bytes | None = None
        self.version: str | None = None
        self.version_info: VersionInfo | None = None
        self.params: dict[int, int | float] = {}  # 読み書きで確認できた最新値

        self._unwrap = PositionUnwrapper(profile.position.period)
        # ゼロ設定後など、次のフィードバックで合わせる mechPos
        self._pending_seed: float | None = None
        self._fb_listeners: list[Callable[[Feedback, float], None]] = []
        self._rx = RateCounter(1.0)
        self._state_lock = threading.Lock()
        self._state = MotorState(motor_id)

        self.last_stop_tx: float | None = None  # 最後の type 4 を実際に送信した時刻(clock.now())

        self._op_lock = threading.Lock()
        self._ops: deque[tuple[Callable[[], Future[Any]], Future[Any]]] = deque()
        self._busy = False

        self._unsubscribe = bus.subscribe(self._on_message)

    def close(self) -> None:
        """バス購読を外す。"""
        self._unsubscribe()

    # ------------------------------------------------------------------
    # 状態
    # ------------------------------------------------------------------
    @property
    def state(self) -> MotorState:
        s = self._state
        if s.online and s.last_rx is not None:
            now = clock.now()
            if now - s.last_rx > ONLINE_TIMEOUT_S:
                # 書き戻さない(RX スレッドの更新と競合させない)
                return replace(s, online=False, rx_rate=self._rx.rate(now))
        return s

    def _set_state(self, **changes: Any) -> MotorState:
        with self._state_lock:
            self._state = replace(self._state, **changes)
            return self._state

    def add_feedback_listener(self, cb: Callable[[Feedback, float], None]) -> Callable[[], None]:
        """フィードバック(type 2 / 24)を状態へ反映した直後に `cb(fb, position_multi)` を呼ぶ
        (RX スレッド。重い処理は不可)。戻り値は購読解除。"""
        self._fb_listeners.append(cb)

        def remove() -> None:
            try:
                self._fb_listeners.remove(cb)
            except ValueError:
                pass

        return remove

    def _on_message(self, msg: Message) -> None:
        """RX スレッドから呼ばれる。"""
        if isinstance(msg, Unknown) or msg.motor_id != self.motor_id:
            return
        now = clock.now()
        if isinstance(msg, Feedback):
            if msg.comm_type == 2 and try_parse_version(msg.frame) is not None:
                return  # バージョン応答は状態に入れない
            self._on_feedback(msg, now)
        elif isinstance(msg, ParamResponse):
            self._set_state(online=True, last_rx=now)
            if msg.ok and msg.value is not None:
                self._param_known(msg.index, msg.value)
        elif isinstance(msg, UidResponse):
            self.uid = msg.uid
            self._set_state(online=True, last_rx=now)
        elif isinstance(msg, FaultReport):
            self._set_state(online=True, last_rx=now)

    def _on_feedback(self, fb: Feedback, now: float) -> None:
        self._rx.add(now)
        with self._state_lock:
            multi = self._unwrap.update(fb.position)
            prev = self._state
            valid = prev.multi_valid
            if self._pending_seed is not None:
                seeded = self._unwrap.seed(self._pending_seed)
                self._pending_seed = None
                if seeded is not None:
                    multi, valid = seeded, True
            self._state = replace(
                prev,
                online=True,
                mode=fb.mode,
                fault_bits=fb.fault_bits,
                position=fb.position,
                position_multi=multi,
                multi_valid=valid,
                velocity=fb.velocity,
                torque=fb.torque,
                temperature=fb.temperature,
                last_rx=now,
                rx_rate=self._rx.rate(now),
                fb_t=fb.frame.timestamp or now,
            )
        for cb in list(self._fb_listeners):
            try:
                cb(fb, multi)
            except Exception:  # 購読側の失敗で RX スレッドを止めない
                pass
        events = self.bus.events
        if prev.mode != fb.mode:
            events.publish(
                MOTOR_MODE_CHANGED,
                {"motor_id": self.motor_id, "mode": fb.mode, "previous": prev.mode},
            )
        if prev.fault_bits != fb.fault_bits:
            events.publish(
                MOTOR_FAULT,
                {
                    "motor_id": self.motor_id,
                    "fault_bits": fb.fault_bits,
                    "previous": prev.fault_bits,
                    "source": "feedback",
                },
            )

    def _param_known(self, index: int, value: int | float) -> None:
        self.params[index] = value
        key: str | None = None
        try:
            pd = self.profile.param(index)
        except KeyError:
            pd = None
        if pd is not None:
            key = pd.key
            if pd.key == _RUN_MODE_KEY:
                try:
                    self._set_state(run_mode=RunMode(int(value)))
                except ValueError:
                    self._set_state(run_mode=None)
        self.bus.events.publish(
            PARAM_UPDATED, {"motor_id": self.motor_id, "index": index, "key": key, "value": value}
        )

    # ------------------------------------------------------------------
    # 要求の直列化
    # ------------------------------------------------------------------
    def _submit(self, start: Callable[[], Future[Any]], *, front: bool = False) -> Future[Any]:
        outer: Future[Any] = Future()
        with self._op_lock:
            if front:
                self._ops.appendleft((start, outer))
            else:
                self._ops.append((start, outer))
        self._pump()
        return outer

    def _pump(self) -> None:
        while True:
            with self._op_lock:
                if self._busy or not self._ops:
                    return
                start, outer = self._ops.popleft()
                self._busy = True
            if not outer.set_running_or_notify_cancel():  # 開始前にキャンセルされた
                with self._op_lock:
                    self._busy = False
                continue
            try:
                inner = start()
            except BaseException as e:
                with self._op_lock:
                    self._busy = False
                outer.set_exception(e)
                continue
            inner.add_done_callback(lambda f, o=outer: self._finish(o, f))
            return

    def _finish(self, outer: Future[Any], inner: Future[Any]) -> None:
        try:
            if inner.cancelled():
                outer.set_exception(CancelledError())
            elif (exc := inner.exception()) is not None:
                outer.set_exception(exc)
            else:
                outer.set_result(inner.result())
        finally:
            with self._op_lock:
                self._busy = False
            self._pump()

    # ------------------------------------------------------------------
    # 要求の部品
    # ------------------------------------------------------------------
    def _req(
        self,
        frame: CanFrame,
        match: Callable[[Message], bool],
        priority: Priority = Priority.REQUEST,
        raw_match: Callable[[CanFrame], bool] | None = None,
        on_tx: Callable[[float], None] | None = None,
    ) -> Future[Any]:
        return self.bus.request(frame, match, self.request_timeout, priority, raw_match, on_tx)

    def _req_retry(
        self,
        what: str,
        frame: CanFrame,
        match: Callable[[Message], bool],
        priority: Priority = Priority.REQUEST,
        on_tx: Callable[[float], None] | None = None,
        *,
        type2: bool = True,
    ) -> Future[Any]:
        """冪等な要求: `RequestTimeout` なら最大 `self.retries` 回、フレームを再送する。

        各試行は独立した `BusManager.request`(TX 時刻で応答の受理開始を決める)。再送ごとに
        `bus.note_retry()` と `MOTOR_RETRY` イベント。キャンセル・送信失敗(TransportError)など
        タイムアウト以外の失敗は再送しない。`type2`: 応答が type 2(モータ ID のみで照合)の要求は、
        再送で成功した場合に完了を `RETRY_SETTLE_S` 遅らせる(前の試行の遅れた応答の混入防止)。
        """
        out: Future[Any] = Future()

        def finish(result: Any) -> None:
            try:
                out.set_result(result)
            except Exception:  # out がキャンセル済みなど
                pass

        def fail(e: BaseException) -> None:
            try:
                out.set_exception(e)
            except Exception:
                pass

        def attempt(n: int) -> None:
            try:
                f = self._req(frame, match, priority, None, on_tx)
            except BaseException as e:
                fail(e)
                return

            def done(f: Future[Any]) -> None:
                try:
                    if f.cancelled():
                        fail(CancelledError())
                        return
                    exc = f.exception()
                    if isinstance(exc, RequestTimeout) and n < self.retries:
                        self.retry_count += 1
                        self.bus.note_retry()
                        self.bus.events.publish(
                            MOTOR_RETRY,
                            {
                                "motor_id": self.motor_id,
                                "what": what,
                                "attempt": n + 2,
                                "error": exc,
                            },
                        )
                        attempt(n + 1)
                        return
                    if exc is not None:
                        fail(exc)
                        return
                    if n > 0 and type2:
                        t = threading.Timer(RETRY_SETTLE_S, finish, args=(f.result(),))
                        t.daemon = True
                        t.start()
                    else:
                        finish(f.result())
                except BaseException as e:
                    fail(e)

            f.add_done_callback(done)

        attempt(0)
        return out

    def _is_feedback(self, m: Message) -> bool:
        """type 1/3/4/6/18/22 への応答は type 2(モータ ID 一致のみで照合)。"""
        return (
            isinstance(m, Feedback)
            and m.motor_id == self.motor_id
            and m.comm_type == CommType.FEEDBACK
        )

    def _is_any_report(self, m: Message) -> bool:
        return isinstance(m, Feedback) and m.motor_id == self.motor_id

    def _is_version_frame(self, frame: CanFrame) -> bool:
        info = try_parse_version(frame)
        return info is not None and info.motor_id == self.motor_id

    def _lookup(self, key_or_index: int | str) -> ParamDef:
        try:
            return self.profile.param(key_or_index)
        except KeyError as e:
            raise ProtocolError(str(e.args[0])) from None

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------
    def get_uid(self) -> Future[UidResponse]:
        frame = private.get_uid(self.motor_id, self.host_id)

        def match(m: Message) -> bool:
            return isinstance(m, UidResponse) and m.motor_id == self.motor_id

        return self._submit(lambda: self._req_retry("get_uid", frame, match, type2=False))

    def read_version(self) -> Future[VersionInfo]:
        """type 4 方式(EL05 マニュアル)→ タイムアウトなら type 26 を試す。

        type 4 は運転中に送ると停止してしまう(§2.6 注意 1)ため、運転中(mode=MOTOR)と
        分かっているときは type 26 のみ。応答は `try_parse_version` に合致するフレームだけ
        受理する(type 26 が通常の type 2 を返すだけの機種では RequestTimeout)。
        """
        return self._submit(self._start_version)

    def _start_version(self) -> Future[Any]:
        out: Future[Any] = Future()
        attempts = [True] if self._state.mode == MotorMode.MOTOR else [False, True]

        def attempt(i: int) -> None:
            try:
                frame = private.read_version(self.motor_id, self.host_id, alt=attempts[i])
                f = self._req(frame, lambda _m: False, raw_match=self._is_version_frame)
            except Exception as e:
                out.set_exception(e)
                return

            def done(f: Future[Any]) -> None:
                try:
                    if f.cancelled():
                        raise CancelledError()
                    exc = f.exception()
                    if isinstance(exc, RequestTimeout) and i + 1 < len(attempts):
                        attempt(i + 1)
                        return
                    if exc is not None:
                        raise exc
                    info = try_parse_version(f.result().frame)
                    if info is None:  # raw_match を通ったので通常は起きない
                        raise ProtocolError("バージョン応答を解釈できません")
                    self.version = info.version
                    self.version_info = info
                    out.set_result(info)
                except BaseException as e:
                    out.set_exception(e)

            f.add_done_callback(done)

        attempt(0)
        return out

    def read_param(self, key_or_index: int | str) -> Future[ParamResponse]:
        """type 17。読出失敗(状態バイト 0x01)は ok=False の ParamResponse で解決する。"""
        index = key_or_index if isinstance(key_or_index, int) else self._lookup(key_or_index).index
        if not 0 <= index <= 0xFFFF:
            raise ProtocolError(f"パラメータ index が範囲外です: {index}")
        return self._submit(lambda: self._read_req(index))

    def _read_req(self, index: int) -> Future[Any]:
        frame = private.read_param(self.motor_id, self.host_id, index)

        def match(m: Message) -> bool:
            return isinstance(m, ParamResponse) and m.motor_id == self.motor_id and m.index == index

        return self._req_retry(f"read 0x{index:04X}", frame, match, type2=False)

    def read_all_params(self) -> Future[dict[int, ParamResponse]]:
        """プロファイルの全パラメータを順に読む。ok=False の応答も結果に含める。

        1 件ずつ要求を出すので、間に `disable()` などが割り込める。
        連続 3 回タイムアウトしたら RequestTimeout で打ち切る(モータ不在)。
        個別のタイムアウトは結果から欠落させて続行する。
        """
        out: Future[dict[int, ParamResponse]] = Future()
        indices = [p.index for p in self.profile.params]
        results: dict[int, ParamResponse] = {}

        def step(i: int, timeouts: int) -> None:
            if i >= len(indices):
                out.set_result(results)
                return
            try:
                f = self.read_param(indices[i])
            except Exception as e:
                out.set_exception(e)
                return

            def done(f: Future[Any]) -> None:
                try:
                    if f.cancelled():
                        raise CancelledError()
                    exc = f.exception()
                    if isinstance(exc, RequestTimeout):
                        if timeouts + 1 >= READ_ALL_ABORT_AFTER:
                            raise exc
                        step(i + 1, timeouts + 1)
                        return
                    if exc is not None:
                        raise exc
                    results[indices[i]] = f.result()
                    step(i + 1, 0)
                except BaseException as e:
                    out.set_exception(e)

            f.add_done_callback(done)

        step(0, 0)
        return out

    def check_write(
        self, key_or_index: int | str, value: int | float
    ) -> tuple[ParamDef, int | float]:
        """`write_param` の検証だけ(送信しない)。違反は ProtocolError。(定義, 検証値)を返す。"""
        pd = self._lookup(key_or_index)
        return pd, self._validate_write(pd, value)

    def write_param(self, key_or_index: int | str, value: int | float) -> Future[Feedback]:
        """type 18。dtype・範囲・書込可否をプロファイルで検証(違反は ProtocolError)。

        応答は type 2(書込の成否は判別できない。読み戻しで確認する)。
        """
        pd, checked = self.check_write(key_or_index, value)
        return self._submit(lambda: self._write_req(pd, checked))

    def _validate_write(self, pd: ParamDef, value: int | float) -> int | float:
        if not pd.writable:
            raise ProtocolError(f"{pd.key} (0x{pd.index:04X}) は書込不可です")
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ProtocolError(f"{pd.key}: 数値が必要です: {value!r}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ProtocolError(f"{pd.key}: 有限の数値が必要です: {value!r}")
        if pd.dtype.is_integer:
            if int(value) != value:
                raise ProtocolError(f"{pd.key}: 整数が必要です: {value!r}")
            v: int | float = int(value)
        else:
            v = float(value)
        if pd.min is not None and v < pd.min:
            raise ProtocolError(f"{pd.key}: {v} は下限 {pd.min} 未満です")
        if pd.max is not None and v > pd.max:
            raise ProtocolError(f"{pd.key}: {v} は上限 {pd.max} 超です")
        private.write_param(self.motor_id, self.host_id, pd.index, pd.dtype, v)  # dtype 幅の検証
        if pd.key == _RUN_MODE_KEY:
            try:
                RunMode(int(v))
            except ValueError:
                raise ProtocolError(f"未定義の run_mode です: {v}") from None
            # 運転中のモード切替は禁止(el05_protocol.md §3.1)。不明(None)は許す。
            if self._state.mode in (MotorMode.MOTOR, MotorMode.CALI):
                raise ProtocolError("run_mode は停止(Reset)中にのみ変更できます")
        return v

    def _write_req(self, pd: ParamDef, value: int | float) -> Future[Any]:
        frame = private.write_param(self.motor_id, self.host_id, pd.index, pd.dtype, value)
        fut = self._req_retry(f"write 0x{pd.index:04X}", frame, self._is_feedback)

        def after(f: Future[Any]) -> None:
            if not f.cancelled() and f.exception() is None:
                self._param_known(pd.index, value)

        fut.add_done_callback(after)
        return fut

    def enable(self) -> Future[Feedback]:
        frame = private.enable(self.motor_id, self.host_id)
        return self._submit(lambda: self._req_retry("enable", frame, self._is_feedback))

    def disable(self, clear_fault: bool = False) -> Future[Feedback]:
        """type 4(停止)。最優先: 待ち行列の先頭に入り、ESTOP 優先度で送る。"""
        frame = private.stop(self.motor_id, self.host_id, clear_fault)
        return self._submit(
            lambda: self._req_retry(
                "disable", frame, self._is_feedback, Priority.ESTOP, self._note_stop_tx
            ),
            front=True,
        )

    def _note_stop_tx(self, t: float) -> None:
        self.last_stop_tx = t

    def is_reset_since(self, t: float | None) -> bool:
        """`t` 以降に受信したフィードバックが Reset を示しているか(`t` 以前の古い応答は無視)。

        type 4 への応答は type 2 だが、送信前に出ていた古い応答(運転中の指令への返事)や、
        受信処理の遅れで後から処理される古いフィードバックと区別するため、受信時刻で判定する。
        """
        s = self._state
        return (
            t is not None
            and s.mode == MotorMode.RESET
            and s.fb_t is not None
            and s.fb_t >= t
        )

    def set_run_mode(self, mode: RunMode) -> Future[Feedback]:
        """run_mode の変更。停止(state.mode == RESET)でなければ ProtocolError。

        mode が未知(まだフィードバックを受けていない)場合も ProtocolError
        (先に `disable()` か主動上報でモードを確定させる)。
        """
        try:
            rm = RunMode(mode)
        except ValueError:
            raise ProtocolError(f"未定義の run_mode です: {mode!r}") from None
        cur = self._state.mode
        if cur != MotorMode.RESET:
            what = "不明" if cur is None else cur.name
            raise ProtocolError(f"run_mode は停止(Reset)中にのみ変更できます(現在のモード: {what})")
        return self.write_param(_RUN_MODE_KEY, int(rm))

    def set_active_report(self, on: bool, period_ms: int | None = None) -> Future[None]:
        """type 24。`period_ms` 指定時は先に EPScan_time を書く(周期 = 10 + 5·N ms。N=0 → 10 ms)。

        最初の Feedback(OFF なら type 2 の応答 1 件、ON なら最初の上報)で完了する。
        応答がなければ再送する(冪等)。
        """
        frame = private.set_active_report(self.motor_id, self.host_id, on)
        pd: ParamDef | None = None
        n = 0
        if period_ms is not None:
            if period_ms < _REPORT_BASE_MS:
                raise ProtocolError(f"主動上報の周期は {_REPORT_BASE_MS} ms 以上です: {period_ms}")
            pd = self._lookup(_REPORT_PERIOD_KEY)
            n = round((period_ms - _REPORT_BASE_MS) / _REPORT_STEP_MS)
            n = self._validate_write(pd, n)  # type: ignore[assignment]

        def start() -> Future[Any]:
            out: Future[Any] = Future()

            def send_report() -> None:
                # OFF の応答は type 2 が 1 件。ON 中は type 24 の上報が流れているので、OFF では
                # type 2 だけを待つ(上報フレームを OFF の応答と取り違えない)。ON は最初の上報。
                match = self._is_any_report if on else self._is_feedback
                try:
                    f = self._req_retry(f"active_report {'on' if on else 'off'}", frame, match)
                except Exception as e:
                    out.set_exception(e)
                    return
                _pipe(f, out, lambda _m: None)

            if pd is None:
                send_report()
                return out
            wf = self._write_req(pd, n)

            def written(f: Future[Any]) -> None:
                if f.cancelled() or f.exception() is not None:
                    _pipe(f, out)
                else:
                    send_report()

            wf.add_done_callback(written)
            return out

        return self._submit(start)

    def save_params(self) -> Future[Feedback | UidResponse]:
        """type 22(§2.12)。再送しない(冪等でない)。

        実機 EL05 は type 2 ではなく **type 0(UID、宛先 0xFE)** で応答する(実測 HW-6、§8.0)。
        他のファームのために type 2 のフィードバックも受理する。
        なお type 22 は 0x70xx の運転パラメータを保存しない(電源再投入で工場値へ戻る。実測)。
        """
        frame = private.save_params(self.motor_id, self.host_id)

        def match(m: Message) -> bool:
            return self._is_feedback(m) or self._is_uid_reply(m)

        return self._submit(lambda: self._req(frame, match))

    def set_zero(self) -> Future[Feedback]:
        """type 6(§2.7)。再送しない。run_mode が運控(0)/ CSP(5)のときだけ有効(§2.7)。"""
        frame = private.set_zero(self.motor_id, self.host_id)
        return self._submit(lambda: self._req(frame, self._is_feedback))

    def set_can_id(self, new_id: int) -> Future[UidResponse]:
        """type 7(§2.8)。応答は **新 ID から** の type 0(UID 付き)。再送しない。

        応答を受けても `self.motor_id` は変えない(付け替えは呼び出し側が `rekey` で行う。
        Session / MotorService の登録も同時に付け替える必要があるため)。
        """
        frame = private.set_can_id(self.motor_id, self.host_id, new_id)  # 範囲検証(同期)

        def match(m: Message) -> bool:
            return isinstance(m, UidResponse) and m.motor_id == new_id

        return self._submit(lambda: self._req(frame, match))

    def set_baud(self, code: int) -> Future[UidResponse]:
        """type 23(§2.13)。1=1M / 2=500k / 3=250k / 4=125k。再投入後有効。再送しない。"""
        frame = private.set_baud(self.motor_id, self.host_id, code)
        return self._submit(lambda: self._req(frame, self._is_uid_reply))

    def switch_protocol(self, protocol: int) -> Future[UidResponse]:
        """type 25(§2.15)。0=独自 / 1=CANopen / 2=MIT。再投入後有効。再送しない。"""
        frame = private.switch_protocol(self.motor_id, self.host_id, protocol)
        return self._submit(lambda: self._req(frame, self._is_uid_reply))

    def _is_uid_reply(self, m: Message) -> bool:
        return isinstance(m, UidResponse) and m.motor_id == self.motor_id

    def seed_multi(self, mech_pos: float) -> float | None:
        """多回転角を mechPos(折り返さない多回転値)に合わせ、合わせた後の `position_multi` を返す。

        展開オフセットは k·8π(k = round((mechPos - 折り返し位置) / 8π))。折り返し位置を
        まだ受信していなければ何もせず None(呼び出し側が失敗として扱う)。
        """
        if not math.isfinite(mech_pos):
            raise ProtocolError(f"mechPos が有限値ではありません: {mech_pos!r}")
        with self._state_lock:
            multi = self._unwrap.seed(mech_pos)
            if multi is None:
                return None
            self._pending_seed = None
            self._state = replace(self._state, position_multi=multi, multi_valid=True)
            return multi

    def sync_multi(self) -> Future[float]:
        """mechPos(0x7019)を読んで多回転角を較正する(冪等なので再送する)。

        結果は較正後の `position_multi`。mechPos を読めない(`RequestTimeout` / 読出失敗)か、
        折り返し位置のフィードバックをまだ受信していなければ失敗(`multi_valid` は変わらない)。
        """
        return self._submit(self._start_sync)

    def _start_sync(self) -> Future[Any]:
        out: Future[Any] = Future()
        try:
            f = self._read_req(self._lookup("mechPos").index)
        except Exception as e:
            out.set_exception(e)
            return out

        def done(f: Future[Any]) -> None:
            try:
                if f.cancelled():
                    raise CancelledError()
                if (exc := f.exception()) is not None:
                    raise exc
                r = f.result()
                if not r.ok or r.value is None:
                    raise ProtocolError(f"ID {self.motor_id}: mechPos を読み出せませんでした")
                multi = self.seed_multi(float(r.value))
                if multi is None:
                    raise ProtocolError(
                        f"ID {self.motor_id}: 位置 FB 未受信のため多回転角を合わせられません"
                    )
                out.set_result(multi)
            except BaseException as e:
                out.set_exception(e)

        f.add_done_callback(done)
        return out

    def reset_position_unwrap(self, known_multi: float | None = None) -> None:
        """多回転角の展開をやり直す(ゼロ点設定で位置が跳んだ後)。

        `multi_valid` は False に戻る。`known_multi`(ゼロ設定後に読んだ mechPos など)を渡すと、
        次のフィードバックでその値に合わせて `multi_valid` が True に戻る。
        """
        with self._state_lock:
            self._unwrap.reset()
            self._pending_seed = known_multi
            self._state = replace(self._state, multi_valid=False)

    def rekey(self, new_id: int) -> None:
        """CAN ID の付け替え(type 7 の成功後)。以降の送信・受信照合は新 ID。

        Session / MotorService の登録の付け替えは呼び出し側(`MotorService.set_can_id`)。
        """
        if not 0 <= new_id <= 127:
            raise ProtocolError(f"CAN ID が範囲外です: {new_id}")
        with self._state_lock:
            self.motor_id = new_id
            self._state = replace(self._state, motor_id=new_id)
