"""The single entry point views are allowed to use.

A view asks the controller for data and tells it what the player did. It never touches the
repository, the scoring rules or the providers. Everything the controller changes is
announced on the event bus, which is how the view learns that one row, and only one row,
needs redrawing.

The controller is synchronous and knows nothing about asyncio or Qt. The current time comes
from an injected clock, so tests can stand a minute before a deadline without waiting.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime, timedelta

from typerapp_core.events import (
    ActivePlayerChanged,
    EventBus,
    MatchesUpdated,
    PointsAwarded,
    PredictionSaved,
    RankingChanged,
    ResultsUpdated,
    SettingsChanged,
    SyncFailed,
    SyncFinished,
    SyncStarted,
)
from typerapp_core.models import (
    Match,
    Matchday,
    MatchStatus,
    Player,
    Prediction,
    RankingEntry,
    Score,
    ScoringRules,
    utc_now,
)
from typerapp_core.providers.base import FetchedMatch, ProviderError, ResultsProvider
from typerapp_core.providers.manual import ManualResultsProvider
from typerapp_core.ranking import PlayerStats, build_player_stats, build_ranking
from typerapp_core.repository import (
    ACTIVE_LEAGUE,
    ACTIVE_PLAYER,
    ACTIVE_SEASON,
    LAST_SYNC,
    Repository,
)
from typerapp_core.scoring import (
    PointsBreakdown,
    PredictionError,
    ensure_can_predict,
    ensure_joker_available,
    is_matchday_open,
    matchday_deadline,
    score_prediction,
)

#: How stale the cache has to be before the app syncs on its own. The free plan allows about
#: ten requests a minute, so this is deliberately generous.
MIN_SYNC_INTERVAL = timedelta(minutes=5)


def season_for(moment: datetime) -> str:
    """The football season a date belongs to, written the way people write it: ``2026/27``."""
    year = moment.year if moment.month >= 7 else moment.year - 1
    return f"{year}/{(year + 1) % 100:02d}"


class TyperController:
    """Game logic as the views see it."""

    def __init__(
        self,
        repository: Repository,
        events: EventBus,
        clock: Callable[[], datetime] = utc_now,
        provider: ResultsProvider | None = None,
    ) -> None:
        self.repository = repository
        self.events = events
        self.clock = clock
        self.provider: ResultsProvider = provider or ManualResultsProvider()
        self._rules: ScoringRules | None = None
        #: Last published ranking per view (``None`` key is the overall one), for animations.
        self._last_ranking: dict[int | None, tuple[RankingEntry, ...]] = {}

    def now(self) -> datetime:
        return self.clock()

    # ----------------------------------------------------------------- players

    def players(self) -> list[Player]:
        return self.repository.players()

    def player(self, player_id: int) -> Player | None:
        return self.repository.player(player_id)

    def active_player(self) -> Player | None:
        """The player holding the keyboard right now. Falls back to the first one."""
        stored = self.repository.setting(ACTIVE_PLAYER)
        if stored is not None:
            player = self.repository.player(int(stored))
            if player is not None:
                return player
        players = self.players()
        return players[0] if players else None

    def set_active_player(self, player_id: int | None) -> Player | None:
        self.repository.set_setting(ACTIVE_PLAYER, player_id)
        player = self.repository.player(player_id) if player_id is not None else None
        self.events.publish(ActivePlayerChanged(player=player))
        return player

    def switch_player(self, step: int = 1) -> Player | None:
        """Hot-seat: hand the keyboard to the next or previous player in the list."""
        players = self.players()
        if not players:
            return None
        current = self.active_player()
        index = players.index(current) if current in players else 0
        following = players[(index + step) % len(players)]
        return self.set_active_player(following.id)

    def add_player(self, name: str) -> Player:
        """Add a player. Names have to be unique, ignoring case, so hot-seat stays readable."""
        name = name.strip()
        if any(existing.name.casefold() == name.casefold() for existing in self.players()):
            raise ValueError(f"Player {name} already exists")
        player = self.repository.add_player(name)
        # The stored setting, not active_player(): its fallback would already point at
        # somebody, so the first player added would never actually take the seat.
        if self.repository.setting(ACTIVE_PLAYER) is None:
            self.set_active_player(player.id)
        return player

    def rename_player(self, player_id: int, name: str) -> None:
        self.repository.rename_player(player_id, name)
        self.refresh_ranking()

    def delete_player(self, player_id: int) -> None:
        """Removes the player and their predictions, and hands the seat to somebody else."""
        self.repository.delete_player(player_id)
        if self.repository.setting(ACTIVE_PLAYER) == str(player_id):
            remaining = self.players()
            self.set_active_player(remaining[0].id if remaining else None)
        self.refresh_ranking()

    # --------------------------------------------------------------- matchdays

    def league(self) -> tuple[str, str] | None:
        """The active league and season, or ``None`` when nothing is configured yet."""
        league = self.repository.setting(ACTIVE_LEAGUE)
        season = self.repository.setting(ACTIVE_SEASON)
        return (league, season) if league and season else None

    def set_league(self, league: str, season: str) -> None:
        self.repository.set_setting(ACTIVE_LEAGUE, league)
        self.repository.set_setting(ACTIVE_SEASON, season)
        self.events.publish(SettingsChanged(key=ACTIVE_LEAGUE, value=league))

    def matchdays(self) -> list[Matchday]:
        active = self.league()
        return self.repository.matchdays(*active) if active else []

    def known_leagues(self) -> list[tuple[str, str]]:
        """Leagues and seasons already in the database, so the settings can offer them."""
        return self.repository.leagues()

    def current_matchday(self) -> Matchday | None:
        """The matchday to open by default: the next one there is still something to do in.

        A matchday counts as that when its deadline has not passed *and* at least one match
        is still to be played. A round whose results are already in therefore does not block
        the way. With nothing open, the most recent matchday is shown, so the app always has
        something to display.
        """
        matchdays = self.matchdays()
        if not matchdays:
            return None

        now = self.now()
        rules = self.rules()
        todo: list[tuple[datetime, Matchday]] = []
        open_days: list[tuple[datetime, Matchday]] = []
        latest: list[tuple[datetime, Matchday]] = []
        for matchday in matchdays:
            matches = self.matches(matchday.id)  # type: ignore[arg-type]
            deadline = matchday_deadline(matches, rules)
            if deadline is None:
                continue
            latest.append((deadline, matchday))
            if now < deadline:
                open_days.append((deadline, matchday))
                if any(match.status is MatchStatus.SCHEDULED for match in matches):
                    todo.append((deadline, matchday))
        for candidates in (todo, open_days):
            if candidates:
                return min(candidates, key=lambda pair: pair[0])[1]
        if latest:
            return max(latest, key=lambda pair: pair[0])[1]
        return matchdays[-1]

    def matches(self, matchday_id: int) -> list[Match]:
        return self.repository.matches(matchday_id)

    def match(self, match_id: int) -> Match | None:
        return self.repository.match(match_id)

    def matchday(self, matchday_id: int) -> Matchday | None:
        return self.repository.matchday(matchday_id)

    def deadline(self, matchday_id: int) -> datetime | None:
        return matchday_deadline(self.matches(matchday_id), self.rules())

    def is_open(self, matchday_id: int) -> bool:
        """Whether there is still something to predict in the matchday.

        The deadline has to be ahead *and* at least one match still has to be played: a
        matchday whose results are all in is over, whatever the clock says. In real life the
        two coincide, since matches are played after the deadline, but the demo plays its
        matches out ahead of time.
        """
        matches = self.matches(matchday_id)
        if not is_matchday_open(matches, self.now(), self.rules()):
            return False
        return any(match.status is MatchStatus.SCHEDULED for match in matches)

    def is_finished(self, matchday_id: int) -> bool:
        """Every match has been played, or never will be: nothing is left to predict or enter.

        Not the same as "not open": a matchday whose deadline has passed but whose matches are
        still being played is closed for predictions, yet results are still to come in.
        """
        matches = self.matches(matchday_id)
        return bool(matches) and all(match.is_settled or match.is_void for match in matches)

    def time_left(self, matchday_id: int) -> timedelta | None:
        """How long until predictions close. Negative or ``None`` means closed."""
        deadline = self.deadline(matchday_id)
        return deadline - self.now() if deadline else None

    def store_matches(self, matches: Iterable[Match]) -> list[Match]:
        """Used by the sync: save fixtures and tell the views the list changed."""
        stored = self.repository.upsert_matches(matches)
        for matchday_id in {match.matchday_id for match in stored}:
            self.events.publish(MatchesUpdated(matchday_id=matchday_id))
        return stored

    # ------------------------------------------------------------- predictions

    def predictions(self, matchday_id: int, player_id: int | None = None) -> dict[int, Prediction]:
        """The player's predictions in a matchday, keyed by match id for easy lookup."""
        if player_id is None:
            player = self.active_player()
            if player is None:
                return {}
            player_id = player.id
        return {
            prediction.match_id: prediction
            for prediction in self.repository.predictions(
                matchday_id=matchday_id, player_id=player_id
            )
        }

    def save_prediction(
        self,
        match_id: int,
        home_goals: int,
        away_goals: int,
        *,
        player_id: int | None = None,
    ) -> Prediction:
        """Save or overwrite a prediction, keeping any joker already placed on the match."""
        match = self._match(match_id)
        player_id = player_id if player_id is not None else self._active_player_id()
        ensure_can_predict(match, self.matches(match.matchday_id), self.now(), self.rules())

        existing = self.repository.predictions(player_id=player_id)
        joker = any(p.match_id == match_id and p.joker for p in existing)
        saved = self.repository.save_prediction(
            Prediction(
                player_id=player_id,
                match_id=match_id,
                home_goals=home_goals,
                away_goals=away_goals,
                joker=joker,
                created_at=self.now(),
            )
        )
        self.events.publish(PredictionSaved(prediction=saved))
        return saved

    def toggle_joker(self, match_id: int, *, player_id: int | None = None) -> Prediction:
        """Move the joker onto this match, or take it off if it is already here.

        Only a predicted match can carry the joker, so the view asks for a score first.
        """
        match = self._match(match_id)
        player_id = player_id if player_id is not None else self._active_player_id()
        ensure_can_predict(match, self.matches(match.matchday_id), self.now(), self.rules())

        in_matchday = self.predictions(match.matchday_id, player_id)
        current = in_matchday.get(match_id)
        if current is None:
            raise PredictionError("The match has to be predicted before it can carry the joker")

        if not current.joker:
            ensure_joker_available(
                Prediction(
                    player_id=player_id,
                    match_id=match_id,
                    home_goals=current.home_goals,
                    away_goals=current.away_goals,
                    joker=True,
                ),
                [],  # the joker moves freely until the deadline, so nothing blocks it
                self.rules(),
            )
            self.repository.clear_joker(player_id, match.matchday_id)

        saved = self.repository.save_prediction(
            Prediction(
                player_id=player_id,
                match_id=match_id,
                home_goals=current.home_goals,
                away_goals=current.away_goals,
                joker=not current.joker,
                created_at=current.created_at,
            )
        )
        self.events.publish(PredictionSaved(prediction=saved))
        return saved

    # ----------------------------------------------------------------- results

    def enter_result(
        self,
        match_id: int,
        score: Score | None,
        status: MatchStatus = MatchStatus.FINISHED,
    ) -> dict[int, PointsBreakdown]:
        """Store a result, award the points and refresh the leaderboard.

        Returns each player's breakdown, which the view uses to animate the points flying in.
        """
        match = self._match(match_id)
        self.repository.set_result(match_id, score, status)
        self.events.publish(ResultsUpdated(match_ids=(match_id,)))

        awarded: dict[int, PointsBreakdown] = {}
        if score is not None and status is MatchStatus.FINISHED:
            awarded = self._award(replace(match, status=status, score=score))
        self.refresh_ranking()
        return awarded

    def _award(self, match: Match) -> dict[int, PointsBreakdown]:
        """Score every prediction for a match that just got its result."""
        if match.score is None or match.id is None:
            return {}
        rules = self.rules()
        awarded = {
            prediction.player_id: score_prediction(prediction, match.score, rules)
            for prediction in self.repository.predictions(matchday_id=match.matchday_id)
            if prediction.match_id == match.id
        }
        self.events.publish(
            PointsAwarded(
                match_id=match.id,
                points=tuple(
                    (player_id, breakdown.total) for player_id, breakdown in awarded.items()
                ),
            )
        )
        return awarded

    # -------------------------------------------------------------------- sync

    def can_sync(self) -> bool:
        """True when the source is online and actually covers the league that is active."""
        league = self.league()
        if league is None or not self.provider.online:
            return False
        return self.provider.supports(league[0])

    def last_sync(self) -> datetime | None:
        stored = self.repository.setting(LAST_SYNC)
        if not stored:
            return None
        try:
            return datetime.fromisoformat(stored)
        except ValueError:
            return None

    def should_sync(self) -> bool:
        """Whether the cache is stale enough to ask the source again on start-up."""
        if not self.can_sync():
            return False
        last = self.last_sync()
        return last is None or self.now() - last >= MIN_SYNC_INTERVAL

    def sync(self, *, force: bool = True) -> int:
        """Fetch the whole season of the active league, award points, refresh the ranking.

        One request brings every matchday, played and still to come, so none is missing from
        the first one on, and the results that arrived late are filled in by the same call.
        Matchdays are created from what the source returns, so nothing is left behind empty.

        Returns the number of matches that actually changed. Raises
        :class:`~typerapp_core.providers.base.ProviderError` on failure, after announcing it
        with :class:`~typerapp_core.events.SyncFailed`.
        """
        league = self.league()
        if league is None:
            raise ProviderError("No league selected")
        if not force and not self.should_sync():
            return 0

        source = self.provider.name
        self.events.publish(SyncStarted(source=source))
        changed = 0
        try:
            fetched = self.provider.fetch_season(league[0], league[1])
            by_matchday: dict[int, list[FetchedMatch]] = {}
            for entry in fetched:
                if entry.matchday is not None:
                    by_matchday.setdefault(entry.matchday, []).append(entry)
            for number in sorted(by_matchday):
                matchday = self.repository.upsert_matchday(
                    Matchday(league=league[0], season=league[1], number=number)
                )
                changed += self._store_fetched(matchday.id, by_matchday[number])  # type: ignore[arg-type]
        except ProviderError as error:
            self.events.publish(SyncFailed(source=source, message=str(error)))
            raise

        self.repository.set_setting(LAST_SYNC, self.now().isoformat())
        self.events.publish(SyncFinished(source=source, updated_matches=changed))
        return changed

    def _store_fetched(self, matchday_id: int, fetched: Iterable[FetchedMatch]) -> int:
        before = {match.id: match for match in self.repository.matches(matchday_id)}
        stored = self.repository.upsert_matches([entry.to_match(matchday_id) for entry in fetched])

        changed = 0
        settled: list[Match] = []
        for match in stored:
            previous = before.get(match.id)
            if previous is None or (
                previous.status,
                previous.score,
                previous.kickoff,
            ) != (match.status, match.score, match.kickoff):
                changed += 1
            if match.is_settled and (previous is None or not previous.is_settled):
                settled.append(match)

        if changed:
            self.events.publish(MatchesUpdated(matchday_id=matchday_id))
        for match in settled:
            self._award(match)
        if settled:
            self.events.publish(
                ResultsUpdated(match_ids=tuple(match.id for match in settled))  # type: ignore[misc]
            )
            self.refresh_ranking()
        return changed

    # ----------------------------------------------------------------- ranking

    def ranking(self, matchday_id: int | None = None) -> tuple[RankingEntry, ...]:
        """The leaderboard of one matchday, or of the active league.

        The overall table counts the active league only: with the demo league and a fetched
        one side by side in the database, summing everything would mix two games into one
        score.
        """
        return build_ranking(
            self.players(),
            self.repository.predictions(matchday_id=matchday_id),
            self._ranked_matches(matchday_id),
            self.rules(),
            matchday_id=matchday_id,
        )

    def _ranked_matches(self, matchday_id: int | None) -> list[Match]:
        if matchday_id is not None:
            return self.repository.matches(matchday_id)
        if self.league() is None:
            return self.repository.matches()  # nothing chosen yet, so count everything
        own = {matchday.id for matchday in self.matchdays()}
        return [match for match in self.repository.matches() if match.matchday_id in own]

    def refresh_ranking(self, matchday_id: int | None = None) -> tuple[RankingEntry, ...]:
        """Recompute the table and publish it together with the previous one."""
        entries = self.ranking(matchday_id)
        previous = self._last_ranking.get(matchday_id, ())
        self._last_ranking[matchday_id] = entries
        self.events.publish(
            RankingChanged(matchday_id=matchday_id, entries=entries, previous=previous)
        )
        return entries

    def points_for(self, prediction: Prediction, match: Match) -> PointsBreakdown | None:
        """What a prediction was worth, so the view can show it next to the match."""
        if not match.is_settled or match.score is None:
            return None
        return score_prediction(prediction, match.score, self.rules())

    def player_entry(self, player_id: int) -> RankingEntry | None:
        return next((entry for entry in self.ranking() if entry.player_id == player_id), None)

    def player_stats(self, player_id: int) -> PlayerStats | None:
        """Everything the profile screen needs, including a line per matchday."""
        entry = self.player_entry(player_id)
        if entry is None:
            return None
        return build_player_stats(
            entry,
            self.repository.predictions(player_id=player_id),
            self.repository.matches(),
            self.matchdays(),
            self.rules(),
        )

    # ---------------------------------------------------------------- settings

    def rules(self) -> ScoringRules:
        if self._rules is None:
            self._rules = self.repository.scoring_rules()
        return self._rules

    def save_rules(self, rules: ScoringRules) -> None:
        """Changing the rules recomputes every point in the game, by design."""
        self.repository.save_scoring_rules(rules)
        self._rules = rules
        self.events.publish(SettingsChanged(key="rules", value=rules))
        self.refresh_ranking()

    # -------------------------------------------------------------------- demo

    def is_empty(self) -> bool:
        return self.repository.is_empty()

    # The demo is a mode of the Session, not data in this database. The two methods further
    # down only clean up the demo league that earlier versions wrote into a real database.

    def remove_league(self, league: str, season: str) -> int:
        """Drop everything stored for one league: matchdays, matches and predictions."""
        matchdays = self.repository.matchdays(league, season)
        for matchday in matchdays:
            self.repository.delete_matchday(matchday.id)  # type: ignore[arg-type]

        self._reselect_league()
        self.refresh_ranking()
        return len(matchdays)

    def _reselect_league(self) -> None:
        """After data was deleted: keep a league that still has something in it."""
        remaining = self.known_leagues()
        if self.league() in remaining:
            return
        if remaining:
            self.set_league(*remaining[0])
            return
        self.repository.set_setting(ACTIVE_LEAGUE, None)
        self.repository.set_setting(ACTIVE_SEASON, None)
        self.events.publish(SettingsChanged(key=ACTIVE_LEAGUE, value=None))

    def has_demo_data(self) -> bool:
        from typerapp_core.demo import LEAGUE, PLAYER_NAMES

        if any(league == LEAGUE for league, _ in self.repository.leagues()):
            return True
        return any(player.name in PLAYER_NAMES for player in self.players())

    def remove_demo_data(self) -> tuple[int, int]:
        """Drop the sample league and its players, for when the real fixtures took over."""
        from typerapp_core.demo import remove_demo_data

        removed = remove_demo_data(self.repository)
        self._reselect_league()

        stored = self.repository.setting(ACTIVE_PLAYER)
        if stored is None or self.repository.player(int(stored)) is None:
            remaining = self.players()
            self.set_active_player(remaining[0].id if remaining else None)

        self.refresh_ranking()
        return removed

    # ------------------------------------------------------------------ helpers

    def _match(self, match_id: int) -> Match:
        match = self.repository.match(match_id)
        if match is None:
            raise LookupError(f"No match with id {match_id}")
        return match

    def _active_player_id(self) -> int:
        player = self.active_player()
        if player is None or player.id is None:
            raise PredictionError("No active player")
        return player.id
