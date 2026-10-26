"""Storage tests. Everything runs on an in-memory database, so nothing touches the disk."""

import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from typerapp_core.models import Match, Matchday, MatchStatus, Prediction, Score, ScoringRules
from typerapp_core.repository import (
    ACTIVE_PLAYER,
    SCHEMA_VERSION,
    Repository,
    default_database_path,
)

from helpers import FIRST_KICKOFF


@pytest.fixture
def repo() -> Iterator[Repository]:
    with Repository() as repository:
        yield repository


@pytest.fixture
def matchday(repo: Repository) -> Matchday:
    return repo.upsert_matchday(Matchday(league="Premier League", season="2026/27", number=12))


def add_match(repo: Repository, matchday: Matchday, **kwargs: object) -> Match:
    defaults: dict[str, object] = {
        "matchday_id": matchday.id,
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "kickoff": FIRST_KICKOFF,
    }
    return repo.upsert_matches([Match(**(defaults | kwargs))])[0]  # type: ignore[arg-type]


def test_fresh_database_has_the_current_schema_version(repo: Repository) -> None:
    assert repo.schema_version == SCHEMA_VERSION
    assert repo.is_empty()


def test_default_path_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPERAPP_DB", "/tmp/typerapp-test.db")
    assert default_database_path().name == "typerapp-test.db"


def test_default_path_lives_outside_the_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPERAPP_DB", raising=False)
    path = default_database_path()
    assert path.name == "typerapp.db"
    assert "typerApp" in path.parts


def test_player_round_trip(repo: Repository) -> None:
    player = repo.add_player("  Kuba  ")

    assert player.id is not None
    assert player.name == "Kuba"
    assert repo.player(player.id) == player
    assert repo.players() == [player]
    assert not repo.is_empty()


def test_players_are_sorted_by_name(repo: Repository) -> None:
    for name in ("Celina", "Ala", "Bartek"):
        repo.add_player(name)

    assert [player.name for player in repo.players()] == ["Ala", "Bartek", "Celina"]


def test_duplicate_player_name_rejected(repo: Repository) -> None:
    repo.add_player("Kuba")
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_player("Kuba")


def test_rename_player(repo: Repository) -> None:
    player = repo.add_player("Kuba")
    repo.rename_player(player.id, "Jakub")  # type: ignore[arg-type]
    assert repo.player(player.id).name == "Jakub"  # type: ignore[arg-type, union-attr]


def test_deleting_a_player_removes_their_predictions(repo: Repository, matchday: Matchday) -> None:
    # Foreign keys are off by default in SQLite; this fails loudly if the pragma is lost.
    player = repo.add_player("Kuba")
    match = add_match(repo, matchday)
    repo.save_prediction(
        Prediction(player_id=player.id, match_id=match.id, home_goals=1, away_goals=0)  # type: ignore[arg-type]
    )

    repo.delete_player(player.id)  # type: ignore[arg-type]

    assert repo.predictions() == []


def test_matchday_is_not_duplicated(repo: Repository) -> None:
    first = repo.upsert_matchday(Matchday(league="PL", season="2026/27", number=12))
    again = repo.upsert_matchday(Matchday(league="PL", season="2026/27", number=12))

    assert first.id == again.id
    assert len(repo.matchdays("PL", "2026/27")) == 1


def test_match_round_trip(repo: Repository, matchday: Matchday) -> None:
    match = add_match(repo, matchday, status=MatchStatus.FINISHED, score=Score(2, 1))

    stored = repo.match(match.id)  # type: ignore[arg-type]

    assert stored == match
    assert stored.score == Score(2, 1)  # type: ignore[union-attr]
    assert stored.kickoff == FIRST_KICKOFF  # type: ignore[union-attr]


def test_match_without_result_has_no_score(repo: Repository, matchday: Matchday) -> None:
    match = add_match(repo, matchday)
    assert repo.match(match.id).score is None  # type: ignore[arg-type, union-attr]


def test_kickoff_keeps_its_timezone(repo: Repository, matchday: Matchday) -> None:
    warsaw_evening = datetime(2026, 11, 7, 20, 30, tzinfo=UTC) + timedelta(hours=1)
    match = add_match(repo, matchday, kickoff=warsaw_evening)

    stored = repo.match(match.id)  # type: ignore[arg-type]

    assert stored.kickoff == warsaw_evening  # type: ignore[union-attr]
    assert stored.kickoff.tzinfo is not None  # type: ignore[union-attr]


def test_naive_kickoff_cannot_be_stored(repo: Repository, matchday: Matchday) -> None:
    with pytest.raises(ValueError):
        add_match(repo, matchday, kickoff=datetime(2026, 11, 7, 18, 0))


def test_matches_are_updated_not_duplicated(repo: Repository, matchday: Matchday) -> None:
    first = add_match(repo, matchday, external_id="pl-1")
    moved = Match(
        matchday_id=matchday.id,  # type: ignore[arg-type]
        home_team="Arsenal",
        away_team="Chelsea",
        kickoff=FIRST_KICKOFF + timedelta(days=1),
        status=MatchStatus.FINISHED,
        score=Score(2, 1),
        external_id="pl-1",
    )

    updated = repo.upsert_matches([moved])[0]

    assert updated.id == first.id
    assert len(repo.matches(matchday.id)) == 1  # type: ignore[arg-type]
    assert repo.match(first.id).score == Score(2, 1)  # type: ignore[arg-type, union-attr]


def test_matches_without_external_id_are_matched_by_teams(
    repo: Repository, matchday: Matchday
) -> None:
    first = add_match(repo, matchday)
    again = add_match(repo, matchday, status=MatchStatus.LIVE)

    assert first.id == again.id
    assert len(repo.matches(matchday.id)) == 1  # type: ignore[arg-type]


def test_matches_are_ordered_by_kickoff(repo: Repository, matchday: Matchday) -> None:
    add_match(repo, matchday, home_team="Late", kickoff=FIRST_KICKOFF + timedelta(hours=3))
    add_match(repo, matchday, home_team="Early", kickoff=FIRST_KICKOFF)

    assert [match.home_team for match in repo.matches(matchday.id)] == ["Early", "Late"]  # type: ignore[arg-type]


def test_set_result(repo: Repository, matchday: Matchday) -> None:
    match = add_match(repo, matchday)

    repo.set_result(match.id, Score(0, 3))  # type: ignore[arg-type]

    stored = repo.match(match.id)  # type: ignore[arg-type]
    assert stored.is_settled  # type: ignore[union-attr]
    assert stored.score == Score(0, 3)  # type: ignore[union-attr]


def test_result_can_be_withdrawn(repo: Repository, matchday: Matchday) -> None:
    match = add_match(repo, matchday, status=MatchStatus.FINISHED, score=Score(1, 1))

    repo.set_result(match.id, None, MatchStatus.POSTPONED)  # type: ignore[arg-type]

    stored = repo.match(match.id)  # type: ignore[arg-type]
    assert stored.is_void and stored.score is None  # type: ignore[union-attr]


def test_prediction_is_overwritten_not_duplicated(repo: Repository, matchday: Matchday) -> None:
    player = repo.add_player("Kuba")
    match = add_match(repo, matchday)

    first = repo.save_prediction(
        Prediction(player_id=player.id, match_id=match.id, home_goals=1, away_goals=0)  # type: ignore[arg-type]
    )
    second = repo.save_prediction(
        Prediction(player_id=player.id, match_id=match.id, home_goals=2, away_goals=2, joker=True)  # type: ignore[arg-type]
    )

    stored = repo.predictions()
    assert first.id == second.id
    assert len(stored) == 1
    assert (stored[0].score, stored[0].joker) == (Score(2, 2), True)


def test_predictions_can_be_filtered(repo: Repository, matchday: Matchday) -> None:
    other_matchday = repo.upsert_matchday(Matchday(league="PL", season="2026/27", number=13))
    kuba, ala = repo.add_player("Kuba"), repo.add_player("Ala")
    here = add_match(repo, matchday)
    elsewhere = add_match(repo, other_matchday, home_team="Leeds", away_team="Burnley")

    for player in (kuba, ala):
        for match in (here, elsewhere):
            repo.save_prediction(
                Prediction(player_id=player.id, match_id=match.id, home_goals=1, away_goals=1)  # type: ignore[arg-type]
            )

    assert len(repo.predictions()) == 4
    assert len(repo.predictions(matchday_id=matchday.id)) == 2  # type: ignore[arg-type]
    assert len(repo.predictions(player_id=kuba.id)) == 2  # type: ignore[arg-type]
    assert len(repo.predictions(matchday_id=matchday.id, player_id=kuba.id)) == 1  # type: ignore[arg-type]


def test_clear_joker_only_touches_one_player_and_matchday(
    repo: Repository, matchday: Matchday
) -> None:
    other_matchday = repo.upsert_matchday(Matchday(league="PL", season="2026/27", number=13))
    kuba, ala = repo.add_player("Kuba"), repo.add_player("Ala")
    here = add_match(repo, matchday)
    elsewhere = add_match(repo, other_matchday, home_team="Leeds", away_team="Burnley")

    for player in (kuba, ala):
        for match in (here, elsewhere):
            repo.save_prediction(
                Prediction(
                    player_id=player.id,  # type: ignore[arg-type]
                    match_id=match.id,  # type: ignore[arg-type]
                    home_goals=1,
                    away_goals=1,
                    joker=True,
                )
            )

    repo.clear_joker(kuba.id, matchday.id)  # type: ignore[arg-type]

    jokers = {
        (prediction.player_id, prediction.match_id)
        for prediction in repo.predictions()
        if prediction.joker
    }
    assert jokers == {
        (kuba.id, elsewhere.id),
        (ala.id, here.id),
        (ala.id, elsewhere.id),
    }


def test_settings_round_trip(repo: Repository) -> None:
    assert repo.setting(ACTIVE_PLAYER) is None
    assert repo.setting(ACTIVE_PLAYER, "7") == "7"

    repo.set_setting(ACTIVE_PLAYER, 3)
    assert repo.setting(ACTIVE_PLAYER) == "3"

    repo.set_setting(ACTIVE_PLAYER, 5)
    assert repo.setting(ACTIVE_PLAYER) == "5"

    repo.set_setting(ACTIVE_PLAYER, None)
    assert repo.setting(ACTIVE_PLAYER) is None


def test_unconfigured_rules_fall_back_to_defaults(repo: Repository) -> None:
    assert repo.scoring_rules() == ScoringRules()


def test_rules_round_trip(repo: Repository) -> None:
    rules = ScoringRules(exact_score=10, total_goals=0, prediction_deadline_minutes=60)

    repo.save_scoring_rules(rules)

    assert repo.scoring_rules() == rules


def test_rules_added_later_keep_their_default(repo: Repository) -> None:
    # Only part of the rules was ever written, which is what an older database looks like.
    repo.set_setting("rules.exact_score", 7)

    assert repo.scoring_rules() == ScoringRules(exact_score=7)


def test_transaction_rolls_back_on_error(repo: Repository) -> None:
    repo.add_player("Kuba")

    with pytest.raises(RuntimeError), repo.transaction() as connection:
        connection.execute("INSERT INTO players (name, created_at) VALUES ('Ala', '2026-01-01')")
        raise RuntimeError("boom")

    assert [player.name for player in repo.players()] == ["Kuba"]


def test_writes_from_another_thread(repo: Repository) -> None:
    # The API sync runs in a worker thread and writes through the same connection.
    def worker() -> None:
        repo.add_player("Worker")

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert [player.name for player in repo.players()] == ["Worker"]
