"""EventBus のテスト(phase1.md §1.6)。"""

from __future__ import annotations

import threading

from edulite05.core.events import EventBus


def test_publish_to_subscribers_and_unsubscribe() -> None:
    bus = EventBus()
    got: list[object] = []
    unsub = bus.subscribe("a", got.append)
    bus.publish("a", 1)
    bus.publish("b", 2)  # 別トピックは届かない
    unsub()
    unsub()  # 冪等
    bus.publish("a", 3)
    assert got == [1]


def test_callback_exception_is_swallowed_and_others_run() -> None:
    bus = EventBus()
    got: list[object] = []

    def bad(_p: object) -> None:
        raise RuntimeError("boom")

    bus.subscribe("t", bad)
    bus.subscribe("t", got.append)
    bus.publish("t", "x")  # 例外は出ない
    assert got == ["x"]


def test_callback_runs_in_publisher_thread() -> None:
    bus = EventBus()
    seen: list[int] = []
    bus.subscribe("t", lambda _p: seen.append(threading.get_ident()))
    th = threading.Thread(target=lambda: bus.publish("t", None))
    th.start()
    th.join()
    assert seen == [th.ident]


def test_unsubscribe_during_publish_is_safe() -> None:
    bus = EventBus()
    got: list[str] = []
    holder: list = []

    def once(_p: object) -> None:
        got.append("once")
        holder[0]()

    holder.append(bus.subscribe("t", once))
    bus.subscribe("t", lambda _p: got.append("other"))
    bus.publish("t", None)
    bus.publish("t", None)
    assert got == ["once", "other", "other"]
