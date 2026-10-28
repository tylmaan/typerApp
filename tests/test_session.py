"""Demo mode: a throw-away game in memory that never touches the real one."""

import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from typerapp_core.controller import TyperController
from typerapp_core.demo import GOAL_WEIGHTS, OPEN_MATCHDAY, PLAYED_MATCHDAY, simulated_score
from typerapp_core.events import DemoModeChanged, Event, EventBus, PointsAwarded
from typerapp_core.models import MatchStatus
from typerapp_core.repository import Repository
from typerapp_core.session import Session

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def repo() -> Iterator[Repository]:
    with Repository() as repository:
        yield repository


@pytest.fixture
def session(repo: Repository, bus: EventBus, clock: Clock) -> Iterator[Session]:
    live = Session(TyperController(repo, bus, clock), rng=random.Random(7))
    yield live
    live.close()


def test_a_session_starts_on_the_real_game(session: Session, repo: Repository) -> None:
    assert not session.demo
    assert session.controller is session.real
    assert session.controller.repository is repo


def test_entering_the_demo_switches_to_another_database(session: Session, repo: Repository) -> None:
    session.enter_demo()

    assert session.demo
    assert session.controller is not session.real
    assert session.controller.repository is not repo
    assert not session.controller.is_empty()


def test_the_real_data_is_untouched_by_the_demo(session: Session, repo: Repository) -> None:
    real = session.real
    kuba = real.add_player("Prawdziwy")

    session.enter_demo()
    session.controller.add_player("Tylko w demie")
    session.controller.set_league("Bundesliga", "2026/27")
    session.leave_demo()

    assert [player.name for player in real.players()] == ["Prawdziwy"]
    assert real.active_player() == kuba
    assert real.league() is None
    assert repo.leagues() == []  # the demo league never reached the real database


def test_an_empty_database_is_still_empty_after_the_demo(
    session: Session, repo: Repository
) -> None:
    assert repo.is_empty()

    session.enter_demo()
    session.leave_demo()

    assert repo.is_empty()
    assert session.real.is_empty()


def test_leaving_goes_back_to_the_real_game(session: Session) -> None:
    session.enter_demo()
    session.leave_demo()

    assert not session.demo
    assert session.controller is session.real


def test_leaving_twice_is_harmless(session: Session) -> None:
    session.leave_demo()
    session.enter_demo()
    session.leave_demo()
    session.leave_demo()

    assert not session.demo


def test_switching_is_announced_on_the_shared_bus(session: Session, bus: EventBus) -> None:
    heard: list[Event] = []
    bus.subscribe(DemoModeChanged, heard.append)

    session.enter_demo()
    session.enter_demo()  # already on: nothing new to announce
    session.leave_demo()

    assert heard == [DemoModeChanged(active=True), DemoModeChanged(active=False)]


def test_entering_twice_keeps_what_the_player_just_typed(session: Session) -> None:
    first = session.enter_demo()
    first.add_player("Nowy w demie")

    assert session.enter_demo() is first
    assert "Nowy w demie" in [player.name for player in first.players()]


def test_every_visit_builds_the_demo_from_scratch(session: Session) -> None:
    session.enter_demo().add_player("Zostawiony")
    session.leave_demo()

    names = [player.name for player in session.enter_demo().players()]

    assert "Zostawiony" not in names
    assert len(names) == 5


def test_the_demo_has_a_played_matchday_and_an_open_one(session: Session) -> None:
    demo = session.enter_demo()
    by_number = {day.number: day for day in demo.matchdays()}

    played = demo.matches(by_number[PLAYED_MATCHDAY].id)
    upcoming = demo.matches(by_number[OPEN_MATCHDAY].id)

    assert all(match.is_settled for match in played)
    assert all(match.status is MatchStatus.SCHEDULED for match in upcoming)
    assert demo.is_open(by_number[OPEN_MATCHDAY].id)
    assert demo.ranking()[0].points > 0  # the played matchday already feeds a leaderboard


def test_the_demo_never_goes_stale(repo: Repository, bus: EventBus, clock: Clock) -> None:
    # The whole point of building it on entry: a year later there is still a matchday open.
    clock.moment = NOW + timedelta(days=400)
    session = Session(TyperController(repo, bus, clock))

    demo = session.enter_demo()
    current = demo.current_matchday()

    assert current is not None and current.number == OPEN_MATCHDAY
    assert demo.is_open(current.id)
    assert demo.time_left(current.id) > timedelta(days=1)
    session.close()


def test_the_active_player_still_has_something_to_predict(session: Session) -> None:
    demo = session.enter_demo()
    open_day = demo.current_matchday()

    assert demo.predictions(open_day.id) == {}  # the active player's card is empty
    others = [
        player
        for player in demo.players()
        if player.id != demo.active_player().id and demo.predictions(open_day.id, player.id)
    ]
    assert len(others) == 3  # and three of the others have already typed


def test_the_demo_cannot_fetch_anything(session: Session) -> None:
    demo = session.enter_demo()
    assert not demo.can_sync()  # no league of the demo exists in any API


def test_simulating_enters_a_result_and_awards_points(session: Session, bus: EventBus) -> None:
    demo = session.enter_demo()
    open_day = demo.current_matchday()
    match = demo.matches(open_day.id)[0]
    awarded: list[PointsAwarded] = []
    bus.subscribe(PointsAwarded, awarded.append)

    score = session.simulate_result(match.id)

    stored = demo.match(match.id)
    assert stored.is_settled and stored.score == score
    assert awarded and awarded[0].match_id == match.id


def test_simulating_a_whole_matchday_reshuffles_the_leaderboard(session: Session) -> None:
    demo = session.enter_demo()
    open_day = demo.current_matchday()
    before = [(entry.player_id, entry.points) for entry in demo.ranking()]

    for match in demo.matches(open_day.id):
        session.simulate_result(match.id)

    after = [(entry.player_id, entry.points) for entry in demo.ranking()]
    assert after != before
    assert all(match.is_settled for match in demo.matches(open_day.id))


def test_results_can_only_be_simulated_in_the_demo(session: Session) -> None:
    with pytest.raises(RuntimeError):
        session.simulate_result(1)


def test_simulation_is_reproducible_with_a_seeded_generator(
    repo: Repository, bus: EventBus, clock: Clock
) -> None:
    def results(seed: int) -> list[str]:
        live = Session(TyperController(repo, bus, clock), rng=random.Random(seed))
        demo = live.enter_demo()
        day = demo.current_matchday()
        scores = [str(live.simulate_result(match.id)) for match in demo.matches(day.id)]
        live.close()
        return scores

    assert results(3) == results(3)


def test_simulated_scores_look_like_football() -> None:
    rng = random.Random(1)
    scores = [simulated_score(rng) for _ in range(500)]
    goals = [goal for score in scores for goal in (score.home, score.away)]

    assert set(goals) <= set(range(len(GOAL_WEIGHTS)))
    assert goals.count(1) > goals.count(4)  # a single goal is far likelier than four
    assert 0.8 < sum(goals) / len(goals) < 1.8  # about what a league season averages
