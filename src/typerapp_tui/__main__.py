"""Entry point: builds the core, hands it to the app and starts the interface.

The results source is chosen here, once: with a key in the environment or in ``.env`` the app
talks to football-data.org, and without one it falls back to results typed in by hand. Either
way the rest of the application sees the same provider interface.

``--demo`` starts in demo mode, with fresh sample data in memory and nothing saved. The same
mode can be switched on and off from the menu.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from typerapp_core.config import football_data_api_key
from typerapp_core.controller import TyperController
from typerapp_core.events import EventBus
from typerapp_core.providers.api import ApiResultsProvider
from typerapp_core.providers.base import ResultsProvider
from typerapp_core.providers.manual import ManualResultsProvider
from typerapp_core.repository import Repository
from typerapp_core.session import Session
from typerapp_tui.app import TyperApp


def build_provider() -> ResultsProvider:
    key = football_data_api_key()
    return ApiResultsProvider(key) if key else ManualResultsProvider()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="typerapp_tui", description="typerApp, terminal UI")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="start in demo mode: fresh sample data in memory, nothing is saved",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    with Repository.open_default() as repository:
        controller = TyperController(repository, EventBus(), provider=build_provider())
        session = Session(controller)
        if args.demo:
            session.enter_demo()
        TyperApp(session).run()


if __name__ == "__main__":
    main()
