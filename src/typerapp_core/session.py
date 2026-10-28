"""The running game: the real data, and the demo mode that can be laid over it.

Demo mode is not a league and it is not stored. Switching it on builds a brand new database
in memory, seeded from the current moment, and makes its controller the active one; switching
it off throws that database away and brings the real one back untouched. Two consequences
follow from that:

* the demo can never go stale, because it is built when it is switched on, and
* nothing the player does in it, and none of its sample players, ever reaches the real data.

Both controllers publish on the same event bus, so a view subscribed through the bridge keeps
working across the switch and only has to refresh itself when it hears
:class:`~typerapp_core.events.DemoModeChanged`.
"""

from __future__ import annotations

import random

from typerapp_core.controller import TyperController
from typerapp_core.demo import seed_demo_data, simulated_score
from typerapp_core.events import DemoModeChanged, EventBus
from typerapp_core.models import Score
from typerapp_core.providers.manual import ManualResultsProvider
from typerapp_core.repository import Repository


class Session:
    """Holds the real controller and, while demo mode is on, a throw-away demo one."""

    def __init__(self, controller: TyperController, *, rng: random.Random | None = None) -> None:
        self._real = controller
        self._demo: TyperController | None = None
        self._rng = rng or random.Random()
        self.events: EventBus = controller.events

    @property
    def controller(self) -> TyperController:
        """The controller views talk to: the demo one while demo mode is on."""
        return self._demo or self._real

    @property
    def real(self) -> TyperController:
        return self._real

    @property
    def demo(self) -> bool:
        return self._demo is not None

    def enter_demo(self) -> TyperController:
        """Build fresh sample data in memory and make it the active game.

        A no-op when demo mode is already on, so pressing the menu entry twice does not throw
        away what the player just typed.
        """
        if self._demo is None:
            repository = Repository()  # in memory: nothing about it survives the session
            seed_demo_data(repository, now=self._real.now())
            self._demo = TyperController(
                repository,
                self.events,
                self._real.clock,
                ManualResultsProvider(),  # no league of the demo exists in any API
            )
            self.events.publish(DemoModeChanged(active=True))
        return self._demo

    def leave_demo(self) -> None:
        """Discard the demo and go back to the real data, which was never touched."""
        if self._demo is None:
            return
        demo, self._demo = self._demo, None
        demo.repository.close()
        self.events.publish(DemoModeChanged(active=False))

    def simulate_result(self, match_id: int) -> Score:
        """Play one demo match: draw a plausible score and enter it like a real result.

        It goes through the same path as a result from the API or typed by hand, so the points
        are awarded and the leaderboard refreshed exactly as they would be for the real thing.
        """
        if self._demo is None:
            raise RuntimeError("Results can only be simulated in demo mode")
        score = simulated_score(self._rng)
        self._demo.enter_result(match_id, score)
        return score

    def close(self) -> None:
        """Release the demo database without announcing anything, for shutting down."""
        if self._demo is not None:
            self._demo.repository.close()
            self._demo = None
