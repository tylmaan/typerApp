"""Results providers, tested against a recorded answer instead of the real service."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from typerapp_core.config import API_KEY_VARIABLE, football_data_api_key, load_env_file
from typerapp_core.models import MatchStatus, Score
from typerapp_core.providers.api import (
    API_KEY_HEADER,
    COMPETITIONS,
    ApiResultsProvider,
    competition_code,
    competition_name,
    parse_match,
    season_parameter,
)
from typerapp_core.providers.base import (
    CompetitionUnavailable,
    FetchedMatch,
    MissingApiKey,
    ProviderUnavailable,
    RateLimited,
    ResultsProvider,
)
from typerapp_core.providers.manual import ManualResultsProvider

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "football_data_matchday.json").read_text(encoding="utf-8")
)


def provider_with(handler) -> ApiResultsProvider:
    transport = httpx.MockTransport(handler)
    return ApiResultsProvider("test-key", client=httpx.Client(transport=transport))


def answering(payload: dict, status_code: int = 200) -> ApiResultsProvider:
    return provider_with(lambda request: httpx.Response(status_code, json=payload))


# --------------------------------------------------------------------- contract


def test_both_providers_satisfy_the_contract() -> None:
    assert isinstance(ManualResultsProvider(), ResultsProvider)
    assert isinstance(ApiResultsProvider("key"), ResultsProvider)


def test_manual_provider_fetches_nothing() -> None:
    manual = ManualResultsProvider()
    assert manual.fetch_matchday("Premier League", "2026/27", 12) == []
    assert manual.current_matchday("Premier League", "2026/27") is None
    assert not manual.online


def test_fetched_match_becomes_a_match() -> None:
    fetched = FetchedMatch(
        home_team="Arsenal",
        away_team="Chelsea",
        kickoff=datetime(2026, 11, 7, 15, 0, tzinfo=UTC),
        status=MatchStatus.FINISHED,
        score=Score(2, 1),
        external_id="497365",
    )

    match = fetched.to_match(matchday_id=7)

    assert (match.matchday_id, match.external_id, match.score) == (7, "497365", Score(2, 1))
    assert match.is_settled


# ------------------------------------------------------------------- the client


def test_recorded_matchday_is_mapped() -> None:
    matches = answering(FIXTURE).fetch_matchday("Premier League", "2026/27", 12)

    assert len(matches) == 3
    finished, upcoming, postponed = matches

    assert (finished.home_team, finished.away_team) == ("Arsenal", "Chelsea")
    assert finished.kickoff == datetime(2026, 11, 7, 15, 0, tzinfo=UTC)
    assert (finished.status, finished.score) == (MatchStatus.FINISHED, Score(2, 1))
    assert finished.external_id == "497365"

    assert (upcoming.status, upcoming.score) == (MatchStatus.SCHEDULED, None)
    assert (postponed.status, postponed.score) == (MatchStatus.POSTPONED, None)


def test_request_carries_the_key_and_the_filters() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get(API_KEY_HEADER, "")
        return httpx.Response(200, json=FIXTURE)

    provider_with(handler).fetch_matchday("Premier League", "2026/27", 12)

    assert seen["key"] == "test-key"
    assert "/v4/competitions/PL/matches" in seen["url"]
    assert "matchday=12" in seen["url"]
    assert "season=2026" in seen["url"]  # the API wants the starting year


def test_current_matchday_is_read_from_the_competition() -> None:
    payload = {"currentSeason": {"currentMatchday": 13}}
    assert answering(payload).current_matchday("PL", "2026/27") == 13
    assert answering({"currentSeason": {}}).current_matchday("PL", "2026/27") is None


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (429, RateLimited),
        (403, CompetitionUnavailable),
        (401, CompetitionUnavailable),
        (500, ProviderUnavailable),
    ],
)
def test_http_errors_are_translated(status_code: int, expected: type[Exception]) -> None:
    with pytest.raises(expected):
        answering({}, status_code).fetch_matchday("Premier League", "2026/27", 12)


def test_network_failure_is_translated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("nothing there")

    with pytest.raises(ProviderUnavailable):
        provider_with(handler).fetch_matchday("Premier League", "2026/27", 12)


def test_answer_without_matches_is_an_error() -> None:
    with pytest.raises(ProviderUnavailable):
        answering({"competition": {}}).fetch_matchday("Premier League", "2026/27", 12)


def test_missing_key_is_reported_before_any_request() -> None:
    with pytest.raises(MissingApiKey):
        ApiResultsProvider(None).fetch_matchday("Premier League", "2026/27", 12)


def test_league_outside_the_free_plan_is_reported() -> None:
    with pytest.raises(CompetitionUnavailable):
        answering(FIXTURE).fetch_matchday("Ekstraklasa", "2026/27", 12)


# ------------------------------------------------------------------- the mapping


@pytest.mark.parametrize(
    ("api_status", "expected"),
    [
        ("SCHEDULED", MatchStatus.SCHEDULED),
        ("TIMED", MatchStatus.SCHEDULED),
        ("IN_PLAY", MatchStatus.LIVE),
        ("PAUSED", MatchStatus.LIVE),
        ("FINISHED", MatchStatus.FINISHED),
        ("AWARDED", MatchStatus.FINISHED),
        ("POSTPONED", MatchStatus.POSTPONED),
        ("SUSPENDED", MatchStatus.POSTPONED),
        ("CANCELLED", MatchStatus.CANCELLED),
        ("SOMETHING_NEW", MatchStatus.SCHEDULED),  # never guess, treat it as not played
    ],
)
def test_status_mapping(api_status: str, expected: MatchStatus) -> None:
    entry = {
        "id": 1,
        "utcDate": "2026-11-07T15:00:00Z",
        "status": api_status,
        "homeTeam": {"shortName": "Arsenal"},
        "awayTeam": {"shortName": "Chelsea"},
        "score": {"fullTime": {"home": 2, "away": 1}},
    }
    assert parse_match(entry).status is expected


def test_finished_without_a_score_is_not_treated_as_nil_nil() -> None:
    entry = {
        "id": 1,
        "utcDate": "2026-11-07T15:00:00Z",
        "status": "FINISHED",
        "homeTeam": {"shortName": "Arsenal"},
        "awayTeam": {"shortName": "Chelsea"},
        "score": {"fullTime": {"home": None, "away": None}},
    }

    parsed = parse_match(entry)

    assert parsed.score is None
    assert parsed.status is MatchStatus.SCHEDULED


def test_team_name_falls_back_through_the_fields() -> None:
    entry = {
        "utcDate": "2026-11-07T15:00:00Z",
        "status": "TIMED",
        "homeTeam": {"name": "Arsenal FC"},
        "awayTeam": {"tla": "CHE"},
    }

    parsed = parse_match(entry)

    assert (parsed.home_team, parsed.away_team) == ("Arsenal FC", "CHE")
    assert parsed.external_id is None


@pytest.mark.parametrize(
    "entry",
    [
        {"utcDate": "2026-11-07T15:00:00Z", "homeTeam": {}, "awayTeam": {"tla": "CHE"}},
        {"homeTeam": {"tla": "ARS"}, "awayTeam": {"tla": "CHE"}},
        {"utcDate": "wczoraj", "homeTeam": {"tla": "ARS"}, "awayTeam": {"tla": "CHE"}},
    ],
)
def test_unusable_entries_are_errors(entry: dict) -> None:
    with pytest.raises(ProviderUnavailable):
        parse_match(entry)


def test_naive_dates_are_read_as_utc() -> None:
    entry = {
        "utcDate": "2026-11-07T15:00:00",
        "status": "TIMED",
        "homeTeam": {"tla": "ARS"},
        "awayTeam": {"tla": "CHE"},
    }
    assert parse_match(entry).kickoff == datetime(2026, 11, 7, 15, 0, tzinfo=UTC)


def test_competition_lookup() -> None:
    assert competition_code("Premier League") == "PL"
    assert competition_code("PL") == "PL"
    assert competition_code("Ekstraklasa") is None
    assert competition_name("BL1") == "Bundesliga"


def test_only_the_big_five_and_the_champions_league_are_offered() -> None:
    assert [code for code, _ in COMPETITIONS] == ["PL", "SA", "PD", "BL1", "FL1", "CL"]
    assert [name for _, name in COMPETITIONS] == [
        "Premier League",
        "Serie A",
        "La Liga",
        "Bundesliga",
        "Ligue 1",
        "Liga Mistrzów",
    ]


@pytest.mark.parametrize("league", ["Eredivisie", "Primeira Liga", "Mistrzostwa świata", "WC"])
def test_other_competitions_are_neither_chosen_nor_fetched(league: str) -> None:
    # The API has them, but the app does not offer them: one list decides both things.
    assert competition_code(league) is None
    assert not ApiResultsProvider("key").supports(league)
    with pytest.raises(CompetitionUnavailable):
        answering(FIXTURE).fetch_matchday(league, "2026/27", 1)


@pytest.mark.parametrize("league", [name for _, name in COMPETITIONS])
def test_every_offered_competition_can_be_fetched(league: str) -> None:
    assert ApiResultsProvider("key").supports(league)
    assert answering(FIXTURE).fetch_matchday(league, "2026/27", 12)


@pytest.mark.parametrize(
    ("season", "expected"), [("2026/27", "2026"), ("2026", "2026"), (" 2025/26 ", "2025")]
)
def test_season_parameter(season: str, expected: str) -> None:
    assert season_parameter(season) == expected


# ------------------------------------------------------------------- the api key


def test_env_file_is_parsed(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        f'# komentarz\n{API_KEY_VARIABLE}="abc123"\nPUSTA=\nbez_znaku_rownosci\n',
        encoding="utf-8",
    )

    values = load_env_file(env)

    assert values[API_KEY_VARIABLE] == "abc123"
    assert values["PUSTA"] == ""
    assert "bez_znaku_rownosci" not in values


def test_missing_env_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "nie-ma-mnie") == {}


def test_environment_wins_over_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text(f"{API_KEY_VARIABLE}=z-pliku\n", encoding="utf-8")

    monkeypatch.setenv(API_KEY_VARIABLE, "ze-srodowiska")
    assert football_data_api_key(env) == "ze-srodowiska"

    monkeypatch.setenv(API_KEY_VARIABLE, "   ")
    assert football_data_api_key(env) == "z-pliku"

    monkeypatch.delenv(API_KEY_VARIABLE)
    assert football_data_api_key(tmp_path / "brak") is None
