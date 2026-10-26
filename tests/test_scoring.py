from datetime import timedelta

import pytest

from typerapp_core.models import MatchStatus, Score, ScoringRules
from typerapp_core.scoring import (
    Bonus,
    HitLevel,
    JokerLimitError,
    PredictionClosedError,
    ensure_can_predict,
    ensure_joker_available,
    hit_level,
    is_matchday_open,
    matchday_deadline,
    score_prediction,
    sweep_awarded,
)

from helpers import FIRST_KICKOFF, finished, make_match, predict

RESULT = Score(2, 1)


@pytest.mark.parametrize(
    ("home", "away", "level"),
    [
        (2, 1, HitLevel.EXACT),
        (3, 2, HitLevel.GOAL_DIFFERENCE),
        (4, 0, HitLevel.OUTCOME),
        (1, 1, HitLevel.MISS),
        (0, 2, HitLevel.MISS),
    ],
)
def test_hit_level(home: int, away: int, level: HitLevel) -> None:
    assert hit_level(Score(home, away), RESULT) is level


def test_predicted_draw_always_reaches_goal_difference_level() -> None:
    # Every draw has a goal difference of zero, so a draw predicted as a draw never drops
    # to the plain outcome level.
    assert hit_level(Score(1, 1), Score(3, 3)) is HitLevel.GOAL_DIFFERENCE


@pytest.mark.parametrize(
    ("home", "away", "expected"),
    [
        (2, 1, 7),  # exact 5 + both scored 1 + total goals 1
        (3, 2, 4),  # goal difference 3 + both scored 1
        (1, 0, 3),  # goal difference 3
        (3, 0, 3),  # outcome 2 + total goals 1
        (4, 0, 2),  # outcome 2
        (1, 1, 1),  # miss, but both teams scored
        (0, 3, 1),  # miss, but the right number of goals
        (0, 4, 0),  # nothing
    ],
)
def test_points_with_default_rules(home: int, away: int, expected: int) -> None:
    assert score_prediction(predict(home, away), RESULT, ScoringRules()).total == expected


def test_bonuses_are_independent_of_the_base_scale() -> None:
    # A wrong result can still collect a bonus: 0:3 misses 2:1 completely, yet it does get
    # the number of goals right. Set the bonus to 0 points to turn that off.
    breakdown = score_prediction(predict(0, 3), RESULT, ScoringRules())
    assert (breakdown.level, breakdown.base, breakdown.total) == (HitLevel.MISS, 0, 1)


def test_breakdown_lists_bonuses() -> None:
    breakdown = score_prediction(predict(2, 1), RESULT, ScoringRules())
    assert breakdown.level is HitLevel.EXACT
    assert breakdown.base == 5
    assert breakdown.bonuses == ((Bonus.BOTH_TEAMS_SCORED, 1), (Bonus.TOTAL_GOALS, 1))
    assert breakdown.is_hit


def test_bonus_can_be_switched_off_with_zero_points() -> None:
    rules = ScoringRules(both_teams_scored=0, total_goals=0)
    breakdown = score_prediction(predict(2, 1), RESULT, rules)
    assert (breakdown.bonuses, breakdown.total) == ((), 5)


def test_joker_doubles_base_and_bonuses() -> None:
    rules = ScoringRules()
    assert score_prediction(predict(2, 1, joker=True), RESULT, rules).total == 14
    assert score_prediction(predict(4, 0, joker=True), RESULT, rules).total == 4
    assert score_prediction(predict(0, 4, joker=True), RESULT, rules).total == 0


def test_custom_rules() -> None:
    rules = ScoringRules(exact_score=10, joker_multiplier=3, both_teams_scored=0, total_goals=0)
    assert score_prediction(predict(2, 1, joker=True), RESULT, rules).total == 30


def test_sweep_awarded_when_every_settled_match_is_hit(rules: ScoringRules) -> None:
    matches = [finished(1, 2, 1), finished(2, 0, 0)]
    predictions = [predict(3, 0, match_id=1), predict(1, 1, match_id=2)]
    assert sweep_awarded(predictions, matches, rules)


def test_sweep_lost_on_a_single_miss(rules: ScoringRules) -> None:
    matches = [finished(1, 2, 1), finished(2, 0, 0)]
    predictions = [predict(3, 0, match_id=1), predict(0, 2, match_id=2)]
    assert not sweep_awarded(predictions, matches, rules)


def test_sweep_waits_for_the_whole_matchday(rules: ScoringRules) -> None:
    # One match still to be played: the bonus must not be handed out and taken back later.
    matches = [finished(1, 2, 1), make_match(2)]
    assert not sweep_awarded([predict(2, 1, match_id=1)], matches, rules)


def test_sweep_requires_a_prediction_for_every_match(rules: ScoringRules) -> None:
    matches = [finished(1, 2, 1), finished(2, 0, 0)]
    assert not sweep_awarded([predict(2, 1, match_id=1)], matches, rules)


def test_sweep_ignores_void_matches(rules: ScoringRules) -> None:
    matches = [finished(1, 2, 1), make_match(2, status=MatchStatus.POSTPONED)]
    assert sweep_awarded([predict(2, 1, match_id=1)], matches, rules)


def test_sweep_needs_at_least_one_settled_match(rules: ScoringRules) -> None:
    assert not sweep_awarded([predict(2, 1)], [make_match(1)], rules)


def test_sweep_can_be_switched_off() -> None:
    matches = [finished(1, 2, 1)]
    rules = ScoringRules(matchday_sweep=0)
    assert not sweep_awarded([predict(2, 1, match_id=1)], matches, rules)


def matchday() -> list:
    return [
        make_match(1, kickoff=FIRST_KICKOFF),
        make_match(2, kickoff=FIRST_KICKOFF + timedelta(hours=2, minutes=30)),
    ]


def test_deadline_is_before_the_first_kickoff(rules: ScoringRules) -> None:
    assert matchday_deadline(matchday(), rules) == FIRST_KICKOFF - timedelta(minutes=5)


def test_deadline_offset_is_configurable() -> None:
    rules = ScoringRules(prediction_deadline_minutes=60)
    assert matchday_deadline(matchday(), rules) == FIRST_KICKOFF - timedelta(hours=1)


def test_empty_matchday_has_no_deadline_and_is_closed(rules: ScoringRules) -> None:
    assert matchday_deadline([], rules) is None
    assert not is_matchday_open([], FIRST_KICKOFF, rules)


def test_matchday_open_before_the_deadline(rules: ScoringRules) -> None:
    matches = matchday()
    now = FIRST_KICKOFF - timedelta(minutes=6)
    assert is_matchday_open(matches, now, rules)
    ensure_can_predict(matches[1], matches, now, rules)


def test_later_match_also_closes_at_the_matchday_deadline(rules: ScoringRules) -> None:
    matches = matchday()
    now = FIRST_KICKOFF - timedelta(minutes=4)
    assert not is_matchday_open(matches, now, rules)
    with pytest.raises(PredictionClosedError):
        ensure_can_predict(matches[1], matches, now, rules)


def test_naive_now_rejected(rules: ScoringRules) -> None:
    with pytest.raises(ValueError, match="timezone aware"):
        is_matchday_open(matchday(), FIRST_KICKOFF.replace(tzinfo=None), rules)


@pytest.mark.parametrize("status", [MatchStatus.LIVE, MatchStatus.FINISHED, MatchStatus.POSTPONED])
def test_match_that_is_not_scheduled_cannot_be_predicted(
    status: MatchStatus, rules: ScoringRules
) -> None:
    # Safeguard for fixtures the API moves around after the matchday was loaded.
    late = FIRST_KICKOFF + timedelta(hours=4)
    matches = [make_match(1, kickoff=late, status=status), make_match(2, kickoff=late)]
    now = late - timedelta(hours=1)
    assert is_matchday_open(matches, now, rules)
    with pytest.raises(PredictionClosedError):
        ensure_can_predict(matches[0], matches, now, rules)


def test_first_joker_is_allowed(rules: ScoringRules) -> None:
    ensure_joker_available(predict(1, 0, joker=True), [predict(2, 2, match_id=2)], rules)


def test_second_joker_in_a_matchday_rejected(rules: ScoringRules) -> None:
    existing = [predict(2, 2, joker=True, match_id=2)]
    with pytest.raises(JokerLimitError):
        ensure_joker_available(predict(1, 0, joker=True), existing, rules)


def test_joker_may_be_moved_within_the_same_match(rules: ScoringRules) -> None:
    existing = [predict(2, 2, joker=True, match_id=1)]
    ensure_joker_available(predict(1, 0, joker=True, match_id=1), existing, rules)


def test_other_players_jokers_do_not_count(rules: ScoringRules) -> None:
    other = predict(0, 0, player_id=2, match_id=2, joker=True)
    ensure_joker_available(predict(1, 0, joker=True), [other], rules)


def test_joker_count_is_configurable() -> None:
    existing = [predict(2, 2, joker=True, match_id=2)]
    rules = ScoringRules(jokers_per_matchday=2)
    ensure_joker_available(predict(1, 0, joker=True), existing, rules)
