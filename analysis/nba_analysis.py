"""
analysis/nba_analysis.py - NBA-specific analysis helpers.

Provides prop-type parsing, position mapping, usage/minutes trend
calculation, and back-to-back detection for NBA game logs.
"""

import re
from typing import Optional
from datetime import date, timedelta

from analysis.hit_rates import minutes_trend, detect_back_to_back

# ── Prop type normalisation ────────────────────────────────────────────────────

# Accepted input strings → our canonical internal key
_PROP_ALIASES: dict[str, str] = {
    # Points
    "pts":       "PTS",
    "points":    "PTS",
    "point":     "PTS",
    # Rebounds
    "reb":       "REB",
    "rebounds":  "REB",
    "rebound":   "REB",
    "trb":       "REB",
    # Assists
    "ast":       "AST",
    "assists":   "AST",
    "assist":    "AST",
    # Threes
    "3pm":       "3PM",
    "threes":    "3PM",
    "3-pointers": "3PM",
    "three pointers": "3PM",
    "3s":        "3PM",
    "fg3m":      "3PM",
    # Blocks
    "blk":       "BLK",
    "blocks":    "BLK",
    "block":     "BLK",
    # Steals
    "stl":       "STL",
    "steals":    "STL",
    "steal":     "STL",
    # Combos
    "pra":       "PRA",
    "pts+reb+ast": "PRA",
    "points+rebounds+assists": "PRA",
    "p+r+a":     "PRA",
    "pr":        "PR",
    "pts+reb":   "PR",
    "points+rebounds": "PR",
    "pa":        "PA",
    "pts+ast":   "PA",
    "points+assists": "PA",
    "ra":        "RA",
    "reb+ast":   "RA",
    "rebounds+assists": "RA",
    "bs":        "BS",
    "blk+stl":   "BS",
    "blocks+steals": "BS",
}


def normalize_prop_type(raw: str) -> str:
    """
    Map a free-text prop string to our canonical internal key.

    Examples
    --------
    "points"       → "PTS"
    "Rebounds"     → "REB"
    "pts+reb+ast"  → "PRA"
    """
    cleaned = raw.strip().lower().replace(" ", "").replace("-", "")
    # Try direct lookup
    if cleaned in _PROP_ALIASES:
        return _PROP_ALIASES[cleaned]
    # Try with spaces preserved
    cleaned_space = raw.strip().lower()
    if cleaned_space in _PROP_ALIASES:
        return _PROP_ALIASES[cleaned_space]
    # Return uppercased original as fallback
    return raw.strip().upper()


# ── Position mapping ───────────────────────────────────────────────────────────

def map_position(raw_position: str) -> str:
    """
    Normalise a BallDontLie position string to G / F / C.

    BallDontLie may return: "G", "F", "C", "G-F", "F-C", "F-G", "C-F", ""
    """
    from analysis.defense import NBA_POSITION_MAP
    key = raw_position.strip().upper() if raw_position else ""
    return NBA_POSITION_MAP.get(key, "F")  # default to forward


# ── Usage & minutes trend ─────────────────────────────────────────────────────

def usage_and_minutes_analysis(game_logs: list[dict]) -> dict:
    """
    Compute minutes trend + a crude usage estimate (pts/reb/ast per minute).

    Returns
    -------
    dict:
        minutes_trend  : dict  {avg_l5, avg_l10, trend}
        usage_l5       : float  avg (pts+reb+ast) over L5
        usage_l10      : float  avg (pts+reb+ast) over L10
        usage_trend    : str   'up'|'down'|'flat'
    """
    min_data = minutes_trend(game_logs, is_nhl=False)

    def _sum_stats(log: dict) -> float:
        return (
            (log.get("pts") or 0)
            + (log.get("reb") or 0)
            + (log.get("ast") or 0)
        )

    valid_logs = [log for log in game_logs if log.get("min")]
    usage_vals = [_sum_stats(log) for log in valid_logs[:10]]

    usage_l5  = round(sum(usage_vals[:5]) / max(len(usage_vals[:5]), 1), 1)
    usage_l10 = round(sum(usage_vals[:10]) / max(len(usage_vals[:10]), 1), 1)

    if usage_l5 > usage_l10 + 3:
        usage_trend = "up"
    elif usage_l5 < usage_l10 - 3:
        usage_trend = "down"
    else:
        usage_trend = "flat"

    return {
        "minutes_trend": min_data,
        "usage_l5": usage_l5,
        "usage_l10": usage_l10,
        "usage_trend": usage_trend,
    }


# ── Back-to-back detection ────────────────────────────────────────────────────

def is_back_to_back(game_logs: list[dict]) -> bool:
    """
    Return True if the player played yesterday (back-to-back situation today).
    Uses the game date from the most recent log entry.
    """
    if not game_logs:
        return False
    last_date_str = game_logs[0].get("game", {}).get("date", "")
    if not last_date_str:
        return False
    try:
        last = date.fromisoformat(last_date_str[:10])
        return last == date.today() - timedelta(days=1)
    except ValueError:
        return False


# ── Injury context parsing ────────────────────────────────────────────────────

def check_injury_flag(player_id: int, injuries: list[dict]) -> tuple[bool, str]:
    """
    Check the injury list for a player.

    Returns (flagged: bool, note: str)
    """
    for injury in injuries:
        p = injury.get("player", {})
        if p.get("id") == player_id:
            status = injury.get("status", "")
            description = injury.get("return_date", "")
            note = f"{status}"
            if description:
                note += f" – return {description}"
            return True, note
    return False, ""


# ── Slip parsing ──────────────────────────────────────────────────────────────

# Regex to parse a single slip leg like:
#   "LeBron James Over 25.5 PTS"
#   "Connor McDavid Over 3.5 SOG"
#   "Anthony Davis Over 10.5 REB"
_SLIP_RE = re.compile(
    r"(?P<player>[A-Za-z][A-Za-z\.\-\' ]+?)"   # player name
    r"\s+(?P<direction>over|under)"              # over/under
    r"\s+(?P<line>\d+(?:\.\d+)?)"               # line value
    r"\s+(?P<prop>[A-Za-z\+\s]+)",              # prop type
    re.IGNORECASE,
)


def parse_slip_line(text: str) -> Optional[dict]:
    """
    Parse a single slip leg string into components.

    Returns dict with keys: player, direction, line, prop_type
    or None if parsing fails.
    """
    text = text.strip().rstrip(",;")
    m = _SLIP_RE.search(text)
    if not m:
        return None
    return {
        "player":    m.group("player").strip(),
        "direction": m.group("direction").lower(),
        "line":      float(m.group("line")),
        "prop_type": normalize_prop_type(m.group("prop").strip()),
    }


def parse_slip(slip_text: str) -> list[dict]:
    """
    Parse a full multi-leg slip string.

    Splits on commas/newlines and attempts to parse each segment.
    Returns a list of successfully parsed leg dicts.
    """
    # Split on comma or newline
    segments = re.split(r"[,\n]+", slip_text)
    legs = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        parsed = parse_slip_line(seg)
        if parsed:
            legs.append(parsed)
    return legs


# ── Alt-line suggestion ────────────────────────────────────────────────────────

def suggest_alt_lines(
    original_line: float,
    hit_rates: dict,
    direction: str = "over",
) -> list[dict]:
    """
    Suggest safer alternative lines based on historical hit-rate data.

    For 'over', suggest lower lines that improve hit rate.
    For 'under', suggest higher lines that improve hit rate.

    Returns a list of dicts: {line, estimated_hit_rate, odds_adjustment}
    """
    avg = hit_rates.get("avg", original_line)
    suggestions = []

    if direction.lower() == "over":
        # Suggest 0.5, 1.5, 2.5 lower than original
        for delta in [0.5, 1.5, 2.5]:
            new_line = original_line - delta
            if new_line <= 0:
                continue
            # Estimate hit rate: if avg is near/above original, lower line helps
            est_rate = min(hit_rates.get("l10", 0.5) + delta * 0.05, 0.95)
            suggestions.append({
                "line": new_line,
                "estimated_hit_rate": round(est_rate, 2),
                "odds_adjustment": f"-{int(delta * 20 + 110)}",  # rough juice
                "note": f"{delta} lower — safer line",
            })
    else:
        # Under: suggest higher lines
        for delta in [0.5, 1.5, 2.5]:
            new_line = original_line + delta
            est_rate = min(hit_rates.get("l10", 0.5) + delta * 0.05, 0.95)
            suggestions.append({
                "line": new_line,
                "estimated_hit_rate": round(est_rate, 2),
                "odds_adjustment": f"-{int(delta * 20 + 110)}",
                "note": f"{delta} higher — safer line",
            })

    return suggestions
