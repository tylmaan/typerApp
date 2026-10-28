"""Polish wording and number formatting in the interface layer."""

from datetime import UTC, datetime, timedelta

import pytest

from typerapp_core.models import MatchStatus
from typerapp_tui.formatting import (
    STATUS_LABELS,
    STATUS_WIDTH,
    format_countdown,
    format_decimal,
    format_kickoff,
    format_score,
    format_status,
    plural_matches,
    plural_predictions,
)


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, "0 meczów"),
        (1, "1 mecz"),
        (2, "2 mecze"),
        (4, "4 mecze"),
        (5, "5 meczów"),
        (12, "12 meczów"),  # 12, 13 and 14 take the "many" form
        (22, "22 mecze"),
        (25, "25 meczów"),
    ],
)
def test_plural_matches(count: int, expected: str) -> None:
    assert plural_matches(count) == expected


def test_plural_predictions() -> None:
    assert (plural_predictions(1), plural_predictions(3), plural_predictions(7)) == (
        "1 typ",
        "3 typy",
        "7 typów",
    )


@pytest.mark.parametrize(
    ("left", "expected"),
    [
        (None, "brak terminarza"),
        (timedelta(0), "zamknięte"),
        (timedelta(seconds=-5), "zamknięte"),
        (timedelta(seconds=9), "9 s"),
        (timedelta(minutes=3, seconds=4), "3 min 04 s"),
        (timedelta(hours=2, minutes=30), "2 godz 30 min"),
        (timedelta(days=1, hours=23), "1 d 23 godz"),
    ],
)
def test_format_countdown(left: timedelta | None, expected: str) -> None:
    assert format_countdown(left) == expected


def test_format_decimal_uses_a_comma() -> None:
    assert format_decimal(9.0) == "9,0"
    assert format_decimal(4.25, places=2) == "4,25"


def test_format_score_marks_what_is_missing() -> None:
    assert format_score(2, 1) == "2:1"
    assert format_score(None, None) == "·:·"
    assert format_score(2, None, placeholder="_") == "2:_"


def test_format_kickoff_shows_a_polish_weekday() -> None:
    saturday = datetime(2026, 11, 7, 18, 0, tzinfo=UTC)
    formatted = format_kickoff(saturday)

    assert formatted.startswith(("sob", "nd"))  # depends on the machine's timezone
    assert "07.11" in formatted or "08.11" in formatted


@pytest.mark.parametrize(
    ("status", "label"),
    [
        (MatchStatus.SCHEDULED, "zaplanowany"),
        (MatchStatus.LIVE, "trwa"),
        (MatchStatus.FINISHED, "zakończony"),
        (MatchStatus.POSTPONED, "przełożony"),
        (MatchStatus.CANCELLED, "odwołany"),
    ],
)
def test_every_status_has_a_label(status: MatchStatus, label: str) -> None:
    assert format_status(status) == label


def test_every_status_is_covered_and_the_column_fits_the_widest() -> None:
    assert set(STATUS_LABELS) == set(MatchStatus)  # a new status cannot go without a label
    assert max(len(label) for label in STATUS_LABELS.values()) == STATUS_WIDTH
