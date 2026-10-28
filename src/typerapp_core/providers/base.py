"""The contract every source of results implements.

A provider knows nothing about the database: it returns :class:`FetchedMatch` values, and the
controller turns them into stored matches. That way a new source is one small class, and the
rest of the application does not change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from typerapp_core.models import Match, MatchStatus, Score


class ProviderError(Exception):
    """Results could not be fetched. The view turns this into a Polish message."""


class MissingApiKey(ProviderError):
    """No API key configured, so the online source cannot be used at all."""


class RateLimited(ProviderError):
    """Too many requests. The free plan allows about ten per minute."""


class CompetitionUnavailable(ProviderError):
    """The league is not part of the plan the key belongs to."""


class ProviderUnavailable(ProviderError):
    """The service did not answer, or answered with something unexpected."""


@dataclass(frozen=True, slots=True)
class FetchedMatch:
    """One match as a source describes it, before it gets a database identity."""

    home_team: str
    away_team: str
    kickoff: datetime
    status: MatchStatus = MatchStatus.SCHEDULED
    score: Score | None = None
    external_id: str | None = None
    #: The round the source puts the match in. A whole season comes back as one list, so each
    #: match has to say where it belongs; ``None`` means the source does not number it.
    matchday: int | None = None

    def to_match(self, matchday_id: int) -> Match:
        return Match(
            matchday_id=matchday_id,
            home_team=self.home_team,
            away_team=self.away_team,
            kickoff=self.kickoff,
            status=self.status,
            score=self.score,
            external_id=self.external_id,
        )


@runtime_checkable
class ResultsProvider(Protocol):
    """Anything that can deliver the fixtures and results of a season."""

    #: Short identifier carried by the sync events, e.g. ``football-data.org``.
    name: str

    #: False for sources that fetch nothing, so the app knows not to offer syncing.
    online: bool

    def supports(self, league: str) -> bool:
        """Whether this source has anything to say about that league.

        Asked before every sync, so a league the source does not cover (the demo one, say)
        never produces an error the player can do nothing about.
        """
        ...

    def fetch_season(self, league: str, season: str) -> list[FetchedMatch]:
        """Every fixture of the season, with the results of the ones already played.

        The whole season in one go, rather than a matchday at a time: it is one request instead
        of dozens, and it leaves no holes, so every matchday from the first one is there to be
        looked at. Matches that cannot be placed in a numbered matchday are left out.

        Raises a :class:`ProviderError` when the data cannot be fetched.
        """
        ...
