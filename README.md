# typerApp

A football score prediction game for a group of friends. Players predict the results of
upcoming league matches; the app fetches real results from the
[football-data.org](https://www.football-data.org/) API (or accepts them entered by hand),
awards points and keeps a leaderboard.

The project is built in stages for a Human–Computer Interaction university course:

| Stage | Description | Location | Tag |
|-------|-------------|----------|-----|
| 1 | Terminal UI (Textual) | `src/typerapp_tui` | `v1.0-tekstowy` |
| 2 | Desktop GUI (PySide6) | `src/typerapp_gui` | `v2.0-graficzny` |
| 3 | Specification and Figma prototype | `docs/` | – |

The shared core (domain model, scoring, ranking, storage, controller) lives in
`src/typerapp_core` and does not depend on any UI library, so moving from the terminal UI
to the desktop GUI only means adding a new presentation layer.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Running

```bash
uv sync                           # install dependencies
uv run python -m typerapp_tui      # terminal version
```

## Results

Copy `.env.example` to `.env` and paste a [football-data.org](https://www.football-data.org/)
key into it. The free plan covers a dozen competitions (Premier League, Bundesliga, La Liga,
Serie A and so on) and allows about ten requests a minute, so the app fetches one matchday at
a time and caches everything in SQLite.

- `S` fetches the current and the previous matchday; the app also does it on start-up when
  the cache is older than five minutes.
- `W` types a result in by hand, which is how the app works with no key and no network.
- `L` in the settings picks the league.

Without a key the app still runs: cached data, manually entered results and the demo seed.

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```
