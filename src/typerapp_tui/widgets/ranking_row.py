"""One line of the leaderboard.

Each player keeps their own widget for the whole life of the screen, so a new result moves
the existing row instead of redrawing the table. The row remembers where it used to be and
slides in from there, which is what makes a change of position visible.
"""

from __future__ import annotations

from typing import ClassVar

from rich.console import RenderableType
from rich.text import Text
from textual.widget import Widget

from typerapp_core.models import RankingEntry

NAME_WIDTH = 12


class RankingRow(Widget):
    can_focus = True

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "rankingrow--position",
        "rankingrow--name",
        "rankingrow--points",
        "rankingrow--up",
        "rankingrow--down",
        "rankingrow--details",
    }

    def __init__(self, entry: RankingEntry, *, is_me: bool = False) -> None:
        super().__init__(id=f"rank-{entry.player_id}")
        self.player_id = entry.player_id
        self.entry = entry
        self.delta = 0
        if is_me:
            self.add_class("-me")

    def update_entry(self, entry: RankingEntry, delta: int = 0) -> None:
        """New numbers for the same player. ``delta`` is how many places they gained."""
        self.entry = entry
        self.delta = delta
        self.refresh()

    def flash(self) -> None:
        """Fade the row in after it changed place, so the movement catches the eye.

        Textual can only animate numbers, so the row cannot be slid from its old position;
        the fade plus the ▲▼ marker carries the same information.
        """
        self.styles.opacity = 0.2
        self.styles.animate("opacity", value=1.0, duration=0.45, easing="out_cubic")

    def render(self) -> RenderableType:
        entry = self.entry
        row = Text(no_wrap=True, overflow="crop")
        row.append(f"{entry.position:>2}. ", self.get_component_rich_style("rankingrow--position"))
        row.append(
            f"{_clip(entry.player_name):<{NAME_WIDTH}} ",
            self.get_component_rich_style("rankingrow--name"),
        )
        row.append(f"{entry.points:>3} pkt ", self.get_component_rich_style("rankingrow--points"))
        row.append_text(self._movement())
        row.append(
            f"  dokładne {entry.exact_hits} · różnice {entry.difference_hits}"
            f" · rozstrzyg. {entry.outcome_hits}",
            self.get_component_rich_style("rankingrow--details"),
        )
        return row

    def _movement(self) -> Text:
        if self.delta > 0:
            return Text(f"▲{self.delta:<2}", self.get_component_rich_style("rankingrow--up"))
        if self.delta < 0:
            return Text(f"▼{abs(self.delta):<2}", self.get_component_rich_style("rankingrow--down"))
        return Text("—  ", self.get_component_rich_style("rankingrow--details"))

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()


def _clip(name: str, width: int = NAME_WIDTH) -> str:
    return name if len(name) <= width else f"{name[: width - 1]}…"
