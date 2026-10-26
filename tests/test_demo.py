"""The demo data has to be presentable at any time, not only on the day it was written."""

from collections.abc import Iterator

import pytest

from typerapp_core.demo import LEAGUE, OPEN_MATCHDAY, PLAYED_MATCHDAY, PLAYER_NAMES, seed_demo_data
from typerapp_core.models import ScoringRules, utc_now
from typerapp_core.ranking import build_ranking
from typerapp_core.repository import ACTIVE_LEAGUE, ACTIVE_PLAYER, Repository
from typerapp_core.scoring import is_matchday_open


@pytest.fixture
def seeded() -> Iterator[Repository]:
    with Repository() as repository:
        seed_demo_data(repository)
        yield repository


def matchday_matches(repository: Repository, number: int) -> list:
    matchday = next(day for day in repository.matchdays(LEAGUE, "2026/27") if day.number == number)
    return repository.matches(matchday.id)


def test_empty_database_is_recognised() -> None:
    with Repository() as repository:
        assert repository.is_empty()
        seed_demo_data(repository)
        assert not repository.is_empty()


def test_players_are_created(seeded: Repository) -> None:
    assert {player.name for player in seeded.players()} == set(PLAYER_NAMES)


def test_played_matchday_has_results(seeded: Repository) -> None:
    matches = matchday_matches(seeded, PLAYED_MATCHDAY)

    assert len(matches) == 5
    assert all(match.is_settled for match in matches)


def test_open_matchday_is_still_open(seeded: Repository) -> None:
    matches = matchday_matches(seeded, OPEN_MATCHDAY)

    assert len(matches) == 5
    assert all(match.score is None for match in matches)
    assert is_matchday_open(matches, utc_now(), ScoringRules())


def test_everybody_predicted_the_played_matchday(seeded: Repository) -> None:
    matchday_id = matchday_matches(seeded, PLAYED_MATCHDAY)[0].matchday_id
    predictions = seeded.predictions(matchday_id=matchday_id)

    assert len(predictions) == len(PLAYER_NAMES) * 5


def test_the_active_player_still_has_something_to_do(seeded: Repository) -> None:
    active = int(seeded.setting(ACTIVE_PLAYER))  # type: ignore[arg-type]
    matchday_id = matchday_matches(seeded, OPEN_MATCHDAY)[0].matchday_id

    assert seeded.predictions(matchday_id=matchday_id, player_id=active) == []
    assert seeded.predictions(matchday_id=matchday_id) != []


def test_every_player_has_one_joker_per_matchday(seeded: Repository) -> None:
    for number in (PLAYED_MATCHDAY, OPEN_MATCHDAY):
        matchday_id = matchday_matches(seeded, number)[0].matchday_id
        for player in seeded.players():
            predictions = seeded.predictions(matchday_id=matchday_id, player_id=player.id)
            if predictions:
                assert sum(prediction.joker for prediction in predictions) == 1


def test_leaderboard_is_not_a_flat_line(seeded: Repository) -> None:
    table = build_ranking(seeded.players(), seeded.predictions(), seeded.matches(), ScoringRules())

    assert len(table) == len(PLAYER_NAMES)
    assert table[0].points > 0
    assert len({entry.points for entry in table}) > 1, "everybody scored the same"
    assert table[0].position == 1


def test_settings_point_at_the_demo_league(seeded: Repository) -> None:
    assert seeded.setting(ACTIVE_LEAGUE) == LEAGUE
    assert seeded.setting(ACTIVE_PLAYER) is not None


def test_demo_data_is_reproducible() -> None:
    def predictions() -> list[tuple[int, int, int, bool]]:
        with Repository() as repository:
            seed_demo_data(repository)
            return [
                (p.player_id, p.match_id, p.home_goals, p.joker) for p in repository.predictions()
            ]

    assert predictions() == predictions()
