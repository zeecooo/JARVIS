"""
data/tennis_client.py - Async client for tennis stats via ESPN + ATP/WTA public endpoints.

ESPN tennis base: site.api.espn.com/apis/site/v2/sports/tennis
Also uses rankings from live.atptour.com and wtatennis.com where accessible.
"""

import aiohttp
import asyncio
import logging
from typing import Optional
from datetime import date

import config

log = logging.getLogger(__name__)

ESPN_TENNIS_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis"

# Tournament slug mapping
TENNIS_TOURS: dict[str, str] = {
    "atp": "atp",
    "wta": "wta",
    "mens": "atp",
    "womens": "wta",
    "us open": "atp",
    "wimbledon": "atp",
    "french open": "atp",
    "roland garros": "atp",
    "australian open": "atp",
    "miami open": "atp",
    "indian wells": "atp",
}

# Tennis prop types → stat keys
TENNIS_PROP_TO_KEY: dict[str, str] = {
    "ACES": "aces",
    "DOUBLEFAULTS": "doubleFaults",
    "DOUBLE FAULTS": "doubleFaults",
    "SERVICEGAMES": "serviceGamesWon",
    "SETS": "setsWon",
    "GAMES": "totalGames",
    "BREAKPOINTS": "breakPointsConverted",
    "FIRSTSERVEPCT": "firstServePct",
    "WINNERSCOUNT": "winners",
    "UNFORCEDERRORS": "unforcedErrors",
    "TOTALGAMES": "totalGames",
}

# Common player nicknames
_TENNIS_NICKNAMES: dict[str, str] = {
    "djokovic": "Novak Djokovic",
    "nole": "Novak Djokovic",
    "federer": "Roger Federer",
    "fed": "Roger Federer",
    "nadal": "Rafael Nadal",
    "rafa": "Rafael Nadal",
    "alcaraz": "Carlos Alcaraz",
    "carlitos": "Carlos Alcaraz",
    "sinner": "Jannik Sinner",
    "jannik": "Jannik Sinner",
    "zverev": "Alexander Zverev",
    "sascha": "Alexander Zverev",
    "medvedev": "Daniil Medvedev",
    "tsitsipas": "Stefanos Tsitsipas",
    "fritz": "Taylor Fritz",
    "tiafoe": "Frances Tiafoe",
    "swiatek": "Iga Swiatek",
    "iga": "Iga Swiatek",
    "sabalenka": "Aryna Sabalenka",
    "aryna": "Aryna Sabalenka",
    "gauff": "Coco Gauff",
    "coco": "Coco Gauff",
    "rybakina": "Elena Rybakina",
    "kvitova": "Petra Kvitova",
    "pegula": "Jessica Pegula",
    "wozniacki": "Caroline Wozniacki",
    "kerber": "Angelique Kerber",
    "halep": "Simona Halep",
    "serena": "Serena Williams",
    "venus": "Venus Williams",
    "kyrgios": "Nick Kyrgios",
    "nick k": "Nick Kyrgios",
    "berrettini": "Matteo Berrettini",
    "ruud": "Casper Ruud",
    "rune": "Holger Rune",
    "draper": "Jack Draper",
    "paul": "Tommy Paul",
    "de minaur": "Alex de Minaur",
    "demon": "Alex de Minaur",
}


def _normalize_name(raw: str) -> str:
    key = raw.strip().lower()
    if key in _TENNIS_NICKNAMES:
        return _TENNIS_NICKNAMES[key]
    return raw.strip()


class TennisClient:
    """Async ESPN Tennis API wrapper."""

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
            log.warning("Tennis API HTTP %s for %s: %s", exc.status, url, exc.message)
            return {}
        except asyncio.TimeoutError:
            log.warning("Tennis API timeout for %s", url)
            return {}
        except Exception as exc:  # noqa: BLE001
            log.error("Tennis API error for %s: %s", url, exc)
            return {}

    # ── Player search ──────────────────────────────────────────────────────────

    async def get_player(self, name: str, tour: str = "atp") -> Optional[dict]:
        """Search for a tennis player by name."""
        resolved = _normalize_name(name)
        url = f"{ESPN_TENNIS_BASE}/{tour}/athletes"
        data = await self._get(url, {"limit": 10, "search": resolved})
        athletes = data.get("items", []) if isinstance(data, dict) else []

        if not athletes and resolved != name:
            data = await self._get(url, {"limit": 10, "search": name})
            athletes = data.get("items", []) if isinstance(data, dict) else []

        # Try WTA if ATP fails
        if not athletes and tour == "atp":
            url2 = f"{ESPN_TENNIS_BASE}/wta/athletes"
            data2 = await self._get(url2, {"limit": 10, "search": resolved})
            athletes = data2.get("items", []) if isinstance(data2, dict) else []

        if not athletes:
            return None

        name_lower = resolved.lower()
        for a in athletes:
            full = a.get("fullName", a.get("displayName", "")).lower()
            if name_lower in full:
                return a
        return athletes[0]

    # ── Match history / game logs ──────────────────────────────────────────────

    async def get_player_match_history(
        self, player_id: str, tour: str = "atp"
    ) -> list[dict]:
        """Return recent match results with stats for a player."""
        url = f"{ESPN_TENNIS_BASE}/{tour}/athletes/{player_id}/stats"
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
                            "surface": entry.get("surface", "hard"),
                            "tournament": entry.get("event", {}).get("shortName", ""),
                            "result": entry.get("result", ""),
                            **stats,
                        })
        logs.sort(key=lambda x: x.get("gameDate", ""), reverse=True)
        return logs[:20]

    # ── Player ranking ─────────────────────────────────────────────────────────

    async def get_rankings(self, tour: str = "atp", limit: int = 50) -> list[dict]:
        """Return current ATP/WTA rankings."""
        url = f"{ESPN_TENNIS_BASE}/{tour}/rankings"
        data = await self._get(url, {"limit": limit})
        if isinstance(data, dict):
            return data.get("rankings", data.get("items", []))
        return []

    # ── Schedule ───────────────────────────────────────────────────────────────

    async def get_todays_matches(self, tour: str = "atp") -> list[dict]:
        """Return today's tennis matches."""
        today = date.today().strftime("%Y%m%d")
        url = f"{ESPN_TENNIS_BASE}/{tour}/scoreboard"
        data = await self._get(url, {"dates": today})
        if isinstance(data, dict):
            return data.get("events", [])
        return []

    async def get_upcoming_matches(self, tour: str = "atp") -> list[dict]:
        """Return upcoming tournament matches."""
        url = f"{ESPN_TENNIS_BASE}/{tour}/scoreboard"
        data = await self._get(url)
        if isinstance(data, dict):
            return data.get("events", [])
        return []

    # ── Head-to-head ──────────────────────────────────────────────────────────

    async def get_h2h(self, player1_id: str, player2_id: str, tour: str = "atp") -> dict:
        """Fetch H2H record between two players."""
        url = f"{ESPN_TENNIS_BASE}/{tour}/athletes/{player1_id}/headtohead/{player2_id}"
        data = await self._get(url)
        return data if isinstance(data, dict) else {}
