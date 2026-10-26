"""Interface tests driven by Textual's Pilot: real key presses against a real app.

Each test runs the app headless on an in-memory database, so the whole path from a keystroke
to a row in SQLite is covered.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from textual.widgets import Label, ListItem

from typerapp_core.controller import TyperController
from typerapp_core.demo import OPEN_MATCHDAY, seed_demo_data
from typerapp_core.events import EventBus
from typerapp_core.models import Score
from typerapp_core.repository import Repository
from typerapp_tui.app import TyperApp
from typerapp_tui.screens.main_menu import MainMenuScreen
from typerapp_tui.screens.matchday import MatchdayScreen
from typerapp_tui.widgets.match_row import MatchRow


def build(*, demo: bool = True) -> tuple[Repository, TyperApp]:
    repository = Repository()
    if demo:
        seed_demo_data(repository)
    controller = TyperController(repository, EventBus())
    return repository, TyperApp(controller)


def run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(scenario())


def item_text(screen: MainMenuScreen, key: str) -> str:
    item = screen.query_one(f"#menu-{key}", ListItem)
    return str(item.query_one(Label).content)


def open_matchday_id(controller: TyperController) -> int:
    matchday = next(day for day in controller.matchdays() if day.number == OPEN_MATCHDAY)
    return matchday.id or 0


def test_menu_opens_with_live_hints() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test():
            screen = app.screen
            assert isinstance(screen, MainMenuScreen)
            assert f"Typuj kolejkę {OPEN_MATCHDAY}" in item_text(screen, "matchday")
            assert "zamknięcie za" in item_text(screen, "matchday")
            assert "pkt" in item_text(screen, "ranking")
            assert "Kuba" in str(screen.query_one("#player-bar").content)
        repository.close()

    run(scenario)


def test_empty_database_offers_the_demo() -> None:
    async def scenario() -> None:
        repository, app = build(demo=False)
        async with app.run_test() as pilot:
            screen = app.screen
            assert isinstance(screen, MainMenuScreen)
            assert "Wczytaj dane demo" in item_text(screen, "demo")

            await pilot.press("enter")
            await pilot.pause()

            assert not app.controller.is_empty()
            assert f"Typuj kolejkę {OPEN_MATCHDAY}" in item_text(screen, "matchday")
        repository.close()

    run(scenario)


def test_a_player_can_be_added_from_an_empty_database() -> None:
    async def scenario() -> None:
        repository, app = build(demo=False)
        async with app.run_test() as pilot:
            await pilot.press("down", "enter")  # "Dodaj gracza"
            await pilot.pause()

            await pilot.press("K", "u", "b", "a", "enter")
            await pilot.pause()

            assert [player.name for player in app.controller.players()] == ["Kuba"]
            assert app.controller.active_player().name == "Kuba"  # type: ignore[union-attr]
            assert "Kuba" in str(app.screen.query_one("#player-bar").content)
        repository.close()

    run(scenario)


def test_arrow_hands_the_keyboard_to_the_next_player() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("right")
            await pilot.pause()

            assert app.controller.active_player().name != "Kuba"  # type: ignore[union-attr]
            assert "Kuba" not in str(app.screen.query_one("#player-bar").content)
        repository.close()

    run(scenario)


def test_enter_opens_the_matchday() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()

            assert isinstance(app.screen, MatchdayScreen)
            assert len(app.screen.query(MatchRow)) == 5
            assert isinstance(app.screen.focused, MatchRow)
        repository.close()

    run(scenario)


def test_two_keystrokes_save_a_prediction() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            row = app.screen.focused
            assert isinstance(row, MatchRow)

            await pilot.press("2")
            await pilot.press("1")
            await pilot.pause()

            saved = app.controller.predictions(open_matchday_id(app.controller))
            assert saved[row.match_id].score == Score(2, 1)
            assert row.saved
        repository.close()

    run(scenario)


def test_half_a_score_is_not_saved_yet() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("3")
            await pilot.pause()

            assert app.controller.predictions(open_matchday_id(app.controller)) == {}
        repository.close()

    run(scenario)


def test_plus_and_minus_adjust_the_focused_field() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            row = app.screen.focused
            assert isinstance(row, MatchRow)

            await pilot.press("1", "1")  # 1:1, focus sits on the away field
            await pilot.press("plus", "plus")
            await pilot.pause()

            saved = app.controller.predictions(open_matchday_id(app.controller))
            assert saved[row.match_id].score == Score(1, 3)

            await pilot.press("minus", "minus", "minus", "minus")  # never goes below zero
            await pilot.pause()
            saved = app.controller.predictions(open_matchday_id(app.controller))
            assert saved[row.match_id].score == Score(1, 0)
        repository.close()

    run(scenario)


def test_arrows_move_between_matches_and_fields() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            first = app.screen.focused
            assert isinstance(first, MatchRow)

            await pilot.press("down")
            await pilot.pause()
            second = app.screen.focused
            assert isinstance(second, MatchRow)
            assert second.match_id != first.match_id

            await pilot.press("right")
            assert second.field == 1
            await pilot.press("left")
            assert second.field == 0
        repository.close()

    run(scenario)


def test_joker_needs_a_prediction_then_moves() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            first = app.screen.focused
            assert isinstance(first, MatchRow)

            await pilot.press("j")  # nothing predicted yet
            await pilot.pause()
            assert not first.joker

            await pilot.press("1", "0", "j")
            await pilot.pause()
            assert first.joker

            await pilot.press("down", "2", "2", "j")
            await pilot.pause()
            second = app.screen.focused
            assert isinstance(second, MatchRow)
            assert second.joker
            assert not first.joker  # only one joker per matchday
        repository.close()

    run(scenario)


def test_switching_player_reloads_the_rows_in_place() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            rows = list(app.screen.query(MatchRow))
            assert not any(row.saved for row in rows)  # Kuba has not predicted yet

            ala = next(player for player in app.controller.players() if player.name == "Ala")
            app.controller.set_active_player(ala.id)
            await pilot.pause()

            assert all(row.saved for row in app.screen.query(MatchRow))
            assert app.screen.query(MatchRow).first() is rows[0]  # same widgets, new values
        repository.close()

    run(scenario)


def test_escape_goes_back_to_the_menu() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, MatchdayScreen)

            await pilot.press("escape")
            await pilot.pause()

            assert isinstance(app.screen, MainMenuScreen)
        repository.close()

    run(scenario)


def test_summary_counts_the_predictions() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            summary = app.screen.query_one("#matchday-summary")
            assert "Wytypowano 0 z 5 meczów" in str(summary.content)

            await pilot.press("2", "1")
            await pilot.pause()

            assert "Wytypowano 1 z 5 meczów" in str(summary.content)
        repository.close()

    run(scenario)
