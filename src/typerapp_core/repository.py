"""SQLite storage: the only module in the project that writes SQL.

The database doubles as the offline cache: matches fetched from the API are stored here, so
the app keeps working without a connection.

Everything is plain ``sqlite3``. One connection is shared between threads and guarded by a
lock, because the view fetches results in a worker thread while the UI thread keeps reading.
Timestamps are stored as ISO-8601 text in UTC, which sorts correctly and stays readable in
any database browser.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

from typerapp_core.models import (
    Match,
    Matchday,
    MatchStatus,
    Player,
    Prediction,
    Score,
    ScoringRules,
)

#: Bumped whenever the schema changes, so an old database can be recognised.
SCHEMA_VERSION = 1

#: Environment variable that overrides the database location, handy for demos and debugging.
DATABASE_ENV_VAR = "TYPERAPP_DB"

APP_DIRECTORY_NAME = "typerApp"
DATABASE_FILE_NAME = "typerapp.db"

#: Settings keys that are not scoring rules.
ACTIVE_PLAYER = "active_player_id"
ACTIVE_LEAGUE = "active_league"
ACTIVE_SEASON = "active_season"
LAST_SYNC = "last_sync_at"

#: Scoring rules live in the settings table under this prefix, one row per field.
RULES_PREFIX = "rules."

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matchdays (
    id     INTEGER PRIMARY KEY,
    league TEXT NOT NULL,
    season TEXT NOT NULL,
    number INTEGER NOT NULL,
    UNIQUE (league, season, number)
);

CREATE TABLE IF NOT EXISTS matches (
    id          INTEGER PRIMARY KEY,
    matchday_id INTEGER NOT NULL REFERENCES matchdays(id) ON DELETE CASCADE,
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    kickoff     TEXT NOT NULL,
    status      TEXT NOT NULL,
    home_goals  INTEGER,
    away_goals  INTEGER,
    external_id TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS predictions (
    id         INTEGER PRIMARY KEY,
    player_id  INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    match_id   INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    home_goals INTEGER NOT NULL,
    away_goals INTEGER NOT NULL,
    joker      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (player_id, match_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS matches_by_matchday ON matches(matchday_id);
CREATE INDEX IF NOT EXISTS predictions_by_match ON predictions(match_id);
"""


def default_database_path() -> Path:
    """Where the database lives when nothing else is configured.

    The platform's own data directory, so the file survives moving the project around and
    can never be committed by accident. ``TYPERAPP_DB`` overrides it.
    """
    override = os.environ.get(DATABASE_ENV_VAR)
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_DIRECTORY_NAME / DATABASE_FILE_NAME


def _to_db(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("Timestamps must be timezone aware")
    return moment.astimezone(UTC).isoformat()


def _from_db(text: str) -> datetime:
    return datetime.fromisoformat(text)


class Repository:
    """Stores and reads the whole game. Views never touch it directly, only the controller."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self.path = path
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False plus the lock below: the worker thread that talks to the
        # API writes through the same connection as the UI thread.
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._prepare()

    @classmethod
    def open_default(cls) -> Repository:
        return cls(default_database_path())

    def _prepare(self) -> None:
        with self._lock:
            # SQLite ignores foreign keys unless they are switched on for the connection.
            self._connection.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.executescript(SCHEMA)
            self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._connection.commit()

    @property
    def schema_version(self) -> int:
        return int(self._query_one("PRAGMA user_version")[0])

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> Repository:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Group several writes so they either all land or none of them do."""
        with self._lock:
            try:
                yield self._connection
            except Exception:
                self._connection.rollback()
                raise
            self._connection.commit()

    def _execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._connection.execute(sql, parameters)
            self._connection.commit()
            return cursor

    def _query(self, sql: str, parameters: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(sql, parameters).fetchall()

    def _query_one(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(sql, parameters).fetchone()

    # ----------------------------------------------------------------- players

    def add_player(self, name: str) -> Player:
        player = Player(name=name.strip())
        cursor = self._execute(
            "INSERT INTO players (name, created_at) VALUES (?, ?)",
            (player.name, _to_db(player.created_at)),
        )
        return Player(name=player.name, created_at=player.created_at, id=cursor.lastrowid)

    def rename_player(self, player_id: int, name: str) -> None:
        self._execute("UPDATE players SET name = ? WHERE id = ?", (name.strip(), player_id))

    def delete_player(self, player_id: int) -> None:
        """Removes the player together with every prediction they made."""
        self._execute("DELETE FROM players WHERE id = ?", (player_id,))

    def players(self) -> list[Player]:
        return [_player(row) for row in self._query("SELECT * FROM players ORDER BY name")]

    def player(self, player_id: int) -> Player | None:
        row = self._query_one("SELECT * FROM players WHERE id = ?", (player_id,))
        return _player(row) if row else None

    # --------------------------------------------------------------- matchdays

    def upsert_matchday(self, matchday: Matchday) -> Matchday:
        row = self._query_one(
            """
            INSERT INTO matchdays (league, season, number) VALUES (?, ?, ?)
            ON CONFLICT (league, season, number) DO UPDATE SET number = excluded.number
            RETURNING id
            """,
            (matchday.league, matchday.season, matchday.number),
        )
        with self._lock:
            self._connection.commit()
        return Matchday(
            league=matchday.league,
            season=matchday.season,
            number=matchday.number,
            id=int(row[0]),  # type: ignore[index]
        )

    def leagues(self) -> list[tuple[str, str]]:
        """Every league and season that has at least one matchday stored."""
        rows = self._query("SELECT DISTINCT league, season FROM matchdays ORDER BY league, season")
        return [(row["league"], row["season"]) for row in rows]

    def delete_matchday(self, matchday_id: int) -> None:
        """Removes the matchday together with its matches and all their predictions."""
        self._execute("DELETE FROM matchdays WHERE id = ?", (matchday_id,))

    def matchdays(self, league: str, season: str) -> list[Matchday]:
        rows = self._query(
            "SELECT * FROM matchdays WHERE league = ? AND season = ? ORDER BY number",
            (league, season),
        )
        return [_matchday(row) for row in rows]

    def matchday(self, matchday_id: int) -> Matchday | None:
        row = self._query_one("SELECT * FROM matchdays WHERE id = ?", (matchday_id,))
        return _matchday(row) if row else None

    # ----------------------------------------------------------------- matches

    def upsert_matches(self, matches: Iterable[Match]) -> list[Match]:
        """Store matches coming from a results provider, keeping ids stable.

        A match is recognised by its external id when it has one, otherwise by the teams
        inside the matchday. Kickoff times and results are refreshed, so a fixture the league
        moves around is updated instead of duplicated.
        """
        stored: list[Match] = []
        with self.transaction() as connection:
            for match in matches:
                row = None
                if match.external_id:
                    row = connection.execute(
                        "SELECT id FROM matches WHERE external_id = ?", (match.external_id,)
                    ).fetchone()
                if row is None:
                    row = connection.execute(
                        """
                        SELECT id FROM matches
                        WHERE matchday_id = ? AND home_team = ? AND away_team = ?
                        """,
                        (match.matchday_id, match.home_team, match.away_team),
                    ).fetchone()

                values = (
                    match.matchday_id,
                    match.home_team,
                    match.away_team,
                    _to_db(match.kickoff),
                    match.status.value,
                    match.score.home if match.score else None,
                    match.score.away if match.score else None,
                    match.external_id,
                )
                if row is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO matches (matchday_id, home_team, away_team, kickoff,
                                             status, home_goals, away_goals, external_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )
                    match_id = int(cursor.lastrowid or 0)
                else:
                    match_id = int(row["id"])
                    connection.execute(
                        """
                        UPDATE matches SET matchday_id = ?, home_team = ?, away_team = ?,
                               kickoff = ?, status = ?, home_goals = ?, away_goals = ?,
                               external_id = ?
                        WHERE id = ?
                        """,
                        (*values, match_id),
                    )
                stored.append(
                    Match(
                        matchday_id=match.matchday_id,
                        home_team=match.home_team,
                        away_team=match.away_team,
                        kickoff=match.kickoff,
                        status=match.status,
                        score=match.score,
                        external_id=match.external_id,
                        id=match_id,
                    )
                )
        return stored

    def matches(self, matchday_id: int | None = None) -> list[Match]:
        """Matches of one matchday, or all of them, which is what the overall ranking needs."""
        if matchday_id is None:
            rows = self._query("SELECT * FROM matches ORDER BY kickoff, id")
        else:
            rows = self._query(
                "SELECT * FROM matches WHERE matchday_id = ? ORDER BY kickoff, id", (matchday_id,)
            )
        return [_match(row) for row in rows]

    def match(self, match_id: int) -> Match | None:
        row = self._query_one("SELECT * FROM matches WHERE id = ?", (match_id,))
        return _match(row) if row else None

    def set_result(
        self,
        match_id: int,
        score: Score | None,
        status: MatchStatus = MatchStatus.FINISHED,
    ) -> None:
        """Save a result, whether it came from the API or was typed in by hand."""
        self._execute(
            "UPDATE matches SET home_goals = ?, away_goals = ?, status = ? WHERE id = ?",
            (
                score.home if score else None,
                score.away if score else None,
                status.value,
                match_id,
            ),
        )

    # ------------------------------------------------------------- predictions

    def save_prediction(self, prediction: Prediction) -> Prediction:
        """Insert the prediction, or overwrite the player's earlier one for that match."""
        row = self._query_one(
            """
            INSERT INTO predictions (player_id, match_id, home_goals, away_goals, joker,
                                     created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (player_id, match_id) DO UPDATE SET
                home_goals = excluded.home_goals,
                away_goals = excluded.away_goals,
                joker = excluded.joker,
                created_at = excluded.created_at
            RETURNING id
            """,
            (
                prediction.player_id,
                prediction.match_id,
                prediction.home_goals,
                prediction.away_goals,
                int(prediction.joker),
                _to_db(prediction.created_at),
            ),
        )
        with self._lock:
            self._connection.commit()
        return Prediction(
            player_id=prediction.player_id,
            match_id=prediction.match_id,
            home_goals=prediction.home_goals,
            away_goals=prediction.away_goals,
            joker=prediction.joker,
            created_at=prediction.created_at,
            id=int(row[0]),  # type: ignore[index]
        )

    def predictions(
        self,
        *,
        matchday_id: int | None = None,
        player_id: int | None = None,
    ) -> list[Prediction]:
        sql = [
            "SELECT p.* FROM predictions p JOIN matches m ON m.id = p.match_id",
            "WHERE 1 = 1",
        ]
        parameters: list[object] = []
        if matchday_id is not None:
            sql.append("AND m.matchday_id = ?")
            parameters.append(matchday_id)
        if player_id is not None:
            sql.append("AND p.player_id = ?")
            parameters.append(player_id)
        sql.append("ORDER BY m.kickoff, p.id")
        rows = self._query(" ".join(sql), tuple(parameters))
        return [_prediction(row) for row in rows]

    def clear_joker(self, player_id: int, matchday_id: int) -> None:
        """Take the joker off every match of the matchday, ready to be placed somewhere else."""
        self._execute(
            """
            UPDATE predictions SET joker = 0
            WHERE player_id = ?
              AND match_id IN (SELECT id FROM matches WHERE matchday_id = ?)
            """,
            (player_id, matchday_id),
        )

    # ---------------------------------------------------------------- settings

    def setting(self, key: str, default: str | None = None) -> str | None:
        row = self._query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str | int | None) -> None:
        if value is None:
            self._execute("DELETE FROM settings WHERE key = ?", (key,))
            return
        self._execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def scoring_rules(self) -> ScoringRules:
        """Read the rules, falling back to the defaults for anything never configured."""
        stored = {
            row["key"].removeprefix(RULES_PREFIX): int(row["value"])
            for row in self._query(
                "SELECT key, value FROM settings WHERE key LIKE ?", (f"{RULES_PREFIX}%",)
            )
        }
        known = {field.name for field in fields(ScoringRules)}
        return ScoringRules(**{k: v for k, v in stored.items() if k in known})

    def save_scoring_rules(self, rules: ScoringRules) -> None:
        with self.transaction() as connection:
            for field in fields(ScoringRules):
                connection.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (f"{RULES_PREFIX}{field.name}", str(getattr(rules, field.name))),
                )

    # ------------------------------------------------------------------- misc

    def is_empty(self) -> bool:
        """True for a brand new database, so the UI can offer the demo data."""
        row = self._query_one("SELECT EXISTS (SELECT 1 FROM players) AS any_player")
        return not row["any_player"]  # type: ignore[index]


def _player(row: sqlite3.Row) -> Player:
    return Player(name=row["name"], created_at=_from_db(row["created_at"]), id=row["id"])


def _matchday(row: sqlite3.Row) -> Matchday:
    return Matchday(league=row["league"], season=row["season"], number=row["number"], id=row["id"])


def _match(row: sqlite3.Row) -> Match:
    goals = (row["home_goals"], row["away_goals"])
    return Match(
        matchday_id=row["matchday_id"],
        home_team=row["home_team"],
        away_team=row["away_team"],
        kickoff=_from_db(row["kickoff"]),
        status=MatchStatus(row["status"]),
        score=Score(*goals) if None not in goals else None,
        external_id=row["external_id"],
        id=row["id"],
    )


def _prediction(row: sqlite3.Row) -> Prediction:
    return Prediction(
        player_id=row["player_id"],
        match_id=row["match_id"],
        home_goals=row["home_goals"],
        away_goals=row["away_goals"],
        joker=bool(row["joker"]),
        created_at=_from_db(row["created_at"]),
        id=row["id"],
    )
