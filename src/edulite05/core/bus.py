"""BusManager: TX/RX スレッド・優先度キュー・要求応答の照合(phase1.md §1.7)。

構成(can_transport.md §5.6、公式 SDK の問題点 §3 を避ける設計):
- **TX スレッド 1 本**: `(優先度, 連番)` の heap + Condition。ESTOP < CONTROL < REQUEST <
  BACKGROUND、同優先度は FIFO。送信は TX ロックの下で行う。
- **`send_now`**: キューを飛ばし TX ロックだけで即送信(E-STOP 用)。TX スレッドは
  send_now の待ち手がいる間は次の送信を譲る。
- **RX スレッド 1 本**: `transport.recv(0.02)` → decode → 購読者 → pending 照合(raw_match →
  match)→ イベント。購読者を先にするのは、Future 完了時に Motor.state が応答を反映済み
  であるようにするため。**ブロッキング読みなし**: 毎ループ末尾に `core.clock`(高分解能の
  単調時計)で期限を確認し `RequestTimeout` にする。
- **pending は複数同時**: 要求ごとに独立(SDK の「モータ 1 スロット」問題を避ける)。
  照合はフレームごとの述語で行う(「次に来た type 2」ではなく ID/index/データで判別)。
- **ID 範囲の制限なし**: モータ ID 127 を含め全 ID を扱う(SDK の ≤16 制限を避ける)。
- どの例外でもスレッドは死なない(捕捉 → BUS_ERROR イベント → 継続)。

バージョン応答(type 2 の ID・データ `00 C4 56 ..`、el05_protocol.md §2.6)は通常の
フィードバックとしてデコードすると値が壊れるため、`try_parse_version` に合致する
フレームは購読者・照合には `Unknown("version reply")` として渡す。要求側は `raw_match`
でフレームを直接判定する。
"""

from __future__ import annotations

import heapq
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass
from enum import IntEnum

from edulite05.core import clock
from edulite05.core.errors import ProtocolError, RequestTimeout, TransportError
from edulite05.core.events import (
    BUS_ERROR,
    BUS_UNKNOWN,
    MOTOR_FAULT,
    SCAN_PROGRESS,
    EventBus,
    Unsubscribe,
)
from edulite05.core.protocol import private
from edulite05.core.protocol.private import (
    DecodeContext,
    FaultReport,
    Feedback,
    Message,
    UidResponse,
    Unknown,
    decode,
    try_parse_version,
)
from edulite05.core.transport.base import CanFrame, CanTransport, Direction
from edulite05.core.transport.framelog import FrameLogWriter

log = logging.getLogger(__name__)

RX_POLL_S = 0.02  # transport.recv のタイムアウト(= 期限判定の最大遅れ)
QUEUE_GRACE_S = 1.0  # 未送信の pending が送信待ちで失効するまでの猶予(timeout に加算)
STOP_JOIN_S = 2.0
ERROR_LOG_INTERVAL_S = 1.0  # 同じ場所の連続エラーの通知間隔


class Priority(IntEnum):
    ESTOP = 0
    CONTROL = 1
    REQUEST = 2
    BACKGROUND = 3


@dataclass(frozen=True, slots=True)
class BusStats:
    tx_fps: float
    rx_fps: float
    queue_len: int
    timeouts: int
    parse_errors: int
    retries: int = 0  # 冪等要求の再送回数(Motor が `note_retry` で加算)


class RateCounter:
    """直近 `window` 秒のイベント数(スレッド安全)。"""

    __slots__ = ("window", "_lock", "_times")

    def __init__(self, window: float = 1.0) -> None:
        self.window = window
        self._lock = threading.Lock()
        self._times: deque[float] = deque()

    def add(self, now: float | None = None) -> None:
        t = clock.now() if now is None else now
        with self._lock:
            self._times.append(t)
            self._prune(t)

    def count(self, now: float | None = None) -> int:
        t = clock.now() if now is None else now
        with self._lock:
            self._prune(t)
            return len(self._times)

    def rate(self, now: float | None = None) -> float:
        return self.count(now) / self.window

    def _prune(self, now: float) -> None:
        lim = now - self.window
        times = self._times
        while times and times[0] < lim:
            times.popleft()


class _Pending:
    __slots__ = ("frame", "match", "raw_match", "timeout", "future", "deadline", "armed", "on_tx")

    def __init__(
        self,
        frame: CanFrame,
        match: Callable[[Message], bool],
        raw_match: Callable[[CanFrame], bool] | None,
        timeout: float,
        deadline: float,
    ) -> None:
        self.frame = frame
        self.match = match
        self.raw_match = raw_match
        self.timeout = timeout
        self.future: Future[Message] = Future()
        self.deadline = deadline
        self.armed = False  # 送信済み(= 応答を受理してよい)
        self.on_tx: Callable[[float], None] | None = None


def _settle(fut: Future, *, result: object = None, exc: BaseException | None = None) -> None:
    """キャンセル済みの Future に対する InvalidStateError を無視して完了させる。"""
    try:
        if exc is not None:
            fut.set_exception(exc)
        else:
            fut.set_result(result)
    except InvalidStateError:
        pass


class BusManager:
    def __init__(
        self,
        transport: CanTransport,
        ctx: DecodeContext,
        events: EventBus | None = None,
        framelog: FrameLogWriter | None = None,
    ) -> None:
        self.transport = transport
        self.ctx = ctx
        self.events = events if events is not None else EventBus()
        self.framelog = framelog

        self._heap: list[tuple[int, int, CanFrame, _Pending | None]] = []
        self._inflight: tuple[int, int, CanFrame, _Pending | None] | None = None
        self._seq = 0
        self._tx_cv = threading.Condition()
        self._tx_lock = threading.Lock()
        self._urgent = 0  # send_now の待ち手の数(TX スレッドが譲る)
        self._urgent_lock = threading.Lock()

        self._pend_lock = threading.Lock()
        self._pendings: list[_Pending] = []
        self._subs: tuple[Callable[[Message], None], ...] = ()
        self._subs_lock = threading.Lock()

        self._tx_rate = RateCounter()
        self._rx_rate = RateCounter()
        self._timeouts = 0
        self._retries = 0
        self._decode_errors = 0

        self._stop_evt = threading.Event()
        self._rx_thread: threading.Thread | None = None
        self._tx_thread: threading.Thread | None = None
        self._running = False
        self._last_err: dict[str, float] = {}

    # ------------------------------------------------------------------
    # ライフサイクル
    # ------------------------------------------------------------------
    @property
    def host_id(self) -> int:
        return self.ctx.host_id

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        """RX/TX スレッドを起動する(transport.open() 済み前提)。"""
        if self._running:
            raise RuntimeError("BusManager は既に開始しています")
        self._stop_evt.clear()
        self._running = True
        self._rx_thread = threading.Thread(
            target=self._rx_loop, name="edulite05-bus-rx", daemon=True
        )
        self._tx_thread = threading.Thread(
            target=self._tx_loop, name="edulite05-bus-tx", daemon=True
        )
        self._rx_thread.start()
        self._tx_thread.start()

    def stop(self) -> None:
        """スレッドを停止して join する。transport は閉じない。残りの pending は失敗させる。"""
        if not self._running:
            return
        self._running = False
        self._stop_evt.set()
        with self._tx_cv:
            self._tx_cv.notify_all()
        me = threading.current_thread()
        for t in (self._tx_thread, self._rx_thread):
            if t is not None and t is not me:
                t.join(STOP_JOIN_S)
        self._tx_thread = self._rx_thread = None
        with self._tx_cv:
            self._heap.clear()
        with self._pend_lock:
            pend, self._pendings = self._pendings, []
        for p in pend:
            _settle(p.future, exc=TransportError("バスが停止しました"))

    # ------------------------------------------------------------------
    # 送信
    # ------------------------------------------------------------------
    def send(self, frame: CanFrame, priority: Priority = Priority.REQUEST) -> None:
        """TX キューへ積む(非ブロッキング)。"""
        self._require_running()
        self._push(frame, priority, None)

    def send_many(self, frames: Sequence[CanFrame], priority: Priority = Priority.CONTROL) -> None:
        """複数フレームを 1 回のロックで TX キューへ積む(非ブロッキング)。

        制御ループが 1 周期分の全モータの指令をまとめて積むのに使う。積んだ順(FIFO)で送られる。
        """
        self._require_running()
        if not frames:
            return
        with self._tx_cv:
            for frame in frames:
                self._seq += 1
                heapq.heappush(self._heap, (int(priority), self._seq, frame, None))
            self._tx_cv.notify()

    def queue_len(self) -> int:
        """TX キューの未送信フレーム数。"""
        return self._queue_len()

    def purge_control(self, motor_ids: Iterable[int]) -> int:
        """TX キュー内の **CONTROL 優先度** のフレームのうち、`motor_ids` 宛てのものを捨てる。

        運転停止・無効化の直後に、古い制御指令(速度指令など)が停止指令の後ろから送られて
        モータを再び動かさないようにするため(宛先 = CAN ID 下位 8 bit)。
        捨てた数を返す。ESTOP / REQUEST / BACKGROUND と、応答待ち(pending)付きは触らない。
        """
        ids = {int(i) & 0xFF for i in motor_ids}
        if not ids:
            return 0

        def doomed(e: tuple[int, int, CanFrame, _Pending | None]) -> bool:
            return e[0] == Priority.CONTROL and e[3] is None and (e[2].can_id & 0xFF) in ids

        with self._tx_cv:
            keep = [e for e in self._heap if not doomed(e)]
            n = len(self._heap) - len(keep)
            if n:
                heapq.heapify(keep)
                self._heap = keep
            inflight = self._inflight  # TX スレッドが取り出し済み・送信前のフレームも捨てる
            if inflight is not None and doomed(inflight):
                self._inflight = None
                n += 1
            return n

    def send_now(self, frames: Sequence[CanFrame]) -> None:
        """E-STOP 用: キューを飛ばして即送信する(TX ロックのみ)。

        送信に失敗しても残りのフレームは試みる。最初の例外を最後に送出する。
        """
        if not frames:
            return
        first: BaseException | None = None
        with self._urgent_lock:
            self._urgent += 1
        try:
            with self._tx_lock:
                now = clock.now()
                stamped = [f.with_meta(timestamp=now, direction=Direction.TX) for f in frames]
                try:
                    self.transport.send_many(stamped)
                    sent = stamped
                except Exception as e:
                    first = e
                    sent = []
                    for f in stamped:  # 1 本ずつ再試行(重複送信は stop 系なので無害)
                        try:
                            self.transport.send(f)
                            sent.append(f)
                        except Exception as e2:
                            first = first or e2
                    if len(sent) == len(stamped):
                        first = None  # 個別送信が全部通った
                self._after_send(sent)
        finally:
            with self._urgent_lock:
                self._urgent -= 1
        if first is not None:
            raise first

    def request(
        self,
        frame: CanFrame,
        match: Callable[[Message], bool],
        timeout: float = 0.1,
        priority: Priority = Priority.REQUEST,
        raw_match: Callable[[CanFrame], bool] | None = None,
        on_tx: Callable[[float], None] | None = None,
    ) -> Future[Message]:
        """要求を送り、応答フレームで解決する Future を返す。

        照合: 受信ごとに `raw_match(frame)`(あれば)を先に、次に `match(message)` を判定する。
        期限(送信時刻 + timeout)を過ぎると `RequestTimeout`。送信失敗はその例外。
        `on_tx(t)` は送信の直前(transport.send の前)に TX スレッドから `t = clock.now()` で
        呼ばれる(それ以降に受信したフレームだけが「送信後の応答」)。
        """
        self._require_running()
        now = clock.now()
        p = _Pending(frame, match, raw_match, timeout, now + timeout + QUEUE_GRACE_S)
        p.on_tx = on_tx
        with self._pend_lock:
            self._pendings.append(p)
        try:
            self._push(frame, priority, p)
        except BaseException:
            self._drop_pending(p)
            raise
        return p.future

    def subscribe(self, cb: Callable[[Message], None]) -> Unsubscribe:
        """全受信メッセージの購読。コールバックは RX スレッドから呼ばれる。"""
        with self._subs_lock:
            self._subs = (*self._subs, cb)

        def unsubscribe() -> None:
            with self._subs_lock:
                if cb in self._subs:
                    lst = list(self._subs)
                    lst.remove(cb)
                    self._subs = tuple(lst)

        return unsubscribe

    def scan(
        self,
        ids: Iterable[int] = range(1, 128),
        gap_s: float = 0.002,
        settle_s: float = 0.3,
    ) -> Future[list[UidResponse]]:
        """各 ID へ type 0 を送り、応答(UID)を集める。呼び出しはブロックしない。

        全 ID の送信後 `settle_s` 待ってから完了する。結果はモータ ID 昇順。
        """
        self._require_running()
        id_list = list(ids)
        for i in id_list:
            if not 0 <= i <= 0xFF:
                raise ProtocolError(f"モータ ID が範囲外です: {i}")
        wanted = set(id_list)
        fut: Future[list[UidResponse]] = Future()
        found: dict[int, UidResponse] = {}

        def collect(m: Message) -> None:
            if isinstance(m, UidResponse) and m.motor_id in wanted:
                found.setdefault(m.motor_id, m)

        unsub = self.subscribe(collect)

        def run() -> None:
            try:
                total = len(id_list)
                for n, mid in enumerate(id_list, 1):
                    if not self._running:
                        raise TransportError("スキャン中にバスが停止しました")
                    self.send(private.get_uid(mid, self.ctx.host_id), Priority.BACKGROUND)
                    if n % 8 == 0 or n == total:
                        self.events.publish(SCAN_PROGRESS, {"done": n, "total": total})
                    if gap_s > 0:
                        time.sleep(gap_s)
                # 自分の送信がキューから出るまで待つ(上限あり)→ 応答待ち
                limit = clock.now() + 5.0
                while self._running and self._queue_len() > 0 and clock.now() < limit:
                    time.sleep(0.001)
                end = clock.now() + settle_s
                while self._running and (rem := end - clock.now()) > 0:
                    time.sleep(min(rem, 0.01))
                if not self._running:
                    raise TransportError("スキャン中にバスが停止しました")
                _settle(fut, result=[found[k] for k in sorted(found)])
            except BaseException as e:
                _settle(fut, exc=e)
            finally:
                unsub()

        threading.Thread(target=run, name="edulite05-bus-scan", daemon=True).start()
        return fut

    def stats(self) -> BusStats:
        now = clock.now()
        try:
            transport_errors = self.transport.stats().parse_errors
        except Exception:
            transport_errors = 0
        return BusStats(
            tx_fps=self._tx_rate.rate(now),
            rx_fps=self._rx_rate.rate(now),
            queue_len=self._queue_len(),
            timeouts=self._timeouts,
            parse_errors=transport_errors + self._decode_errors,
            retries=self._retries,
        )

    def note_retry(self) -> None:
        """冪等要求の再送を数える(`stats().retries`)。"""
        self._retries += 1

    # ------------------------------------------------------------------
    # 内部: 送信側
    # ------------------------------------------------------------------
    def _require_running(self) -> None:
        if not self._running:
            raise TransportError("バスが開始されていません")

    def _queue_len(self) -> int:
        with self._tx_cv:
            return len(self._heap)

    def _push(self, frame: CanFrame, priority: Priority, pending: _Pending | None) -> None:
        with self._tx_cv:
            self._seq += 1
            heapq.heappush(self._heap, (int(priority), self._seq, frame, pending))
            self._tx_cv.notify()

    def _drop_pending(self, p: _Pending) -> None:
        with self._pend_lock:
            try:
                self._pendings.remove(p)
            except ValueError:
                pass

    def _after_send(self, frames: Sequence[CanFrame]) -> None:
        now = clock.now()
        for f in frames:
            self._tx_rate.add(now)
            if self.framelog is not None:
                try:
                    self.framelog.write(f)
                except Exception as e:
                    self._error("framelog", e)

    def _tx_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                with self._tx_cv:
                    while not self._heap and not self._stop_evt.is_set():
                        self._tx_cv.wait(0.5)
                    if self._stop_evt.is_set():
                        return
                    entry = heapq.heappop(self._heap)
                    self._inflight = entry
                _, _, frame, pending = entry
                if pending is not None and pending.future.done():
                    self._inflight = None
                    self._drop_pending(pending)  # 送信前に失効/キャンセル済み → 送らない
                    continue
                while self._urgent > 0 and not self._stop_evt.is_set():
                    time.sleep(0.0002)  # send_now に譲る
                with self._tx_cv:
                    if self._inflight is not entry:
                        continue  # purge_control で捨てられた
                    self._inflight = None
                self._transmit(frame, pending)
            except Exception as e:  # 想定外でもスレッドは死なせない
                self._error("tx", e)

    def _transmit(self, frame: CanFrame, pending: _Pending | None) -> None:
        try:
            with self._tx_lock:
                now = clock.now()
                stamped = frame.with_meta(timestamp=now, direction=Direction.TX)
                if pending is not None:
                    pending.deadline = now + pending.timeout
                    pending.armed = True  # 応答は送信後のものだけ受理する
                    if pending.on_tx is not None:
                        try:
                            pending.on_tx(now)
                        except Exception as e:
                            self._error("on_tx", e)
                self.transport.send(stamped)
                self._after_send((stamped,))
        except Exception as e:
            self._error("tx", e)
            if pending is not None:
                self._drop_pending(pending)
                _settle(
                    pending.future,
                    exc=e if isinstance(e, TransportError) else TransportError(f"送信失敗: {e!r}"),
                )

    # ------------------------------------------------------------------
    # 内部: 受信側
    # ------------------------------------------------------------------
    def _rx_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                try:
                    frame = self.transport.recv(RX_POLL_S)
                except Exception as e:
                    self._error("recv", e)
                    self._stop_evt.wait(0.05)  # 壊れた transport で busy loop しない
                    frame = None
                if frame is not None:
                    self._handle_frame(frame)
                self._expire(clock.now())
            except Exception as e:  # 想定外でもスレッドは死なせない
                self._error("rx", e)

    def _handle_frame(self, frame: CanFrame) -> None:
        now = clock.now()
        if frame.timestamp == 0.0:
            frame = frame.with_meta(timestamp=now)
        self._rx_rate.add(now)
        if self.framelog is not None:
            try:
                self.framelog.write(frame)
            except Exception as e:
                self._error("framelog", e)
        try:
            msg: Message = decode(frame, self.ctx)
        except Exception as e:
            self._decode_errors += 1
            self._error("decode", e)
            msg = Unknown(frame, f"decode error: {e!r}")
        # バージョン応答は type 2 の ID なので Feedback になってしまう(値は無意味)。
        if isinstance(msg, Feedback) and msg.comm_type == 2:
            ver = try_parse_version(frame)
            if ver is not None:
                msg = Unknown(frame, "version reply", ver.motor_id)

        # 購読者(Motor の状態更新など)を先に走らせる: Future が完了した時点で
        # `Motor.state` が応答を反映済みになる(呼び出し側の読み取りが競合しない)。
        for cb in self._subs:
            try:
                cb(msg)
            except Exception as e:
                self._error("subscriber", e)

        self._resolve_pendings(frame, msg, now)

        if isinstance(msg, FaultReport):
            self.events.publish(
                MOTOR_FAULT,
                {
                    "motor_id": msg.motor_id,
                    "fault": msg.fault,
                    "warning": msg.warning,
                    "source": "type21",
                },
            )
        elif isinstance(msg, Unknown) and msg.reason != "not for host":
            self.events.publish(BUS_UNKNOWN, msg)

    def _resolve_pendings(self, frame: CanFrame, msg: Message, now: float) -> None:
        """raw_match → match の順に照合。ロックの外で Future を完了させる(コールバック対策)。"""
        with self._pend_lock:
            candidates = [p for p in self._pendings if p.armed and not p.future.done()]
        if not candidates:
            return
        hits: list[tuple[_Pending, BaseException | None]] = []
        taken: set[int] = set()
        for p in candidates:  # 1) raw_match
            if p.raw_match is None:
                continue
            try:
                if p.raw_match(frame):
                    hits.append((p, None))
                    taken.add(id(p))
            except Exception as e:
                hits.append((p, e))
                taken.add(id(p))
        for p in candidates:  # 2) match
            if id(p) in taken:
                continue
            try:
                if p.match(msg):
                    hits.append((p, None))
            except Exception as e:
                hits.append((p, e))
        if not hits:
            return
        with self._pend_lock:
            for p, _ in hits:
                try:
                    self._pendings.remove(p)
                except ValueError:
                    pass
        for p, err in hits:
            if err is not None:
                self._error("matcher", err)
                _settle(p.future, exc=err)
            else:
                _settle(p.future, result=msg)

    def _expire(self, now: float) -> None:
        with self._pend_lock:
            if not self._pendings:
                return
            keep: list[_Pending] = []
            expired: list[_Pending] = []
            for p in self._pendings:
                if p.future.done():
                    continue  # キャンセル済みなど
                (expired if now >= p.deadline else keep).append(p)
            self._pendings = keep
        for p in expired:
            self._timeouts += 1
            _settle(p.future, exc=RequestTimeout(f"応答がありません (timeout {p.timeout:g} s)"))

    def _error(self, where: str, exc: BaseException) -> None:
        now = clock.now()
        last = self._last_err.get(where)
        if last is not None and now - last < ERROR_LOG_INTERVAL_S:
            return  # 連続エラーでログ/イベントを埋めない
        self._last_err[where] = now
        log.warning("BusManager %s でエラー: %r", where, exc)
        self.events.publish(BUS_ERROR, {"where": where, "error": exc})
