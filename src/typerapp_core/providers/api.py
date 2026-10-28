"""Results from football-data.org (API v4).

The free plan allows about ten requests a minute and covers a dozen competitions, so the app
asks for one matchday at a time and keeps everything in SQLite. Field names and statuses are
mapped defensively: an unknown status is treated as "not played yet" and a missing score as
"not known", never as nil-nil.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import httpx

from typerapp_core.models import MatchStatus, Score
from typerapp_core.providers.base import (
    CompetitionUnavailable,
    FetchedMatch,
    MissingApiKey,
    ProviderUnavailable,
    RateLimited,
)

BASE_URL = "https://api.football-data.org/v4"

API_KEY_HEADER = "X-Auth-Token"

DEFAULT_TIMEOUT = 10.0

#: The competitions the app offers, as ``(API code, name shown to the player)``: the big five
#: leagues and the Champions League, in the order the settings list them. All of them are on
#: the free plan. The API has more (Eredivisie, the World Cup and so on), left out on purpose,
#: and this one list is what both the league picker and the sync go by, so a league that is not
#: here can neither be chosen nor fetched.
COMPETITIONS: tuple[tuple[str, str], ...] = (
    ("PL", "Premier League"),
    ("SA", "Serie A"),
    ("PD", "La Liga"),
    ("BL1", "Bundesliga"),
    ("FL1", "Ligue 1"),
    ("CL", "Liga Mistrzów"),
)

#: The API's own status names, mapped onto the ones the game uses.
STATUS_MAP = {
    "SCHEDULED": MatchStatus.SCHEDULED,
    "TIMED": MatchStatus.SCHEDULED,
    "IN_PLAY": MatchStatus.LIVE,
    "PAUSED": MatchStatus.LIVE,
    "FINISHED": MatchStatus.FINISHED,
    "AWARDED": MatchStatus.FINISHED,
    "POSTPONED": MatchStatus.POSTPONED,
    "SUSPENDED": MatchStatus.POSTPONED,
    "CANCELLED": MatchStatus.CANCELLED,
    "CANCELED": MatchStatus.CANCELLED,
}


def competition_code(league: str) -> str | None:
    """The API code for a league name, or ``None`` when it is not in the free plan."""
    for code, name in COMPETITIONS:
        if league in (code, name):
            return code
    return None


def competition_name(code: str) -> str:
    return next((name for api_code, name in COMPETITIONS if api_code == code), code)


def season_parameter(season: str) -> str:
    """``2026/27`` is the season as people write it; the API wants the starting year."""
    return season.split("/")[0].strip()


class ApiResultsProvider:
    """Reads fixtures and results over HTTP. Synchronous on purpose: the view runs it in a
    worker thread, and the core stays free of any event loop."""

    name = "football-data.org"
    online = True

    def __init__(
        self,
        api_key: str | None,
        *,
        client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key
        self._timeout = timeout
        self._client = client

    def supports(self, league: str) -> bool:
        return competition_code(league) is not None

    def fetch_season(self, league: str, season: str) -> list[FetchedMatch]:
        """The whole season in a single request: a few hundred matches, one of the ten the
        free plan allows each minute.

        Only the rounds that are played in numbered matchdays come back. A knockout round has
        no matchday number of its own to put it under, so it is left out rather than merged
        into the league phase; the Champions League does not publish its knockouts this early
        anyway, and the ties that are not decided yet have no teams to show.
        """
        if not self.api_key:
            raise MissingApiKey(
                "No football-data.org key: set FOOTBALL_DATA_API_KEY in the .env file"
            )
        code = competition_code(league)
        if code is None:
            raise CompetitionUnavailable(f"{league} is not part of the offered competitions")

        payload = self._get(f"/competitions/{code}/matches", {"season": season_parameter(season)})
        entries = payload.get("matches")
        if not isinstance(entries, list):
            raise ProviderUnavailable("Unexpected answer: no list of matches")
        return _parse_season(entry for entry in entries if isinstance(entry, dict))

    # ------------------------------------------------------------------ private

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            response = client.get(
                f"{BASE_URL}{path}",
                params=params,
                headers={API_KEY_HEADER: self.api_key or ""},
            )
        except httpx.HTTPError as error:
            raise ProviderUnavailable(f"No answer from {self.name}: {error}") from error
        finally:
            if self._client is None:
                client.close()

        if response.status_code == 429:
            raise RateLimited("Request limit reached, try again in a minute")
        if response.status_code in (401, 403):
            raise CompetitionUnavailable(
                "The key does not give access to this competition (free plan)"
            )
        if response.status_code >= 400:
            raise ProviderUnavailable(f"{self.name} answered with {response.status_code}")

        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderUnavailable("The answer was not valid JSON") from error
        if not isinstance(payload, dict):
            raise ProviderUnavailable("Unexpected answer shape")
        return payload


#: Stages played in numbered matchdays. Anything else is a knockout round.
NUMBERED_STAGES = frozenset({"REGULAR_SEASON", "LEAGUE_STAGE", "GROUP_STAGE"})


def _parse_season(entries: Iterable[dict[str, Any]]) -> list[FetchedMatch]:
    """Read a season's worth of matches, skipping the ones that cannot be shown.

    A match with no teams yet (a tie still to be decided) or without a matchday number has
    nowhere to go, and one such entry must not fail the whole sync. If *nothing* can be read
    the format has probably changed, and that is reported instead of silently saving nothing.
    """
    entries = list(entries)
    candidates = [
        entry for entry in entries if entry.get("stage", "REGULAR_SEASON") in NUMBERED_STAGES
    ]
    parsed: list[FetchedMatch] = []
    for entry in candidates:
        try:
            match = parse_match(entry)
        except ProviderUnavailable:
            continue
        if match.matchday is not None:
            parsed.append(match)
    if candidates and not parsed:
        raise ProviderUnavailable(f"None of the {len(candidates)} matches could be read")
    return parsed


def parse_match(entry: dict[str, Any]) -> FetchedMatch:
    """Map one match from the API onto the game's own model."""
    home = _team_name(entry.get("homeTeam"))
    away = _team_name(entry.get("awayTeam"))
    if not home or not away:
        raise ProviderUnavailable("A match without team names")

    status = STATUS_MAP.get(str(entry.get("status", "")).upper(), MatchStatus.SCHEDULED)
    score = _full_time_score(entry.get("score"))
    if score is None and status is MatchStatus.FINISHED:
        # Finished but without a score: treat it as still to come rather than invent 0:0.
        status = MatchStatus.SCHEDULED

    external_id = entry.get("id")
    matchday = entry.get("matchday")
    return FetchedMatch(
        home_team=home,
        away_team=away,
        kickoff=_kickoff(entry.get("utcDate")),
        status=status,
        score=score,
        external_id=str(external_id) if external_id is not None else None,
        matchday=matchday if isinstance(matchday, int) and matchday > 0 else None,
    )


def _team_name(team: Any) -> str:
    if isinstance(team, dict):
        for field in ("shortName", "name", "tla"):
            value = team.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _kickoff(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ProviderUnavailable("A match without a kickoff time")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProviderUnavailable(f"Unreadable date: {value}") from error
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _full_time_score(score: Any) -> Score | None:
    if not isinstance(score, dict):
        return None
    full_time = score.get("fullTime")
    if not isinstance(full_time, dict):
        return None
    home, away = full_time.get("home"), full_time.get("away")
    if isinstance(home, int) and isinstance(away, int):
        return Score(home, away)
    return None
