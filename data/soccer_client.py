"""
data/soccer_client.py - Async client for soccer stats via ESPN public API.

Supports multiple leagues: EPL, La Liga, MLS, UCL, Ligue 1, Bundesliga, Serie A.
ESPN base: site.api.espn.com/apis/site/v2/sports/soccer/{league}/
"""

import aiohttp
import asyncio
import logging
from typing import Optional
from datetime import date

import config

log = logging.getLogger(__name__)

ESPN_SOCCER_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# League slug mapping
SOCCER_LEAGUES: dict[str, str] = {
    "epl": "eng.1",
    "premier league": "eng.1",
    "english premier league": "eng.1",
    "la liga": "esp.1",
    "laliga": "esp.1",
    "bundesliga": "ger.1",
    "serie a": "ita.1",
    "ligue 1": "fra.1",
    "mls": "usa.1",
    "champions league": "UEFA.CHAMPIONS",
    "ucl": "UEFA.CHAMPIONS",
    "europa league": "UEFA.EUROPA",
    "uel": "UEFA.EUROPA",
    "eredivisie": "ned.1",
    "primeira liga": "por.1",
    "liga mx": "mex.1",
}

DEFAULT_LEAGUES = ["eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "usa.1", "UEFA.CHAMPIONS"]

# Soccer prop types → stat keys
SOCCER_PROP_TO_KEY: dict[str, str] = {
    "GOALS": "goals",
    "SHOTS": "shots",
    "SHOTSONTARGET": "shotsOnTarget",
    "ASSISTS": "goalAssists",
    "CARDS": "yellowCards",
    "YELLOWCARDS": "yellowCards",
    "REDCARDS": "redCards",
    "FOULS": "foulsCommitted",
    "TACKLES": "tackles",
    "SAVES": "saves",
    "CORNERS": "cornerKicks",
    "PASSES": "passes",
    "KEYPASS": "keyPasses",
    "XGOALS": "expectedGoals",
    "XG": "expectedGoals",
}

# Common player nicknames
_SOCCER_NICKNAMES: dict[str, str] = {
    "messi": "Lionel Messi",
    "leo": "Lionel Messi",
    "cr7": "Cristiano Ronaldo",
    "ronaldo": "Cristiano Ronaldo",
    "mbappe": "Kylian Mbappe",
    "kylian": "Kylian Mbappe",
    "neymar": "Neymar Jr",
    "haaland": "Erling Haaland",
    "salah": "Mohamed Salah",
    "mo salah": "Mohamed Salah",
    "van dijk": "Virgil van Dijk",
    "de bruyne": "Kevin De Bruyne",
    "kdb": "Kevin De Bruyne",
    "benzema": "Karim Benzema",
    "lewandowski": "Robert Lewandowski",
    "lewa": "Robert Lewandowski",
    "kane": "Harry Kane",
    "bellingham": "Jude Bellingham",
    "vinicius": "Vinicius Junior",
    "vini jr": "Vinicius Junior",
    "pedri": "Pedro Gonzalez",
    "gavi": "Pablo Martin Paez Gavira",
    "modric": "Luka Modric",
    "kroos": "Toni Kroos",
    "rashford": "Marcus Rashford",
    "saka": "Bukayo Saka",
    "odegaard": "Martin Odegaard",
    "son": "Son Heung-min",
    "pulisic": "Christian Pulisic",
    "reyna": "Giovanni Reyna",
    "weah": "Timothy Weah",
}


def _normalize_name(raw: str) -> str:
    key = raw.strip().lower()
    if key in _SOCCER_NICKNAMES:
        return _SOCCER_NICKNAMES[key]
    return raw.strip()


def resolve_league(raw: str) -> str:
    """Convert user-provided league name to ESPN league slug."""
    key = raw.strip().lower()
    return SOCCER_LEAGUES.get(key, "eng.1")  # Default to EPL


class SoccerClient:
    """Async ESPN Soccer API wrapper."""

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
            log.warning("ESPN Soccer HTTP %s for %s: %s", exc.status, url, exc.message)
            return {}
        except asyncio.TimeoutError:
            log.warning("ESPN Soccer timeout for %s", url)
            return {}
        except Exception as exc:  # noqa: BLE001
            log.error("ESPN Soccer error for %s: %s", url, exc)
            return {}

    # ── Player search ──────────────────────────────────────────────────────────

    async def get_player(self, name: str, league: str = "eng.1") -> Optional[dict]:
        """Search for a soccer player by name."""
        resolved = _normalize_name(name)
        url = f"{ESPN_SOCCER_BASE}/{league}/athletes"
        data = await self._get(url, {"limit": 10, "search": resolved})
        athletes = []
        if isinstance(data, dict):
            athletes = data.get("items", []) or data.get("athletes", [])

        if not athletes and resolved != name:
            data = await self._get(url, {"limit": 10, "search": name})
            athletes = data.get("items", []) if isinstance(data, dict) else []

        if not athletes:
            # Try other leagues
            for lg in DEFAULT_LEAGUES[:3]:
                if lg == league:
                    continue
                url2 = f"{ESPN_SOCCER_BASE}/{lg}/athletes"
                data2 = await self._get(url2, {"limit": 5, "search": resolved})
                athletes = data2.get("items", []) if isinstance(data2, dict) else []
                if athletes:
                    break

        if not athletes:
            return None

        name_lower = resolved.lower()
        for a in athletes:
            full = a.get("fullName", a.get("displayName", "")).lower()
            if name_lower in full:
                return a
        return athletes[0]

    # ── Game logs ──────────────────────────────────────────────────────────────

    async def get_player_game_logs(
        self, player_id: str, league: str = "eng.1"
    ) -> list[dict]:
        """Return recent match stats for a player."""
        url = f"{ESPN_SOCCER_BASE}/{league}/athletes/{player_id}/stats"
        data = await self._get(url)
        logs = []
        if isinstance(data, dict):
            splits = data.get("splits", {}).get("categories", [])
            for cat in splits:
                for entry in cat.get("entries", []):
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
        logs.sort(key=lambda x: x.get("gameDate", ""), reverse=True)
        return logs[:20]

    # ── Schedule ───────────────────────────────────────────────────────────────

    async def get_todays_games(self, leagues: Optional[list[str]] = None) -> list[dict]:
        """Return today's matches across specified leagues."""
        target_leagues = leagues or DEFAULT_LEAGUES[:4]
        today = date.today().strftime("%Y%m%d")
        all_games = []
        for lg in target_leagues:
            url = f"{ESPN_SOCCER_BASE}/{lg}/scoreboard"
            data = await self._get(url, {"dates": today})
            if isinstance(data, dict):
                events = data.get("events", [])
                for e in events:
                    e["league"] = lg
                all_games.extend(events)
        return all_games

    async def get_upcoming_games(self, league: str = "eng.1") -> list[dict]:
        """Return upcoming matches for a league."""
        url = f"{ESPN_SOCCER_BASE}/{league}/scoreboard"
        data = await self._get(url)
        return data.get("events", []) if isinstance(data, dict) else []

    # ── Team stats ─────────────────────────────────────────────────────────────

    async def get_team_stats(self, team_id: str, league: str = "eng.1") -> dict:
        url = f"{ESPN_SOCCER_BASE}/{league}/teams/{team_id}/statistics"
        return await self._get(url) or {}

    # ── Standings ──────────────────────────────────────────────────────────────

    async def get_standings(self, league: str = "eng.1") -> list[dict]:
        url = f"{ESPN_SOCCER_BASE}/{league}/standings"
        data = await self._get(url)
        if isinstance(data, dict):
            groups = data.get("standings", {}).get("entries", [])
            return groups
        return []
