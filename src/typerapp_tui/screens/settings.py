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

from typerapp_core.controller import season_for
from typerapp_core.demo import LEAGUE as DEMO_LEAGUE
from typerapp_core.demo import PLAYER_NAMES as DEMO_PLAYERS
from typerapp_core.models import ScoringRules
from typerapp_core.providers.api import COMPETITIONS
from typerapp_tui.screens.dialogs import Confirm, LeaguePicker, NamePrompt
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
        Binding("l", "pick_league", "Liga"),
        Binding("d", "remove_league_data", "Usuń dane ligi"),
        Binding("r", "restore_defaults", "Domyślne"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._cancels: list = []
        self._options: list[tuple[str, str, str]] = []

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
            yield Static("ŹRÓDŁO WYNIKÓW", classes="section")
            yield Static(id="settings-source")
            yield Static("GRACZE", classes="section")
            yield ListView(id="settings-players")
        yield Static(
            "↑↓ opcja · +− lub ←→ zmień · 0-9 wpisz · L liga · A dodaj gracza"
            " · U usuń gracza · D usuń dane ligi · R domyślne",
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
        self._refresh_source()
        rows = list(self.query(SettingRow))
        if rows:
            rows[0].focus()

    def _refresh_source(self) -> None:
        controller = self.controller
        league = controller.league()
        where = f"{league[0]} {league[1]}" if league else "nie wybrano ligi"
        source = controller.provider.name if controller.can_sync() else "ręcznie wpisywane"
        last = controller.last_sync()
        when = f"ostatnio {last.astimezone():%d.%m %H:%M}" if last else "jeszcze nie pobierano"
        self.query_one("#settings-source", Static).update(
            f"liga [b]{escape(where)}[/b]   źródło [b]{escape(source)}[/b]   [dim]{when}[/dim]"
        )

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

    def _league_options(self) -> list[tuple[str, str, str]]:
        """``(key, label, season)``: leagues already in the database, then the free plan.

        Without the stored ones there would be no way back to the demo league, or to a
        league fetched earlier, once something else was chosen.
        """
        controller = self.controller
        season = season_for(controller.now())
        options: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for league, stored_season in controller.known_leagues():
            options.append(
                (
                    f"{league}|{stored_season}",
                    f"{escape(league)}  [dim]{stored_season}[/dim]",
                    stored_season,
                )
            )
            seen.add(league)
        for code, name in COMPETITIONS:
            if name not in seen:
                options.append((f"{name}|{season}", f"{name}  [dim]{code}[/dim]", season))
        return options

    def action_pick_league(self) -> None:
        league = self.controller.league()
        self._options = self._league_options()
        current = f"{league[0]}|{league[1]}" if league else None
        self.app.push_screen(
            LeaguePicker([(key, label) for key, label, _ in self._options], current),
            self._picked_league,
        )

    def _picked_league(self, key: str | None) -> None:
        if not key:
            return
        chosen = next((option for option in self._options if option[0] == key), None)
        if chosen is None:
            return
        league = chosen[0].split("|")[0]
        controller = self.controller
        controller.set_league(league, chosen[2])
        self._refresh_source()
        self.notify(f"Liga: {escape(league)}")
        if controller.can_sync():
            cast("TyperApp", self.app).sync_now()

    def action_remove_league_data(self) -> None:
        """Clears out the league that is active. For the demo one that includes its players."""
        controller = self.controller
        league = controller.league()
        if league is None:
            self.notify("Najpierw wybierz ligę", severity="warning")
            return

        if league[0] == DEMO_LEAGUE:
            if not controller.has_demo_data():
                self.notify("Nie ma już danych demo", severity="information")
                return
            question = (
                f"Usunąć dane demo: ligę {escape(DEMO_LEAGUE)} oraz graczy "
                f"{', '.join(DEMO_PLAYERS)} razem z ich typami?"
            )
        else:
            question = (
                f"Usunąć dane ligi {escape(league[0])} {league[1]}:"
                " kolejki, mecze i wszystkie typy?"
            )
        self.app.push_screen(Confirm(question, confirm="Tak, usuń"), self._remove_league_data)

    async def _remove_league_data(self, confirmed: bool) -> None:
        if not confirmed:
            return
        controller = self.controller
        league = controller.league()
        if league is None:
            return
        if league[0] == DEMO_LEAGUE:
            matchdays, players = controller.remove_demo_data()
            message = f"Usunięto kolejki demo: {matchdays}, graczy: {players}"
        else:
            matchdays = controller.remove_league(*league)
            message = f"Usunięto kolejki ligi {league[0]}: {matchdays}"
        await self._reload_players()
        self._refresh_source()
        self.notify(message)

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
