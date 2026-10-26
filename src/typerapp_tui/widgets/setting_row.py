"""A single numeric setting.

Typed the same way as a score: digits, plus and minus. Digits accumulate here, because a
deadline of 60 minutes needs two of them, and Backspace deletes the last one.
"""

from __future__ import annotations

from typing import ClassVar

from rich.console import RenderableType
from rich.text import Text
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget

LABEL_WIDTH = 30


class SettingRow(Widget):
    can_focus = True

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "settingrow--label",
        "settingrow--value",
        "settingrow--value-active",
        "settingrow--hint",
    }

    value: reactive[int] = reactive(0)

    class Changed(Message):
        def __init__(self, key: str, value: int) -> None:
            super().__init__()
            self.key = key
            self.value = value

    def __init__(
        self,
        key: str,
        label: str,
        value: int,
        *,
        minimum: int = 0,
        maximum: int = 99,
        suffix: str = "",
        hint: str = "",
    ) -> None:
        super().__init__(id=f"setting-{key}")
        self.key = key
        self.label = label
        self.minimum = minimum
        self.maximum = maximum
        self.suffix = suffix
        self.hint = hint
        self.value = value
        #: Digits typed in a row build one number until the row loses focus.
        self._typing = False

    def render(self) -> RenderableType:
        row = Text(no_wrap=True, overflow="crop")
        row.append(
            f"{self.label:<{LABEL_WIDTH}}", self.get_component_rich_style("settingrow--label")
        )
        style = self.get_component_rich_style(
            "settingrow--value-active" if self.has_focus else "settingrow--value"
        )
        row.append(f"{self.value:^5}", style)
        if self.suffix:
            row.append(f" {self.suffix}", self.get_component_rich_style("settingrow--hint"))
        if self.hint:
            row.append(f"   {self.hint}", self.get_component_rich_style("settingrow--hint"))
        return row

    def on_focus(self) -> None:
        self._typing = False
        self.refresh()

    def on_blur(self) -> None:
        self._typing = False
        self.refresh()

    def on_key(self, event: events.Key) -> None:
        if event.character and event.character.isdigit():
            digit = int(event.character)
            candidate = self.value * 10 + digit if self._typing else digit
            if candidate > self.maximum:
                candidate = digit
            self._typing = True
            self._commit(candidate)
            event.stop()
        elif event.key in ("backspace", "delete"):
            self._commit(self.value // 10)
            event.stop()
        elif event.key in ("right", "plus", "equals_sign", "kp_plus"):
            self._typing = False
            self._commit(self.value + 1)
            event.stop()
        elif event.key in ("left", "minus", "underscore", "kp_minus"):
            self._typing = False
            self._commit(self.value - 1)
            event.stop()

    def _commit(self, candidate: int) -> None:
        value = max(self.minimum, min(self.maximum, candidate))
        if value == self.value:
            self.refresh()
            return
        self.value = value
        self.post_message(self.Changed(self.key, value))
