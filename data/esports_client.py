"""
data/esports_client.py - Async client for esports stats via Pandascore API.

Supported games: CS2 (CSGO), League of Legends (LoL), Dota 2, Valorant, Overwatch 2.
Pandascore API: https://pandascore.co/

Free tier: 1,000 requests/hour.
"""

import aiohttp
import asyncio
import logging
from typing import Optional
from datetime import date, timedelta

import config

log = logging.getLogger(__name__)

PANDASCORE_BASE = "https://api.pandascore.co"

# Game slug mapping
ESPORTS_GAMES: dict[str, str] = {
    "cs2": "csgo",
    "csgo": "csgo",
    "cs:go": "csgo",
    "cs": "csgo",
    "counter-strike": "csgo",
    "lol": "lol",
    "league of legends": "lol",
    "league": "lol",
    "dota": "dota2",
    "dota 2": "dota2",
    "dota2": "dota2",
    "valorant": "valorant",
    "val": "valorant",
    "overwatch": "ow",
    "ow2": "ow",
    "overwatch 2": "ow",
    "r6": "r6siege",
    "rainbow six": "r6siege",
    "cod": "codmw",
    "call of duty": "codmw",
    "rocket league": "rl",
    "rl": "rl",
}

DEFAULT_GAMES = ["csgo", "lol", "valorant", "dota2"]

# Esports prop types → stat keys by game
ESPORTS_PROPS: dict[str, dict[str, str]] = {
    "csgo": {
        "KILLS": "kills",
        "DEATHS": "deaths",
        "ASSISTS": "assists",
        "KDA": "kda_ratio",
        "HEADSHOTS": "headshots",
        "HEADSHOTPCT": "headshot_percentage",
        "ADR": "adr",
        "HLTV": "hltv_rating",
        "MAPS": "maps_played",
        "ROUNDS": "rounds_played",
    },
    "lol": {
        "KILLS": "kills",
        "DEATHS": "deaths",
        "ASSISTS": "assists",
        "KDA": "kda_ratio",
        "CS": "creep_score",
        "GOLD": "gold_earned",
        "DAMAGE": "total_damage_dealt",
        "VISIONCORE": "vision_score",
        "MAPS": "games_played",
    },
    "valorant": {
        "KILLS": "kills",
        "DEATHS": "deaths",
        "ASSISTS": "assists",
        "ACS": "average_combat_score",
        "ADR": "average_damage_per_round",
        "HEADSHOTS": "headshots",
        "MAPS": "maps_played",
        "ROUNDS": "rounds_played",
    },
    "dota2": {
        "KILLS": "kills",
        "DEATHS": "deaths",
        "ASSISTS": "assists",
        "GPM": "gold_per_minute",
        "XPM": "xp_per_minute",
        "LASTTHITS": "last_hits",
        "MAPS": "games_played",
    },
}

# Common player nicknames
_ESPORTS_NICKNAMES: dict[str, str] = {
    # CS2
    "s1mple": "Oleksandr Kostyliev",
    "niko": "Nikola Kovac",
    "device": "Nicolai Reedtz",
    "zywoo": "Mathieu Herbaut",
    "sh1ro": "Dmitry Sokolov",
    "broky": "Helvijs Saukants",
    "electronic": "Denis Sharipov",
    "frozen": "David Cernansky",
    # LoL
    "faker": "Lee Sang-hyeok",
    "uzi": "Jian Zi-Hao",
    "caps": "Rasmus Borregaard Winther",
    "rookie": "Song Eui-jin",
    "ruler": "Park Jae-hyuk",
    "chovy": "Jeong Ji-hoon",
    "showmaker": "Heo Su",
    "knight": "Zheng Jie",
    "bin": "Chen Ze-Bin",
    # Valorant
    "tenz": "Tyson Ngo",
    "yay": "Jaccob Whiteaker",
    "nats": "Ayaz Akhmetshin",
    "aspas": "Erick Santos",
    "chronicle": "Timofey Khromov",
}


def _normalize_name(raw: str) -> str:
    key = raw.strip().lower()
    if key in _ESPORTS_NICKNAMES:
        return _ESPORTS_NICKNAMES[key]
    return raw.strip()


def resolve_game(raw: str) -> str:
    """Convert user-provided game name to Pandascore game slug."""
    key = raw.strip().lower()
    return ESPORTS_GAMES.get(key, "csgo")


class EsportsClient:
    """Async Pandascore API wrapper for esports data."""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._api_key = getattr(config, "PANDASCORE_API_KEY", "")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=config.HTTP_TIMEOUT)
            headers = {"Accept": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._session = aiohttp.ClientSession(headers=headers, timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: Optional[dict] = None) -> dict | list:
        session = await self._get_session()
        url = f"{PANDASCORE_BASE}/{path}"
        p = {"token": self._api_key, **(params or {})} if self._api_key else (params or {})
        try:
            async with session.get(url, params=p) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientResponseError as exc:
            log.warning("Pandascore HTTP %s for %s: %s", exc.status, url, exc.message)
            return []
        except asyncio.TimeoutError:
            log.warning("Pandascore timeout for %s", url)
            return []
        except Exception as exc:  # noqa: BLE001
            log.error("Pandascore error for %s: %s", url, exc)
            return []

    # ── Player search ──────────────────────────────────────────────────────────

    async def get_player(self, name: str, game: str = "csgo") -> Optional[dict]:
        """Search for an esports player by name/handle."""
        resolved = _normalize_name(name)
        data = await self._get(f"{game}/players", {"search[name]": resolved, "per_page": 5})
        players = data if isinstance(data, list) else []

        if not players and resolved != name:
            data = await self._get(f"{game}/players", {"search[name]": name, "per_page": 5})
            players = data if isinstance(data, list) else []

        if not players:
            return None

        name_lower = resolved.lower()
        for p in players:
            handle = p.get("name", "").lower()
            full = f"{p.get('first_name', '')} {p.get('last_name', '')}".lower()
            if name_lower in handle or name_lower in full:
                return p
        return players[0]

    # ── Match history / stats ─────────────────────────────────────────────────

    async def get_player_stats(self, player_id: int, game: str = "csgo") -> list[dict]:
        """
        Return recent match stats for a player.
        Returns list of per-match stat dicts.
        """
        # Get recent completed matches
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=90)).isoformat()

        data = await self._get(
            f"{game}/players/{player_id}/stats",
            {"from": start, "to": end},
        )
        if isinstance(data, dict):
            return data.get("results", [])
        return []

    async def get_player_recent_matches(
        self, player_id: int, game: str = "csgo", limit: int = 20
    ) -> list[dict]:
        """Return recent matches a player participated in."""
        data = await self._get(
            f"{game}/players/{player_id}/matches",
            {"sort": "-scheduled_at", "per_page": limit, "filter[status]": "finished"},
        )
        return data if isinstance(data, list) else []

    # ── Team stats ─────────────────────────────────────────────────────────────

    async def get_team(self, name: str, game: str = "csgo") -> Optional[dict]:
        """Search for an esports team."""
        data = await self._get(f"{game}/teams", {"search[name]": name, "per_page": 5})
        teams = data if isinstance(data, list) else []
        if not teams:
            return None
        name_lower = name.lower()
        for t in teams:
            if name_lower in t.get("name", "").lower():
                return t
        return teams[0]

    async def get_team_stats(self, team_id: int, game: str = "csgo") -> dict:
        """Return team stats for a given game."""
        data = await self._get(f"{game}/teams/{team_id}/stats")
        return data if isinstance(data, dict) else {}

    # ── Schedule / upcoming matches ────────────────────────────────────────────

    async def get_todays_matches(self, game: Optional[str] = None) -> list[dict]:
        """Return today's esports matches across specified or all games."""
        games = [game] if game else DEFAULT_GAMES
        today = date.today().isoformat()
        all_matches = []
        for g in games:
            data = await self._get(
                f"{g}/matches",
                {
                    "filter[begin_at]": today,
                    "filter[status]": "running,not_started",
                    "sort": "begin_at",
                    "per_page": 10,
                },
            )
            matches = data if isinstance(data, list) else []
            for m in matches:
                m["_game"] = g
            all_matches.extend(matches)
        return all_matches

    async def get_upcoming_tournaments(self, game: str = "csgo") -> list[dict]:
        """Return upcoming / in-progress tournaments."""
        data = await self._get(
            f"{game}/tournaments",
            {"filter[prizepool_not_null]": True, "sort": "begin_at", "per_page": 10},
        )
        return data if isinstance(data, list) else []

    # ── H2H ───────────────────────────────────────────────────────────────────

    async def get_team_h2h(
        self, team1_id: int, team2_id: int, game: str = "csgo"
    ) -> list[dict]:
        """Return past matches between two teams."""
        data = await self._get(
            f"{game}/matches",
            {
                "filter[opponent_id]": f"{team1_id},{team2_id}",
                "filter[status]": "finished",
                "sort": "-scheduled_at",
                "per_page": 10,
            },
        )
        return data if isinstance(data, list) else []
