"""Domain entities of the prediction game.

All entities are frozen dataclasses. They can therefore be passed between the worker thread
that talks to the API and the UI thread, and carried inside events, without any risk of being
mutated behind the view's back. A change means building a new instance, usually with
``dataclasses.replace``.

Database identifiers are optional: an entity that has not been stored yet has ``id is None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


def utc_now() -> datetime:
    """Current time as an aware UTC timestamp. All timestamps in the core are aware."""
    return datetime.now(UTC)


class Outcome(StrEnum):
    """Result of a match seen from the home team's side."""

    HOME_WIN = "home_win"
    DRAW = "draw"
    AWAY_WIN = "away_win"


class MatchStatus(StrEnum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINISHED = "finished"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


#: Statuses that void every prediction for the match: no points, and a joker spent on it is lost.
VOID_STATUSES = frozenset({MatchStatus.POSTPONED, MatchStatus.CANCELLED})


@dataclass(frozen=True, slots=True)
class Score:
    """A pair of goal counts, used both for real results and for predicted ones."""

    home: int
    away: int

    def __post_init__(self) -> None:
        if self.home < 0 or self.away < 0:
            raise ValueError(f"Goal counts cannot be negative: {self.home}:{self.away}")

    @property
    def outcome(self) -> Outcome:
        if self.home > self.away:
            return Outcome.HOME_WIN
        if self.home < self.away:
            return Outcome.AWAY_WIN
        return Outcome.DRAW

    @property
    def difference(self) -> int:
        """Goal difference from the home team's side, e.g. ``-1`` for 1:2."""
        return self.home - self.away

    @property
    def total(self) -> int:
        return self.home + self.away

    @property
    def both_scored(self) -> bool:
        return self.home > 0 and self.away > 0

    def __str__(self) -> str:
        return f"{self.home}:{self.away}"

    def __format__(self, spec: str) -> str:
        """So ``f"{score:>5}"`` lines results up in a column instead of raising."""
        return format(str(self), spec)


@dataclass(frozen=True, slots=True)
class Player:
    """A player taking part in the game. Hot-seat only, so no credentials."""

    name: str
    created_at: datetime = field(default_factory=utc_now)
    id: int | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Player name cannot be empty")


@dataclass(frozen=True, slots=True)
class Matchday:
    """A single round of a league season."""

    league: str
    season: str
    number: int
    id: int | None = None

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError(f"Matchday number must be positive: {self.number}")


@dataclass(frozen=True, slots=True)
class Match:
    matchday_id: int
    home_team: str
    away_team: str
    kickoff: datetime
    status: MatchStatus = MatchStatus.SCHEDULED
    score: Score | None = None
    external_id: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        if self.kickoff.tzinfo is None:
            raise ValueError("Match kickoff must be timezone aware")

    @property
    def is_settled(self) -> bool:
        """The match is over and has a result, so predictions for it can be scored."""
        return self.status is MatchStatus.FINISHED and self.score is not None

    @property
    def is_void(self) -> bool:
        """The match will not be played, so predictions for it are worth nothing."""
        return self.status in VOID_STATUSES


@dataclass(frozen=True, slots=True)
class Prediction:
    """One player's predicted score for one match. At most one per player and match."""

    player_id: int
    match_id: int
    home_goals: int
    away_goals: int
    joker: bool = False
    created_at: datetime = field(default_factory=utc_now)
    id: int | None = None

    def __post_init__(self) -> None:
        Score(self.home_goals, self.away_goals)  # reuse the goal count validation

    @property
    def score(self) -> Score:
        return Score(self.home_goals, self.away_goals)


@dataclass(frozen=True, slots=True)
class ScoringRules:
    """Configurable rules of the game: how points are awarded and when predictions close.

    Only the highest matched level of the base scale is awarded (an exact hit does not also
    pay out the goal difference). The bonuses are added on top of it, and the joker then
    multiplies the whole per-match total.

    Setting any points field to ``0`` disables that reward, so the settings screen needs
    plain numbers only, with no separate on/off switches.
    """

    # Base scale, highest matched level wins.
    exact_score: int = 5
    goal_difference: int = 3
    correct_outcome: int = 2
    # Bonuses, added to the base.
    both_teams_scored: int = 1
    total_goals: int = 1
    matchday_sweep: int = 5
    # Joker.
    joker_multiplier: int = 2
    jokers_per_matchday: int = 1
    # Predictions for a matchday close this many minutes before its first kickoff.
    prediction_deadline_minutes: int = 5

    def __post_init__(self) -> None:
        points = {
            "exact_score": self.exact_score,
            "goal_difference": self.goal_difference,
            "correct_outcome": self.correct_outcome,
            "both_teams_scored": self.both_teams_scored,
            "total_goals": self.total_goals,
            "matchday_sweep": self.matchday_sweep,
        }
        for name, value in points.items():
            if value < 0:
                raise ValueError(f"{name} cannot be negative: {value}")
        if self.joker_multiplier < 1:
            raise ValueError(f"Joker multiplier must be at least 1: {self.joker_multiplier}")
        if self.jokers_per_matchday < 0:
            raise ValueError(f"Joker count cannot be negative: {self.jokers_per_matchday}")
        if self.prediction_deadline_minutes < 0:
            raise ValueError(
                f"Prediction deadline cannot be negative: {self.prediction_deadline_minutes}"
            )


@dataclass(frozen=True, slots=True)
class RankingEntry:
    """One row of the leaderboard. Always derived from predictions, never stored."""

    player_id: int
    player_name: str
    points: int = 0
    exact_hits: int = 0
    difference_hits: int = 0
    outcome_hits: int = 0
    predictions: int = 0
    position: int = 0

    @property
    def hits(self) -> int:
        """Predictions that scored at least the outcome level."""
        return self.exact_hits + self.difference_hits + self.outcome_hits
