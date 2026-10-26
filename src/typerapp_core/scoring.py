"""Game rules: awarding points, the prediction deadline and the joker limit.

Pure functions over the domain entities: no storage, no I/O, no UI. Everything here is
driven by :class:`~typerapp_core.models.ScoringRules`, which the player edits in the
settings screen.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from typerapp_core.models import Match, MatchStatus, Prediction, Score, ScoringRules


class PredictionError(Exception):
    """The prediction cannot be saved."""


class PredictionClosedError(PredictionError):
    """Predictions for this matchday are already closed."""


class JokerLimitError(PredictionError):
    """The player has already used up the jokers available in this matchday."""


class HitLevel(StrEnum):
    """How close a prediction was. Only the highest level reached is paid out."""

    EXACT = "exact"
    GOAL_DIFFERENCE = "goal_difference"
    OUTCOME = "outcome"
    MISS = "miss"


class Bonus(StrEnum):
    """Extra rewards added on top of the base scale.

    Both are derived from the predicted score itself, so they need no extra input from the
    player and work offline.
    """

    BOTH_TEAMS_SCORED = "both_teams_scored"
    TOTAL_GOALS = "total_goals"


@dataclass(frozen=True, slots=True)
class PointsBreakdown:
    """The result of scoring one prediction, split up so the UI can explain it."""

    level: HitLevel
    base: int
    bonuses: tuple[tuple[Bonus, int], ...]
    joker: bool
    total: int

    @property
    def is_hit(self) -> bool:
        return self.level is not HitLevel.MISS


def hit_level(predicted: Score, result: Score) -> HitLevel:
    if predicted == result:
        return HitLevel.EXACT
    if predicted.difference == result.difference:
        return HitLevel.GOAL_DIFFERENCE
    if predicted.outcome is result.outcome:
        return HitLevel.OUTCOME
    return HitLevel.MISS


def score_prediction(prediction: Prediction, result: Score, rules: ScoringRules) -> PointsBreakdown:
    """Points for a single prediction against the real result.

    A draw predicted as a draw always lands on :attr:`HitLevel.GOAL_DIFFERENCE` at worst,
    because every draw has a goal difference of zero.
    """
    predicted = prediction.score
    level = hit_level(predicted, result)
    base = {
        HitLevel.EXACT: rules.exact_score,
        HitLevel.GOAL_DIFFERENCE: rules.goal_difference,
        HitLevel.OUTCOME: rules.correct_outcome,
        HitLevel.MISS: 0,
    }[level]

    bonuses: list[tuple[Bonus, int]] = []
    if rules.both_teams_scored and predicted.both_scored == result.both_scored:
        bonuses.append((Bonus.BOTH_TEAMS_SCORED, rules.both_teams_scored))
    if rules.total_goals and predicted.total == result.total:
        bonuses.append((Bonus.TOTAL_GOALS, rules.total_goals))

    subtotal = base + sum(points for _, points in bonuses)
    total = subtotal * rules.joker_multiplier if prediction.joker else subtotal
    return PointsBreakdown(
        level=level,
        base=base,
        bonuses=tuple(bonuses),
        joker=prediction.joker,
        total=total,
    )


def sweep_awarded(
    predictions: Iterable[Prediction],
    matches: Iterable[Match],
    rules: ScoringRules,
) -> bool:
    """True when the player got the outcome right in every settled match of a matchday.

    Void matches (postponed, cancelled) are skipped, so a matchday that falls apart does not
    block the bonus. At least one settled match is required, and every one of them must have
    been predicted.
    """
    if not rules.matchday_sweep:
        return False
    settled = [match for match in matches if match.is_settled]
    if not settled:
        return False

    by_match = {prediction.match_id: prediction for prediction in predictions}
    for match in settled:
        prediction = by_match.get(match.id)  # type: ignore[arg-type]
        if prediction is None or match.score is None:
            return False
        if hit_level(prediction.score, match.score) is HitLevel.MISS:
            return False
    return True


def matchday_deadline(matches: Iterable[Match], rules: ScoringRules) -> datetime | None:
    """When predictions for the matchday close: shortly before its first kickoff.

    Returns ``None`` for an empty matchday, which then counts as closed.
    """
    kickoffs = [match.kickoff for match in matches]
    if not kickoffs:
        return None
    return min(kickoffs) - timedelta(minutes=rules.prediction_deadline_minutes)


def is_matchday_open(matches: Iterable[Match], now: datetime, rules: ScoringRules) -> bool:
    """Until the deadline the player may freely change predictions and move the joker."""
    if now.tzinfo is None:
        raise ValueError("Current time must be timezone aware")
    deadline = matchday_deadline(matches, rules)
    return deadline is not None and now < deadline


def ensure_can_predict(
    match: Match,
    matchday_matches: Iterable[Match],
    now: datetime,
    rules: ScoringRules,
) -> None:
    """Raise :class:`PredictionClosedError` unless the match can still be predicted.

    The matchday deadline is the rule the player sees. The per-match check behind it is a
    safeguard for fixtures that move around after the matchday was loaded.
    """
    matches = list(matchday_matches)
    if not is_matchday_open(matches, now, rules):
        raise PredictionClosedError(
            f"Predictions for the matchday of {match.home_team} – {match.away_team} are closed"
        )
    if match.status is not MatchStatus.SCHEDULED or now >= match.kickoff:
        raise PredictionClosedError(
            f"The match {match.home_team} – {match.away_team} is no longer open for predictions"
        )


def ensure_joker_available(
    prediction: Prediction,
    matchday_predictions: Iterable[Prediction],
    rules: ScoringRules,
) -> None:
    """Check the player's joker limit inside one matchday.

    ``matchday_predictions`` are the player's existing predictions in the same matchday. A
    prediction for the same match is ignored, because the new one replaces it, which is what
    makes moving the joker around possible until the deadline.
    """
    if not prediction.joker:
        return
    used = sum(
        1
        for other in matchday_predictions
        if other.joker
        and other.player_id == prediction.player_id
        and other.match_id != prediction.match_id
    )
    if used >= rules.jokers_per_matchday:
        raise JokerLimitError("The joker for this matchday has already been used")


def settled_matches_by_id(matches: Iterable[Match]) -> Mapping[int, Match]:
    """Matches that can be scored, keyed by id. Handy for the controller and the ranking."""
    return {match.id: match for match in matches if match.is_settled and match.id is not None}
