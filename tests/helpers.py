"""Shared builders for the core tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from typerapp_core.models import Match, MatchStatus, Player, Prediction, Score
from typerapp_core.providers.base import FetchedMatch

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


class FakeProvider:
    """An online source the tests drive: it answers with whatever it was given."""

    name = "fake"
    online = True

    def supports(self, league: str) -> bool:
        del league
        return True

    def __init__(
        self,
        matches: dict[int, list[FetchedMatch]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        #: Matchday number -> its matches, which is how a test thinks about a season.
        self.matches = matches or {}
        self.error = error
        #: One ``(league, season)`` per request, since a season is fetched in a single one.
        self.asked: list[tuple[str, str]] = []

    def fetch_season(self, league: str, season: str) -> list[FetchedMatch]:
        if self.error:
            raise self.error
        self.asked.append((league, season))
        return [
            replace(entry, matchday=number)
            for number, entries in sorted(self.matches.items())
            for entry in entries
        ]


def fetched(
    home: str = "Arsenal",
    away: str = "Chelsea",
    *,
    status: MatchStatus = MatchStatus.SCHEDULED,
    score: Score | None = None,
    external_id: str = "1",
    kickoff: datetime | None = None,
) -> FetchedMatch:
    return FetchedMatch(
        home_team=home,
        away_team=away,
        kickoff=kickoff or FIRST_KICKOFF,
        status=status,
        score=score,
        external_id=external_id,
    )
