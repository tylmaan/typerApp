"""Controller tests. The clock is frozen, so deadlines can be examined from both sides."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from typerapp_core.controller import TyperController
from typerapp_core.events import (
    ActivePlayerChanged,
    Event,
    EventBus,
    PointsAwarded,
    PredictionSaved,
    RankingChanged,
    ResultsUpdated,
)
from typerapp_core.models import Match, Matchday, MatchStatus, Score, ScoringRules
from typerapp_core.repository import ACTIVE_PLAYER, Repository
from typerapp_core.scoring import PredictionClosedError, PredictionError

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 11, 7, 18, 0, tzinfo=UTC)
LEAGUE = ("Premier League", "2026/27")


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def recorded(bus: EventBus) -> list[Event]:
    events: list[Event] = []
    bus.subscribe(Event, events.append)
    return events


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def repo() -> Iterator[Repository]:
    with Repository() as repository:
        yield repository


@pytest.fixture
def controller(repo: Repository, bus: EventBus, clock: Clock) -> TyperController:
    controller = TyperController(repo, bus, clock)
    controller.set_league(*LEAGUE)
    return controller


@pytest.fixture
def matchday(repo: Repository) -> Matchday:
    day = repo.upsert_matchday(Matchday(league=LEAGUE[0], season=LEAGUE[1], number=12))
    repo.upsert_matches(
        [
            Match(
                matchday_id=day.id,  # type: ignore[arg-type]
                home_team=home,
                away_team=away,
                kickoff=KICKOFF + timedelta(hours=offset),
            )
            for offset, (home, away) in enumerate(
                [("Arsenal", "Chelsea"), ("Everton", "Liverpool")]
            )
        ]
    )
    return day


def only(events: list[Event], kind: type[Event]) -> list[Event]:
    return [event for event in events if isinstance(event, kind)]


def test_active_player_defaults_to_the_first_one(controller: TyperController) -> None:
    assert controller.active_player() is None

    controller.add_player("Kuba")
    ala = controller.add_player("Ala")

    assert controller.active_player().name == "Kuba"  # type: ignore[union-attr]
    assert ala.id is not None


def test_first_player_takes_the_seat(controller: TyperController, repo: Repository) -> None:
    kuba = controller.add_player("Kuba")
    assert repo.setting(ACTIVE_PLAYER) == str(kuba.id)


def test_set_active_player_announces_the_change(
    controller: TyperController, recorded: list[Event]
) -> None:
    ala = controller.add_player("Ala")

    controller.set_active_player(ala.id)

    assert only(recorded, ActivePlayerChanged)[-1].player == ala  # type: ignore[attr-defined]


def test_switch_player_walks_the_list_in_both_directions(controller: TyperController) -> None:
    for name in ("Ala", "Bartek", "Celina"):
        controller.add_player(name)

    assert controller.switch_player().name == "Bartek"  # type: ignore[union-attr]
    assert controller.switch_player().name == "Celina"  # type: ignore[union-attr]
    assert controller.switch_player().name == "Ala"  # wraps around
    assert controller.switch_player(-1).name == "Celina"  # type: ignore[union-attr]


def test_deleting_the_active_player_hands_over_the_seat(controller: TyperController) -> None:
    kuba = controller.add_player("Kuba")
    controller.add_player("Ala")

    controller.delete_player(kuba.id)  # type: ignore[arg-type]

    assert controller.active_player().name == "Ala"  # type: ignore[union-attr]


def test_no_league_means_no_matchdays(repo: Repository, bus: EventBus) -> None:
    bare = TyperController(repo, bus)
    assert bare.league() is None
    assert bare.matchdays() == []
    assert bare.current_matchday() is None


def test_current_matchday_is_the_next_open_one(
    controller: TyperController, repo: Repository, matchday: Matchday
) -> None:
    later = repo.upsert_matchday(Matchday(league=LEAGUE[0], season=LEAGUE[1], number=13))
    repo.upsert_matches(
        [
            Match(
                matchday_id=later.id,  # type: ignore[arg-type]
                home_team="Leeds",
                away_team="Burnley",
                kickoff=KICKOFF + timedelta(days=7),
            )
        ]
    )

    assert controller.current_matchday() == matchday


def test_current_matchday_falls_back_to_the_last_played(
    controller: TyperController, matchday: Matchday, clock: Clock
) -> None:
    clock.moment = KICKOFF + timedelta(days=30)
    assert controller.current_matchday() == matchday


def test_deadline_sits_before_the_first_kickoff(
    controller: TyperController, matchday: Matchday
) -> None:
    assert controller.deadline(matchday.id) == KICKOFF - timedelta(minutes=5)  # type: ignore[arg-type]
    assert controller.is_open(matchday.id)  # type: ignore[arg-type]
    assert controller.time_left(matchday.id) > timedelta(days=2)  # type: ignore[arg-type, operator]


def test_prediction_is_saved_and_announced(
    controller: TyperController, matchday: Matchday, recorded: list[Event]
) -> None:
    controller.add_player("Kuba")
    match = controller.matches(matchday.id)[0]  # type: ignore[arg-type]

    saved = controller.save_prediction(match.id, 2, 1)  # type: ignore[arg-type]

    assert saved.score == Score(2, 1)
    assert controller.predictions(matchday.id)[match.id].score == Score(2, 1)  # type: ignore[arg-type, index]
    assert only(recorded, PredictionSaved)[-1].prediction == saved  # type: ignore[attr-defined]


def test_prediction_can_be_corrected(controller: TyperController, matchday: Matchday) -> None:
    controller.add_player("Kuba")
    match = controller.matches(matchday.id)[0]  # type: ignore[arg-type]

    controller.save_prediction(match.id, 2, 1)  # type: ignore[arg-type]
    controller.save_prediction(match.id, 0, 0)  # type: ignore[arg-type]

    predictions = controller.predictions(matchday.id)  # type: ignore[arg-type]
    assert len(predictions) == 1
    assert predictions[match.id].score == Score(0, 0)  # type: ignore[index]


def test_prediction_refused_after_the_deadline(
    controller: TyperController, matchday: Matchday, clock: Clock
) -> None:
    controller.add_player("Kuba")
    match = controller.matches(matchday.id)[0]  # type: ignore[arg-type]
    clock.moment = KICKOFF - timedelta(minutes=1)

    with pytest.raises(PredictionClosedError):
        controller.save_prediction(match.id, 2, 1)  # type: ignore[arg-type]


def test_prediction_without_a_player_is_refused(
    controller: TyperController, matchday: Matchday
) -> None:
    match = controller.matches(matchday.id)[0]  # type: ignore[arg-type]
    with pytest.raises(PredictionError):
        controller.save_prediction(match.id, 1, 1)  # type: ignore[arg-type]


def test_correcting_a_prediction_keeps_the_joker(
    controller: TyperController, matchday: Matchday
) -> None:
    controller.add_player("Kuba")
    match = controller.matches(matchday.id)[0]  # type: ignore[arg-type]
    controller.save_prediction(match.id, 2, 1)  # type: ignore[arg-type]
    controller.toggle_joker(match.id)  # type: ignore[arg-type]

    controller.save_prediction(match.id, 3, 0)  # type: ignore[arg-type]

    assert controller.predictions(matchday.id)[match.id].joker  # type: ignore[arg-type, index]


def test_joker_moves_between_matches(controller: TyperController, matchday: Matchday) -> None:
    controller.add_player("Kuba")
    first, second = controller.matches(matchday.id)  # type: ignore[arg-type]
    controller.save_prediction(first.id, 1, 0)  # type: ignore[arg-type]
    controller.save_prediction(second.id, 2, 2)  # type: ignore[arg-type]

    controller.toggle_joker(first.id)  # type: ignore[arg-type]
    controller.toggle_joker(second.id)  # type: ignore[arg-type]

    predictions = controller.predictions(matchday.id)  # type: ignore[arg-type]
    assert not predictions[first.id].joker  # type: ignore[index]
    assert predictions[second.id].joker  # type: ignore[index]


def test_joker_can_be_taken_off(controller: TyperController, matchday: Matchday) -> None:
    controller.add_player("Kuba")
    match = controller.matches(matchday.id)[0]  # type: ignore[arg-type]
    controller.save_prediction(match.id, 1, 0)  # type: ignore[arg-type]

    controller.toggle_joker(match.id)  # type: ignore[arg-type]
    controller.toggle_joker(match.id)  # type: ignore[arg-type]

    assert not controller.predictions(matchday.id)[match.id].joker  # type: ignore[arg-type, index]


def test_joker_needs_a_prediction_first(controller: TyperController, matchday: Matchday) -> None:
    controller.add_player("Kuba")
    match = controller.matches(matchday.id)[0]  # type: ignore[arg-type]

    with pytest.raises(PredictionError):
        controller.toggle_joker(match.id)  # type: ignore[arg-type]


def test_entering_a_result_awards_points_and_refreshes_the_ranking(
    controller: TyperController, matchday: Matchday, recorded: list[Event], clock: Clock
) -> None:
    kuba = controller.add_player("Kuba")
    ala = controller.add_player("Ala")
    match = controller.matches(matchday.id)[0]  # type: ignore[arg-type]
    controller.save_prediction(match.id, 2, 1, player_id=kuba.id)  # type: ignore[arg-type]
    controller.save_prediction(match.id, 0, 0, player_id=ala.id)  # type: ignore[arg-type]
    clock.moment = KICKOFF + timedelta(hours=2)

    awarded = controller.enter_result(match.id, Score(2, 1))  # type: ignore[arg-type]

    assert awarded[kuba.id].total == 7  # type: ignore[index]
    assert awarded[ala.id].total == 0  # type: ignore[index]
    assert only(recorded, ResultsUpdated)[-1].match_ids == (match.id,)  # type: ignore[attr-defined]
    assert dict(only(recorded, PointsAwarded)[-1].points)[kuba.id] == 7  # type: ignore[attr-defined, index]

    table = only(recorded, RankingChanged)[-1].entries  # type: ignore[attr-defined]
    assert (table[0].player_name, table[0].points) == ("Kuba", 7)


def test_ranking_change_carries_the_previous_table(
    controller: TyperController, matchday: Matchday, recorded: list[Event], clock: Clock
) -> None:
    kuba = controller.add_player("Kuba")
    match = controller.matches(matchday.id)[0]  # type: ignore[arg-type]
    controller.save_prediction(match.id, 2, 1, player_id=kuba.id)  # type: ignore[arg-type]
    controller.refresh_ranking()
    clock.moment = KICKOFF + timedelta(hours=2)

    controller.enter_result(match.id, Score(2, 1))  # type: ignore[arg-type]

    event = only(recorded, RankingChanged)[-1]
    assert event.previous[0].points == 0  # type: ignore[attr-defined]
    assert event.entries[0].points == 7  # type: ignore[attr-defined]


def test_postponed_match_pays_nothing(
    controller: TyperController, matchday: Matchday, clock: Clock
) -> None:
    kuba = controller.add_player("Kuba")
    match = controller.matches(matchday.id)[0]  # type: ignore[arg-type]
    controller.save_prediction(match.id, 2, 1, player_id=kuba.id)  # type: ignore[arg-type]
    clock.moment = KICKOFF + timedelta(hours=2)

    awarded = controller.enter_result(match.id, None, MatchStatus.POSTPONED)  # type: ignore[arg-type]

    assert awarded == {}
    assert controller.ranking()[0].points == 0


def test_changing_the_rules_recomputes_the_ranking(
    controller: TyperController, matchday: Matchday, clock: Clock
) -> None:
    kuba = controller.add_player("Kuba")
    match = controller.matches(matchday.id)[0]  # type: ignore[arg-type]
    controller.save_prediction(match.id, 2, 1, player_id=kuba.id)  # type: ignore[arg-type]
    clock.moment = KICKOFF + timedelta(hours=2)
    controller.enter_result(match.id, Score(2, 1))  # type: ignore[arg-type]

    controller.save_rules(ScoringRules(exact_score=100, both_teams_scored=0, total_goals=0))

    assert controller.rules().exact_score == 100
    assert controller.ranking()[0].points == 100


def test_demo_data_can_be_loaded_from_the_menu(repo: Repository, bus: EventBus) -> None:
    controller = TyperController(repo, bus)
    assert controller.is_empty()

    controller.load_demo_data()

    assert not controller.is_empty()
    assert controller.league() is not None
    assert controller.current_matchday() is not None
    assert controller.active_player() is not None
    assert controller.ranking()[0].points > 0
