"""The event bus (Observer pattern) and the events the core publishes.

This is the only way the core talks back to a view: the core never knows whether it is
driving a terminal or a desktop window. Events are published synchronously, on the thread
that publishes them, so a view that does work in a background thread is responsible for
handing the event over to its own UI thread (Textual: ``call_from_thread``, Qt: a signal).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from typerapp_core.models import Player, Prediction, RankingEntry

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Event:
    """Base class for all events. Subscribing to ``Event`` receives every event."""


@dataclass(frozen=True, slots=True)
class MatchesUpdated(Event):
    """The fixture list of a matchday changed (new matches, new kickoff times)."""

    matchday_id: int


@dataclass(frozen=True, slots=True)
class ResultsUpdated(Event):
    """Real results arrived, from the API or entered by hand."""

    match_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PredictionSaved(Event):
    prediction: Prediction


@dataclass(frozen=True, slots=True)
class PointsAwarded(Event):
    """A match was settled. ``points`` holds ``(player_id, points)`` pairs."""

    match_id: int
    points: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class RankingChanged(Event):
    """A new leaderboard. ``previous`` lets the view animate players moving up and down."""

    matchday_id: int | None
    entries: tuple[RankingEntry, ...]
    previous: tuple[RankingEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class ActivePlayerChanged(Event):
    """Hot-seat: somebody else took over the keyboard."""

    player: Player | None


@dataclass(frozen=True, slots=True)
class SettingsChanged(Event):
    key: str
    value: Any


@dataclass(frozen=True, slots=True)
class SyncStarted(Event):
    source: str


@dataclass(frozen=True, slots=True)
class SyncFinished(Event):
    source: str
    updated_matches: int


@dataclass(frozen=True, slots=True)
class SyncFailed(Event):
    source: str
    message: str


type Handler[E: Event] = Callable[[E], None]


class EventBus:
    """Synchronous event bus, safe to subscribe to and unsubscribe from any thread."""

    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[Handler[Any]]] = {}
        self._lock = threading.Lock()

    def subscribe[E: Event](self, event_type: type[E], handler: Handler[E]) -> Callable[[], None]:
        """Register a handler and return a function that cancels the subscription."""
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(event_type, [])
                if handler in handlers:
                    handlers.remove(handler)

        return unsubscribe

    def publish(self, event: Event) -> None:
        """Call every handler registered for the event's type or any of its base classes.

        The handler list is copied under the lock, so a handler may subscribe or unsubscribe
        while it runs. A handler that raises is logged and does not stop the others: a broken
        widget must never break the game logic.
        """
        with self._lock:
            handlers = [
                handler
                for cls in type(event).__mro__
                if isinstance(cls, type) and issubclass(cls, Event)
                for handler in self._handlers.get(cls, ())
            ]
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                log.exception("Handler failed for event %s", type(event).__name__)
