"""EventBus: スレッド安全な pub/sub(phase1.md §1.6、architecture.md §4.7)。

`publish` は呼び出しスレッドで同期的にコールバックを実行する。コールバックの例外は
ログに残して握りつぶす(RX/TX スレッドを巻き込まない)。GUI 側は Qt の QueuedConnection
で main thread へ移すアダプタを被せる。

ペイロードは dict(キーはトピックごとに下に記す)。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

Unsubscribe = Callable[[], None]
Callback = Callable[[Any], None]

# --- トピック定数 -------------------------------------------------------
CONNECTION_CHANGED = "connection.changed"  # {"connected": bool}
MOTOR_DISCOVERED = "motor.discovered"  # {"motor_id": int, "uid": bytes | None}
MOTOR_FAULT = "motor.fault"  # {"motor_id", "fault_bits"|"fault"/"warning", "source"}
MOTOR_MODE_CHANGED = "motor.mode_changed"  # {"motor_id", "mode", "previous"}
MOTOR_RETRY = "motor.retry"  # {"motor_id", "what": str, "attempt": int(次の試行番号 2..), "error"}
PARAM_UPDATED = "param.updated"  # {"motor_id", "index", "key", "value"}
ESTOP = "estop"  # {"motor_ids": list[int]}
LOG = "log"  # {"level": str, "message": str}
BUS_ERROR = "bus.error"  # {"where": str, "error": BaseException}
BUS_UNKNOWN = "bus.unknown"  # Unknown メッセージ(宛先違いを除く)
SCAN_PROGRESS = "bus.scan.progress"  # {"done": int, "total": int}


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # copy-on-write: publish はロックなしでスナップショットを走査する
        self._subs: dict[str, tuple[Callback, ...]] = {}

    def subscribe(self, topic: str, cb: Callback) -> Unsubscribe:
        with self._lock:
            self._subs[topic] = (*self._subs.get(topic, ()), cb)

        def unsubscribe() -> None:
            with self._lock:
                cur = self._subs.get(topic, ())
                if cb in cur:
                    lst = list(cur)
                    lst.remove(cb)  # 同じ cb の重複登録は 1 件ずつ外す
                    self._subs[topic] = tuple(lst)

        return unsubscribe

    def publish(self, topic: str, payload: Any = None) -> None:
        for cb in self._subs.get(topic, ()):
            try:
                cb(payload)
            except Exception:
                log.exception("EventBus: %s のコールバックで例外", topic)
