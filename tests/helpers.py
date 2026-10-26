"""Shared builders for the core tests."""

from datetime import UTC, datetime, timedelta

from typerapp_core.models import Match, MatchStatus, Player, Prediction, Score

#: Saturday 18:00 UTC, the first kickoff of the test matchday.
FIRST_KICKOFF = datetime(2026, 11, 7, 18, 0, tzinfo=UTC)


def make_match(
    match_id: int = 1,
    *,
    matchday_id: int = 1,
    home_team: str = "Legia",
    away_team: str = "Lech",
    kickoff: datetime | None = None,
    status: MatchStatus = MatchStatus.SCHEDULED,
    score: Score | None = None,
) -> Match:
    return Match(
        matchday_id=matchday_id,
        home_team=home_team,
        away_team=away_team,
        kickoff=kickoff or FIRST_KICKOFF,
        status=status,
        score=score,
        id=match_id,
    )


def finished(match_id: int, home: int, away: int, *, matchday_id: int = 1) -> Match:
    """A settled match with a real result."""
    return make_match(
        match_id,
        matchday_id=matchday_id,
        kickoff=FIRST_KICKOFF + timedelta(hours=match_id),
        status=MatchStatus.FINISHED,
        score=Score(home, away),
    )


def predict(
    home: int,
    away: int,
    *,
    player_id: int = 1,
    match_id: int = 1,
    joker: bool = False,
) -> Prediction:
    return Prediction(
        player_id=player_id,
        match_id=match_id,
        home_goals=home,
        away_goals=away,
        joker=joker,
    )


def player(player_id: int, name: str) -> Player:
    return Player(name=name, id=player_id)
