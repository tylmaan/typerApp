from typerapp_core.models import MatchStatus, ScoringRules
from typerapp_core.ranking import build_ranking

from helpers import finished, make_match, player, predict

NO_BONUSES = ScoringRules(
    exact_score=4,
    goal_difference=2,
    correct_outcome=2,
    both_teams_scored=0,
    total_goals=0,
    matchday_sweep=0,
)


def test_ranking_is_ordered_by_points(rules: ScoringRules) -> None:
    players = [player(1, "Ala"), player(2, "Bartek")]
    matches = [finished(1, 2, 1), finished(2, 0, 0)]
    predictions = [
        predict(2, 1, player_id=1, match_id=1),  # exact: 5 + 1 + 1
        predict(0, 0, player_id=1, match_id=2),  # exact: 5 + 1 + 1
        predict(1, 0, player_id=2, match_id=1),  # goal difference: 3
        predict(1, 1, player_id=2, match_id=2),  # goal difference: 3
    ]

    table = build_ranking(players, predictions, matches, rules)

    assert [(entry.player_name, entry.points, entry.position) for entry in table] == [
        ("Ala", 19, 1),  # 14 + 5 for the matchday sweep
        ("Bartek", 11, 2),  # 6 + 5
    ]


def test_player_without_predictions_is_last_with_zero(rules: ScoringRules) -> None:
    players = [player(1, "Ala"), player(2, "Bartek")]
    matches = [finished(1, 2, 1)]
    predictions = [predict(2, 1, player_id=1, match_id=1)]

    table = build_ranking(players, predictions, matches, rules)

    assert (table[1].player_name, table[1].points, table[1].predictions) == ("Bartek", 0, 0)


def test_tie_is_broken_by_more_exact_scores() -> None:
    players = [player(1, "Ala"), player(2, "Bartek")]
    matches = [finished(1, 2, 1), finished(2, 3, 1)]
    predictions = [
        predict(2, 1, player_id=1, match_id=1),  # exact: 4
        predict(4, 0, player_id=2, match_id=1),  # outcome: 2
        predict(5, 0, player_id=2, match_id=2),  # outcome: 2
    ]

    table = build_ranking(players, predictions, matches, NO_BONUSES)

    assert [(entry.player_name, entry.points) for entry in table] == [("Ala", 4), ("Bartek", 4)]
    assert (table[0].exact_hits, table[1].outcome_hits) == (1, 2)
    assert [entry.position for entry in table] == [1, 2]


def test_players_equal_on_every_criterion_share_a_position(rules: ScoringRules) -> None:
    players = [player(1, "Ala"), player(2, "Basia"), player(3, "Celina"), player(4, "Dorota")]
    matches = [finished(1, 2, 1)]
    predictions = [
        predict(2, 1, player_id=1, match_id=1),
        predict(4, 0, player_id=2, match_id=1),
        predict(4, 0, player_id=3, match_id=1),
    ]

    table = build_ranking(players, predictions, matches, rules)

    assert [(entry.player_name, entry.points, entry.position) for entry in table] == [
        ("Ala", 12, 1),
        ("Basia", 7, 2),
        ("Celina", 7, 2),
        ("Dorota", 0, 4),
    ]


def test_ranking_can_be_limited_to_one_matchday(rules: ScoringRules) -> None:
    players = [player(1, "Ala")]
    matches = [finished(1, 2, 1, matchday_id=1), finished(3, 1, 0, matchday_id=2)]
    predictions = [
        predict(2, 1, player_id=1, match_id=1),
        predict(1, 0, player_id=1, match_id=3),
    ]

    overall = build_ranking(players, predictions, matches, rules)
    first = build_ranking(players, predictions, matches, rules, matchday_id=1)

    assert overall[0].points == 24
    assert (first[0].points, first[0].predictions) == (12, 1)


def test_unplayed_and_void_matches_do_not_count(rules: ScoringRules) -> None:
    players = [player(1, "Ala")]
    matches = [make_match(1), make_match(2, status=MatchStatus.POSTPONED)]
    predictions = [predict(2, 1, match_id=1), predict(0, 0, match_id=2)]

    table = build_ranking(players, predictions, matches, rules)

    assert (table[0].points, table[0].predictions) == (0, 0)


def test_sweep_bonus_is_lost_on_a_single_miss(rules: ScoringRules) -> None:
    players = [player(1, "Ala")]
    matches = [finished(1, 2, 1), finished(2, 0, 0)]
    predictions = [
        predict(2, 1, player_id=1, match_id=1),  # exact: 7
        predict(1, 2, player_id=1, match_id=2),  # miss, no bonuses either
    ]

    table = build_ranking(players, predictions, matches, rules)

    assert table[0].points == 7


def test_hit_counters(rules: ScoringRules) -> None:
    players = [player(1, "Ala")]
    matches = [finished(1, 2, 1), finished(2, 3, 1), finished(3, 1, 0)]
    predictions = [
        predict(2, 1, match_id=1),  # exact
        predict(5, 3, match_id=2),  # goal difference
        predict(4, 0, match_id=3),  # outcome
    ]

    entry = build_ranking(players, predictions, matches, rules)[0]

    assert (entry.exact_hits, entry.difference_hits, entry.outcome_hits) == (1, 1, 1)
    assert (entry.hits, entry.predictions) == (3, 3)
