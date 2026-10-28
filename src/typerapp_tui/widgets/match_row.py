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
from textual.timer import Timer
from textual.widget import Widget

from typerapp_core.models import Match, Prediction
from typerapp_tui.formatting import format_kickoff, format_score

HOME, AWAY = 0, 1

#: Widest sensible score. Typing another digit starts the number again.
MAX_GOALS = 19

TEAM_WIDTH = 15
TIME_WIDTH = 13
FIELD_WIDTH = 3

#: Where the home field starts in the rendered line, used to turn a click into a field.
HOME_FIELD_COLUMN = TIME_WIDTH + 1 + TEAM_WIDTH + 1
AWAY_FIELD_COLUMN = HOME_FIELD_COLUMN + FIELD_WIDTH + 1

#: How long the points take to count up after a match is settled.
COUNT_DURATION = 0.45
COUNT_STEPS = 10


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
    points: reactive[int | None] = reactive(None)

    class ScoreEntered(Message):
        """The row holds a complete score that the screen should save."""

        def __init__(self, match_id: int, home: int, away: int) -> None:
            super().__init__()
            self.match_id = match_id
            self.home = home
            self.away = away

    class Locked(Message):
        """The player tried to type into a match that can no longer be predicted."""

        def __init__(self, match_id: int) -> None:
            super().__init__()
            self.match_id = match_id

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
        self._target_points = points or 0
        self._counter: Timer | None = None
        if prediction is not None:
            self.home = prediction.home_goals
            self.away = prediction.away_goals
            self.joker = prediction.joker
            self.saved = True

    def update_match(self, match: Match, *, editable: bool) -> None:
        """A result arrived: the same row, new status and a new score."""
        self.match = match
        self.editable = editable
        self.refresh()

    def apply(self, prediction: Prediction) -> None:
        """Take over what the core stored, after the controller confirmed a save."""
        self.home = prediction.home_goals
        self.away = prediction.away_goals
        self.joker = prediction.joker
        self.saved = True

    def award(self, total: int) -> None:
        """Count the points up from zero, so they look like they are landing on the row."""
        self.flash()
        if self._counter is not None:
            self._counter.stop()
        self._target_points = total
        if total <= 0:
            self.points = total
            return
        self.points = 0
        self._counter = self.set_interval(COUNT_DURATION / COUNT_STEPS, self._count_up)

    def _count_up(self) -> None:
        step = max(1, round(self._target_points / COUNT_STEPS))
        self.points = min(self._target_points, (self.points or 0) + step)
        if self.points >= self._target_points and self._counter is not None:
            self._counter.stop()
            self._counter = None

    def flash(self) -> None:
        """Fade the row in, so a result arriving from the sync catches the eye."""
        self.styles.opacity = 0.2
        self.styles.animate("opacity", value=1.0, duration=0.4, easing="out_cubic")

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
        row.append(f"{format_kickoff(self.match.kickoff):<{TIME_WIDTH}} ", time_style)
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
        return Text(f"{'·' if value is None else value:^{FIELD_WIDTH}}", style=style)

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

    def on_click(self, event: events.Click) -> None:
        """Clicking a score field puts the cursor there, so the mouse works too."""
        self.focus()
        if not self.editable:
            return
        column = event.offset.x - self.styles.padding.left
        self.field = AWAY if column >= AWAY_FIELD_COLUMN else HOME

    def on_focus(self) -> None:
        self.refresh()  # the active field is drawn only on the focused row

    def on_blur(self) -> None:
        self.refresh()

    def on_key(self, event: events.Key) -> None:
        if not self.editable:
            if _is_entry_key(event):
                # Say why nothing happens, instead of swallowing the key in silence.
                self.post_message(self.Locked(self.match_id))
                event.stop()
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


#: Keys that change a score, apart from the digits.
ENTRY_KEYS = ("plus", "equals_sign", "kp_plus", "minus", "underscore", "kp_minus")


def _is_entry_key(event: events.Key) -> bool:
    """Whether the key is an attempt to type a score."""
    return bool(event.character and event.character.isdigit()) or event.key in ENTRY_KEYS


def _clip(name: str, width: int = TEAM_WIDTH) -> str:
    return name if len(name) <= width else f"{name[: width - 1]}…"
