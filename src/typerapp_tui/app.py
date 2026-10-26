"""The Textual application: wiring only, no game logic.

Everything the app knows about the game it asks the controller for, and everything it learns
it learns from the event bus through :class:`~typerapp_tui.bridge.EventBridge`.
"""

from __future__ import annotations

from functools import partial

from textual.app import App
from textual.binding import Binding
from textual.screen import Screen

from typerapp_core.controller import TyperController
from typerapp_core.providers.base import (
    CompetitionUnavailable,
    MissingApiKey,
    ProviderError,
    RateLimited,
)
from typerapp_tui.bridge import EventBridge
from typerapp_tui.formatting import plural_matches
from typerapp_tui.screens.main_menu import MainMenuScreen


class TyperApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "typerApp"
    SUB_TITLE = "typowanie wyników w gronie znajomych"

    BINDINGS = [Binding("ctrl+q", "quit", "Wyjście", show=False)]

    def __init__(self, controller: TyperController) -> None:
        super().__init__()
        self.controller = controller
        self.bridge = EventBridge(self, controller.events)

    def get_default_screen(self) -> Screen[None]:
        """The menu is always at the bottom of the stack, so Esc anywhere leads home."""
        return MainMenuScreen()

    def on_mount(self) -> None:
        if self.controller.should_sync():
            self.sync_now(force=False, quiet=True)

    # -------------------------------------------------------------------- sync

    def sync_now(self, *, force: bool = True, quiet: bool = False) -> None:
        """Fetch results in a worker thread. The core publishes events; the bridge hands
        them over to this thread, so the screens update themselves."""
        if not self.controller.can_sync():
            if not quiet:
                self.notify(self._why_not(), severity="warning")
            return
        if not quiet:
            self.notify(f"Pobieram wyniki z {self.controller.provider.name}…")
        self.run_worker(
            partial(self._sync, force, quiet),
            thread=True,
            exclusive=True,
            group="sync",
        )

    def _why_not(self) -> str:
        """Why nothing can be fetched right now, said in a way the player can act on."""
        controller = self.controller
        if not controller.provider.online:
            return "Brak klucza API: wpisz FOOTBALL_DATA_API_KEY do pliku .env"
        league = controller.league()
        if league is None:
            return "Najpierw wybierz ligę w Ustawieniach (L)"
        return f"Liga {league[0]} nie ma wyników w API — wyniki wpisujesz sam klawiszem W"

    def _sync(self, force: bool, quiet: bool) -> None:
        """Runs off the UI thread: nothing here touches a widget directly."""
        try:
            changed = self.controller.sync(force=force)
        except ProviderError as error:
            self.call_from_thread(self.notify, explain_provider(error), severity="warning")
            return
        if quiet and not changed:
            return
        message = f"Zaktualizowano {plural_matches(changed)}" if changed else "Brak nowych wyników"
        self.call_from_thread(self.notify, message)


def explain_provider(error: ProviderError) -> str:
    """Core messages are in English; the player gets Polish."""
    if isinstance(error, MissingApiKey):
        return "Brak klucza API: wpisz FOOTBALL_DATA_API_KEY do pliku .env"
    if isinstance(error, RateLimited):
        return "Limit zapytań wyczerpany, spróbuj za minutę"
    if isinstance(error, CompetitionUnavailable):
        return "Ta liga nie jest pobierana z API — wybierz inną w Ustawieniach (L)"
    return f"Nie udało się pobrać wyników: {error}"
