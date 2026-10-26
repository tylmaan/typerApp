"""Turning core values into the Polish text shown on screen."""

from __future__ import annotations

from datetime import datetime, timedelta

#: Monday first, matching ``datetime.weekday()``.
WEEKDAYS = ("pon", "wt", "śr", "czw", "pt", "sob", "nd")


def format_kickoff(kickoff: datetime) -> str:
    """``sob 28.10 17:45`` in the player's own timezone."""
    local = kickoff.astimezone()
    return f"{WEEKDAYS[local.weekday()]} {local:%d.%m %H:%M}"


def format_countdown(left: timedelta | None) -> str:
    """How much time is left, in as few characters as stay readable."""
    if left is None:
        return "brak terminarza"
    if left <= timedelta(0):
        return "zamknięte"
    minutes, seconds = divmod(int(left.total_seconds()), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days} d {hours} godz"
    if hours:
        return f"{hours} godz {minutes} min"
    if minutes:
        return f"{minutes} min {seconds:02} s"
    return f"{seconds} s"


def format_score(home: int | None, away: int | None, placeholder: str = "·") -> str:
    left = placeholder if home is None else str(home)
    right = placeholder if away is None else str(away)
    return f"{left}:{right}"


def plural_matches(count: int) -> str:
    """Polish needs three forms: 1 mecz, 2 mecze, 5 meczów."""
    if count == 1:
        return "1 mecz"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return f"{count} mecze"
    return f"{count} meczów"
