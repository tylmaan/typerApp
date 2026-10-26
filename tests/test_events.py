import threading

from typerapp_core.events import (
    Event,
    EventBus,
    MatchesUpdated,
    SyncFailed,
    SyncStarted,
)


def test_handler_receives_only_its_event() -> None:
    bus = EventBus()
    received: list[Event] = []
    bus.subscribe(MatchesUpdated, received.append)

    bus.publish(MatchesUpdated(matchday_id=3))
    bus.publish(SyncStarted(source="api"))

    assert received == [MatchesUpdated(matchday_id=3)]


def test_subscribing_to_the_base_class_receives_everything() -> None:
    bus = EventBus()
    received: list[Event] = []
    bus.subscribe(Event, received.append)

    bus.publish(MatchesUpdated(matchday_id=1))
    bus.publish(SyncStarted(source="api"))

    assert [type(event) for event in received] == [MatchesUpdated, SyncStarted]


def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    received: list[Event] = []
    unsubscribe = bus.subscribe(SyncStarted, received.append)

    unsubscribe()
    unsubscribe()  # calling it twice is harmless
    bus.publish(SyncStarted(source="api"))

    assert received == []


def test_failing_handler_does_not_block_the_others(caplog) -> None:
    bus = EventBus()
    received: list[Event] = []

    def broken(event: SyncFailed) -> None:
        raise RuntimeError("boom")

    bus.subscribe(SyncFailed, broken)
    bus.subscribe(SyncFailed, received.append)

    bus.publish(SyncFailed(source="api", message="timeout"))

    assert received == [SyncFailed(source="api", message="timeout")]
    assert "SyncFailed" in caplog.text


def test_handler_may_unsubscribe_while_it_runs() -> None:
    bus = EventBus()
    calls: list[str] = []

    def once(event: SyncStarted) -> None:
        calls.append("once")
        unsubscribe()

    unsubscribe = bus.subscribe(SyncStarted, once)
    bus.publish(SyncStarted(source="api"))
    bus.publish(SyncStarted(source="api"))

    assert calls == ["once"]


def test_publishing_from_another_thread_reaches_the_handler() -> None:
    bus = EventBus()
    received: list[Event] = []
    bus.subscribe(SyncStarted, received.append)

    worker = threading.Thread(target=bus.publish, args=(SyncStarted(source="api"),))
    worker.start()
    worker.join()

    assert received == [SyncStarted(source="api")]
