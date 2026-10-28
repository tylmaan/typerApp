"""Ranking: the overall table and every matchday, switched with the arrow keys."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from typerapp_core.events import RankingChanged
from typerapp_core.models import Matchday, RankingEntry
from typerapp_tui.widgets.ranking_row import RankingRow

if TYPE_CHECKING:
    from typerapp_core.controller import TyperController
    from typerapp_tui.app import TyperApp


class RankingScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "back", "Menu"),
        Binding("enter", "open_profile", "Profil"),
        Binding("left", "previous_scope", "W lewo", show=False),
        Binding("right", "next_scope", "W prawo", show=False),
        Binding("down", "next_row", "Niżej", show=False),
        Binding("up", "previous_row", "Wyżej", show=False),
    ]

    def __init__(self, matchday_id: int | None = None) -> None:
        super().__init__()
        self._scopes: list[Matchday | None] = []
        self._scope = 0
        self._initial_scope = matchday_id
        self._cancels: list = []

    @property
    def _positions(self) -> dict[int | None, dict[int, int]]:
        """Positions the player last saw, per table, kept by the app rather than the screen.

        That way ▲▼ means "since you last looked": results that came in while another screen
        was open still show up here as movement the next time the ranking is opened.
        """
        return cast("TyperApp", self.app).ranking_positions

    @property
    def controller(self) -> TyperController:
        return cast("TyperApp", self.app).controller

    def compose(self) -> ComposeResult:
        yield Static(id="ranking-title")
        with VerticalScroll(id="ranking"):
            yield from self._rows()
        yield Static("←→ ogólny i kolejki · ↑↓ gracz · Enter profil", id="hints")
        yield Footer()

    def _rows(self) -> list[RankingRow]:
        active = self.controller.active_player()
        return [
            RankingRow(entry, is_me=bool(active and entry.player_id == active.id))
            for entry in self.controller.ranking()
        ]

    def on_mount(self) -> None:
        self._scopes = [None, *self.controller.matchdays()]
        if self._initial_scope is not None:
            self._scope = next(
                (
                    index
                    for index, scope in enumerate(self._scopes)
                    if scope is not None and scope.id == self._initial_scope
                ),
                0,
            )
        bridge = cast("TyperApp", self.app).bridge
        self._cancels = [bridge.subscribe(RankingChanged, self._on_ranking_changed)]
        self._show_scope()
        rows = list(self.query(RankingRow))
        if rows:
            rows[0].focus()

    def on_unmount(self) -> None:
        for cancel in self._cancels:
            cancel()

    # ------------------------------------------------------------------- scope

    @property
    def _current_scope(self) -> Matchday | None:
        return self._scopes[self._scope] if self._scopes else None

    def _show_scope(self) -> None:
        scope = self._current_scope
        league = self.controller.league()
        where = f"{league[0]} {league[1]}" if league else ""
        what = "ranking ogólny" if scope is None else f"kolejka {scope.number}"
        position = f"{self._scope + 1}/{len(self._scopes)}" if self._scopes else ""
        self.query_one("#ranking-title", Static).update(
            f"[b]{what}[/b]   [dim]{escape(where)} · {position}[/dim]"
        )
        self._apply(self._entries())

    def _entries(self) -> tuple[RankingEntry, ...]:
        scope = self._current_scope
        return self.controller.ranking(scope.id if scope else None)

    def action_next_scope(self) -> None:
        if self._scopes:
            self._scope = (self._scope + 1) % len(self._scopes)
            self._show_scope()

    def action_previous_scope(self) -> None:
        if self._scopes:
            self._scope = (self._scope - 1) % len(self._scopes)
            self._show_scope()

    # ----------------------------------------------------------------- updates

    def _on_ranking_changed(self, event: RankingChanged) -> None:
        # Whichever table changed, ours is recomputed: the rules may have changed for all.
        del event
        self._apply(self._entries())

    def _apply(self, entries: tuple[RankingEntry, ...]) -> None:
        """Move the existing rows into the new order and animate whoever changed place."""
        container = self.query_one("#ranking", VerticalScroll)
        rows = {row.player_id: row for row in self.query(RankingRow)}
        scope = self._current_scope
        was = self._positions.get(scope.id if scope else None, {})

        for index, entry in enumerate(entries):
            row = rows.get(entry.player_id)
            if row is None:
                continue
            row.update_entry(entry, was.get(entry.player_id, entry.position) - entry.position)
            if list(container.children).index(row) != index:
                container.move_child(row, before=index)
                row.flash()

        self._positions[scope.id if scope else None] = {
            entry.player_id: entry.position for entry in entries
        }

    # ----------------------------------------------------------------- actions

    def action_next_row(self) -> None:
        self._move(1)

    def action_previous_row(self) -> None:
        self._move(-1)

    def _move(self, step: int) -> None:
        rows = list(self.query(RankingRow))
        if not rows:
            return
        current = rows.index(self.focused) if self.focused in rows else -1  # type: ignore[arg-type]
        rows[(current + step) % len(rows)].focus()

    def action_open_profile(self) -> None:
        row = self.focused
        if isinstance(row, RankingRow):
            from typerapp_tui.screens.profile import ProfileScreen

            self.app.push_screen(ProfileScreen(row.player_id))

    def action_back(self) -> None:
        self.app.pop_screen()
