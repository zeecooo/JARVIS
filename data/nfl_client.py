"""
data/nfl_client.py - Async client for NFL stats via ESPN's public API.

ESPN endpoints (no key required for basic data):
  site.api.espn.com/apis/site/v2/sports/football/nfl/...
"""

import aiohttp
import asyncio
import logging
from typing import Optional
from datetime import date

import config

log = logging.getLogger(__name__)

ESPN_NFL_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
ESPN_CORE_BASE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"

# ── Nickname / abbreviation map ───────────────────────────────────────────────
_NFL_NICKNAMES: dict[str, str] = {
    "mahomes": "Patrick Mahomes",
    "pm15": "Patrick Mahomes",
    "kelce": "Travis Kelce",
    "tk": "Travis Kelce",
    "lamar": "Lamar Jackson",
    "jalen hurts": "Jalen Hurts",
    "hurts": "Jalen Hurts",
    "jefferson": "Justin Jefferson",
    "jj": "Justin Jefferson",
    "cmc": "Christian McCaffrey",
    "mccaffrey": "Christian McCaffrey",
    "tyreek": "Tyreek Hill",
    "cheetah": "Tyreek Hill",
    "hill": "Tyreek Hill",
    "stafford": "Matthew Stafford",
    "burrow": "Joe Burrow",
    "joey b": "Joe Burrow",
    "chase": "Ja'Marr Chase",
    "diggs": "Stefon Diggs",
    "henry": "Derrick Henry",
    "dk": "DK Metcalf",
    "metcalf": "DK Metcalf",
    "waddle": "Jaylen Waddle",
    "swift": "D'Andre Swift",
    "lamb": "CeeDee Lamb",
    "aj brown": "A.J. Brown",
    "saquon": "Saquon Barkley",
    "barkley": "Saquon Barkley",
    "ceedee": "CeeDee Lamb",
    "allen": "Josh Allen",
    "dak": "Dak Prescott",
    "prescott": "Dak Prescott",
    "micah": "Micah Parsons",
    "parsons": "Micah Parsons",
}

# Map internal prop names to ESPN stat keys
NFL_PROP_TO_ESPN: dict[str, str] = {
    "PASSYDS": "passingYards",
    "PASSTD": "passingTouchdowns",
    "RUSHYDS": "rushingYards",
    "RUSHATTS": "rushingAttempts",
    "RECYDS": "receivingYards",
    "REC": "receptions",
    "TARGETS": "receivingTargets",
    "INT": "interceptions",
    "SACKS": "sacks",
    "PASSINGYARDS": "passingYards",
    "RUSHINGYARDS": "rushingYards",
    "RECEIVINGYARDS": "receivingYards",
    "RECEPTIONS": "receptions",
    "TOUCHDOWNS": "passingTouchdowns",
    "TACKLES": "totalTackles",
}

# NFL team abbreviation map
NFL_TEAMS: dict[str, str] = {
    "ARI": "Arizona Cardinals",
    "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers",
    "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals",
    "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos",
    "DET": "Detroit Lions",
    "GB": "Green Bay Packers",
    "HOU": "Houston Texans",
    "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs",
    "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams",
    "LV": "Las Vegas Raiders",
    "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings",
    "NE": "New England Patriots",
    "NO": "New Orleans Saints",
    "NYG": "New York Giants",
    "NYJ": "New York Jets",
    "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks",
    "SF": "San Francisco 49ers",
    "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans",
    "WSH": "Washington Commanders",
}


def _normalize_name(raw: str) -> str:
    key = raw.strip().lower()
    if key in _NFL_NICKNAMES:
        return _NFL_NICKNAMES[key]
    # Handle "P. Mahomes" abbreviation style
    parts = raw.strip().split()
    if len(parts) == 2 and len(parts[0]) <= 2 and parts[0].endswith("."):
        return parts[1]
    return raw.strip()


class NFLClient:
    """Async ESPN NFL API wrapper."""

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

    async def _get(self, url: str, params: Optional[dict] = None) -> dict | list:
        session = await self._get_session()
        try:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientResponseError as exc:
            log.warning("ESPN NFL HTTP %s for %s: %s", exc.status, url, exc.message)
            return {}
        except asyncio.TimeoutError:
            log.warning("ESPN NFL timeout for %s", url)
            return {}
        except Exception as exc:  # noqa: BLE001
            log.error("ESPN NFL error for %s: %s", url, exc)
            return {}

    # ── Player search ──────────────────────────────────────────────────────────

    async def get_player(self, name: str) -> Optional[dict]:
        """Search for an NFL player by name."""
        resolved = _normalize_name(name)
        url = f"{ESPN_NFL_BASE}/athletes"
        data = await self._get(url, {"limit": 10, "search": resolved})
        if isinstance(data, dict):
            athletes = data.get("items", []) or data.get("athletes", [])
        else:
            athletes = []

        if not athletes and resolved != name:
            data = await self._get(url, {"limit": 10, "search": name})
            athletes = data.get("items", []) if isinstance(data, dict) else []

        if not athletes:
            return None

        # Best match by name similarity
        name_lower = resolved.lower()
        for a in athletes:
            full = a.get("fullName", a.get("displayName", "")).lower()
            if name_lower in full or full.startswith(name_lower.split()[-1].lower()):
                return a
        return athletes[0]

    # ── Game logs ──────────────────────────────────────────────────────────────

    async def get_player_game_logs(
        self, player_id: str, season: Optional[str] = None
    ) -> list[dict]:
        """
        Return recent game logs for a player from ESPN.
        Returns list of dicts with stat fields.
        """
        yr = season or str(date.today().year if date.today().month >= 9 else date.today().year - 1)
        url = f"{ESPN_CORE_BASE}/athletes/{player_id}/statisticslog"
        data = await self._get(url)

        # ESPN returns nested $ref links — try direct stats endpoint
        url2 = f"{ESPN_NFL_BASE}/athletes/{player_id}/stats"
        data2 = await self._get(url2)

        # Build synthetic game logs from splits
        logs = []
        if isinstance(data2, dict):
            splits = data2.get("splits", {}).get("categories", [])
            for cat in splits:
                entries = cat.get("entries", []) or []
                for entry in entries:
                    stats = {}
                    for stat in entry.get("stats", []):
                        stats[stat.get("name", "")] = stat.get("value")
                    if stats:
                        logs.append({
                            "gameDate": entry.get("displayDate", ""),
                            "opponent": entry.get("opponent", {}).get("displayName", ""),
                            "isHome": entry.get("homeAway", "away") == "home",
                            **stats,
                        })

        # Sort newest first
        logs.sort(key=lambda x: x.get("gameDate", ""), reverse=True)
        return logs[:20]

    # ── Team defensive stats ───────────────────────────────────────────────────

    async def get_all_teams_stats(self) -> list[dict]:
        """Return all 32 NFL teams with defensive stats."""
        url = f"{ESPN_NFL_BASE}/teams"
        data = await self._get(url, {"limit": 32})
        if isinstance(data, dict):
            return data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
        return []

    async def get_team_defense_stats(self, team_abbrev: str) -> dict:
        """Fetch a specific team's defensive rankings."""
        url = f"{ESPN_NFL_BASE}/teams/{team_abbrev}/statistics"
        return await self._get(url) or {}

    # ── Schedule ───────────────────────────────────────────────────────────────

    async def get_todays_games(self) -> list[dict]:
        """Return today's NFL scoreboard games."""
        today = date.today().strftime("%Y%m%d")
        url = f"{ESPN_NFL_BASE}/scoreboard"
        data = await self._get(url, {"dates": today})
        if isinstance(data, dict):
            return data.get("events", [])
        return []

    async def get_upcoming_games(self) -> list[dict]:
        """Return this week's NFL schedule."""
        url = f"{ESPN_NFL_BASE}/scoreboard"
        data = await self._get(url)
        if isinstance(data, dict):
            return data.get("events", [])
        return []

    # ── Injuries ───────────────────────────────────────────────────────────────

    async def get_injuries(self) -> list[dict]:
        """Return current NFL injury report."""
        url = f"{ESPN_NFL_BASE}/injuries"
        data = await self._get(url)
        if isinstance(data, dict):
            return data.get("items", [])
        return []
