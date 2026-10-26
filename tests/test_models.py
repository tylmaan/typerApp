from datetime import datetime

import pytest

from typerapp_core.models import (
    Matchday,
    MatchStatus,
    Outcome,
    Player,
    Score,
    ScoringRules,
)

from helpers import finished, make_match, predict


@pytest.mark.parametrize(
    ("score", "outcome"),
    [
        (Score(2, 1), Outcome.HOME_WIN),
        (Score(0, 0), Outcome.DRAW),
        (Score(1, 3), Outcome.AWAY_WIN),
    ],
)
def test_outcome(score: Score, outcome: Outcome) -> None:
    assert score.outcome is outcome


def test_score_derived_values() -> None:
    score = Score(3, 1)
    assert (score.difference, score.total, score.both_scored) == (2, 4, True)
    assert not Score(3, 0).both_scored
    assert str(score) == "3:1"
    assert f"{score:>5}" == "  3:1"  # results line up in a column


@pytest.mark.parametrize(("home", "away"), [(-1, 0), (0, -2)])
def test_negative_goals_rejected(home: int, away: int) -> None:
    with pytest.raises(ValueError):
        Score(home, away)
    with pytest.raises(ValueError):
        predict(home, away)


def test_match_requires_timezone() -> None:
    with pytest.raises(ValueError, match="timezone aware"):
        make_match(kickoff=datetime(2026, 11, 7, 18, 0))


def test_match_settled_only_when_finished_with_score() -> None:
    assert finished(1, 2, 1).is_settled
    assert not make_match(status=MatchStatus.FINISHED).is_settled
    assert not make_match(status=MatchStatus.LIVE).is_settled


@pytest.mark.parametrize("status", [MatchStatus.POSTPONED, MatchStatus.CANCELLED])
def test_void_statuses(status: MatchStatus) -> None:
    assert make_match(status=status).is_void


def test_entities_are_immutable() -> None:
    match = make_match()
    with pytest.raises(AttributeError):
        match.home_team = "Raków"  # type: ignore[misc]


def test_empty_player_name_rejected() -> None:
    with pytest.raises(ValueError):
        Player(name="  ")


def test_matchday_number_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Matchday(league="Premier League", season="2026/27", number=0)


def test_default_rules_match_the_agreed_scale() -> None:
    rules = ScoringRules()
    assert (rules.exact_score, rules.goal_difference, rules.correct_outcome) == (5, 3, 2)
    assert (rules.both_teams_scored, rules.total_goals, rules.matchday_sweep) == (1, 1, 5)
    assert (rules.joker_multiplier, rules.jokers_per_matchday) == (2, 1)
    assert rules.prediction_deadline_minutes == 5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"exact_score": -1},
        {"total_goals": -5},
        {"joker_multiplier": 0},
        {"jokers_per_matchday": -1},
        {"prediction_deadline_minutes": -10},
    ],
)
def test_invalid_rules_rejected(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        ScoringRules(**kwargs)
