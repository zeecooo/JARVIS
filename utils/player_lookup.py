"""
utils/player_lookup.py - Unified player name resolution for Jarvis.

Provides fuzzy matching, nickname resolution, and abbreviated-name handling
for both NBA and NHL players.
"""

import re
import unicodedata
from typing import Optional

# ── Comprehensive nickname maps ───────────────────────────────────────────────

NBA_NICKNAMES: dict[str, str] = {
    # LeBron James
    "lebron":       "LeBron James",
    "bron":         "LeBron James",
    "king james":   "LeBron James",
    "lbj":          "LeBron James",
    "king":         "LeBron James",
    # Kevin Durant
    "kd":           "Kevin Durant",
    "slim reaper":  "Kevin Durant",
    "durantula":    "Kevin Durant",
    # Anthony Davis
    "ad":           "Anthony Davis",
    "the brow":     "Anthony Davis",
    "brow":         "Anthony Davis",
    # Stephen Curry
    "steph":        "Stephen Curry",
    "chef curry":   "Stephen Curry",
    "curry":        "Stephen Curry",
    # Giannis Antetokounmpo
    "giannis":      "Giannis Antetokounmpo",
    "greek freak":  "Giannis Antetokounmpo",
    "freak":        "Giannis Antetokounmpo",
    # Nikola Jokic
    "jokic":        "Nikola Jokic",
    "joker":        "Nikola Jokic",
    "the joker":    "Nikola Jokic",
    # Luka Doncic
    "luka":         "Luka Doncic",
    "luka magic":   "Luka Doncic",
    "doncic":       "Luka Doncic",
    # Jayson Tatum
    "tatum":        "Jayson Tatum",
    "jt":           "Jayson Tatum",
    "jt3":          "Jayson Tatum",
    # Joel Embiid
    "embiid":       "Joel Embiid",
    "the process":  "Joel Embiid",
    "jo":           "Joel Embiid",
    # Damian Lillard
    "dame":         "Damian Lillard",
    "dame time":    "Damian Lillard",
    "lillard":      "Damian Lillard",
    # Kawhi Leonard
    "kawhi":        "Kawhi Leonard",
    "the claw":     "Kawhi Leonard",
    "leonard":      "Kawhi Leonard",
    # Trae Young
    "trae":         "Trae Young",
    "ice trae":     "Trae Young",
    # Devin Booker
    "book":         "Devin Booker",
    "booker":       "Devin Booker",
    # Ja Morant
    "ja":           "Ja Morant",
    "morant":       "Ja Morant",
    # Donovan Mitchell
    "spida":        "Donovan Mitchell",
    "mitchell":     "Donovan Mitchell",
    # Paul George
    "pg":           "Paul George",
    "pg13":         "Paul George",
    # Tyrese Haliburton
    "hali":         "Tyrese Haliburton",
    "haliburton":   "Tyrese Haliburton",
    # Shai Gilgeous-Alexander
    "sga":          "Shai Gilgeous-Alexander",
    "shai":         "Shai Gilgeous-Alexander",
    # Cade Cunningham
    "cade":         "Cade Cunningham",
    # Bam Adebayo
    "bam":          "Bam Adebayo",
    # Victor Wembanyama
    "wemby":        "Victor Wembanyama",
    "wembanyama":   "Victor Wembanyama",
    # James Harden
    "the beard":    "James Harden",
    "harden":       "James Harden",
    # Russell Westbrook
    "russ":         "Russell Westbrook",
    "brodie":       "Russell Westbrook",
    # Jimmy Butler
    "jimmy buckets": "Jimmy Butler",
    "buckets":       "Jimmy Butler",
    # Zion Williamson
    "zion":         "Zion Williamson",
    # Paolo Banchero
    "paolo":        "Paolo Banchero",
    # Franz Wagner
    "franz":        "Franz Wagner",
    # Tyrese Maxey
    "maxey":        "Tyrese Maxey",
    # Karl-Anthony Towns
    "kat":          "Karl-Anthony Towns",
    "towns":        "Karl-Anthony Towns",
    # Draymond Green
    "draymond":     "Draymond Green",
    # Klay Thompson
    "klay":         "Klay Thompson",
    # Chris Paul
    "cp3":          "Chris Paul",
    "the point god": "Chris Paul",
    # Kyrie Irving
    "kyrie":        "Kyrie Irving",
    "uncle drew":   "Kyrie Irving",
    # Bradley Beal
    "beal":         "Bradley Beal",
    # Darius Garland
    "darius":       "Darius Garland",
    # Jaren Jackson Jr.
    "jjj":          "Jaren Jackson Jr.",
    # De'Aaron Fox
    "swipa":        "De'Aaron Fox",
    "fox":          "De'Aaron Fox",
    # LaMelo Ball
    "melo":         "LaMelo Ball",
    "lamelo":       "LaMelo Ball",
    # Alperen Sengun
    "sengun":       "Alperen Sengun",
}

NHL_NICKNAMES: dict[str, str] = {
    "mcdavid":    "Connor McDavid",
    "the kid":    "Connor McDavid",
    "97":         "Connor McDavid",
    "draisaitl":  "Leon Draisaitl",
    "leon":       "Leon Draisaitl",
    "ovechkin":   "Alex Ovechkin",
    "ovi":        "Alex Ovechkin",
    "the great 8": "Alex Ovechkin",
    "crosby":     "Sidney Crosby",
    "sid":        "Sidney Crosby",
    "matthews":   "Auston Matthews",
    "34":         "Auston Matthews",
    "marner":     "Mitch Marner",
    "tkachuk":    "Matthew Tkachuk",
    "pasta":      "David Pastrnak",
    "pastrnak":   "David Pastrnak",
    "makar":      "Cale Makar",
    "hedman":     "Victor Hedman",
    "point":      "Brayden Point",
    "stamkos":    "Steven Stamkos",
    "kucherov":   "Nikita Kucherov",
    "hub":        "Nikita Kucherov",
    "hellebuyck": "Connor Hellebuyck",
    "vasilevskiy": "Andrei Vasilevskiy",
    "vasi":       "Andrei Vasilevskiy",
    "mackinnon":  "Nathan MacKinnon",
    "nate":       "Nathan MacKinnon",
    "barkov":     "Aleksander Barkov",
    "huberdeau":  "Jonathan Huberdeau",
    "gaudreau":   "Johnny Gaudreau",
    "johnny hockey": "Johnny Gaudreau",
    "bergeron":   "Patrice Bergeron",
    "marchand":   "Brad Marchand",
    "little ball of hate": "Brad Marchand",
    "tavares":    "John Tavares",
    "jt":         "John Tavares",
    "price":      "Carey Price",
    "fleury":     "Marc-Andre Fleury",
    "flower":     "Marc-Andre Fleury",
    "shea":       "Shea Weber",
    "karlsson":   "Erik Karlsson",
    "ekblad":     "Aaron Ekblad",
    "fox":        "Adam Fox",
    "panarin":    "Artemi Panarin",
    "breadman":   "Artemi Panarin",
    "zibanejad":  "Mika Zibanejad",
    "boeser":     "Brock Boeser",
    "pettersson": "Elias Pettersson",
    "ep40":       "Elias Pettersson",
    "hughes":     "Quinn Hughes",
    "bedard":     "Connor Bedard",
}


# ── Name normalisation helpers ────────────────────────────────────────────────

def _strip_accents(text: str) -> str:
    """Remove diacritics from a string (é→e, ñ→n, etc.)."""
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _clean(text: str) -> str:
    """Lowercase, strip accents, remove punctuation except hyphens."""
    t = _strip_accents(text.strip().lower())
    t = re.sub(r"[^\w\s\-]", "", t)
    return t


def resolve_nba_name(raw: str) -> str:
    """
    Resolve a raw input string to the best NBA player full name guess.

    Order of resolution:
    1. Direct nickname match
    2. Abbreviated first-name (T. Harris → Harris)
    3. Return original (let the API fuzzy-match)
    """
    key = _clean(raw)
    if key in NBA_NICKNAMES:
        return NBA_NICKNAMES[key]

    # Abbreviated: "T. Harris", "K. Durant"
    parts = raw.strip().split()
    if len(parts) == 2 and len(parts[0]) <= 2 and parts[0].endswith("."):
        return parts[1]

    return raw.strip()


def resolve_nhl_name(raw: str) -> str:
    """Resolve a raw input to the best NHL player full name guess."""
    key = _clean(raw)
    if key in NHL_NICKNAMES:
        return NHL_NICKNAMES[key]

    parts = raw.strip().split()
    if len(parts) == 2 and len(parts[0]) <= 2 and parts[0].endswith("."):
        return parts[1]

    return raw.strip()


# ── Fuzzy matching ────────────────────────────────────────────────────────────

def fuzzy_match(query: str, candidates: list[str], threshold: float = 0.6) -> Optional[str]:
    """
    Find the best fuzzy match for `query` in `candidates`.

    Uses a simple character-overlap score (Jaccard similarity on trigrams).
    Returns the best match above `threshold` or None.

    This avoids a third-party dependency (no rapidfuzz/fuzzywuzzy required).
    """
    def trigrams(s: str) -> set:
        s = _clean(s)
        return {s[i:i+3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}

    q_tri = trigrams(query)
    best_score = 0.0
    best_match: Optional[str] = None

    for candidate in candidates:
        c_tri = trigrams(candidate)
        if not q_tri or not c_tri:
            continue
        intersection = len(q_tri & c_tri)
        union = len(q_tri | c_tri)
        score = intersection / union if union > 0 else 0.0
        if score > best_score:
            best_score = score
            best_match = candidate

    return best_match if best_score >= threshold else None


def find_best_player_match(
    query: str,
    player_list: list[dict],
    name_keys: tuple = ("first_name", "last_name"),
) -> Optional[dict]:
    """
    Find the best player dict from a list using fuzzy name matching.

    Works with both BallDontLie format (first_name / last_name) and
    NHL format (firstName.default / lastName.default).
    """
    query_clean = _clean(query)

    def _get_name(p: dict) -> str:
        first_val = p.get("first_name") or p.get("firstName", {})
        last_val  = p.get("last_name")  or p.get("lastName", {})
        if isinstance(first_val, dict):
            first_val = first_val.get("default", "")
        if isinstance(last_val, dict):
            last_val = last_val.get("default", "")
        return f"{first_val} {last_val}".strip()

    names = [_get_name(p) for p in player_list]
    best = fuzzy_match(query_clean, [_clean(n) for n in names])

    if best is None:
        return None

    # Find the original player dict for this name
    for i, n in enumerate(names):
        if _clean(n) == best:
            return player_list[i]

    return None


# ── Sport detection from slip text ───────────────────────────────────────────

_NHL_PROP_KEYWORDS = {"sog", "shots on goal", "shots on net", "saves", "blocked shots",
                      "hits", "faceoff", "toi", "time on ice"}
_NBA_PROP_KEYWORDS = {"pts", "points", "reb", "rebounds", "ast", "assists",
                      "3pm", "threes", "blk", "blocks", "stl", "steals", "pra"}


def detect_sport(text: str) -> str:
    """
    Guess the sport (NBA or NHL) from a prop slip string.
    Returns 'NBA' or 'NHL'.
    """
    lower = text.lower()
    nhl_score = sum(1 for kw in _NHL_PROP_KEYWORDS if kw in lower)
    nba_score  = sum(1 for kw in _NBA_PROP_KEYWORDS if kw in lower)
    return "NHL" if nhl_score > nba_score else "NBA"


def detect_sport_for_player(player_name: str) -> str:
    """
    Make a best guess about a player's sport from nickname lookups.
    Returns 'NHL', 'NBA', 'NFL', 'SOCCER', 'TENNIS', 'ESPORTS', or 'UNKNOWN'.
    """
    key = _clean(player_name)
    if key in NBA_NICKNAMES or any(key in _clean(v) for v in NBA_NICKNAMES.values()):
        return "NBA"
    if key in NHL_NICKNAMES or any(key in _clean(v) for v in NHL_NICKNAMES.values()):
        return "NHL"

    # Import sport-specific nickname maps and check them
    try:
        from data.nfl_client import _NFL_NICKNAMES
        if key in {k.lower() for k in _NFL_NICKNAMES}:
            return "NFL"
    except ImportError:
        pass

    try:
        from data.soccer_client import _SOCCER_NICKNAMES
        if key in {k.lower() for k in _SOCCER_NICKNAMES}:
            return "SOCCER"
    except ImportError:
        pass

    try:
        from data.tennis_client import _TENNIS_NICKNAMES
        if key in {k.lower() for k in _TENNIS_NICKNAMES}:
            return "TENNIS"
    except ImportError:
        pass

    try:
        from data.esports_client import _ESPORTS_NICKNAMES
        if key in {k.lower() for k in _ESPORTS_NICKNAMES}:
            return "ESPORTS"
    except ImportError:
        pass

    return "UNKNOWN"


def resolve_name(raw: str, sport: str = "NBA") -> str:
    """
    Dispatch name resolution to the correct sport resolver.
    Falls back to the raw input if the sport has no specific resolver.
    """
    sport_upper = sport.upper()

    if sport_upper == "NBA":
        return resolve_nba_name(raw)
    elif sport_upper == "NHL":
        return resolve_nhl_name(raw)
    elif sport_upper == "NFL":
        try:
            from data.nfl_client import _normalize_name as _nfl_norm
            return _nfl_norm(raw)
        except ImportError:
            pass
    elif sport_upper == "SOCCER":
        try:
            from data.soccer_client import _normalize_name as _soccer_norm
            return _soccer_norm(raw)
        except ImportError:
            pass
    elif sport_upper == "TENNIS":
        try:
            from data.tennis_client import _normalize_name as _tennis_norm
            return _tennis_norm(raw)
        except ImportError:
            pass
    elif sport_upper == "ESPORTS":
        try:
            from data.esports_client import _normalize_name as _esports_norm
            return _esports_norm(raw)
        except ImportError:
            pass

    return raw.strip()
