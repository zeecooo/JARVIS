"""
data/sports_router.py - Universal sport detection and routing.

Detects which sport a pick/slip belongs to and routes to the correct data client.
"""

from typing import Optional

# ── Sport detection keywords ──────────────────────────────────────────────────

_NBA_PROPS = {
    "pts", "reb", "ast", "3pm", "blk", "stl", "pra", "pr", "pa", "ra", "bs",
    "points", "rebounds", "assists", "threes", "blocks", "steals",
    "pts+reb+ast", "pts+reb", "pts+ast", "reb+ast", "double double", "triple double",
    "first basket", "fga", "fta", "turnovers",
}

_NHL_PROPS = {
    "sog", "shots on goal", "shots", "goals", "saves", "hits", "faceoffs",
    "blocked shots", "pim", "time on ice", "toi", "plus minus", "+/-",
    "fow", "faceoff wins",
}

_NFL_PROPS = {
    "passing yards", "pass yds", "passyds", "rushing yards", "rush yds", "rushyds",
    "receiving yards", "rec yds", "recyds", "receptions", "rec", "touchdowns", "tds",
    "td", "interceptions", "int", "sacks", "completions", "targets",
    "passtd", "rushatts", "rush attempts",
}

_SOCCER_PROPS = {
    "goals", "shots", "shots on target", "key passes", "assists", "tackles",
    "cards", "yellow cards", "red cards", "fouls", "corners", "saves",
    "clean sheet", "xg", "expected goals", "dribbles",
}

_TENNIS_PROPS = {
    "aces", "double faults", "sets", "games", "break points", "first serve",
    "service games", "winners", "unforced errors", "total games",
    "set 1", "set 2", "set 3", "match winner",
}

_ESPORTS_PROPS = {
    "kills", "deaths", "kda", "headshots", "adr", "hltv", "maps", "rounds",
    "cs", "creep score", "gold", "damage", "acs", "rating", "gpm", "xpm",
    "plant", "defuse",
}

# ── NBA team names ─────────────────────────────────────────────────────────────
_NBA_TEAMS = {
    "lakers", "celtics", "warriors", "bulls", "knicks", "nets", "heat", "bucks",
    "suns", "76ers", "sixers", "nuggets", "clippers", "spurs", "raptors",
    "jazz", "grizzlies", "pelicans", "thunder", "blazers", "trail blazers",
    "kings", "magic", "pistons", "cavs", "cavaliers", "hawks", "hornets",
    "pacers", "wizards", "timberwolves", "rockets", "mavs", "mavericks",
}

# ── NFL team names ─────────────────────────────────────────────────────────────
_NFL_TEAMS = {
    "chiefs", "eagles", "cowboys", "patriots", "49ers", "rams", "chargers",
    "ravens", "bengals", "bills", "dolphins", "jets", "giants", "steelers",
    "browns", "texans", "colts", "jaguars", "titans", "broncos", "raiders",
    "seahawks", "cardinals", "falcons", "saints", "buccaneers", "panthers",
    "bears", "packers", "vikings", "lions", "commanders", "redskins",
}

# ── NHL team names ─────────────────────────────────────────────────────────────
_NHL_TEAMS = {
    "maple leafs", "canadiens", "bruins", "rangers", "islanders", "devils",
    "flyers", "penguins", "capitals", "hurricanes", "panthers", "lightning",
    "red wings", "blue jackets", "sabres", "senators", "blackhawks", "predators",
    "blues", "jets", "wild", "avalanche", "stars", "coyotes", "golden knights",
    "kings", "ducks", "sharks", "kraken", "canucks", "flames", "oilers",
}

# ── Soccer leagues/teams ──────────────────────────────────────────────────────
_SOCCER_INDICATORS = {
    "fc", "united", "city", "real", "atletico", "inter", "juventus", "milan",
    "barcelona", "bayern", "ajax", "psv", "celtic", "rangers", "arsenal",
    "chelsea", "liverpool", "tottenham", "spurs", "epl", "premier league",
    "la liga", "bundesliga", "serie a", "mls", "champions league",
}

# ── Esports game indicators ───────────────────────────────────────────────────
_ESPORTS_INDICATORS = {
    "csgo", "cs2", "valorant", "lol", "dota", "dota2", "overwatch",
    "navi", "faze", "astralis", "g2", "cloud9", "c9", "team liquid", "fnatic",
    "t1", "nip", "ence", "vitality", "natus vincere",
}


def detect_sport(text: str) -> str:
    """
    Detect the sport from a free-text pick or slip line.

    Returns one of: 'NBA', 'NFL', 'NHL', 'SOCCER', 'TENNIS', 'ESPORTS'
    Defaults to 'NBA' if no clear match.
    """
    lower = text.lower()
    tokens = set(lower.replace(",", " ").replace(".", " ").split())

    # Score each sport
    scores = {
        "NBA": 0, "NFL": 0, "NHL": 0, "SOCCER": 0, "TENNIS": 0, "ESPORTS": 0
    }

    for prop in _NBA_PROPS:
        if prop in lower:
            scores["NBA"] += 2
    for prop in _NHL_PROPS:
        if prop in lower:
            scores["NHL"] += 2
    for prop in _NFL_PROPS:
        if prop in lower:
            scores["NFL"] += 2
    for prop in _SOCCER_PROPS:
        if prop in lower:
            scores["SOCCER"] += 2
    for prop in _TENNIS_PROPS:
        if prop in lower:
            scores["TENNIS"] += 2
    for prop in _ESPORTS_PROPS:
        if prop in lower:
            scores["ESPORTS"] += 2

    for team in _NBA_TEAMS:
        if team in lower:
            scores["NBA"] += 1
    for team in _NFL_TEAMS:
        if team in lower:
            scores["NFL"] += 1
    for team in _NHL_TEAMS:
        if team in lower:
            scores["NHL"] += 1
    for ind in _SOCCER_INDICATORS:
        if ind in lower:
            scores["SOCCER"] += 1
    for ind in _ESPORTS_INDICATORS:
        if ind in lower:
            scores["ESPORTS"] += 3

    # Explicit sport labels get highest weight
    if "nba" in tokens:
        scores["NBA"] += 10
    if "nfl" in tokens or "football" in lower:
        scores["NFL"] += 10
    if "nhl" in tokens or "hockey" in lower:
        scores["NHL"] += 10
    if any(w in lower for w in ["soccer", "football", "epl", "mls", "ucl"]):
        scores["SOCCER"] += 8
    if "tennis" in lower or "atp" in lower or "wta" in lower:
        scores["TENNIS"] += 10
    if any(w in lower for w in ["esports", "esport", "gaming", "csgo", "cs2", "valorant", "lol"]):
        scores["ESPORTS"] += 10

    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "NBA"  # Default
    return best


def parse_slip_line(line: str) -> Optional[dict]:
    """
    Parse a single slip line into structured components.

    Handles formats like:
      "LeBron James Over 25.5 PTS"
      "Patrick Mahomes Over 280.5 Passing Yards"
      "Erling Haaland Over 1.5 Shots on Target"
      "ZywOo Over 25.5 Kills"
      "Novak Djokovic Over 6.5 Aces"

    Returns:
      {
        player: str,
        direction: "over"|"under",
        line: float,
        prop_type: str,
        sport: str,
        raw: str,
      }
    or None if parsing fails.
    """
    import re
    original = line.strip()
    if not original:
        return None

    # Normalize
    cleaned = original.replace("–", "-").replace("—", "-")

    # Match: <name> Over/Under <number> <prop>
    pattern = re.compile(
        r"^(.+?)\s+(over|under)\s+([\d.]+)\s+(.+)$",
        re.IGNORECASE,
    )
    m = pattern.match(cleaned)
    if not m:
        return None

    player = m.group(1).strip().title()
    direction = m.group(2).lower()
    try:
        line_val = float(m.group(3))
    except ValueError:
        return None
    prop_raw = m.group(4).strip()

    # Normalize prop type
    prop_type = _normalize_prop(prop_raw)
    sport = detect_sport(f"{player} {prop_type} {original}")

    return {
        "player": player,
        "direction": direction,
        "line": line_val,
        "prop_type": prop_type,
        "sport": sport,
        "raw": original,
    }


def _normalize_prop(raw: str) -> str:
    """Normalize user prop strings to internal keys."""
    mapping = {
        # NBA
        "pts": "PTS", "points": "PTS", "point": "PTS",
        "reb": "REB", "rebounds": "REB", "rebound": "REB",
        "ast": "AST", "assists": "AST", "assist": "AST",
        "3pm": "3PM", "threes": "3PM", "three pointers": "3PM", "3-pointers": "3PM",
        "blk": "BLK", "blocks": "BLK", "block": "BLK",
        "stl": "STL", "steals": "STL", "steal": "STL",
        "pra": "PRA", "pts+reb+ast": "PRA", "points+rebounds+assists": "PRA",
        "pr": "PR", "pts+reb": "PR",
        "pa": "PA", "pts+ast": "PA",
        "ra": "RA", "reb+ast": "RA",
        "first basket": "FIRST_BASKET",
        "turnovers": "TOV",
        # NHL
        "sog": "SOG", "shots on goal": "SOG", "shots": "SOG",
        "goals": "GOALS", "goal": "GOALS",
        "saves": "SAVES", "save": "SAVES",
        "hits": "HITS", "hit": "HITS",
        "blocked shots": "BLOCKS", "bs": "BLOCKS",
        "faceoffs": "FACEOFFS", "faceoff wins": "FACEOFFS", "fow": "FACEOFFS",
        "pim": "PIM",
        # NFL
        "passing yards": "PASSYDS", "pass yds": "PASSYDS", "passyds": "PASSYDS",
        "rushing yards": "RUSHYDS", "rush yds": "RUSHYDS", "rushyds": "RUSHYDS",
        "receiving yards": "RECYDS", "rec yds": "RECYDS", "recyds": "RECYDS",
        "receptions": "REC", "rec": "REC",
        "touchdowns": "PASSTD", "td": "PASSTD", "tds": "PASSTD",
        "interceptions": "INT", "int": "INT",
        "sacks": "SACKS",
        "completions": "COMPLETIONS",
        "targets": "TARGETS",
        # Soccer
        "shots on target": "SHOTSONTARGET",
        "key passes": "KEYPASS",
        "yellow cards": "YELLOWCARDS",
        "red cards": "REDCARDS",
        "corners": "CORNERS",
        "fouls": "FOULS",
        "tackles": "TACKLES",
        # Tennis
        "aces": "ACES",
        "double faults": "DOUBLEFAULTS",
        "sets": "SETS",
        "games": "TOTALGAMES",
        "break points": "BREAKPOINTS",
        # Esports
        "kills": "KILLS",
        "deaths": "DEATHS",
        "kda": "KDA",
        "headshots": "HEADSHOTS",
        "adr": "ADR",
        "rating": "HLTV",
        "maps": "MAPS",
        "rounds": "ROUNDS",
        "cs": "CS",
    }
    key = raw.strip().lower()
    return mapping.get(key, raw.upper().replace(" ", ""))


SPORT_EMOJI: dict[str, str] = {
    "NBA": "🏀",
    "NFL": "🏈",
    "NHL": "🏒",
    "SOCCER": "⚽",
    "TENNIS": "🎾",
    "ESPORTS": "🎮",
}
