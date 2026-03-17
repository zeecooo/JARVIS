"""
data/odds_client.py - Async client for The Odds API (v4).

Fetches current odds lines and player prop markets for NBA and NHL.

The Odds API docs: https://the-odds-api.com/lev4/
"""

import aiohttp
import asyncio
import logging
from typing import Optional

import config

log = logging.getLogger(__name__)

# ── Market key constants ───────────────────────────────────────────────────────
# NBA player prop market keys used by The Odds API
NBA_PROP_MARKETS = [
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
    "player_blocks",
    "player_steals",
    "player_points_rebounds_assists",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_first_basket",
]

# NHL player prop market keys
NHL_PROP_MARKETS = [
    "player_shots_on_goal",
    "player_goals",
    "player_assists",
    "player_points",
    "player_hits",
    "player_blocked_shots",
    "player_saves",
]

# Sport keys used by The Odds API
NBA_SPORT_KEY = "basketball_nba"
NHL_SPORT_KEY = "icehockey_nhl"


class OddsClient:
    """Async wrapper for The Odds API v4."""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_params = {"apiKey": config.ODDS_API_KEY, "oddsFormat": "american"}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=config.HTTP_TIMEOUT)
            self._session = aiohttp.ClientSession(
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: Optional[dict] = None) -> dict | list:
        """Issue a GET to the Odds API and return parsed JSON."""
        session = await self._get_session()
        url = f"{config.ODDS_BASE}/{path}"
        merged = {**self._base_params, **(params or {})}
        try:
            async with session.get(url, params=merged) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientResponseError as exc:
            log.warning("Odds API HTTP %s for %s: %s", exc.status, url, exc.message)
            return {}
        except asyncio.TimeoutError:
            log.warning("Odds API timeout for %s", url)
            return {}
        except Exception as exc:  # noqa: BLE001
            log.error("Odds API unexpected error for %s: %s", url, exc)
            return {}

    # ── Events ────────────────────────────────────────────────────────────────

    async def get_todays_nba_events(self) -> list[dict]:
        """
        Return today's NBA events (games) with their IDs and commence times.
        """
        data = await self._get(
            f"sports/{NBA_SPORT_KEY}/events",
            {"dateFormat": "iso"},
        )
        if isinstance(data, list):
            return data
        return data.get("data", [])

    async def get_todays_nhl_events(self) -> list[dict]:
        """Return today's NHL events."""
        data = await self._get(
            f"sports/{NHL_SPORT_KEY}/events",
            {"dateFormat": "iso"},
        )
        if isinstance(data, list):
            return data
        return data.get("data", [])

    # ── NBA props ─────────────────────────────────────────────────────────────

    async def get_nba_props(
        self,
        event_id: str,
        markets: Optional[list[str]] = None,
        bookmakers: Optional[list[str]] = None,
    ) -> dict:
        """
        Fetch player prop odds for a specific NBA event.

        Parameters
        ----------
        event_id   : The Odds API event ID string
        markets    : List of market keys; defaults to NBA_PROP_MARKETS
        bookmakers : List of bookmaker keys (e.g. ['draftkings', 'fanduel'])
                     If None, The Odds API returns all available books.

        Returns
        -------
        dict with 'bookmakers' key containing a list of bookmaker objects,
        each with 'markets' → list of market objects with 'outcomes'.
        """
        markets = markets or NBA_PROP_MARKETS
        params: dict = {"markets": ",".join(markets)}
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)

        data = await self._get(
            f"sports/{NBA_SPORT_KEY}/events/{event_id}/odds",
            params,
        )
        return data if isinstance(data, dict) else {}

    async def get_nhl_props(
        self,
        event_id: str,
        markets: Optional[list[str]] = None,
        bookmakers: Optional[list[str]] = None,
    ) -> dict:
        """Fetch player prop odds for a specific NHL event."""
        markets = markets or NHL_PROP_MARKETS
        params: dict = {"markets": ",".join(markets)}
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)

        data = await self._get(
            f"sports/{NHL_SPORT_KEY}/events/{event_id}/odds",
            params,
        )
        return data if isinstance(data, dict) else {}

    async def get_nba_game_odds(self, bookmakers: Optional[list[str]] = None) -> list[dict]:
        """
        Return head-to-head and spreads for all NBA games today.
        Useful for parlay building and context.
        """
        params: dict = {"markets": "h2h,spreads"}
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)
        data = await self._get(
            f"sports/{NBA_SPORT_KEY}/odds",
            params,
        )
        return data if isinstance(data, list) else []

    # ── Prop extraction helpers ───────────────────────────────────────────────

    def extract_player_lines(
        self, odds_data: dict, player_name: str, market_key: str
    ) -> list[dict]:
        """
        From a raw event odds response, find all outcomes for a player in a
        given market across all bookmakers.

        Returns a list of dicts:
          {bookmaker, name, description, price, point}
        """
        results: list[dict] = []
        bookmakers = odds_data.get("bookmakers", [])
        player_lower = player_name.lower()

        for bm in bookmakers:
            bm_key = bm.get("key", "")
            for market in bm.get("markets", []):
                if market.get("key") != market_key:
                    continue
                for outcome in market.get("outcomes", []):
                    desc = outcome.get("description", "")
                    name = outcome.get("name", "")
                    # description holds the player name; name is Over/Under
                    if player_lower in desc.lower():
                        results.append(
                            {
                                "bookmaker": bm_key,
                                "name": name,  # "Over" or "Under"
                                "description": desc,
                                "price": outcome.get("price"),
                                "point": outcome.get("point"),
                            }
                        )
        return results

    def best_line(
        self, outcomes: list[dict], side: str = "Over"
    ) -> Optional[dict]:
        """
        From a list of extracted outcomes for a player, return the best
        'Over' (or 'Under') line — i.e. the one with the best payout.
        """
        filtered = [o for o in outcomes if o.get("name", "").lower() == side.lower()]
        if not filtered:
            return None
        # Best payout: for Over, highest positive or least negative American odds
        filtered.sort(key=lambda o: o.get("price", -9999), reverse=True)
        return filtered[0]

    def consensus_line(self, outcomes: list[dict], side: str = "Over") -> Optional[float]:
        """
        Return the most common (consensus) point value across bookmakers for
        a given side (Over/Under).
        """
        filtered = [o for o in outcomes if o.get("name", "").lower() == side.lower()]
        if not filtered:
            return None
        from collections import Counter
        pts = [o.get("point") for o in filtered if o.get("point") is not None]
        if not pts:
            return None
        counter = Counter(pts)
        return counter.most_common(1)[0][0]
