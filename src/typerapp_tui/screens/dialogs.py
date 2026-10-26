"""Small modal screens: picking the player and asking for a name."""

from __future__ import annotations

import re
from collections.abc import Sequence

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView

from typerapp_core.models import Match, Player, Score


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


class LeaguePicker(ModalScreen[str | None]):
    """Picks a league from ``(key, label)`` entries and returns the chosen key.

    The caller decides what a key means, so the list can mix leagues already in the database
    with the competitions the free API plan covers.
    """

    BINDINGS = [Binding("escape", "cancel", "Anuluj")]

    def __init__(self, entries: Sequence[tuple[str, str]], current: str | None = None) -> None:
        super().__init__()
        self._entries = list(entries)
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Którą ligę typujemy?", classes="dialog-title")
            yield ListView(
                *[
                    ListItem(
                        Label(f"{'▸ ' if key == self._current else '  '}{label}", markup=True),
                        id=f"league-{index}",
                    )
                    for index, (key, label) in enumerate(self._entries)
                ],
                id="league-list",
            )
            yield Label("↑↓ wybór · Enter zmień · Esc anuluj", classes="dialog-hint")

    def on_mount(self) -> None:
        listing = self.query_one("#league-list", ListView)
        listing.focus()
        listing.index = next(
            (index for index, (key, _) in enumerate(self._entries) if key == self._current),
            0,
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = int((event.item.id or "league-0").removeprefix("league-"))
        self.dismiss(self._entries[index][0] if index < len(self._entries) else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class Confirm(ModalScreen[bool]):
    """Asked before anything that cannot be undone, such as deleting a player."""

    BINDINGS = [Binding("escape", "cancel", "Anuluj")]

    def __init__(self, question: str, confirm: str = "Tak, usuń") -> None:
        super().__init__()
        self._question = question
        self._confirm = confirm

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._question, classes="dialog-title")
            yield Button(self._confirm, variant="error", id="yes")
            yield Button("Nie", variant="primary", id="no")
            yield Label("Enter zatwierdź · Esc anuluj", classes="dialog-hint")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


class ResultPrompt(ModalScreen[Score | None]):
    """Typing in a real result by hand: the fallback when the API has nothing."""

    BINDINGS = [Binding("escape", "cancel", "Anuluj")]

    def __init__(self, match: Match) -> None:
        super().__init__()
        self._match = match

    def compose(self) -> ComposeResult:
        current = str(self._match.score) if self._match.score else ""
        with Vertical(id="dialog"):
            yield Label(
                f"Wynik: {self._match.home_team} – {self._match.away_team}",
                classes="dialog-title",
            )
            yield Input(value=current, placeholder="np. 2:1", id="result")
            yield Label("Enter zapisz · Esc anuluj", classes="dialog-hint")

    def on_mount(self) -> None:
        self.query_one("#result", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        score = parse_result(event.value)
        if score is None:
            self.notify("Wpisz wynik w formacie 2:1", severity="warning")
            return
        self.dismiss(score)

    def action_cancel(self) -> None:
        self.dismiss(None)


def parse_result(text: str) -> Score | None:
    """``2:1``, ``2-1`` and ``2 1`` all mean the same thing."""
    match = re.fullmatch(r"\s*(\d{1,2})\s*[:\-\s]\s*(\d{1,2})\s*", text)
    if match is None:
        return None
    return Score(int(match.group(1)), int(match.group(2)))


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
