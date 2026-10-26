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
    """Anything that can deliver the fixtures and results of a matchday."""

    #: Short identifier carried by the sync events, e.g. ``football-data.org``.
    name: str

    #: False for sources that fetch nothing, so the app knows not to offer syncing.
    online: bool

    def fetch_matchday(self, league: str, season: str, number: int) -> list[FetchedMatch]:
        """Fixtures and, where known, results of one matchday.

        Raises a :class:`ProviderError` when the data cannot be fetched.
        """
        ...

    def current_matchday(self, league: str, season: str) -> int | None:
        """Which matchday the league is playing now, used to bootstrap an empty database.

        ``None`` when the source cannot tell.
        """
        ...
