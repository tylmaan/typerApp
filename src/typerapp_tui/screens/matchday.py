"""Matchday screen: the list of matches and the predictions.

The screen owns every call into the controller; the rows only report what the player typed.
When the controller confirms a save, the event comes back and exactly one row is updated.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, cast

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from typerapp_core.events import (
    ActivePlayerChanged,
    MatchesUpdated,
    PredictionSaved,
    ResultsUpdated,
)
from typerapp_core.models import MatchStatus, Score
from typerapp_core.scoring import JokerLimitError, PredictionClosedError, PredictionError
from typerapp_tui.formatting import format_countdown, plural_matches
from typerapp_tui.screens.dialogs import PlayerPicker, ResultPrompt
from typerapp_tui.widgets.match_row import MatchRow

if TYPE_CHECKING:
    from typerapp_core.controller import TyperController
    from typerapp_tui.app import TyperApp


class MatchdayScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "back", "Menu"),
        Binding("j", "joker", "Joker"),
        Binding("w", "enter_result", "Wpisz wynik"),
        Binding("s", "sync", "Pobierz wyniki"),
        Binding("p", "pick_player", "Gracz"),
        Binding("down", "next_match", "Następny mecz", show=False),
        Binding("up", "previous_match", "Poprzedni mecz", show=False),
    ]

    def __init__(self, matchday_id: int, player_id: int | None = None) -> None:
        super().__init__()
        self.matchday_id = matchday_id
        #: Set when the screen shows somebody else's predictions, opened from a profile.
        self._viewed_player_id = player_id
        self._cancels: list = []

    @property
    def controller(self) -> TyperController:
        return cast("TyperApp", self.app).controller

    @property
    def player_id(self) -> int | None:
        if self._viewed_player_id is not None:
            return self._viewed_player_id
        player = self.controller.active_player()
        return player.id if player else None

    @property
    def is_own_matchday(self) -> bool:
        """Only the player holding the keyboard may type; other profiles are read-only."""
        active = self.controller.active_player()
        return self._viewed_player_id is None or (
            active is not None and active.id == self._viewed_player_id
        )

    def compose(self) -> ComposeResult:
        yield Static(id="matchday-title")
        yield Static(id="matchday-countdown")
        with VerticalScroll(id="matches"):
            yield from self._rows()
        yield Static(id="matchday-summary")
        yield Static("↑↓ mecz · ←→ pole · 0-9 wynik · +/− zmień o jeden", id="hints")
        yield Footer()

    def _rows(self) -> list[MatchRow]:
        controller = self.controller
        editable = controller.is_open(self.matchday_id) and self.is_own_matchday
        predictions = controller.predictions(self.matchday_id, self.player_id)
        rows = []
        for match in controller.matches(self.matchday_id):
            prediction = predictions.get(match.id)
            breakdown = controller.points_for(prediction, match) if prediction else None
            rows.append(
                MatchRow(
                    match,
                    prediction,
                    editable=editable and match.status is MatchStatus.SCHEDULED,
                    points=breakdown.total if breakdown else None,
                )
            )
        return rows

    def on_mount(self) -> None:
        bridge = cast("TyperApp", self.app).bridge
        self._cancels = [
            bridge.subscribe(PredictionSaved, self._on_prediction_saved),
            bridge.subscribe(ActivePlayerChanged, lambda _: self._reload_predictions()),
            bridge.subscribe(ResultsUpdated, lambda _: self._rebuild_rows()),
            bridge.subscribe(MatchesUpdated, self._on_matches_updated),
        ]
        self.set_interval(1.0, self._refresh_countdown)
        self._refresh_title()
        self._refresh_countdown()
        self._refresh_summary()
        self._focus_first_gap()

    def on_unmount(self) -> None:
        for cancel in self._cancels:
            cancel()

    # ----------------------------------------------------------------- updates

    def _refresh_title(self) -> None:
        controller = self.controller
        matchday = controller.repository.matchday(self.matchday_id)
        player_id = self.player_id
        player = controller.repository.player(player_id) if player_id is not None else None
        number = matchday.number if matchday else "?"
        who = escape(player.name) if player else "brak gracza"
        label = "typy" if self.is_own_matchday else "typy gracza"
        self.query_one("#matchday-title", Static).update(
            f"[b]Kolejka {number}[/b]   [dim]{label}[/dim] [b]{who}[/b]"
        )

    def _refresh_countdown(self) -> None:
        controller = self.controller
        if controller.is_open(self.matchday_id):
            left = format_countdown(controller.time_left(self.matchday_id))
            text = f"zamknięcie typowania za [b]{left}[/b]"
        else:
            text = "[b]typowanie zamknięte[/b]"
        self.query_one("#matchday-countdown", Static).update(text)

    def _refresh_summary(self) -> None:
        rows = list(self.query(MatchRow))
        filled = sum(1 for row in rows if row.saved)
        summary = f"Wytypowano {filled} z {plural_matches(len(rows))}"
        joker = next((row for row in rows if row.joker), None)
        if joker is not None:
            summary += f" · joker na {escape(joker.match.home_team)}"
        self.query_one("#matchday-summary", Static).update(summary)

    def _reload_predictions(self) -> None:
        """Another player took the seat, or a result arrived: refill the rows in place."""
        predictions = self.controller.predictions(self.matchday_id, self.player_id)
        for row in self.query(MatchRow):
            prediction = predictions.get(row.match_id)
            if prediction is None:
                row.clear()
            else:
                row.apply(prediction)
        self._refresh_title()
        self._refresh_summary()

    def _on_matches_updated(self, event: MatchesUpdated) -> None:
        if event.matchday_id == self.matchday_id:
            self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        """A sync can add matches, move kickoffs or settle games, so the rows are rebuilt."""
        self.run_worker(self._mount_rows(), exclusive=True, group="rows")

    async def _mount_rows(self) -> None:
        container = self.query_one("#matches", VerticalScroll)
        await container.remove_children()
        await container.mount_all(self._rows())
        self._refresh_title()
        self._refresh_countdown()
        self._refresh_summary()
        self._focus_first_gap()

    def _on_prediction_saved(self, event: PredictionSaved) -> None:
        saved = event.prediction
        if saved.player_id != self.player_id:
            return
        for row in self.query(MatchRow):
            if row.match_id == saved.match_id:
                row.apply(saved)
            elif saved.joker and row.joker:
                row.joker = False  # only one joker per matchday
        self._refresh_summary()

    def _focus_first_gap(self) -> None:
        rows = list(self.query(MatchRow))
        target = next((row for row in rows if row.editable and not row.is_complete), None)
        if target is None:
            target = next((row for row in rows if row.editable), None)
        if target is not None:
            target.focus()

    # ----------------------------------------------------------------- actions

    def on_match_row_score_entered(self, message: MatchRow.ScoreEntered) -> None:
        try:
            self.controller.save_prediction(message.match_id, message.home, message.away)
        except PredictionError as error:
            self.notify(_explain(error), severity="warning")
            self._reload_predictions()

    def action_joker(self) -> None:
        row = self.focused
        if not isinstance(row, MatchRow):
            return
        if not row.is_complete:
            self.notify("Najpierw wytypuj wynik tego meczu", severity="warning")
            return
        try:
            self.controller.toggle_joker(row.match_id)
        except PredictionError as error:
            self.notify(_explain(error), severity="warning")

    def action_enter_result(self) -> None:
        """Type in the real result, which is also how the app works without internet."""
        row = self.focused
        if not isinstance(row, MatchRow):
            return
        self.app.push_screen(ResultPrompt(row.match), partial(self._save_result, row.match_id))

    def _save_result(self, match_id: int, score: Score | None) -> None:
        if score is None:
            return
        self.controller.enter_result(match_id, score)
        self.notify(f"Zapisano wynik {score}")

    def action_sync(self) -> None:
        cast("TyperApp", self.app).sync_now()

    def action_next_match(self) -> None:
        self._move_focus(1)

    def action_previous_match(self) -> None:
        self._move_focus(-1)

    def _move_focus(self, step: int) -> None:
        rows = list(self.query(MatchRow))
        if not rows:
            return
        current = rows.index(self.focused) if self.focused in rows else -1  # type: ignore[arg-type]
        rows[(current + step) % len(rows)].focus()

    def action_pick_player(self) -> None:
        players = self.controller.players()
        if not players:
            return
        active = self.controller.active_player()
        self.app.push_screen(
            PlayerPicker(players, active.id if active else None),
            self._picked_player,
        )

    def _picked_player(self, player_id: int | None) -> None:
        if player_id is not None:
            self.controller.set_active_player(player_id)

    def action_back(self) -> None:
        self.app.pop_screen()


def _explain(error: PredictionError) -> str:
    """Core exceptions are in English; the player gets Polish."""
    if isinstance(error, PredictionClosedError):
        return "Typowanie tej kolejki jest już zamknięte"
    if isinstance(error, JokerLimitError):
        return "Joker w tej kolejce jest już wykorzystany"
    return "Nie udało się zapisać typu"
