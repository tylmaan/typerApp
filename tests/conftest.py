"""Fixtures shared by the core tests. The builders live in ``tests/helpers.py``."""

import pytest

from typerapp_core.models import ScoringRules


@pytest.fixture
def rules() -> ScoringRules:
    """The default, agreed-upon rules of the game."""
    return ScoringRules()
