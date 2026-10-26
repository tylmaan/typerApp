"""One match of a matchday: the row the player types into.

Every match is its own widget, so a saved prediction redraws exactly one line of the screen
instead of the whole list. There is no edit mode: the arrows pick the match and the field,
digits fill the score, plus and minus nudge it.
"""

from __future__ import annotations

from typing import ClassVar

from rich.console import RenderableType
from rich.text import Text
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget

from typerapp_core.models import Match, Prediction
from typerapp_tui.formatting import format_kickoff, format_score

HOME, AWAY = 0, 1

#: Widest sensible score. Typing another digit starts the number again.
MAX_GOALS = 19

TEAM_WIDTH = 15


class MatchRow(Widget):
    can_focus = True

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "matchrow--time",
        "matchrow--team",
        "matchrow--field",
        "matchrow--field-active",
        "matchrow--joker",
        "matchrow--status",
        "matchrow--points",
    }

    field: reactive[int] = reactive(HOME)
    home: reactive[int | None] = reactive(None)
    away: reactive[int | None] = reactive(None)
    joker: reactive[bool] = reactive(False)
    saved: reactive[bool] = reactive(False)

    class ScoreEntered(Message):
        """The row holds a complete score that the screen should save."""

        def __init__(self, match_id: int, home: int, away: int) -> None:
            super().__init__()
            self.match_id = match_id
            self.home = home
            self.away = away

    def __init__(
        self,
        match: Match,
        prediction: Prediction | None = None,
        *,
        editable: bool = True,
        points: int | None = None,
    ) -> None:
        super().__init__(id=f"match-{match.id}")
        self.match = match
        self.match_id: int = match.id or 0
        self.editable = editable
        self.points = points
        if prediction is not None:
            self.home = prediction.home_goals
            self.away = prediction.away_goals
            self.joker = prediction.joker
            self.saved = True

    def apply(self, prediction: Prediction) -> None:
        """Take over what the core stored, after the controller confirmed a save."""
        self.home = prediction.home_goals
        self.away = prediction.away_goals
        self.joker = prediction.joker
        self.saved = True

    def clear(self) -> None:
        """Empty the row, used when another player takes the seat."""
        self.home = None
        self.away = None
        self.joker = False
        self.saved = False

    @property
    def is_complete(self) -> bool:
        return self.home is not None and self.away is not None

    def render(self) -> RenderableType:
        time_style = self.get_component_rich_style("matchrow--time")
        team_style = self.get_component_rich_style("matchrow--team")

        row = Text(no_wrap=True, overflow="crop")
        row.append(f"{format_kickoff(self.match.kickoff):<13} ", time_style)
        row.append(f"{_clip(self.match.home_team):>{TEAM_WIDTH}} ", team_style)
        row.append_text(self._field(HOME))
        row.append(":", team_style)
        row.append_text(self._field(AWAY))
        row.append(f" {_clip(self.match.away_team):<{TEAM_WIDTH}}", team_style)
        row.append_text(self._trailing())
        return row

    def _field(self, which: int) -> Text:
        value = self.home if which == HOME else self.away
        active = self.editable and self.has_focus and self.field == which
        style = self.get_component_rich_style(
            "matchrow--field-active" if active else "matchrow--field"
        )
        return Text(f"{'·' if value is None else value:^3}", style=style)

    def _trailing(self) -> Text:
        if self.match.is_settled and self.match.score is not None:
            trailing = Text(
                f"  {self.match.score}", self.get_component_rich_style("matchrow--status")
            )
            if self.points is not None:
                trailing.append(
                    f"  +{self.points}", self.get_component_rich_style("matchrow--points")
                )
            return trailing

        trailing = Text()
        if self.joker:
            trailing.append("  ★ joker", self.get_component_rich_style("matchrow--joker"))
        if self.saved:
            trailing.append("  ✔", self.get_component_rich_style("matchrow--status"))
        elif not self.editable:
            trailing.append("  zamknięte", self.get_component_rich_style("matchrow--status"))
        return trailing

    def on_focus(self) -> None:
        self.refresh()  # the active field is drawn only on the focused row

    def on_blur(self) -> None:
        self.refresh()

    def on_key(self, event: events.Key) -> None:
        if not self.editable:
            return

        if event.key in ("left", "right"):
            self.field = HOME if event.key == "left" else AWAY
            event.stop()
            return

        if event.character and event.character.isdigit():
            self._set(int(event.character))
            # Typing the home goals jumps to the away field, so a match takes two keystrokes.
            if self.field == HOME:
                self.field = AWAY
            event.stop()
            return

        if event.key in ("plus", "equals_sign", "kp_plus"):
            self._nudge(1)
            event.stop()
        elif event.key in ("minus", "underscore", "kp_minus"):
            self._nudge(-1)
            event.stop()

    def _set(self, value: int) -> None:
        if self.field == HOME:
            self.home = value
        else:
            self.away = value
        self._announce()

    def _nudge(self, step: int) -> None:
        current = self.home if self.field == HOME else self.away
        value = max(0, min(MAX_GOALS, (current or 0) + step))
        self._set(value)

    def _announce(self) -> None:
        if self.is_complete:
            self.post_message(
                self.ScoreEntered(self.match_id, self.home or 0, self.away or 0),
            )
        else:
            self.saved = False

    def __str__(self) -> str:
        return f"{self.match.home_team} {format_score(self.home, self.away)} {self.match.away_team}"


def _clip(name: str, width: int = TEAM_WIDTH) -> str:
    return name if len(name) <= width else f"{name[: width - 1]}…"
