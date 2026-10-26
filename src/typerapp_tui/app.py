"""The Textual application: wiring only, no game logic.

Everything the app knows about the game it asks the controller for, and everything it learns
it learns from the event bus through :class:`~typerapp_tui.bridge.EventBridge`.
"""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding
from textual.screen import Screen

from typerapp_core.controller import TyperController
from typerapp_tui.bridge import EventBridge
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
