"""Interface tests driven by Textual's Pilot: real key presses against a real app.

Each test runs the app headless on an in-memory database, so the whole path from a keystroke
to a row in SQLite is covered.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from textual.widgets import Label, ListItem, ListView

from typerapp_core.controller import TyperController, season_for
from typerapp_core.demo import LEAGUE as DEMO_LEAGUE
from typerapp_core.demo import OPEN_MATCHDAY, PLAYED_MATCHDAY, seed_demo_data
from typerapp_core.events import EventBus
from typerapp_core.models import MatchStatus, Score, ScoringRules, utc_now
from typerapp_core.providers.base import ProviderUnavailable
from typerapp_core.repository import Repository
from typerapp_tui.app import TyperApp
from typerapp_tui.screens.dialogs import ResultPrompt, parse_result
from typerapp_tui.screens.main_menu import MainMenuScreen
from typerapp_tui.screens.matchday import MatchdayScreen
from typerapp_tui.screens.profile import ProfileScreen
from typerapp_tui.screens.ranking import RankingScreen
from typerapp_tui.screens.settings import SettingsScreen
from typerapp_tui.widgets.match_row import AWAY_FIELD_COLUMN, HOME_FIELD_COLUMN, MatchRow
from typerapp_tui.widgets.ranking_row import RankingRow
from typerapp_tui.widgets.setting_row import SettingRow

from helpers import FakeProvider, fetched


@pytest.fixture(autouse=True)
def no_swallowed_errors(caplog: pytest.LogCaptureFixture):
    """The event bus logs and swallows handler errors; here they must fail the test."""
    yield
    broken = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert not broken, broken[0].getMessage()


#: The demo league's season, which tracks the real calendar like any other league's would.
DEMO_SEASON = season_for(utc_now())


def build(*, demo: bool = True, provider=None) -> tuple[Repository, TyperApp]:
    repository = Repository()
    if demo:
        seed_demo_data(repository)
    controller = TyperController(repository, EventBus(), provider=provider)
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


# ------------------------------------------------------------------- ranking


def test_ranking_lists_every_player() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("down", "enter")
            await pilot.pause()

            assert isinstance(app.screen, RankingScreen)
            rows = list(app.screen.query(RankingRow))
            assert len(rows) == 5
            assert "ranking ogolny" in str(app.screen.query_one("#ranking-title").content).replace(
                "ogólny", "ogolny"
            )
            assert rows[0].entry.position == 1
            assert rows[0].entry.points >= rows[-1].entry.points
        repository.close()

    run(scenario)


def test_arrows_switch_between_overall_and_matchdays() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("down", "enter")
            await pilot.pause()
            title = app.screen.query_one("#ranking-title")

            await pilot.press("right")
            await pilot.pause()
            assert f"kolejka {PLAYED_MATCHDAY}" in str(title.content)

            await pilot.press("left")
            await pilot.pause()
            assert "ranking" in str(title.content)
        repository.close()

    run(scenario)


def test_changing_the_rules_reorders_the_ranking() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("down", "enter")
            await pilot.pause()
            before = [row.player_id for row in app.screen.query(RankingRow)]

            # Pay only for goal differences: a different player should come out on top.
            app.controller.save_rules(
                ScoringRules(
                    exact_score=0,
                    goal_difference=50,
                    correct_outcome=0,
                    both_teams_scored=0,
                    total_goals=0,
                    matchday_sweep=0,
                )
            )
            await pilot.pause()

            rows = list(app.screen.query(RankingRow))
            assert [row.player_id for row in rows] != before
            # Strict: both the order of the widgets and their contents have to match the
            # recomputed table, so a half-finished update cannot pass unnoticed.
            assert [row.entry for row in rows] == list(app.controller.ranking())
            assert any(row.delta != 0 for row in rows)
        repository.close()

    run(scenario)


def test_enter_on_a_ranking_row_opens_that_profile() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("down", "enter")
            await pilot.pause()
            leader = list(app.screen.query(RankingRow))[0].player_id

            await pilot.press("enter")
            await pilot.pause()

            assert isinstance(app.screen, ProfileScreen)
            assert app.screen.player_id == leader
        repository.close()

    run(scenario)


# ------------------------------------------------------------------- profile


def test_profile_shows_statistics_and_matchdays() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("down", "down", "enter")
            await pilot.pause()

            assert isinstance(app.screen, ProfileScreen)
            assert "Kuba" in str(app.screen.query_one("#profile-name").content)
            assert "pkt" in str(app.screen.query_one("#profile-headline").content)
            assert "joker" in str(app.screen.query_one("#profile-extras").content)
            assert len(app.screen.query_one("#profile-matchdays", ListView).children) == 2
        repository.close()

    run(scenario)


def test_profile_opens_a_matchday_read_only() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("down", "enter")  # ranking
            await pilot.pause()
            await pilot.press("enter")  # profile of the leader, who is not the active player
            await pilot.pause()
            viewed = app.screen.player_id

            await pilot.press("enter")  # first matchday on the list
            await pilot.pause()

            assert isinstance(app.screen, MatchdayScreen)
            assert app.screen.player_id == viewed
            assert not app.screen.is_own_matchday
            assert all(not row.editable for row in app.screen.query(MatchRow))
        repository.close()

    run(scenario)


# ------------------------------------------------------------------ settings


def test_settings_show_the_current_rules() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("down", "down", "down", "enter")
            await pilot.pause()

            assert isinstance(app.screen, SettingsScreen)
            rows = {row.key: row.value for row in app.screen.query(SettingRow)}
            assert rows["exact_score"] == 5
            assert rows["prediction_deadline_minutes"] == 5
            assert len(app.screen.query_one("#settings-players", ListView).children) == 5
        repository.close()

    run(scenario)


def test_typing_a_number_saves_the_rule_and_recomputes_the_ranking() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("down", "down", "down", "enter")
            await pilot.pause()
            before = app.controller.ranking()[0].points

            await pilot.press("9")
            await pilot.pause()

            assert app.controller.rules().exact_score == 9
            assert repository.scoring_rules().exact_score == 9
            assert app.controller.ranking()[0].points != before
        repository.close()

    run(scenario)


def test_digits_accumulate_in_a_setting() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("down", "down", "down", "enter")
            await pilot.pause()
            deadline = app.screen.query_one("#setting-prediction_deadline_minutes", SettingRow)
            deadline.focus()
            await pilot.pause()

            await pilot.press("6", "0")
            await pilot.pause()

            assert app.controller.rules().prediction_deadline_minutes == 60
        repository.close()

    run(scenario)


def test_plus_and_minus_respect_the_limits() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("down", "down", "down", "enter")
            await pilot.pause()
            multiplier = app.screen.query_one("#setting-joker_multiplier", SettingRow)
            multiplier.focus()
            await pilot.pause()

            await pilot.press("minus", "minus", "minus")
            await pilot.pause()

            assert app.controller.rules().joker_multiplier == 1  # never below one
        repository.close()

    run(scenario)


def test_defaults_can_be_restored() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("down", "down", "down", "enter")
            await pilot.pause()
            await pilot.press("9")
            await pilot.pause()

            await pilot.press("r")
            await pilot.pause()
            await pilot.click("#yes")
            await pilot.pause()

            assert app.controller.rules() == ScoringRules()
            rows = {row.key: row.value for row in app.screen.query(SettingRow)}
            assert rows["exact_score"] == 5
        repository.close()

    run(scenario)


def test_a_player_can_be_deleted_after_confirmation() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("down", "down", "down", "enter")
            await pilot.pause()
            listing = app.screen.query_one("#settings-players", ListView)
            listing.focus()
            listing.index = 0
            await pilot.pause()
            doomed = str(listing.children[0].query_one(Label).content).strip(" \u25b8")

            await pilot.press("u")
            await pilot.pause()
            await pilot.click("#yes")
            await pilot.pause()

            names = [player.name for player in app.controller.players()]
            assert doomed not in names
            assert len(names) == 4
        repository.close()

    run(scenario)


# ------------------------------------------------------------ results and sync


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2:1", Score(2, 1)),
        (" 2 : 1 ", Score(2, 1)),
        ("2-1", Score(2, 1)),
        ("2 1", Score(2, 1)),
        ("10:0", Score(10, 0)),
        ("", None),
        ("2:", None),
        ("dwa:jeden", None),
        ("2:1:1", None),
    ],
)
def test_result_is_parsed_the_way_people_type_it(text: str, expected: Score | None) -> None:
    assert parse_result(text) == expected


def test_a_result_can_be_typed_in_by_hand() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            row = app.screen.focused
            assert isinstance(row, MatchRow)
            match_id = row.match_id

            await pilot.press("w")
            await pilot.pause()
            await pilot.press("3", ":", "0", "enter")
            await pilot.pause()

            stored = repository.match(match_id)
            assert stored is not None and stored.score == Score(3, 0)
            assert stored.is_settled
        repository.close()

    run(scenario)


def test_a_typed_result_awards_points_and_shows_them() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            row = app.screen.focused
            assert isinstance(row, MatchRow)
            match_id = row.match_id
            await pilot.press("2", "1")  # Kuba types 2:1
            await pilot.pause()

            await pilot.press("w")
            await pilot.pause()
            await pilot.press("2", ":", "1", "enter")
            await pilot.pause()

            assert app.controller.ranking()[0].points > 0
            updated = next(r for r in app.screen.query(MatchRow) if r.match_id == match_id)
            assert updated.points == 7  # exact score plus both bonuses
            assert not updated.editable  # a settled match cannot be predicted any more
        repository.close()

    run(scenario)


def test_nonsense_in_the_result_dialog_is_refused() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            row = app.screen.focused
            assert isinstance(row, MatchRow)

            await pilot.press("w")
            await pilot.pause()
            await pilot.press("x", "enter")
            await pilot.pause()

            assert repository.match(row.match_id).score is None  # type: ignore[union-attr]
            assert isinstance(app.screen, ResultPrompt)  # the dialog stays open
        repository.close()

    run(scenario)


def test_without_a_source_syncing_only_explains_itself() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            assert not app.controller.can_sync()

            await pilot.press("s")
            await pilot.pause()

            assert app.controller.last_sync() is None
        repository.close()

    run(scenario)


def test_pressing_s_fetches_and_rebuilds_the_matchday() -> None:
    async def scenario() -> None:
        provider = FakeProvider(current=13)  # nothing to deliver on start-up
        repository, app = build(provider=provider)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()  # the app syncs once on start
            await pilot.press("enter")  # the open matchday
            await pilot.pause()
            before = len(app.screen.query(MatchRow))

            provider.matches = {
                13: [
                    fetched("Chelsea", "Liverpool", external_id="demo-2-0"),  # already known
                    fetched("Leeds", "Burnley", external_id="new-1"),  # brand new
                ]
            }
            provider.asked.clear()
            await pilot.press("s")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert provider.asked == [12, 13, 14]
            assert app.controller.last_sync() is not None
            assert len(app.screen.query(MatchRow)) == before + 1
        repository.close()

    run(scenario)


def test_a_result_from_the_sync_lands_on_the_screen() -> None:
    async def scenario() -> None:
        provider = FakeProvider(current=13)
        repository, app = build(provider=provider)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("enter")
            await pilot.pause()
            row = app.screen.focused
            assert isinstance(row, MatchRow)
            await pilot.press("2", "1")
            await pilot.pause()
            match = repository.match(row.match_id)
            assert match is not None

            provider.matches = {
                13: [
                    fetched(
                        match.home_team,
                        match.away_team,
                        status=MatchStatus.FINISHED,
                        score=Score(2, 1),
                        external_id=match.external_id or "x",
                        kickoff=match.kickoff,
                    )
                ]
            }
            await pilot.press("s")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert repository.match(row.match_id).score == Score(2, 1)  # type: ignore[union-attr]
            assert app.controller.ranking()[0].points > 0
        repository.close()

    run(scenario)


def test_a_failing_source_does_not_break_the_screen() -> None:
    async def scenario() -> None:
        provider = FakeProvider(error=ProviderUnavailable("brak sieci"))
        repository, app = build(provider=provider)
        async with app.run_test() as pilot:
            await pilot.press("s")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert app.controller.last_sync() is None
            assert isinstance(app.screen, MainMenuScreen)
        repository.close()

    run(scenario)


def test_league_can_be_picked_in_the_settings() -> None:
    async def scenario() -> None:
        provider = FakeProvider(current=None)
        repository, app = build(provider=provider)
        async with app.run_test() as pilot:
            await pilot.press("down", "down", "down", "enter")  # settings
            await pilot.pause()

            await pilot.press("l")
            await pilot.pause()
            listing = app.screen.query_one("#league-list", ListView)
            listing.index = 1  # the second entry, whatever the list happens to start with
            expected = str(listing.children[1].query_one(Label).content)
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

            league = app.controller.league()
            assert league is not None
            assert league[0] in expected
            assert "/" in league[1]  # the season, written 2026/27
        repository.close()

    run(scenario)


# --------------------------------------------------- bugs reported after stage 5


def test_q_leaves_the_app() -> None:
    # Textual does not pass a screen binding's action up to the app, so the menu needs
    # its own action_quit. Without it the key did nothing at all.
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("q")
            await pilot.pause()

            assert app._exit
        repository.close()

    run(scenario)


def test_matchday_can_be_switched_without_leaving_the_screen() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            first = app.screen.matchday_id

            await pilot.press("shift+right")
            await pilot.pause()
            second = app.screen.matchday_id

            await pilot.press("]")
            await pilot.pause()

            assert second != first
            assert app.screen.matchday_id == first  # two matchdays, so it wraps around
            assert len(app.screen.query(MatchRow)) == 5
        repository.close()

    run(scenario)


def test_switching_shows_the_other_matchdays_matches() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            before = {row.match.home_team for row in app.screen.query(MatchRow)}

            await pilot.press("[")
            await pilot.pause()

            after = {row.match.home_team for row in app.screen.query(MatchRow)}
            assert before != after
            assert "1/2" in str(app.screen.query_one("#matchday-title").content) or "2/2" in str(
                app.screen.query_one("#matchday-title").content
            )
        repository.close()

    run(scenario)


def test_the_league_picker_offers_the_demo_league() -> None:
    async def scenario() -> None:
        repository, app = build(provider=FakeProvider(current=None))
        async with app.run_test() as pilot:
            await pilot.press("down", "down", "down", "enter")  # settings
            await pilot.pause()

            await pilot.press("l")
            await pilot.pause()

            labels = [
                str(item.query_one(Label).content)
                for item in app.screen.query_one("#league-list", ListView).children
            ]
            assert any(DEMO_LEAGUE in label for label in labels)
            assert any("Premier League  [dim]PL" in label for label in labels)
        repository.close()

    run(scenario)


def test_going_back_to_the_demo_league_is_possible() -> None:
    async def scenario() -> None:
        repository, app = build(provider=FakeProvider(current=None))
        async with app.run_test() as pilot:
            app.controller.set_league("Premier League", "2026/27")
            await pilot.press("down", "down", "down", "enter")
            await pilot.pause()

            await pilot.press("l")
            await pilot.pause()
            listing = app.screen.query_one("#league-list", ListView)
            index = next(
                i
                for i, item in enumerate(listing.children)
                if DEMO_LEAGUE in str(item.query_one(Label).content)
            )
            listing.index = index
            await pilot.press("enter")
            await pilot.pause()

            assert app.controller.league() == (DEMO_LEAGUE, DEMO_SEASON)
        repository.close()

    run(scenario)


def test_demo_data_can_be_removed_from_the_settings() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("down", "down", "down", "enter")
            await pilot.pause()
            assert app.controller.has_demo_data()

            await pilot.press("d")
            await pilot.pause()
            await pilot.click("#yes")
            await pilot.pause()

            assert not app.controller.has_demo_data()
            assert app.controller.players() == []
            assert app.controller.matchdays() == []
            assert app.controller.league() is None
            assert repository.is_empty()
        repository.close()

    run(scenario)


def test_removing_the_demo_leaves_other_leagues_alone() -> None:
    async def scenario() -> None:
        provider = FakeProvider({8: [fetched()]}, current=8)
        repository, app = build(provider=provider)
        async with app.run_test() as pilot:
            app.controller.set_league("Premier League", "2026/27")
            app.controller.sync()
            app.controller.add_player("Marek")
            app.controller.set_league(DEMO_LEAGUE, DEMO_SEASON)
            await pilot.pause()

            await pilot.press("down", "down", "down", "enter")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            await pilot.click("#yes")
            await pilot.pause()

            assert [p.name for p in app.controller.players()] == ["Marek"]
            assert app.controller.active_player().name == "Marek"  # type: ignore[union-attr]
            assert app.controller.league() == ("Premier League", "2026/27")
            assert [d.number for d in app.controller.matchdays()] == [8]
        repository.close()

    run(scenario)


def test_a_fetched_league_can_be_cleared_without_touching_the_players() -> None:
    async def scenario() -> None:
        provider = FakeProvider({8: [fetched()]}, current=8)
        repository, app = build(provider=provider)
        async with app.run_test() as pilot:
            app.controller.set_league("Premier League", "2026/27")
            app.controller.sync()
            await pilot.pause()

            await pilot.press("down", "down", "down", "enter")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            await pilot.click("#yes")
            await pilot.pause()

            assert app.controller.known_leagues() == [(DEMO_LEAGUE, DEMO_SEASON)]
            assert len(app.controller.players()) == 5  # the demo players are untouched
        repository.close()

    run(scenario)


def test_the_menu_shows_only_your_own_standing() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test():
            screen = app.screen
            assert isinstance(screen, MainMenuScreen)
            hint = item_text(screen, "ranking")

            # Kuba is second in the demo, so the leader used to be named here as well.
            assert "miejsce" in hint and "pkt" in hint
            assert "Ala" not in hint
        repository.close()

    run(scenario)


def test_the_demo_league_does_not_try_to_fetch_anything() -> None:
    async def scenario() -> None:
        repository, app = build(provider=FakeProvider(current=8))
        app.controller.provider.supports = lambda league: "demo" not in league  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            assert not app.controller.can_sync()

            await pilot.press("s")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert app.controller.last_sync() is None
            assert app.screen.query(MatchRow) is not None  # the app is still usable
        repository.close()

    run(scenario)


def test_points_count_up_when_a_result_lands() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            row = app.screen.focused
            assert isinstance(row, MatchRow)
            await pilot.press("2", "1")  # exact score, worth seven points
            await pilot.pause()

            await pilot.press("w")
            await pilot.pause()
            await pilot.press("2", ":", "1", "enter")
            await pilot.pause()

            assert row.points == 7  # the row ends up showing what the prediction was worth

            # The counting itself: it starts at zero and climbs there on a timer.
            row.award(7)
            assert row.points == 0
            for _ in range(40):
                await pilot.pause()
                if row.points == 7:
                    break
            assert row.points == 7
        repository.close()

    run(scenario)


def test_the_countdown_gets_louder_near_the_deadline() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            countdown = app.screen.query_one("#matchday-countdown")
            assert not countdown.has_class("-soon")

            deadline = app.controller.deadline(app.screen.matchday_id)
            app.controller.clock = lambda: deadline - timedelta(minutes=30)
            app.screen._refresh_countdown()
            assert countdown.has_class("-soon") and not countdown.has_class("-urgent")

            app.controller.clock = lambda: deadline - timedelta(minutes=2)
            app.screen._refresh_countdown()
            assert countdown.has_class("-urgent")
        repository.close()

    run(scenario)


def test_clicking_a_score_field_selects_it() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            row = app.screen.query(MatchRow).first()

            await pilot.click(row, offset=(AWAY_FIELD_COLUMN + 2, 0))
            await pilot.pause()
            assert row.has_focus and row.field == 1

            await pilot.click(row, offset=(HOME_FIELD_COLUMN + 1, 0))
            await pilot.pause()
            assert row.field == 0
        repository.close()

    run(scenario)


def test_the_menu_shows_a_logo() -> None:
    async def scenario() -> None:
        repository, app = build()
        async with app.run_test():
            logo = str(app.screen.query_one("#logo").content)
            assert logo.count("\n") == 2  # three lines, whatever they draw
        repository.close()

    run(scenario)
