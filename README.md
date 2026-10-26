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

API key: copy `.env.example` to `.env` and paste your football-data.org key.
Without a key the app works offline (cached data, manually entered results, demo mode).

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```
