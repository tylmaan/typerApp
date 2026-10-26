"""Demo data, so the app has something to show without a network connection.

Kickoff times are generated relative to the current moment: one matchday already played and
one still open for predictions. Fixed calendar dates would turn the demo into a pile of
closed matchdays a month from now.

The predictions are drawn from a seeded random generator, so the demo always looks the same
while still spreading the players across the leaderboard.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from typerapp_core.models import Match, Matchday, MatchStatus, Outcome, Prediction, Score, utc_now
from typerapp_core.repository import (
    ACTIVE_LEAGUE,
    ACTIVE_PLAYER,
    ACTIVE_SEASON,
    Repository,
)

#: Deliberately not the name of a real competition: the demo must never share a matchday
#: with fixtures fetched from the API, or the two sets of matches end up in one list.
LEAGUE = "Premier League (demo)"
SEASON = "2026/27"

PLAYER_NAMES = ("Kuba", "Ala", "Bartek", "Celina", "Darek")

#: How often a player nails the exact score. Keeps the leaderboard from being a flat line.
SKILL = {"Kuba": 0.35, "Ala": 0.45, "Bartek": 0.20, "Celina": 0.30, "Darek": 0.15}

PLAYED_MATCHDAY = 12
OPEN_MATCHDAY = 13

PLAYED_FIXTURES = (
    ("Arsenal", "Chelsea"),
    ("Liverpool", "Everton"),
    ("Manchester City", "Brighton"),
    ("Newcastle", "Aston Villa"),
    ("Tottenham", "West Ham"),
)

PLAYED_RESULTS = (Score(2, 1), Score(3, 3), Score(4, 0), Score(0, 1), Score(1, 1))

OPEN_FIXTURES = (
    ("Chelsea", "Liverpool"),
    ("Everton", "Arsenal"),
    ("Brighton", "Newcastle"),
    ("Aston Villa", "Manchester City"),
    ("West Ham", "Tottenham"),
)

#: Kickoffs spread over two days, counted from the first match of the matchday.
KICKOFF_OFFSETS = (
    timedelta(0),
    timedelta(hours=2, minutes=30),
    timedelta(hours=5),
    timedelta(days=1, hours=1),
    timedelta(days=1, hours=3, minutes=30),
)

#: Players who already filled in the open matchday. The first player is left with an empty
#: card on purpose, so the demo opens with something to do.
EARLY_BIRDS = PLAYER_NAMES[1:4]


def seed_demo_data(
    repository: Repository,
    *,
    now: datetime | None = None,
    seed: int = 2026,
) -> None:
    """Fill an empty database with two matchdays, five players and their predictions."""
    now = now or utc_now()
    rng = random.Random(seed)

    players = {name: repository.add_player(name) for name in PLAYER_NAMES}

    played = repository.upsert_matchday(
        Matchday(league=LEAGUE, season=SEASON, number=PLAYED_MATCHDAY)
    )
    open_matchday = repository.upsert_matchday(
        Matchday(league=LEAGUE, season=SEASON, number=OPEN_MATCHDAY)
    )

    played_matches = repository.upsert_matches(
        _fixtures(
            played.id,  # type: ignore[arg-type]
            PLAYED_FIXTURES,
            first_kickoff=_kickoff_day(now, -5),
            status=MatchStatus.FINISHED,
            results=PLAYED_RESULTS,
        )
    )
    open_matches = repository.upsert_matches(
        _fixtures(
            open_matchday.id,  # type: ignore[arg-type]
            OPEN_FIXTURES,
            first_kickoff=_kickoff_day(now, 2),
            status=MatchStatus.SCHEDULED,
        )
    )

    for name, player in players.items():
        _predict_matchday(repository, rng, player.id, played_matches, SKILL[name])  # type: ignore[arg-type]
        if name in EARLY_BIRDS:
            _predict_matchday(repository, rng, player.id, open_matches, SKILL[name])  # type: ignore[arg-type]

    repository.set_setting(ACTIVE_LEAGUE, LEAGUE)
    repository.set_setting(ACTIVE_SEASON, SEASON)
    repository.set_setting(ACTIVE_PLAYER, players[PLAYER_NAMES[0]].id)


def _kickoff_day(now: datetime, days: int) -> datetime:
    """A round kickoff time, because real fixtures do not start at 16:36."""
    return (now + timedelta(days=days)).replace(hour=16, minute=0, second=0, microsecond=0)


def _fixtures(
    matchday_id: int,
    fixtures: tuple[tuple[str, str], ...],
    *,
    first_kickoff: datetime,
    status: MatchStatus,
    results: tuple[Score, ...] | None = None,
) -> list[Match]:
    return [
        Match(
            matchday_id=matchday_id,
            home_team=home,
            away_team=away,
            kickoff=first_kickoff + KICKOFF_OFFSETS[index],
            status=status,
            score=results[index] if results else None,
            external_id=f"demo-{matchday_id}-{index}",
        )
        for index, (home, away) in enumerate(fixtures)
    ]


def _predict_matchday(
    repository: Repository,
    rng: random.Random,
    player_id: int,
    matches: list[Match],
    skill: float,
) -> None:
    joker_index = rng.randrange(len(matches))
    for index, match in enumerate(matches):
        guess = _guess(rng, match.score, skill)
        repository.save_prediction(
            Prediction(
                player_id=player_id,
                match_id=match.id,  # type: ignore[arg-type]
                home_goals=guess.home,
                away_goals=guess.away,
                joker=index == joker_index,
            )
        )


def _guess(rng: random.Random, result: Score | None, skill: float) -> Score:
    """A plausible prediction: sometimes spot on, sometimes close, sometimes way off."""
    if result is None:
        return Score(rng.randint(0, 3), rng.randint(0, 2))

    roll = rng.random()
    if roll < skill:
        return result
    if roll < skill + 0.3:
        return _same_outcome(result)
    return Score(rng.randint(0, 3), rng.randint(0, 3))


def _same_outcome(result: Score) -> Score:
    """A different score that still gets the winner right."""
    if result.outcome is Outcome.HOME_WIN:
        return Score(result.home + 1, result.away)
    if result.outcome is Outcome.AWAY_WIN:
        return Score(result.home, result.away + 1)
    return Score(result.home + 1, result.away + 1)
