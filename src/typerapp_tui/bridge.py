"""The only place that knows the core runs on a different thread than the interface.

Screens subscribe to core events through the bridge instead of the bus itself. Each
subscription remembers the thread it was made on (always the UI thread, because screens
subscribe while mounting), so an event published by a worker thread is handed over with
``call_from_thread`` and a handler never touches a widget from the wrong thread.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from typerapp_core.events import Event, EventBus

if TYPE_CHECKING:
    from textual.app import App


class EventBridge:
    def __init__(self, app: App[Any], events: EventBus) -> None:
        self._app = app
        self._events = events

    def subscribe[E: Event](
        self,
        event_type: type[E],
        handler: Callable[[E], None],
    ) -> Callable[[], None]:
        """Forward events of this type to the handler, always on the subscribing thread."""
        owner_thread = threading.get_ident()

        def forward(event: E) -> None:
            if threading.get_ident() == owner_thread:
                handler(event)
            else:
                self._app.call_from_thread(handler, event)

        return self._events.subscribe(event_type, forward)
