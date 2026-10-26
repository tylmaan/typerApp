"""Settings: the rules of the game and the list of players.

Numbers are typed exactly like scores, and every change is saved at once. Saving the rules
recomputes the whole leaderboard, so a change here is visible in the ranking immediately.
"""

from __future__ import annotations

from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, cast

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Label, ListItem, ListView, Static

from typerapp_core.models import ScoringRules
from typerapp_tui.screens.dialogs import Confirm, NamePrompt
from typerapp_tui.widgets.setting_row import SettingRow

if TYPE_CHECKING:
    from typerapp_core.controller import TyperController
    from typerapp_tui.app import TyperApp

#: key, label, minimum, maximum, suffix, hint
Field = tuple[str, str, int, int, str, str]

SCORING: tuple[Field, ...] = (
    ("exact_score", "dokładny wynik", 0, 99, "pkt", ""),
    ("goal_difference", "trafiona różnica bramek", 0, 99, "pkt", ""),
    ("correct_outcome", "trafione rozstrzygnięcie", 0, 99, "pkt", ""),
    ("both_teams_scored", "oba zespoły strzelą", 0, 99, "pkt", "0 wyłącza"),
    ("total_goals", "łączna liczba goli", 0, 99, "pkt", "0 wyłącza"),
    ("matchday_sweep", "cała kolejka trafiona", 0, 99, "pkt", "0 wyłącza"),
)

JOKER: tuple[Field, ...] = (
    ("joker_multiplier", "mnożnik punktów", 1, 9, "×", "mnoży bazę i dodatki"),
    ("jokers_per_matchday", "jokery na kolejkę", 0, 9, "", "0 wyłącza jokera"),
)

DEADLINE: tuple[Field, ...] = (
    ("prediction_deadline_minutes", "zamknięcie przed 1. meczem", 0, 1440, "min", ""),
)


class SettingsScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "back", "Menu"),
        Binding("a", "add_player", "Dodaj gracza"),
        Binding("u", "delete_player", "Usuń gracza"),
        Binding("r", "restore_defaults", "Domyślne"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._cancels: list = []

    @property
    def controller(self) -> TyperController:
        return cast("TyperApp", self.app).controller

    def compose(self) -> ComposeResult:
        rules = self.controller.rules()
        yield Static("[b]Ustawienia[/b]", id="settings-title")
        with VerticalScroll(id="settings"):
            yield Static("PUNKTACJA", classes="section")
            yield from self._fields(SCORING, rules)
            yield Static("JOKER", classes="section")
            yield from self._fields(JOKER, rules)
            yield Static("TYPOWANIE", classes="section")
            yield from self._fields(DEADLINE, rules)
            yield Static("GRACZE", classes="section")
            yield ListView(id="settings-players")
        yield Static(
            "↑↓ opcja · +− lub ←→ zmień · 0-9 wpisz · A dodaj gracza · U usuń · R domyślne",
            id="hints",
        )
        yield Footer()

    def _fields(self, fields: tuple[Field, ...], rules: ScoringRules):
        for key, label, minimum, maximum, suffix, hint in fields:
            yield SettingRow(
                key,
                label,
                getattr(rules, key),
                minimum=minimum,
                maximum=maximum,
                suffix=suffix,
                hint=hint,
            )

    async def on_mount(self) -> None:
        await self._reload_players()
        rows = list(self.query(SettingRow))
        if rows:
            rows[0].focus()

    # ----------------------------------------------------------------- updates

    async def _reload_players(self) -> None:
        listing = self.query_one("#settings-players", ListView)
        await listing.clear()
        active = self.controller.active_player()
        items = [
            ListItem(
                Label(
                    f"{'▸ ' if active and player.id == active.id else '  '}{escape(player.name)}",
                    markup=True,
                ),
                id=f"settings-player-{player.id}",
            )
            for player in self.controller.players()
        ]
        await listing.extend(items)

    def _refresh_rules(self) -> None:
        rules = self.controller.rules()
        for row in self.query(SettingRow):
            row.value = getattr(rules, row.key)

    # ----------------------------------------------------------------- actions

    def on_setting_row_changed(self, message: SettingRow.Changed) -> None:
        try:
            updated = replace(self.controller.rules(), **{message.key: message.value})
            self.controller.save_rules(updated)
        except ValueError as error:
            self.notify(f"Nieprawidłowa wartość: {error}", severity="warning")
            self._refresh_rules()

    def action_add_player(self) -> None:
        self.app.push_screen(NamePrompt("Jak nazywa się nowy gracz?", "imię"), self._add_player)

    async def _add_player(self, name: str | None) -> None:
        if not name:
            return
        try:
            self.controller.add_player(name)
        except ValueError:
            self.notify("Taki gracz już istnieje", severity="warning")
            return
        await self._reload_players()
        self.notify(f"Dodano gracza {escape(name)}")

    def action_delete_player(self) -> None:
        player_id = self._highlighted_player()
        if player_id is None:
            self.notify("Wybierz gracza na liście", severity="warning")
            return
        player = self.controller.repository.player(player_id)
        if player is None:
            return
        self.app.push_screen(
            Confirm(f"Usunąć gracza {escape(player.name)} razem z jego typami?"),
            partial(self._delete_player, player_id),
        )

    async def _delete_player(self, player_id: int, confirmed: bool) -> None:
        if not confirmed:
            return
        self.controller.delete_player(player_id)
        await self._reload_players()
        self.notify("Gracz usunięty")

    def _highlighted_player(self) -> int | None:
        listing = self.query_one("#settings-players", ListView)
        item = listing.highlighted_child
        if item is None or not item.id:
            return None
        return int(item.id.removeprefix("settings-player-"))

    def action_restore_defaults(self) -> None:
        self.app.push_screen(
            Confirm("Przywrócić domyślne zasady punktacji?", confirm="Tak, przywróć"),
            self._restore_defaults,
        )

    def _restore_defaults(self, confirmed: bool) -> None:
        if not confirmed:
            return
        self.controller.save_rules(ScoringRules())
        self._refresh_rules()
        self.notify("Przywrócono domyślne zasady")

    def action_back(self) -> None:
        self.app.pop_screen()
