"""The offline source: results are typed in by hand.

It fetches nothing, which is the point. The app stays usable without a network or an API key,
and every result entered through the dialog goes in through the same controller method the
online sync uses.
"""

from __future__ import annotations

from typerapp_core.providers.base import FetchedMatch


class ManualResultsProvider:
    name = "manual"
    online = False

    def supports(self, league: str) -> bool:
        del league
        return False

    def fetch_season(self, league: str, season: str) -> list[FetchedMatch]:
        del league, season
        return []
