"""Main menu: who is playing, and where to go next.

Every entry carries a live subtitle, so the state of the game is visible without opening
anything. The subtitles are updated in place when a core event arrives; the list itself is
only rebuilt when its entries actually change, which happens once, when an empty database
gets its first data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Label, ListItem, ListView, Static

from typerapp_core.events import (
    ActivePlayerChanged,
    MatchesUpdated,
    PredictionSaved,
    RankingChanged,
)
from typerapp_tui.formatting import format_countdown
from typerapp_tui.screens.dialogs import NamePrompt, PlayerPicker
from typerapp_tui.screens.matchday import MatchdayScreen

if TYPE_CHECKING:
    from typerapp_core.controller import TyperController
    from typerapp_tui.app import TyperApp


class MainMenuScreen(Screen[None]):
    BINDINGS = [
        Binding("left", "previous_player", "Poprzedni gracz", show=False),
        Binding("right", "next_player", "Następny gracz", show=False),
        Binding("p", "pick_player", "Gracz"),
        Binding("q", "quit", "Wyjście"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._labels: dict[str, Label] = {}
        self._texts: dict[str, str] = {}
        self._cancels: list = []

    @property
    def controller(self) -> TyperController:
        return cast("TyperApp", self.app).controller

    def compose(self) -> ComposeResult:
        yield Static(id="brand")
        yield Static(id="player-bar")
        yield ListView(id="menu")
        yield Static(
            "↑↓ wybór · Enter wejdź · ←→ zmień gracza · P lista graczy",
            id="hints",
        )
        yield Footer()

    async def on_mount(self) -> None:
        bridge = cast("TyperApp", self.app).bridge
        self._cancels = [
            bridge.subscribe(ActivePlayerChanged, lambda _: self._refresh_player()),
            bridge.subscribe(RankingChanged, lambda _: self._refresh_hints()),
            bridge.subscribe(PredictionSaved, lambda _: self._refresh_hints()),
            bridge.subscribe(MatchesUpdated, lambda _: self._refresh_hints()),
        ]
        await self._rebuild()

    def on_unmount(self) -> None:
        for cancel in self._cancels:
            cancel()

    # ------------------------------------------------------------------ layout

    def _entries(self) -> list[tuple[str, str]]:
        if self.controller.is_empty():
            return [
                ("demo", "Wczytaj dane demo"),
                ("add", "Dodaj gracza"),
                ("quit", "Wyjście"),
            ]
        return [
            ("matchday", "Typuj kolejkę"),
            ("ranking", "Ranking"),
            ("profile", "Mój profil"),
            ("settings", "Ustawienia"),
            ("quit", "Wyjście"),
        ]

    async def _rebuild(self) -> None:
        """Replace the entries. Clearing a ListView is asynchronous, hence the await."""
        menu = self.query_one("#menu", ListView)
        await menu.clear()
        self._labels.clear()
        self._texts.clear()

        items = []
        for key, text in self._entries():
            label = Label(text, markup=True)
            self._labels[key] = label
            self._texts[key] = text
            items.append(ListItem(label, id=f"menu-{key}"))
        await menu.extend(items)

        self._refresh_brand()
        self._refresh_player()
        self._refresh_hints()
        menu.index = 0
        menu.focus()

    # ----------------------------------------------------------------- updates

    def _refresh_brand(self) -> None:
        league = self.controller.league()
        where = f"{league[0]} {league[1]}" if league else "brak ligi"
        self.query_one("#brand", Static).update(f"[b]typerApp[/b]   [dim]{escape(where)}[/dim]")

    def _refresh_player(self) -> None:
        player = self.controller.active_player()
        name = escape(player.name) if player else "brak graczy"
        self.query_one("#player-bar", Static).update(f"Gracz:   ◀  [b]{name}[/b]  ▶")
        self._refresh_hints()

    def _refresh_hints(self) -> None:
        for key, hint in self._hints().items():
            label = self._labels.get(key)
            if label is not None:
                text = self._texts[key]
                label.update(f"{text}   [dim]{hint}[/dim]" if hint else text)

    def _hints(self) -> dict[str, str]:
        controller = self.controller
        if controller.is_empty():
            return {
                "demo": "pięciu graczy, dwie kolejki, gotowy ranking",
                "add": "zacznij z czystą bazą",
            }

        matchday = controller.current_matchday()
        hints = {"matchday": "brak terminarza"}
        if matchday is not None and matchday.id is not None:
            number = matchday.number
            self._texts["matchday"] = f"Typuj kolejkę {number}"
            if controller.is_open(matchday.id):
                left = format_countdown(controller.time_left(matchday.id))
                hints["matchday"] = f"otwarta · zamknięcie za {left}"
            else:
                hints["matchday"] = "zamknięta · podejrzyj typy"

        table = controller.ranking()
        player = controller.active_player()
        mine = next((e for e in table if player and e.player_id == player.id), None)
        if table:
            leader = table[0]
            hints["ranking"] = f"1. {escape(leader.player_name)} {leader.points} pkt"
            if mine is not None:
                hints["ranking"] += f" · ty {mine.position}. z {mine.points} pkt"
        if mine is not None:
            accuracy = round(100 * mine.hits / mine.predictions) if mine.predictions else 0
            hints["profile"] = f"skuteczność {accuracy}% · dokładne wyniki: {mine.exact_hits}"

        rules = controller.rules()
        hints["settings"] = (
            f"punktacja {rules.exact_score}/{rules.goal_difference}/{rules.correct_outcome}"
            f" · joker ×{rules.joker_multiplier}"
        )
        return hints

    # ----------------------------------------------------------------- actions

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        key = (event.item.id or "").removeprefix("menu-")
        if key == "matchday":
            self._open_matchday()
        elif key == "ranking":
            self.notify("Ranking pojawi się w następnym etapie", severity="information")
        elif key == "profile":
            self.notify("Profil pojawi się w następnym etapie", severity="information")
        elif key == "settings":
            self.notify("Ustawienia pojawią się w następnym etapie", severity="information")
        elif key == "demo":
            self.controller.load_demo_data()
            await self._rebuild()
            self.notify("Wczytano dane demo")
        elif key == "add":
            self.app.push_screen(NamePrompt("Jak się nazywasz?", "imię gracza"), self._add_player)
        elif key == "quit":
            self.app.exit()

    def _open_matchday(self) -> None:
        matchday = self.controller.current_matchday()
        if matchday is None or matchday.id is None:
            self.notify("Brak kolejek do typowania", severity="warning")
            return
        self.app.push_screen(MatchdayScreen(matchday.id))

    async def _add_player(self, name: str | None) -> None:
        if not name:
            return
        self.controller.add_player(name)
        await self._rebuild()
        self.notify(f"Dodano gracza {escape(name)}")

    def action_next_player(self) -> None:
        self.controller.switch_player(1)

    def action_previous_player(self) -> None:
        self.controller.switch_player(-1)

    def action_pick_player(self) -> None:
        players = self.controller.players()
        if not players:
            self.notify("Najpierw dodaj graczy", severity="warning")
            return
        active = self.controller.active_player()
        self.app.push_screen(
            PlayerPicker(players, active.id if active else None),
            self._picked_player,
        )

    def _picked_player(self, player_id: int | None) -> None:
        if player_id is not None:
            self.controller.set_active_player(player_id)
