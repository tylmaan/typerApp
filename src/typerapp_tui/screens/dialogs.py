"""Small modal screens: picking the player and asking for a name."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView

from typerapp_core.models import Player


class PlayerPicker(ModalScreen[int | None]):
    """Hot-seat: hand the keyboard over without leaving the screen underneath."""

    BINDINGS = [Binding("escape", "cancel", "Anuluj")]

    def __init__(self, players: list[Player], active_id: int | None = None) -> None:
        super().__init__()
        self._players = players
        self._active_id = active_id

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Kto teraz typuje?", classes="dialog-title")
            yield ListView(
                *[
                    ListItem(
                        Label(f"{'▸ ' if player.id == self._active_id else '  '}{player.name}"),
                        id=f"player-{player.id}",
                    )
                    for player in self._players
                ],
                id="player-list",
            )
            yield Label("↑↓ wybór · Enter zmień · Esc anuluj", classes="dialog-hint")

    def on_mount(self) -> None:
        listing = self.query_one("#player-list", ListView)
        listing.focus()
        active = next(
            (index for index, p in enumerate(self._players) if p.id == self._active_id), 0
        )
        listing.index = active

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        self.dismiss(int(item_id.removeprefix("player-")) if item_id else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NamePrompt(ModalScreen[str | None]):
    """Asks for a single line of text, used for adding a player."""

    BINDINGS = [Binding("escape", "cancel", "Anuluj")]

    def __init__(self, title: str, placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Input(placeholder=self._placeholder, id="name")
            yield Button("Dodaj", variant="primary", id="confirm")
            yield Label("Enter zatwierdź · Esc anuluj", classes="dialog-hint")

    def on_mount(self) -> None:
        self.query_one("#name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._confirm(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self._confirm(self.query_one("#name", Input).value)

    def _confirm(self, value: str) -> None:
        self.dismiss(value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
