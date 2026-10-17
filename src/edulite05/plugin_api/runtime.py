"""実行中の `MotorService`(接続中のとき)をプラグインへ渡す置き場。Qt を使わない。

GUI では接続は起動後に行うため、プラグインの `activate()` 時点ではサービスが無い。
プラグインは `ctx.service`(その時点のサービスまたは None)を読むか、
`ctx.on_service(cb)` で接続・切断に追従する。`CoreBridge` が接続・切断のたびに
`set_service` を呼ぶ。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

_lock = threading.Lock()
_service: Any = None
_listeners: list[Callable[[Any], None]] = []


def current_service() -> Any:
    """接続中の `MotorService`。未接続なら None。"""
    return _service


def set_service(service: Any) -> None:
    """接続したとき `MotorService`、切断したとき None を設定する(購読者へ通知)。"""
    global _service
    with _lock:
        _service = service
        listeners = list(_listeners)
    for cb in listeners:
        try:
            cb(service)
        except Exception:  # noqa: BLE001 - プラグインの例外で接続処理を止めない
            import logging

            logging.getLogger("edulite05.plugins").exception("service リスナーが例外を出しました")


def on_service_changed(cb: Callable[[Any], None]) -> Callable[[], None]:
    """購読する。戻り値は購読解除の関数。呼ばれるスレッドは接続処理側(UI スレッドとは限らない)。"""
    with _lock:
        _listeners.append(cb)

    def unsubscribe() -> None:
        with _lock:
            if cb in _listeners:
                _listeners.remove(cb)

    return unsubscribe
