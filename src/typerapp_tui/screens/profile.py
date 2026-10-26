"""Player profile: statistics on top, a line per matchday below.

Selecting a matchday opens the matchday screen in read-only mode, which already knows how to
show predictions next to results, so nothing is drawn twice here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Label, ListItem, ListView, Static

from typerapp_core.events import PredictionSaved, RankingChanged
from typerapp_core.ranking import MatchdayPoints
from typerapp_tui.formatting import format_decimal, plural_predictions
from typerapp_tui.screens.dialogs import PlayerPicker
from typerapp_tui.screens.matchday import MatchdayScreen

if TYPE_CHECKING:
    from typerapp_core.controller import TyperController
    from typerapp_tui.app import TyperApp


class ProfileScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "back", "Menu"),
        Binding("p", "pick_player", "Inny gracz"),
    ]

    def __init__(self, player_id: int | None = None) -> None:
        super().__init__()
        self._player_id = player_id
        self._cancels: list = []
        self._rows: list[MatchdayPoints] = []

    @property
    def controller(self) -> TyperController:
        return cast("TyperApp", self.app).controller

    @property
    def player_id(self) -> int | None:
        if self._player_id is not None:
            return self._player_id
        player = self.controller.active_player()
        return player.id if player else None

    def compose(self) -> ComposeResult:
        yield Static(id="profile-name")
        yield Static(id="profile-headline")
        yield Static(id="profile-counters")
        yield Static(id="profile-extras")
        yield ListView(id="profile-matchdays")
        yield Static("↑↓ kolejka · Enter podejrzyj typy · P inny gracz", id="hints")
        yield Footer()

    async def on_mount(self) -> None:
        bridge = cast("TyperApp", self.app).bridge
        self._cancels = [
            bridge.subscribe(RankingChanged, lambda _: self._refresh()),
            bridge.subscribe(PredictionSaved, lambda _: self._refresh()),
        ]
        await self._reload()

    def on_unmount(self) -> None:
        for cancel in self._cancels:
            cancel()

    # ----------------------------------------------------------------- updates

    async def _reload(self) -> None:
        """Full rebuild: used on mount and when another player is picked."""
        listing = self.query_one("#profile-matchdays", ListView)
        await listing.clear()
        self._refresh(rebuild=False)

        items = []
        for row in self._rows:
            items.append(
                ListItem(
                    Label(_matchday_line(row), markup=True),
                    id=f"profile-matchday-{row.matchday.id}",
                )
            )
        await listing.extend(items)
        listing.index = 0
        listing.focus()

    def _refresh(self, rebuild: bool = True) -> None:
        player_id = self.player_id
        stats = self.controller.player_stats(player_id) if player_id is not None else None
        if stats is None:
            self.query_one("#profile-name", Static).update("[b]brak gracza[/b]")
            return

        self._rows = list(stats.matchdays)
        entry = stats.entry
        self.query_one("#profile-name", Static).update(f"[b]{escape(entry.player_name)}[/b]")
        self.query_one("#profile-headline", Static).update(
            f"{entry.points} pkt · {entry.position}. miejsce · skuteczność {stats.accuracy}%"
        )
        self.query_one("#profile-counters", Static).update(
            f"dokładne {entry.exact_hits} · różnice {entry.difference_hits}"
            f" · rozstrzygnięcia {entry.outcome_hits} · pudła {stats.misses}"
        )
        self.query_one("#profile-extras", Static).update(
            f"joker trafiony {stats.jokers_hit} z {stats.jokers_played}"
            f" · średnio {format_decimal(stats.average_points)} pkt na kolejkę"
        )

        if rebuild:
            for row in self._rows:
                label = self.query(f"#profile-matchday-{row.matchday.id} Label")
                if label:
                    cast(Label, label.first()).update(_matchday_line(row))

    # ----------------------------------------------------------------- actions

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = (event.item.id or "").removeprefix("profile-matchday-")
        if not item_id:
            return
        self.app.push_screen(MatchdayScreen(int(item_id), player_id=self.player_id))

    def action_pick_player(self) -> None:
        players = self.controller.players()
        if not players:
            return
        self.app.push_screen(PlayerPicker(players, self.player_id), self._picked_player)

    def _picked_player(self, player_id: int | None) -> None:
        if player_id is not None:
            self._player_id = player_id
            self.run_worker(self._reload())

    def action_back(self) -> None:
        self.app.pop_screen()


def _matchday_line(row: MatchdayPoints) -> str:
    points = f"{row.points} pkt" if row.predictions else "—"
    state = plural_predictions(row.predictions) if row.predictions else "bez typów"
    if not row.is_finished:
        state += f" · rozegrano {row.settled} z {row.matches}"
    return f"Kolejka {row.matchday.number}   [b]{points:>7}[/b]   [dim]{state}[/dim]"
