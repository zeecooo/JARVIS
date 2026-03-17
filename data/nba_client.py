"""
data/nba_client.py - Async client for the BallDontLie v2 NBA API.

All public methods return plain Python dicts/lists so the rest of the
codebase has no dependency on this HTTP layer.

BallDontLie API docs: https://www.balldontlie.io/
"""

import aiohttp
import asyncio
import logging
from typing import Optional
from datetime import date

import config

log = logging.getLogger(__name__)

# ── Common nickname/abbreviation map ─────────────────────────────────────────
# Entries are resolved *before* the API search so a single canonical
# full name reaches the search endpoint.
_NICKNAME_MAP: dict[str, str] = {
    # LeBron / Lakers
    "lebron": "LeBron James",
    "bron": "LeBron James",
    "king james": "LeBron James",
    # Kevin Durant
    "kd": "Kevin Durant",
    "slim reaper": "Kevin Durant",
    # Anthony Davis
    "ad": "Anthony Davis",
    "the brow": "Anthony Davis",
    # Stephen Curry
    "steph": "Stephen Curry",
    "chef curry": "Stephen Curry",
    # Giannis Antetokounmpo
    "giannis": "Giannis Antetokounmpo",
    "greek freak": "Giannis Antetokounmpo",
    # Nikola Jokic
    "jokic": "Nikola Jokic",
    "joker": "Nikola Jokic",
    # Luka Doncic
    "luka": "Luka Doncic",
    "luka magic": "Luka Doncic",
    # Jayson Tatum
    "tatum": "Jayson Tatum",
    "jt": "Jayson Tatum",
    # Joel Embiid
    "embiid": "Joel Embiid",
    "the process": "Joel Embiid",
    # Damian Lillard
    "dame": "Damian Lillard",
    "dame time": "Damian Lillard",
    # James Harden
    "the beard": "James Harden",
    # Kawhi Leonard
    "kawhi": "Kawhi Leonard",
    "the claw": "Kawhi Leonard",
    # Trae Young
    "trae": "Trae Young",
    "ice trae": "Trae Young",
    # Devin Booker
    "book": "Devin Booker",
    # Ja Morant
    "ja": "Ja Morant",
    # Donovan Mitchell
    "spida": "Donovan Mitchell",
    # Paul George
    "pg": "Paul George",
    "pg13": "Paul George",
    # Tyrese Haliburton
    "hali": "Tyrese Haliburton",
    # Shai Gilgeous-Alexander
    "sga": "Shai Gilgeous-Alexander",
    "shai": "Shai Gilgeous-Alexander",
    # Cade Cunningham
    "cade": "Cade Cunningham",
    # Evan Mobley
    "evan": "Evan Mobley",
    # Bam Adebayo
    "bam": "Bam Adebayo",
    # Victor Wembanyama
    "wemby": "Victor Wembanyama",
    "wembanyama": "Victor Wembanyama",
}


def _normalize_name(raw: str) -> str:
    """
    Resolve a nickname or abbreviated name to a best-guess full name.

    Handles patterns like:
      - 'AD' → 'Anthony Davis'
      - 'T. Harris' → 'Tobias Harris' (partial, still needs API search)
      - 'LeBron' → 'LeBron James'
    """
    key = raw.strip().lower()
    if key in _NICKNAME_MAP:
        return _NICKNAME_MAP[key]

    # Handle "T. Harris" style abbreviations — return just the last name so
    # the search endpoint can find the right player in context.
    parts = raw.strip().split()
    if len(parts) == 2 and len(parts[0]) <= 2 and parts[0].endswith("."):
        # Return the last name; the caller is expected to pass team context
        return parts[1]

    return raw.strip()


class NBAClient:
    """Async BallDontLie v2 API wrapper."""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._headers = {
            "Authorization": config.BALLDONTLIE_API_KEY,
            "Accept": "application/json",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=config.HTTP_TIMEOUT)
            self._session = aiohttp.ClientSession(
                headers=self._headers, timeout=timeout
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        """Issue a GET request and return the parsed JSON dict."""
        session = await self._get_session()
        url = f"{config.BALLDONTLIE_BASE}/{path}"
        try:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientResponseError as exc:
            log.warning("BallDontLie HTTP %s for %s: %s", exc.status, url, exc.message)
            return {}
        except asyncio.TimeoutError:
            log.warning("BallDontLie timeout for %s", url)
            return {}
        except Exception as exc:  # noqa: BLE001
            log.error("BallDontLie unexpected error for %s: %s", url, exc)
            return {}

    # ── Player lookup ─────────────────────────────────────────────────────────

    async def get_player(self, name: str) -> Optional[dict]:
        """
        Search for a player by name (handles nicknames & abbreviations).
        Returns the best-matching player dict or None.
        """
        resolved = _normalize_name(name)
        data = await self._get("players", {"search": resolved, "per_page": 5})
        players = data.get("data", [])
        if not players:
            # Fallback: search with raw name in case resolution was wrong
            data = await self._get("players", {"search": name, "per_page": 5})
            players = data.get("data", [])

        if not players:
            return None

        # Prefer active players and exact first/last name matches
        resolved_lower = resolved.lower()
        for player in players:
            full = f"{player.get('first_name', '')} {player.get('last_name', '')}".lower()
            if resolved_lower in full or full in resolved_lower:
                return player

        # Return the first result as fallback
        return players[0]

    async def get_player_by_id(self, player_id: int) -> Optional[dict]:
        """Fetch a single player record by numeric ID."""
        data = await self._get(f"players/{player_id}")
        return data if data else None

    # ── Game logs ─────────────────────────────────────────────────────────────

    async def get_player_game_logs(
        self, player_id: int, last_n: int = 20
    ) -> list[dict]:
        """
        Return up to `last_n` recent regular-season game logs for a player,
        sorted newest-first.

        Each log includes: game date, points, rebounds, assists, steals,
        blocks, three_pointers_made, min (minutes), and opponent info.
        """
        # BallDontLie v2 supports /stats?player_ids[]=X&per_page=N
        data = await self._get(
            "stats",
            {
                "player_ids[]": player_id,
                "per_page": last_n,
                "seasons[]": _current_season(),
            },
        )
        logs = data.get("data", [])

        # Sort newest first based on game.date
        logs.sort(key=lambda x: x.get("game", {}).get("date", ""), reverse=True)
        return logs[:last_n]

    async def get_h2h_games(
        self,
        player_id: int,
        opponent_team_id: int,
        last_n: int = 10,
    ) -> list[dict]:
        """
        Fetch the last `last_n` games a player has played *against* a specific
        opponent team.
        """
        # Pull a larger window then filter by opponent
        data = await self._get(
            "stats",
            {
                "player_ids[]": player_id,
                "per_page": 100,
                "seasons[]": _current_season(),
            },
        )
        logs = data.get("data", [])

        h2h = [
            log for log in logs
            if (
                log.get("game", {}).get("home_team_id") == opponent_team_id
                or log.get("game", {}).get("visitor_team_id") == opponent_team_id
            )
        ]
        h2h.sort(key=lambda x: x.get("game", {}).get("date", ""), reverse=True)
        return h2h[:last_n]

    # ── Team stats ────────────────────────────────────────────────────────────

    async def get_team_stats(self) -> list[dict]:
        """
        Return season-average team stats (used for defensive ranking).
        Each entry includes team info and aggregated defensive metrics.
        """
        data = await self._get(
            "season_averages",
            {"season": _current_season_year()},
        )
        # BallDontLie also has a /teams endpoint for basic info
        teams_data = await self._get("teams", {"per_page": 30})
        return teams_data.get("data", [])

    async def get_teams(self) -> list[dict]:
        """Return all NBA teams."""
        data = await self._get("teams", {"per_page": 30})
        return data.get("data", [])

    async def get_team_defensive_stats(self) -> list[dict]:
        """
        Approximate defensive rankings by pulling opponent stats per team.
        Returns a list of team dicts augmented with allowed_pts, allowed_reb,
        allowed_ast averages for ranking purposes.
        """
        # Pull all game stats for the current season in bulk
        # (BallDontLie v2 allows filtering by season)
        data = await self._get(
            "stats",
            {"seasons[]": _current_season(), "per_page": 100},
        )
        stats = data.get("data", [])

        # Aggregate points/reb/ast/threes allowed per team (as the defensive team)
        from collections import defaultdict

        team_totals: dict[int, dict] = defaultdict(
            lambda: {
                "games": 0,
                "pts_allowed": 0.0,
                "reb_allowed": 0.0,
                "ast_allowed": 0.0,
                "threes_allowed": 0.0,
                "blk_allowed": 0.0,
                "stl_allowed": 0.0,
            }
        )

        for stat in stats:
            game = stat.get("game", {})
            # The "opponent" depends on whether the player's team is home or away
            player_team_id = stat.get("team", {}).get("id")
            home_id = game.get("home_team_id")
            away_id = game.get("visitor_team_id")
            opp_id = away_id if player_team_id == home_id else home_id
            if opp_id:
                team_totals[opp_id]["games"] += 1
                team_totals[opp_id]["pts_allowed"] += stat.get("pts", 0) or 0
                team_totals[opp_id]["reb_allowed"] += stat.get("reb", 0) or 0
                team_totals[opp_id]["ast_allowed"] += stat.get("ast", 0) or 0
                team_totals[opp_id]["threes_allowed"] += (
                    stat.get("fg3m", 0) or 0
                )
                team_totals[opp_id]["blk_allowed"] += stat.get("blk", 0) or 0
                team_totals[opp_id]["stl_allowed"] += stat.get("stl", 0) or 0

        result = []
        for team_id, totals in team_totals.items():
            g = max(totals["games"], 1)
            result.append(
                {
                    "team_id": team_id,
                    "avg_pts_allowed": round(totals["pts_allowed"] / g, 2),
                    "avg_reb_allowed": round(totals["reb_allowed"] / g, 2),
                    "avg_ast_allowed": round(totals["ast_allowed"] / g, 2),
                    "avg_threes_allowed": round(totals["threes_allowed"] / g, 2),
                    "avg_blk_allowed": round(totals["blk_allowed"] / g, 2),
                    "avg_stl_allowed": round(totals["stl_allowed"] / g, 2),
                    "games_sample": g,
                }
            )
        return result

    # ── Injuries ──────────────────────────────────────────────────────────────

    async def get_injuries(self) -> list[dict]:
        """
        Return current injury report entries.
        BallDontLie v2 exposes /player_injuries.
        """
        data = await self._get("player_injuries", {"per_page": 100})
        return data.get("data", [])

    # ── Schedule ──────────────────────────────────────────────────────────────

    async def get_todays_games(self) -> list[dict]:
        """Return today's NBA game schedule."""
        today = date.today().isoformat()
        data = await self._get("games", {"dates[]": today, "per_page": 15})
        return data.get("data", [])

    async def get_games_on_date(self, game_date: str) -> list[dict]:
        """Return games on a specific ISO date string."""
        data = await self._get("games", {"dates[]": game_date, "per_page": 15})
        return data.get("data", [])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _current_season_year() -> int:
    """Return the *start* year of the current NBA season (e.g. 2024 for 2024-25)."""
    today = date.today()
    # NBA season starts in October; if we're before October use last year
    return today.year if today.month >= 10 else today.year - 1


def _current_season() -> int:
    """Alias for _current_season_year() — BallDontLie uses the start year."""
    return _current_season_year()
