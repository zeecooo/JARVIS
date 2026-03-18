"""
data/nba_client.py - Async NBA client using stats.nba.com (no API key required).

Replaces the BallDontLie v2 client. All public methods return the same
dict/list shapes as before so no other files need changes.

NBA.com returns data in a resultSets format:
  {"resultSets": [{"name": "...", "headers": [...], "rowSet": [[...]]}]}

We convert each rowSet row to a lowercase-keyed dict for convenience.
"""

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Optional

import aiohttp

# Thread pool for running nba_api's synchronous calls off the event loop
_executor = ThreadPoolExecutor(max_workers=4)


async def _run_sync(fn, *args, **kwargs):
    """Run a blocking function in a thread pool without blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, lambda: fn(*args, **kwargs))

log = logging.getLogger(__name__)

# ── Request headers ────────────────────────────────────────────────────────────
# NBA.com blocks requests without these headers.

_STATS_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    # Do NOT set Host explicitly — aiohttp sets it correctly from the URL.
    # Explicit Host header causes 403s from NBA.com on cloud IPs.
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}

_CDN_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.nba.com/",
}

_STATS_BASE = "https://stats.nba.com/stats"
_CDN_BASE = "https://cdn.nba.com"

# ── Static NBA team data ───────────────────────────────────────────────────────
# NBA team IDs are stable. Used to convert MATCHUP strings to team_id pairs.

_NBA_TEAMS: dict[int, dict] = {
    1610612737: {"abbreviation": "ATL", "full_name": "Atlanta Hawks",           "city": "Atlanta",        "name": "Hawks"},
    1610612738: {"abbreviation": "BOS", "full_name": "Boston Celtics",          "city": "Boston",         "name": "Celtics"},
    1610612751: {"abbreviation": "BKN", "full_name": "Brooklyn Nets",           "city": "Brooklyn",       "name": "Nets"},
    1610612766: {"abbreviation": "CHA", "full_name": "Charlotte Hornets",       "city": "Charlotte",      "name": "Hornets"},
    1610612741: {"abbreviation": "CHI", "full_name": "Chicago Bulls",           "city": "Chicago",        "name": "Bulls"},
    1610612739: {"abbreviation": "CLE", "full_name": "Cleveland Cavaliers",     "city": "Cleveland",      "name": "Cavaliers"},
    1610612742: {"abbreviation": "DAL", "full_name": "Dallas Mavericks",        "city": "Dallas",         "name": "Mavericks"},
    1610612743: {"abbreviation": "DEN", "full_name": "Denver Nuggets",          "city": "Denver",         "name": "Nuggets"},
    1610612765: {"abbreviation": "DET", "full_name": "Detroit Pistons",         "city": "Detroit",        "name": "Pistons"},
    1610612744: {"abbreviation": "GSW", "full_name": "Golden State Warriors",   "city": "Golden State",   "name": "Warriors"},
    1610612745: {"abbreviation": "HOU", "full_name": "Houston Rockets",         "city": "Houston",        "name": "Rockets"},
    1610612754: {"abbreviation": "IND", "full_name": "Indiana Pacers",          "city": "Indiana",        "name": "Pacers"},
    1610612746: {"abbreviation": "LAC", "full_name": "LA Clippers",             "city": "LA",             "name": "Clippers"},
    1610612747: {"abbreviation": "LAL", "full_name": "Los Angeles Lakers",      "city": "Los Angeles",    "name": "Lakers"},
    1610612763: {"abbreviation": "MEM", "full_name": "Memphis Grizzlies",       "city": "Memphis",        "name": "Grizzlies"},
    1610612748: {"abbreviation": "MIA", "full_name": "Miami Heat",              "city": "Miami",          "name": "Heat"},
    1610612749: {"abbreviation": "MIL", "full_name": "Milwaukee Bucks",         "city": "Milwaukee",      "name": "Bucks"},
    1610612750: {"abbreviation": "MIN", "full_name": "Minnesota Timberwolves",  "city": "Minnesota",      "name": "Timberwolves"},
    1610612740: {"abbreviation": "NOP", "full_name": "New Orleans Pelicans",    "city": "New Orleans",    "name": "Pelicans"},
    1610612752: {"abbreviation": "NYK", "full_name": "New York Knicks",         "city": "New York",       "name": "Knicks"},
    1610612760: {"abbreviation": "OKC", "full_name": "Oklahoma City Thunder",   "city": "Oklahoma City",  "name": "Thunder"},
    1610612753: {"abbreviation": "ORL", "full_name": "Orlando Magic",           "city": "Orlando",        "name": "Magic"},
    1610612755: {"abbreviation": "PHI", "full_name": "Philadelphia 76ers",      "city": "Philadelphia",   "name": "76ers"},
    1610612756: {"abbreviation": "PHX", "full_name": "Phoenix Suns",            "city": "Phoenix",        "name": "Suns"},
    1610612757: {"abbreviation": "POR", "full_name": "Portland Trail Blazers",  "city": "Portland",       "name": "Trail Blazers"},
    1610612758: {"abbreviation": "SAC", "full_name": "Sacramento Kings",        "city": "Sacramento",     "name": "Kings"},
    1610612759: {"abbreviation": "SAS", "full_name": "San Antonio Spurs",       "city": "San Antonio",    "name": "Spurs"},
    1610612761: {"abbreviation": "TOR", "full_name": "Toronto Raptors",         "city": "Toronto",        "name": "Raptors"},
    1610612762: {"abbreviation": "UTA", "full_name": "Utah Jazz",               "city": "Utah",           "name": "Jazz"},
    1610612764: {"abbreviation": "WAS", "full_name": "Washington Wizards",      "city": "Washington",     "name": "Wizards"},
}

# Reverse lookup: abbreviation → team_id
_ABBR_TO_ID: dict[str, int] = {v["abbreviation"]: k for k, v in _NBA_TEAMS.items()}

# ── Static player map ─────────────────────────────────────────────────────────
# NBA.com player IDs are stable. This covers all template players so we never
# need to hit commonallplayers just to score a pick.
# Format: display_name → (player_id, team_id, position)

_KNOWN_PLAYERS: dict[str, tuple[int, int, str]] = {
    "LeBron James":           (2544,    1610612747, "F"),   # LAL
    "Stephen Curry":          (201939,  1610612744, "G"),   # GSW
    "Kevin Durant":           (201142,  1610612756, "F"),   # PHX
    "Giannis Antetokounmpo":  (203507,  1610612749, "F"),   # MIL
    "Nikola Jokic":           (203999,  1610612743, "C"),   # DEN
    "Luka Doncic":            (1629029, 1610612747, "F"),   # LAL (traded 2025)
    "Jayson Tatum":           (1628369, 1610612738, "F"),   # BOS
    "Joel Embiid":            (203954,  1610612755, "C"),   # PHI
    "Shai Gilgeous-Alexander":(1628983, 1610612760, "G"),   # OKC
    "Tyrese Haliburton":      (1630169, 1610612754, "G"),   # IND
    "Anthony Davis":          (203076,  1610612747, "C"),   # LAL
    "Donovan Mitchell":       (1628378, 1610612739, "G"),   # CLE
    "Bam Adebayo":            (1628389, 1610612748, "C"),   # MIA
    "De'Aaron Fox":           (1628368, 1610612759, "G"),   # SAS
    "Victor Wembanyama":      (1641705, 1610612759, "C"),   # SAS
    "Cade Cunningham":        (1630595, 1610612765, "G"),   # DET
    "Trae Young":             (1629027, 1610612737, "G"),   # ATL
    "Devin Booker":           (1626164, 1610612756, "G"),   # PHX
    "Ja Morant":              (1629630, 1610612763, "G"),   # MEM
    "Zion Williamson":        (1629627, 1610612740, "F"),   # NOP
    "Paolo Banchero":         (1631094, 1610612753, "F"),   # ORL
    "Damian Lillard":         (203081,  1610612749, "G"),   # MIL
    "James Harden":           (201935,  1610612746, "G"),   # LAC
    "Kawhi Leonard":          (202695,  1610612746, "F"),   # LAC
    "Paul George":            (202331,  1610612755, "F"),   # PHI
    "Evan Mobley":            (1630596, 1610612739, "C"),   # CLE
    "Scottie Barnes":         (1630567, 1610612761, "F"),   # TOR
    "Jaren Jackson Jr.":      (1628991, 1610612763, "C"),   # MEM
    "Darius Garland":         (1629636, 1610612739, "G"),   # CLE
    "Fred VanVleet":          (1627832, 1610612745, "G"),   # HOU
    "Anthony Edwards":        (1630162, 1610612750, "G"),   # MIN
    "Karl-Anthony Towns":     (1626157, 1610612752, "C"),   # NYK
    "Jalen Brunson":          (1628384, 1610612752, "G"),   # NYK
    "Julius Randle":          (203944,  1610612750, "F"),   # MIN
    "Pascal Siakam":          (1627783, 1610612754, "F"),   # IND
    "Domantas Sabonis":       (1627734, 1610612758, "C"),   # SAC
    "De'Andre Hunter":        (1629631, 1610612737, "F"),   # ATL
    "Alperen Sengun":         (1630578, 1610612745, "C"),   # HOU
    "Ivica Zubac":            (1627826, 1610612746, "C"),   # LAC
    "Amen Thompson":          (1641706, 1610612745, "F"),   # HOU
    "Jalen Green":            (1630224, 1610612745, "G"),   # HOU
    "Brandon Ingram":         (1627742, 1610612759, "F"),   # SAS
    "Mikal Bridges":          (1628969, 1610612752, "F"),   # NYK
    "OG Anunoby":             (1628384, 1610612752, "F"),   # NYK
    "Jaylen Brown":           (1627759, 1610612738, "F"),   # BOS
    "Kristaps Porzingis":     (204001,  1610612738, "C"),   # BOS
    "Al Horford":             (201143,  1610612738, "C"),   # BOS
    "Josh Hart":              (1628404, 1610612752, "F"),   # NYK
    "Nikola Vucevic":         (202696,  1610612741, "C"),   # CHI
    "Zach LaVine":            (203897,  1610612741, "G"),   # CHI
}

# ── Nickname map (same as before) ─────────────────────────────────────────────
_NICKNAME_MAP: dict[str, str] = {
    "lebron": "LeBron James", "bron": "LeBron James", "king james": "LeBron James",
    "kd": "Kevin Durant", "slim reaper": "Kevin Durant",
    "ad": "Anthony Davis", "the brow": "Anthony Davis",
    "steph": "Stephen Curry", "chef curry": "Stephen Curry",
    "giannis": "Giannis Antetokounmpo", "greek freak": "Giannis Antetokounmpo",
    "jokic": "Nikola Jokic", "joker": "Nikola Jokic",
    "luka": "Luka Doncic", "luka magic": "Luka Doncic",
    "tatum": "Jayson Tatum", "jt": "Jayson Tatum",
    "embiid": "Joel Embiid", "the process": "Joel Embiid",
    "dame": "Damian Lillard", "dame time": "Damian Lillard",
    "the beard": "James Harden",
    "kawhi": "Kawhi Leonard", "the claw": "Kawhi Leonard",
    "trae": "Trae Young", "ice trae": "Trae Young",
    "book": "Devin Booker",
    "ja": "Ja Morant",
    "spida": "Donovan Mitchell",
    "pg": "Paul George", "pg13": "Paul George",
    "hali": "Tyrese Haliburton",
    "sga": "Shai Gilgeous-Alexander", "shai": "Shai Gilgeous-Alexander",
    "cade": "Cade Cunningham",
    "bam": "Bam Adebayo",
    "wemby": "Victor Wembanyama", "wembanyama": "Victor Wembanyama",
}


def _normalize_name(raw: str) -> str:
    key = raw.strip().lower()
    if key in _NICKNAME_MAP:
        return _NICKNAME_MAP[key]
    parts = raw.strip().split()
    if len(parts) == 2 and len(parts[0]) <= 2 and parts[0].endswith("."):
        return parts[1]
    return raw.strip()


# ── NBA.com result parsing ─────────────────────────────────────────────────────

def _parse_result_set(data: dict, set_name: str = None) -> list[dict]:
    """Convert a NBA.com resultSets response to a list of lowercase-keyed dicts."""
    result_sets = data.get("resultSets") or []
    if not result_sets:
        return []
    if set_name:
        rs = next((r for r in result_sets if r.get("name") == set_name), None)
    else:
        rs = result_sets[0]
    if not rs:
        return []
    headers = [h.lower() for h in rs.get("headers", [])]
    return [dict(zip(headers, row)) for row in rs.get("rowSet", [])]


def _safe_float(val) -> Optional[float]:
    """Convert a value to float, returning None if not possible."""
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _parse_matchup(matchup: str) -> tuple[bool, str]:
    """
    Parse NBA.com MATCHUP string to (is_home, opponent_abbreviation).
    Examples: "LAL vs. GSW" → (True, "GSW"), "LAL @ GSW" → (False, "GSW")
    """
    is_home = "vs." in matchup
    parts = re.split(r"\s+(?:vs\.|@)\s+", matchup)
    opp_abbr = parts[1].strip() if len(parts) > 1 else ""
    return is_home, opp_abbr


def _parse_game_date(raw_date: str) -> str:
    """Convert NBA.com date strings to ISO format YYYY-MM-DD."""
    for fmt in ("%b %d, %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw_date.strip(), fmt).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            continue
    return raw_date[:10] if raw_date else ""


def _convert_game_log(row: dict) -> dict:
    """
    Convert a NBA.com playergamelog row to our internal game log format.

    Internal format (same field names as the old BallDontLie client):
        pts, reb, ast, stl, blk, fg3m, min
        game: {date, home_team_id, visitor_team_id}
    """
    matchup = row.get("matchup", "")
    is_home, opp_abbr = _parse_matchup(matchup)

    # Determine team IDs from matchup
    player_abbr = matchup.split()[0] if matchup else ""
    player_team_id = _ABBR_TO_ID.get(player_abbr, 0)
    opp_team_id = _ABBR_TO_ID.get(opp_abbr, 0)

    home_team_id = player_team_id if is_home else opp_team_id
    visitor_team_id = opp_team_id if is_home else player_team_id

    game_date = _parse_game_date(row.get("game_date", ""))

    return {
        "pts":  row.get("pts"),
        "reb":  row.get("reb"),
        "ast":  row.get("ast"),
        "stl":  row.get("stl"),
        "blk":  row.get("blk"),
        "fg3m": row.get("fg3m"),
        "min":  str(row.get("min", "") or ""),
        "game": {
            "date":            game_date,
            "home_team_id":    home_team_id,
            "visitor_team_id": visitor_team_id,
        },
        # Keep opponent abbr for H2H filtering
        "_opp_abbr": opp_abbr,
        "_opp_team_id": opp_team_id,
    }


# ── Season helper ──────────────────────────────────────────────────────────────

def _current_nba_season() -> str:
    """Return season string like '2025-26' for NBA.com API."""
    today = date.today()
    start_year = today.year if today.month >= 10 else today.year - 1
    end_short = (start_year + 1) % 100
    return f"{start_year}-{end_short:02d}"


# Keep old name for compatibility with nhl_client import pattern
def _current_nhl_season() -> str:
    return _current_nba_season()


# ── NBAClient ──────────────────────────────────────────────────────────────────

class NBAClient:
    """Async NBA stats client using stats.nba.com (no API key required)."""

    def __init__(self) -> None:
        self._stats_session: Optional[aiohttp.ClientSession] = None
        self._cdn_session: Optional[aiohttp.ClientSession] = None
        # In-memory cache: invalidate each bot restart
        self._players_cache: Optional[list[dict]] = None
        self._player_info_cache: dict[int, dict] = {}
        self._def_stats_cache: Optional[list[dict]] = None

    async def _stats_get(self, path: str, params: dict = None) -> dict:
        """GET from stats.nba.com with correct headers."""
        if self._stats_session is None or self._stats_session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self._stats_session = aiohttp.ClientSession(
                headers=_STATS_HEADERS, timeout=timeout
            )
        url = f"{_STATS_BASE}/{path}"
        try:
            async with self._stats_session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                log.debug("NBA.com OK %s", url)
                return data
        except aiohttp.ClientResponseError as exc:
            log.warning("NBA.com HTTP %s for %s: %s", exc.status, url, exc.message)
            return {}
        except asyncio.TimeoutError:
            log.warning("NBA.com timeout for %s", url)
            return {}
        except Exception as exc:
            log.error("NBA.com error for %s: %s", url, exc)
            return {}

    async def _cdn_get(self, path: str) -> dict:
        """GET from cdn.nba.com."""
        if self._cdn_session is None or self._cdn_session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._cdn_session = aiohttp.ClientSession(
                headers=_CDN_HEADERS, timeout=timeout
            )
        url = f"{_CDN_BASE}/{path}"
        try:
            async with self._cdn_session.get(url) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except Exception as exc:
            log.warning("NBA CDN error for %s: %s", url, exc)
            return {}

    async def close(self) -> None:
        for s in [self._stats_session, self._cdn_session]:
            if s and not s.closed:
                await s.close()

    # ── Player lookup ──────────────────────────────────────────────────────────

    async def _get_all_players(self) -> list[dict]:
        """Fetch (and cache) all active NBA players for the current season."""
        if self._players_cache is not None:
            return self._players_cache

        data = await self._stats_get(
            "commonallplayers",
            {
                "LeagueID": "00",
                "Season": _current_nba_season(),
                "IsOnlyCurrentSeason": "1",
            },
        )
        rows = _parse_result_set(data, "CommonAllPlayers")
        self._players_cache = rows
        log.info("Cached %d NBA players from NBA.com", len(rows))
        return rows

    async def get_player(self, name: str) -> Optional[dict]:
        """
        Search for a player by name. Returns a dict matching the old
        BallDontLie shape: {id, first_name, last_name, team: {...}, position}

        Checks the static _KNOWN_PLAYERS map first (no API call needed for
        template players). Falls back to NBA.com commonallplayers API.
        """
        resolved = _normalize_name(name)

        # ── Static map lookup (fast, no API call) ──────────────────────────
        def _static_lookup(query: str) -> Optional[dict]:
            q = query.strip().lower()
            for display, (pid, tid, pos) in _KNOWN_PLAYERS.items():
                if q == display.lower() or q in display.lower() or display.lower() in q:
                    team_info = _NBA_TEAMS.get(tid, {})
                    parts = display.split(" ", 1)
                    return {
                        "id":         pid,
                        "first_name": parts[0],
                        "last_name":  parts[1] if len(parts) > 1 else "",
                        "position":   pos,
                        "team": {
                            "id":           tid,
                            "abbreviation": team_info.get("abbreviation", ""),
                            "full_name":    team_info.get("full_name", ""),
                            "city":         team_info.get("city", ""),
                            "name":         team_info.get("name", ""),
                        },
                    }
            return None

        result = _static_lookup(resolved) or _static_lookup(name)
        if result:
            return result

        # ── NBA.com API fallback for unknown players ────────────────────────
        players = await self._get_all_players()
        if not players:
            log.warning("NBA.com commonallplayers returned empty — player '%s' not found", name)
            return None

        def _score(p: dict) -> int:
            display = (p.get("display_first_last") or "").lower()
            r = resolved.lower()
            if display == r:
                return 3
            if r in display or display in r:
                return 2
            last = display.split()[-1] if display else ""
            if r == last or r in last:
                return 1
            return 0

        scored = [(p, _score(p)) for p in players]
        scored.sort(key=lambda x: x[1], reverse=True)
        best = scored[0] if scored and scored[0][1] > 0 else (None, 0)

        if not best[0]:
            return None

        p = best[0]
        player_id = p.get("person_id") or p.get("personid")
        team_id = int(p.get("team_id") or 0)
        team_info = _NBA_TEAMS.get(team_id, {})
        position = await self._get_player_position(player_id)

        display = p.get("display_first_last", "")
        parts = display.split(" ", 1)

        return {
            "id":         player_id,
            "first_name": parts[0] if parts else "",
            "last_name":  parts[1] if len(parts) > 1 else "",
            "position":   position,
            "team": {
                "id":           team_id,
                "abbreviation": team_info.get("abbreviation", p.get("team_abbreviation", "")),
                "full_name":    team_info.get("full_name", ""),
                "city":         team_info.get("city", ""),
                "name":         team_info.get("name", ""),
            },
        }

    async def _get_player_position(self, player_id: int) -> str:
        """Fetch player position from commonplayerinfo (cached per player)."""
        if player_id in self._player_info_cache:
            return self._player_info_cache[player_id]

        data = await self._stats_get(
            "commonplayerinfo",
            {"PlayerID": player_id},
        )
        rows = _parse_result_set(data, "CommonPlayerInfo")
        position = "F"
        if rows:
            raw_pos = rows[0].get("position", "")
            # Normalize: "Forward", "Guard", "Center", "Forward-Center" etc.
            raw_pos = raw_pos.strip()
            if raw_pos.startswith("G"):
                position = "G"
            elif raw_pos.startswith("C"):
                position = "C"
            else:
                position = "F"

        self._player_info_cache[player_id] = position
        return position

    # ── Game logs ──────────────────────────────────────────────────────────────

    async def get_player_game_logs(
        self, player_id: int, last_n: int = 20
    ) -> list[dict]:
        """
        Return up to last_n recent game logs for a player, newest first.
        Uses nba_api (handles NBA.com session/headers automatically).
        Falls back to direct stats.nba.com call if nba_api unavailable.
        """
        try:
            from nba_api.stats.endpoints import playergamelog

            def _fetch():
                gl = playergamelog.PlayerGameLog(
                    player_id=player_id,
                    season=_current_nba_season(),
                    season_type_all_star="Regular Season",
                    timeout=20,
                )
                return gl.get_data_frames()[0]

            df = await _run_sync(_fetch)
            if df is None or df.empty:
                log.warning("nba_api: no game logs for player %s", player_id)
                return []

            logs = []
            for _, row in df.iterrows():
                matchup = str(row.get("MATCHUP", ""))
                is_home, opp_abbr = _parse_matchup(matchup)
                player_abbr = matchup.split()[0] if matchup else ""
                player_team_id = _ABBR_TO_ID.get(player_abbr, 0)
                opp_team_id = _ABBR_TO_ID.get(opp_abbr, 0)
                home_team_id = player_team_id if is_home else opp_team_id
                visitor_team_id = opp_team_id if is_home else player_team_id
                game_date = _parse_game_date(str(row.get("GAME_DATE", "")))

                logs.append({
                    "pts":  _safe_float(row.get("PTS")),
                    "reb":  _safe_float(row.get("REB")),
                    "ast":  _safe_float(row.get("AST")),
                    "stl":  _safe_float(row.get("STL")),
                    "blk":  _safe_float(row.get("BLK")),
                    "fg3m": _safe_float(row.get("FG3M")),
                    "min":  str(row.get("MIN", "") or ""),
                    "game": {
                        "date":            game_date,
                        "home_team_id":    home_team_id,
                        "visitor_team_id": visitor_team_id,
                    },
                    "_opp_abbr":    opp_abbr,
                    "_opp_team_id": opp_team_id,
                })
                if len(logs) >= last_n:
                    break

            return logs

        except Exception as exc:
            log.warning("nba_api game logs failed for %s: %s — trying direct API", player_id, exc)

        # Fallback: direct stats.nba.com call
        data = await self._stats_get(
            "playergamelog",
            {"PlayerID": player_id, "Season": _current_nba_season(), "SeasonType": "Regular Season"},
        )
        rows = _parse_result_set(data, "PlayerGameLog")
        return [_convert_game_log(r) for r in rows[:last_n]]

    async def get_h2h_games(
        self,
        player_id: int,
        opponent_team_id: int,
        last_n: int = 10,
    ) -> list[dict]:
        """Return game logs where the player faced opponent_team_id."""
        logs = await self.get_player_game_logs(player_id, last_n=100)
        h2h = [
            log for log in logs
            if log.get("_opp_team_id") == opponent_team_id
        ]
        return h2h[:last_n]

    # ── Team data ──────────────────────────────────────────────────────────────

    async def get_teams(self) -> list[dict]:
        """Return all NBA teams in the same shape as the old BallDontLie client."""
        return [
            {
                "id":           tid,
                "abbreviation": info["abbreviation"],
                "full_name":    info["full_name"],
                "city":         info["city"],
                "name":         info["name"],
            }
            for tid, info in _NBA_TEAMS.items()
        ]

    async def get_team_defensive_stats(self) -> list[dict]:
        """
        Fetch opponent (defensive) stats per team.
        Uses nba_api with fallback to direct stats.nba.com call.
        """
        if self._def_stats_cache is not None:
            return self._def_stats_cache

        try:
            from nba_api.stats.endpoints import leaguedashteamstats

            def _fetch():
                ls = leaguedashteamstats.LeagueDashTeamStats(
                    season=_current_nba_season(),
                    season_type_all_star="Regular Season",
                    measure_type_detailed_defense="Opponent",
                    per_mode_simple="PerGame",
                    timeout=20,
                )
                return ls.get_data_frames()[0]

            df = await _run_sync(_fetch)
            if df is not None and not df.empty:
                result = []
                for _, row in df.iterrows():
                    team_id = int(row.get("TEAM_ID") or 0)
                    gp = max(int(row.get("GP") or 1), 1)
                    result.append({
                        "team_id":            team_id,
                        "avg_pts_allowed":    float(row.get("OPP_PTS") or 0),
                        "avg_reb_allowed":    float(row.get("OPP_REB") or 0),
                        "avg_ast_allowed":    float(row.get("OPP_AST") or 0),
                        "avg_threes_allowed": float(row.get("OPP_FG3M") or 0),
                        "avg_blk_allowed":    float(row.get("OPP_BLK") or 0),
                        "avg_stl_allowed":    float(row.get("OPP_STL") or 0),
                        "games_sample":       gp,
                    })
                self._def_stats_cache = result
                return result
        except Exception as exc:
            log.warning("nba_api defensive stats failed: %s — trying direct API", exc)

        # Fallback: direct stats.nba.com call
        data = await self._stats_get(
            "leaguedashteamstats",
            {
                "MeasureType": "Opponent", "PerMode": "PerGame",
                "Season": _current_nba_season(), "SeasonType": "Regular Season",
                "LeagueID": "00", "LastNGames": "0", "Month": "0",
                "OpponentTeamID": "0", "PaceAdjust": "N", "PlusMinus": "N",
                "Rank": "N", "TeamID": "0",
            },
        )
        rows = _parse_result_set(data, "LeagueDashTeamStats")
        result = []
        for row in rows:
            team_id = int(row.get("team_id") or 0)
            gp = max(int(row.get("gp") or 1), 1)
            result.append({
                "team_id":            team_id,
                "avg_pts_allowed":    float(row.get("opp_pts") or 0),
                "avg_reb_allowed":    float(row.get("opp_reb") or 0),
                "avg_ast_allowed":    float(row.get("opp_ast") or 0),
                "avg_threes_allowed": float(row.get("opp_fg3m") or 0),
                "avg_blk_allowed":    float(row.get("opp_blk") or 0),
                "avg_stl_allowed":    float(row.get("opp_stl") or 0),
                "games_sample":       gp,
            })
        self._def_stats_cache = result
        return result

    # ── Injuries ───────────────────────────────────────────────────────────────

    async def get_injuries(self) -> list[dict]:
        """
        Return current injury report. NBA.com doesn't have a free injury API,
        so we return an empty list. The scoring engine handles this gracefully.
        """
        return []

    # ── Schedule ───────────────────────────────────────────────────────────────

    async def get_todays_games(self) -> list[dict]:
        """Return today's NBA games from the CDN scoreboard."""
        data = await self._cdn_get(
            "static/json/liveData/scoreboard/todaysScoreboard_00.json"
        )
        games = data.get("scoreboard", {}).get("games", [])
        result = []
        for g in games:
            home = g.get("homeTeam", {})
            away = g.get("awayTeam", {})
            result.append({
                "id":           g.get("gameId", ""),
                "date":         g.get("gameEt", "")[:10],
                "home_team":    {"id": home.get("teamId"), "abbreviation": home.get("teamTricode", "")},
                "visitor_team": {"id": away.get("teamId"), "abbreviation": away.get("teamTricode", "")},
                "status":       g.get("gameStatusText", ""),
            })
        return result

    async def get_games_on_date(self, game_date: str) -> list[dict]:
        """Alias for get_todays_games (CDN only serves today's scoreboard)."""
        return await self.get_todays_games()
