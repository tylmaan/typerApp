"""Synchronisation: what the controller does with whatever a provider returns."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from typerapp_core.controller import MIN_SYNC_INTERVAL, TyperController, season_for
from typerapp_core.demo import LEAGUE as DEMO_LEAGUE
from typerapp_core.demo import seed_demo_data
from typerapp_core.events import (
    Event,
    EventBus,
    MatchesUpdated,
    PointsAwarded,
    RankingChanged,
    ResultsUpdated,
    SyncFailed,
    SyncFinished,
    SyncStarted,
)
from typerapp_core.models import MatchStatus, Score
from typerapp_core.providers.base import ProviderError, ProviderUnavailable
from typerapp_core.providers.manual import ManualResultsProvider
from typerapp_core.repository import LAST_SYNC, Repository

from helpers import FIRST_KICKOFF, FakeProvider, fetched

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
KICKOFF = FIRST_KICKOFF
LEAGUE = ("Premier League", "2026/27")


class Clock:
    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def recorded(bus: EventBus) -> list[Event]:
    events: list[Event] = []
    bus.subscribe(Event, events.append)
    return events


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def repo() -> Iterator[Repository]:
    with Repository() as repository:
        yield repository


def make_controller(repo: Repository, bus: EventBus, clock: Clock, provider) -> TyperController:
    controller = TyperController(repo, bus, clock, provider)
    controller.set_league(*LEAGUE)
    return controller


def kinds(events: list[Event], kind: type[Event]) -> list[Event]:
    return [event for event in events if isinstance(event, kind)]


def test_season_for_follows_the_football_calendar() -> None:
    assert season_for(datetime(2026, 11, 5, tzinfo=UTC)) == "2026/27"
    assert season_for(datetime(2027, 3, 1, tzinfo=UTC)) == "2026/27"
    assert season_for(datetime(2027, 7, 1, tzinfo=UTC)) == "2027/28"


def test_manual_source_cannot_sync(repo: Repository, bus: EventBus, clock: Clock) -> None:
    controller = make_controller(repo, bus, clock, ManualResultsProvider())

    assert not controller.can_sync()
    assert not controller.should_sync()


def test_sync_needs_a_league(repo: Repository, bus: EventBus, clock: Clock) -> None:
    controller = TyperController(repo, bus, clock, FakeProvider())

    with pytest.raises(ProviderError):
        controller.sync()


def test_sync_stores_the_fixtures(
    repo: Repository, bus: EventBus, clock: Clock, recorded: list[Event]
) -> None:
    provider = FakeProvider({12: [fetched(), fetched("Liverpool", "Everton", external_id="2")]})
    controller = make_controller(repo, bus, clock, provider)

    changed = controller.sync()

    assert changed == 2
    matchday = controller.current_matchday()
    assert matchday is not None and matchday.number == 12
    assert len(controller.matches(matchday.id)) == 2
    assert kinds(recorded, SyncStarted)[0].source == "fake"  # type: ignore[attr-defined]
    assert kinds(recorded, SyncFinished)[0].updated_matches == 2  # type: ignore[attr-defined]
    assert kinds(recorded, MatchesUpdated)


def test_sync_asks_for_the_previous_matchday_too(
    repo: Repository, bus: EventBus, clock: Clock
) -> None:
    provider = FakeProvider({12: [fetched()]}, current=12)
    controller = make_controller(repo, bus, clock, provider)

    controller.sync()

    assert provider.asked == [11, 12]


def test_the_source_decides_which_matchday_to_fetch(
    repo: Repository, bus: EventBus, clock: Clock
) -> None:
    # Demo data says matchday 13, the real league is at 8. Asking about 13 would merge real
    # fixtures into the demo matchdays, which is exactly what happened once.
    seed_demo_data(repo)
    provider = FakeProvider({8: [fetched()]}, current=8)
    controller = TyperController(repo, bus, clock, provider)
    controller.set_league(*LEAGUE)

    controller.sync()

    assert provider.asked == [7, 8]
    numbers = [day.number for day in controller.matchdays()]
    assert numbers == [7, 8]  # the demo lives under its own league name


def test_demo_data_cannot_collide_with_fetched_fixtures(
    repo: Repository, bus: EventBus, clock: Clock
) -> None:
    seed_demo_data(repo)
    controller = TyperController(repo, bus, clock, FakeProvider({8: [fetched()]}, current=8))
    controller.set_league(*LEAGUE)

    controller.sync()

    demo_days = repo.matchdays(DEMO_LEAGUE, "2026/27")
    assert [day.number for day in demo_days] == [12, 13]
    for day in demo_days:
        assert len(repo.matches(day.id)) == 5  # untouched by the sync


def test_first_matchday_has_no_predecessor(repo: Repository, bus: EventBus, clock: Clock) -> None:
    provider = FakeProvider({1: [fetched()]}, current=1)
    controller = make_controller(repo, bus, clock, provider)

    controller.sync()

    assert provider.asked == [1]


def test_nothing_to_sync_without_a_current_matchday(
    repo: Repository, bus: EventBus, clock: Clock
) -> None:
    provider = FakeProvider(current=None)
    controller = make_controller(repo, bus, clock, provider)

    assert controller.sync() == 0
    assert provider.asked == []


def test_syncing_twice_changes_nothing_the_second_time(
    repo: Repository, bus: EventBus, clock: Clock
) -> None:
    provider = FakeProvider({12: [fetched()]})
    controller = make_controller(repo, bus, clock, provider)

    assert controller.sync() == 1
    assert controller.sync() == 0


def test_a_result_awards_points_and_refreshes_the_ranking(
    repo: Repository, bus: EventBus, clock: Clock, recorded: list[Event]
) -> None:
    provider = FakeProvider({12: [fetched()]})
    controller = make_controller(repo, bus, clock, provider)
    controller.sync()
    kuba = controller.add_player("Kuba")
    match = controller.matches(controller.current_matchday().id)[0]  # type: ignore[union-attr]
    controller.save_prediction(match.id, 2, 1)  # type: ignore[arg-type]
    recorded.clear()

    provider.matches = {12: [fetched(status=MatchStatus.FINISHED, score=Score(2, 1))]}
    clock.moment = KICKOFF + timedelta(hours=2)
    controller.sync()

    awarded = dict(kinds(recorded, PointsAwarded)[0].points)  # type: ignore[attr-defined]
    assert awarded[kuba.id] == 7
    assert kinds(recorded, ResultsUpdated)[0].match_ids == (match.id,)  # type: ignore[attr-defined]
    # 7 for the match plus 5 for sweeping a matchday that holds a single game.
    assert kinds(recorded, RankingChanged)[-1].entries[0].points == 12  # type: ignore[attr-defined]


def test_points_are_not_awarded_twice(
    repo: Repository, bus: EventBus, clock: Clock, recorded: list[Event]
) -> None:
    provider = FakeProvider({12: [fetched(status=MatchStatus.FINISHED, score=Score(2, 1))]})
    controller = make_controller(repo, bus, clock, provider)
    controller.sync()
    recorded.clear()

    controller.sync()

    assert not kinds(recorded, PointsAwarded)


def test_api_overwrites_a_result_entered_by_hand(
    repo: Repository, bus: EventBus, clock: Clock
) -> None:
    provider = FakeProvider({12: [fetched()]})
    controller = make_controller(repo, bus, clock, provider)
    controller.sync()
    match = controller.matches(controller.current_matchday().id)[0]  # type: ignore[union-attr]
    clock.moment = KICKOFF + timedelta(hours=2)
    controller.enter_result(match.id, Score(0, 0))  # type: ignore[arg-type]

    provider.matches = {12: [fetched(status=MatchStatus.FINISHED, score=Score(3, 1))]}
    controller.sync()

    assert controller.matches(match.matchday_id)[0].score == Score(3, 1)


def test_a_moved_kickoff_updates_the_match(repo: Repository, bus: EventBus, clock: Clock) -> None:
    provider = FakeProvider({12: [fetched()]})
    controller = make_controller(repo, bus, clock, provider)
    controller.sync()

    provider.matches = {12: [fetched(kickoff=KICKOFF + timedelta(days=1))]}
    assert controller.sync() == 1
    matches = controller.matches(controller.current_matchday().id)  # type: ignore[union-attr]
    assert len(matches) == 1
    assert matches[0].kickoff == KICKOFF + timedelta(days=1)


def test_failure_is_announced_and_raised(
    repo: Repository, bus: EventBus, clock: Clock, recorded: list[Event]
) -> None:
    provider = FakeProvider(error=ProviderUnavailable("brak sieci"))
    controller = make_controller(repo, bus, clock, provider)

    with pytest.raises(ProviderUnavailable):
        controller.sync()

    assert kinds(recorded, SyncStarted)
    assert kinds(recorded, SyncFailed)[0].message == "brak sieci"  # type: ignore[attr-defined]
    assert not kinds(recorded, SyncFinished)


def test_failure_does_not_mark_the_cache_as_fresh(
    repo: Repository, bus: EventBus, clock: Clock
) -> None:
    controller = make_controller(repo, bus, clock, FakeProvider(error=ProviderUnavailable("x")))

    with pytest.raises(ProviderUnavailable):
        controller.sync()

    assert controller.last_sync() is None
    assert controller.should_sync()


def test_staleness_decides_the_sync_on_start(repo: Repository, bus: EventBus, clock: Clock) -> None:
    provider = FakeProvider({12: [fetched()]})
    controller = make_controller(repo, bus, clock, provider)

    assert controller.should_sync()  # never synced
    controller.sync()
    assert controller.last_sync() == NOW
    assert not controller.should_sync()

    clock.moment = NOW + MIN_SYNC_INTERVAL
    assert controller.should_sync()

    assert controller.sync(force=False) == 0  # nothing new, but it did ask
    assert provider.asked == [11, 12, 11, 12]


def test_a_broken_timestamp_is_ignored(repo: Repository, bus: EventBus, clock: Clock) -> None:
    controller = make_controller(repo, bus, clock, FakeProvider())
    repo.set_setting(LAST_SYNC, "nie-data")

    assert controller.last_sync() is None
    assert controller.should_sync()
