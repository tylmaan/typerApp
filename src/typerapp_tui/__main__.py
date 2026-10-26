"""Entry point: builds the core, hands it to the app and starts the interface."""

from __future__ import annotations

from typerapp_core.controller import TyperController
from typerapp_core.events import EventBus
from typerapp_core.repository import Repository
from typerapp_tui.app import TyperApp


def main() -> None:
    with Repository.open_default() as repository:
        controller = TyperController(repository, EventBus())
        TyperApp(controller).run()


if __name__ == "__main__":
    main()
