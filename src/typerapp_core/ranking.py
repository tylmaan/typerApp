"""Leaderboard: turning predictions and results into a sorted table.

The ranking is always computed, never stored, so changing the scoring rules in the settings
screen immediately recalculates the whole table.

Ties are broken by the quality of the hits: more exact scores first, then more goal
differences, then more outcomes. Players who are equal on all of those share a position.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from typerapp_core.models import Match, Player, Prediction, RankingEntry, ScoringRules
from typerapp_core.scoring import HitLevel, score_prediction, sweep_awarded


@dataclass(slots=True)
class _Tally:
    """Mutable accumulator used while walking through the predictions."""

    points: int = 0
    exact_hits: int = 0
    difference_hits: int = 0
    outcome_hits: int = 0
    predictions: int = 0

    def sort_key(self) -> tuple[int, int, int, int]:
        """Points first, then the quality of the hits. Negated, so sorting is descending."""
        return (-self.points, -self.exact_hits, -self.difference_hits, -self.outcome_hits)


@dataclass(slots=True)
class _MatchdayGroup:
    matches: list[Match] = field(default_factory=list)
    predictions: dict[int, list[Prediction]] = field(default_factory=lambda: defaultdict(list))


def build_ranking(
    players: Iterable[Player],
    predictions: Iterable[Prediction],
    matches: Iterable[Match],
    rules: ScoringRules,
    *,
    matchday_id: int | None = None,
) -> tuple[RankingEntry, ...]:
    """Build the leaderboard.

    ``matchday_id`` limits the table to a single matchday; ``None`` gives the overall one.
    Players without a single scored prediction are included with zero points, so everybody
    shows up in the table from the start.
    """
    players = [player for player in players if player.id is not None]
    tallies = {player.id: _Tally() for player in players}

    relevant = [
        match
        for match in matches
        if match.id is not None and (matchday_id is None or match.matchday_id == matchday_id)
    ]
    settled = {match.id: match for match in relevant if match.is_settled}

    # Every match of the matchday, not only the settled ones: the sweep bonus has to know
    # whether anything is still to be played.
    groups: dict[int, _MatchdayGroup] = defaultdict(_MatchdayGroup)
    for match in relevant:
        groups[match.matchday_id].matches.append(match)

    for prediction in predictions:
        tally = tallies.get(prediction.player_id)
        match = settled.get(prediction.match_id)
        if tally is None or match is None or match.score is None:
            continue

        breakdown = score_prediction(prediction, match.score, rules)
        tally.points += breakdown.total
        tally.predictions += 1
        if breakdown.level is HitLevel.EXACT:
            tally.exact_hits += 1
        elif breakdown.level is HitLevel.GOAL_DIFFERENCE:
            tally.difference_hits += 1
        elif breakdown.level is HitLevel.OUTCOME:
            tally.outcome_hits += 1

        groups[match.matchday_id].predictions[prediction.player_id].append(prediction)

    for group in groups.values():
        for player_id, player_predictions in group.predictions.items():
            if sweep_awarded(player_predictions, group.matches, rules):
                tallies[player_id].points += rules.matchday_sweep

    ordered = sorted(
        players,
        key=lambda player: (*tallies[player.id].sort_key(), player.name.casefold()),
    )

    entries: list[RankingEntry] = []
    previous_key: tuple[int, int, int, int] | None = None
    position = 0
    for index, player in enumerate(ordered, start=1):
        tally = tallies[player.id]
        key = tally.sort_key()
        if key != previous_key:
            position = index  # 1, 2, 2, 4 – shared positions keep the gap
            previous_key = key
        entries.append(
            RankingEntry(
                player_id=player.id,  # type: ignore[arg-type]
                player_name=player.name,
                points=tally.points,
                exact_hits=tally.exact_hits,
                difference_hits=tally.difference_hits,
                outcome_hits=tally.outcome_hits,
                predictions=tally.predictions,
                position=position,
            )
        )
    return tuple(entries)
