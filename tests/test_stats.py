"""Per-player statistics shown on the profile screen."""

from typerapp_core.models import Matchday, ScoringRules
from typerapp_core.ranking import build_player_stats, build_ranking

from helpers import finished, make_match, player, predict

RULES = ScoringRules()
DAY_12 = Matchday(league="PL", season="2026/27", number=12, id=1)
DAY_13 = Matchday(league="PL", season="2026/27", number=13, id=2)

ALA = player(1, "Ala")


def stats_for(predictions, matches, matchdays=(DAY_12, DAY_13), rules: ScoringRules = RULES):
    entry = build_ranking([ALA], predictions, matches, rules)[0]
    return build_player_stats(entry, predictions, matches, matchdays, rules)


def test_counters_follow_the_ranking_entry() -> None:
    matches = [finished(1, 2, 1), finished(2, 3, 1), finished(3, 1, 0)]
    predictions = [
        predict(2, 1, match_id=1),  # exact
        predict(5, 3, match_id=2),  # goal difference
        predict(0, 3, match_id=3),  # miss
    ]

    stats = stats_for(predictions, matches)

    assert (stats.entry.exact_hits, stats.entry.difference_hits) == (1, 1)
    assert (stats.entry.predictions, stats.entry.hits, stats.misses) == (3, 2, 1)
    assert stats.accuracy == 67


def test_no_predictions_means_no_division_by_zero() -> None:
    stats = stats_for([], [finished(1, 2, 1)])

    assert (stats.accuracy, stats.average_points, stats.played_matchdays) == (0, 0.0, 0)
    assert stats.misses == 0


def test_joker_hits_are_counted() -> None:
    matches = [finished(1, 2, 1), finished(2, 0, 0, matchday_id=2)]
    predictions = [
        predict(2, 1, match_id=1, joker=True),  # settled and hit
        predict(1, 0, match_id=2, joker=True),  # settled and missed (a draw was played)
        predict(1, 1, match_id=9, joker=True),  # unknown match, ignored
    ]

    stats = stats_for(predictions, matches)

    assert (stats.jokers_played, stats.jokers_hit) == (2, 1)


def test_points_are_split_per_matchday() -> None:
    matches = [finished(1, 2, 1), finished(3, 1, 0, matchday_id=2)]
    predictions = [predict(2, 1, match_id=1), predict(1, 0, match_id=3)]

    stats = stats_for(predictions, matches)

    twelfth, thirteenth = stats.matchdays
    assert (twelfth.matchday.number, twelfth.points, twelfth.predictions) == (12, 12, 1)
    assert (thirteenth.matchday.number, thirteenth.points) == (13, 12)
    assert stats.entry.points == 24
    assert stats.average_points == 12.0


def test_matchday_progress_is_reported() -> None:
    matches = [finished(1, 2, 1), make_match(2)]
    predictions = [predict(2, 1, match_id=1)]

    twelfth = stats_for(predictions, matches, matchdays=(DAY_12,)).matchdays[0]

    assert (twelfth.settled, twelfth.matches) == (1, 2)
    assert not twelfth.is_finished


def test_joker_of_the_matchday_is_pointed_out() -> None:
    matches = [finished(1, 2, 1), finished(2, 0, 0)]
    predictions = [predict(2, 1, match_id=1), predict(0, 0, match_id=2, joker=True)]

    twelfth = stats_for(predictions, matches, matchdays=(DAY_12,)).matchdays[0]

    assert twelfth.joker_match_id == 2


def test_rules_change_the_statistics() -> None:
    matches = [finished(1, 2, 1)]
    predictions = [predict(2, 1, match_id=1)]

    generous = stats_for(predictions, matches, rules=ScoringRules(exact_score=50))

    assert generous.entry.points == 50 + 1 + 1 + 5  # exact, two bonuses and the sweep
