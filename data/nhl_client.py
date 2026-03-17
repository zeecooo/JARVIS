"""
data/nhl_client.py - Async client for the NHL official web API.

Base URL: https://api-web.nhle.com/v1

All methods return plain dicts/lists.  No external library dependency beyond
aiohttp and config.
"""

import aiohttp
import asyncio
import logging
from typing import Optional
from datetime import date

import config

log = logging.getLogger(__name__)

# ── NHL nickname / common-name map ────────────────────────────────────────────
_NHL_NICKNAMES: dict[str, str] = {
    "mcdavid": "Connor McDavid",
    "the kid": "Connor McDavid",
    "draisaitl": "Leon Draisaitl",
    "leon": "Leon Draisaitl",
    "ovechkin": "Alex Ovechkin",
    "ovi": "Alex Ovechkin",
    "the great 8": "Alex Ovechkin",
    "crosby": "Sidney Crosby",
    "sid": "Sidney Crosby",
    "the kid": "Sidney Crosby",
    "matthews": "Auston Matthews",
    "puckstopper": "Auston Matthews",
    "marner": "Mitch Marner",
    "tkachuk": "Matthew Tkachuk",
    "pasta": "David Pastrnak",
    "pastrnak": "David Pastrnak",
    "makar": "Cale Makar",
    "hedman": "Victor Hedman",
    "point": "Brayden Point",
    "stamkos": "Steven Stamkos",
    "kucherov": "Nikita Kucherov",
    "hub": "Nikita Kucherov",
    "price": "Carey Price",
    "fleury": "Marc-Andre Fleury",
    "flower": "Marc-Andre Fleury",
    "hellebuyck": "Connor Hellebuyck",
    "vasilevskiy": "Andrei Vasilevskiy",
    "vasi": "Andrei Vasilevskiy",
    "mackinnon": "Nathan MacKinnon",
    "nate": "Nathan MacKinnon",
}

# NHL team abbreviations → full name (for search fallback)
_TEAM_ABBREVS: dict[str, str] = {
    "TOR": "Toronto Maple Leafs",
    "MTL": "Montreal Canadiens",
    "BOS": "Boston Bruins",
    "NYR": "New York Rangers",
    "NYI": "New York Islanders",
    "NJD": "New Jersey Devils",
    "PHI": "Philadelphia Flyers",
    "PIT": "Pittsburgh Penguins",
    "WSH": "Washington Capitals",
    "CAR": "Carolina Hurricanes",
    "FLA": "Florida Panthers",
    "TBL": "Tampa Bay Lightning",
    "DET": "Detroit Red Wings",
    "CBJ": "Columbus Blue Jackets",
    "BUF": "Buffalo Sabres",
    "OTT": "Ottawa Senators",
    "CHI": "Chicago Blackhawks",
    "NSH": "Nashville Predators",
    "STL": "St. Louis Blues",
    "WPG": "Winnipeg Jets",
    "MIN": "Minnesota Wild",
    "COL": "Colorado Avalanche",
    "DAL": "Dallas Stars",
    "ARI": "Arizona Coyotes",
    "VGK": "Vegas Golden Knights",
    "LAK": "Los Angeles Kings",
    "ANA": "Anaheim Ducks",
    "SJS": "San Jose Sharks",
    "SEA": "Seattle Kraken",
    "VAN": "Vancouver Canucks",
    "CGY": "Calgary Flames",
    "EDM": "Edmonton Oilers",
    "UTA": "Utah Hockey Club",
}


def _normalize_name(raw: str) -> str:
    """Resolve a nickname to a full player name where possible."""
    key = raw.strip().lower()
    if key in _NHL_NICKNAMES:
        return _NHL_NICKNAMES[key]
    # Handle "C. McDavid" abbreviation style
    parts = raw.strip().split()
    if len(parts) == 2 and len(parts[0]) <= 2 and parts[0].endswith("."):
        return parts[1]
    return raw.strip()


class NHLClient:
    """Async wrapper for the NHL official web API."""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None

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

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        """Issue a GET against the NHL base URL and return JSON."""
        session = await self._get_session()
        url = f"{config.NHL_BASE}/{path}"
        try:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientResponseError as exc:
            log.warning("NHL API HTTP %s for %s: %s", exc.status, url, exc.message)
            return {}
        except asyncio.TimeoutError:
            log.warning("NHL API timeout for %s", url)
            return {}
        except Exception as exc:  # noqa: BLE001
            log.error("NHL API unexpected error for %s: %s", url, exc)
            return {}

    # ── Player search ─────────────────────────────────────────────────────────

    async def get_player(self, name: str) -> Optional[dict]:
        """
        Search for an NHL player by name.
        Uses the suggest endpoint which returns partial-match results.
        """
        resolved = _normalize_name(name)
        data = await self._get(f"suggest/players/{resolved.replace(' ', '%20')}")
        # The suggest endpoint returns {"players": [...]}
        players = data.get("players", [])
        if not players and resolved != name:
            data = await self._get(f"suggest/players/{name.replace(' ', '%20')}")
            players = data.get("players", [])

        if not players:
            return None

        # Best match: exact last name
        name_lower = resolved.lower()
        for p in players:
            full = f"{p.get('firstName', {}).get('default', '')} {p.get('lastName', {}).get('default', '')}".lower()
            if name_lower in full or full in name_lower:
                return p
        return players[0]

    async def get_player_details(self, player_id: int) -> dict:
        """Fetch full player bio / landing page data."""
        data = await self._get(f"player/{player_id}/landing")
        return data

    # ── Game logs ─────────────────────────────────────────────────────────────

    async def get_player_game_logs(
        self,
        player_id: int,
        season: str = "20242025",
        game_type: int = 2,
    ) -> list[dict]:
        """
        Return regular-season game logs for a player.

        Parameters
        ----------
        player_id : int
        season    : str  e.g. "20242025"
        game_type : int  2 = regular season, 3 = playoffs
        """
        data = await self._get(
            f"player/{player_id}/game-log/{season}/{game_type}"
        )
        logs = data.get("gameLog", [])
        # Sort newest first
        logs.sort(key=lambda x: x.get("gameDate", ""), reverse=True)
        return logs

    # ── Team / defense stats ──────────────────────────────────────────────────

    async def get_team_defense_stats(self, team_abbrev: str) -> dict:
        """
        Fetch team summary stats which include goals against, shots against, etc.
        Uses /club-stats/{team}/{season}/{game_type}
        """
        season = _current_nhl_season()
        data = await self._get(f"club-stats/{team_abbrev}/{season}/2")
        return data

    async def get_all_teams_stats(self) -> list[dict]:
        """
        Return standings which include basic defensive metrics (goals against)
        for all 32 teams.
        """
        data = await self._get("standings/now")
        standings = data.get("standings", [])
        return standings

    # ── Schedule ──────────────────────────────────────────────────────────────

    async def get_todays_games(self) -> list[dict]:
        """Return today's NHL schedule."""
        today = date.today().isoformat()
        data = await self._get(f"schedule/{today}")
        game_week = data.get("gameWeek", [])
        if not game_week:
            return []
        # gameWeek is a list of day objects; find today's
        for day in game_week:
            if day.get("date") == today:
                return day.get("games", [])
        return []

    async def get_schedule_on_date(self, game_date: str) -> list[dict]:
        """Return NHL games on a specific date."""
        data = await self._get(f"schedule/{game_date}")
        game_week = data.get("gameWeek", [])
        for day in game_week:
            if day.get("date") == game_date:
                return day.get("games", [])
        return []

    # ── Injuries ──────────────────────────────────────────────────────────────

    async def get_injuries(self) -> list[dict]:
        """
        The NHL API does not have a dedicated injury endpoint.
        Return an empty list — callers should handle gracefully.
        A future enhancement could scrape RotoBaller or similar.
        """
        return []

    # ── Roster helpers ────────────────────────────────────────────────────────

    async def get_team_roster(self, team_abbrev: str, season: Optional[str] = None) -> dict:
        """Return the roster for a team in a given season."""
        season = season or _current_nhl_season()
        data = await self._get(f"roster/{team_abbrev}/{season}")
        return data

    async def get_roster_player_by_name(
        self, team_abbrev: str, name: str
    ) -> Optional[dict]:
        """Find a player on a team roster by partial name match."""
        roster = await self.get_team_roster(team_abbrev)
        all_players = (
            roster.get("forwards", [])
            + roster.get("defensemen", [])
            + roster.get("goalies", [])
        )
        name_lower = name.lower()
        for p in all_players:
            first = p.get("firstName", {}).get("default", "")
            last = p.get("lastName", {}).get("default", "")
            full = f"{first} {last}".lower()
            if name_lower in full or last.lower() == name_lower:
                return p
        return None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _current_nhl_season() -> str:
    """
    Return the current NHL season string e.g. "20242025".
    NHL season runs October → June.
    """
    today = date.today()
    if today.month >= 10:
        return f"{today.year}{today.year + 1}"
    else:
        return f"{today.year - 1}{today.year}"
