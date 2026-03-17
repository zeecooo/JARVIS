"""
analysis/nhl_analysis.py - NHL-specific analysis helpers.

Handles prop-type parsing, goalie vs skater detection, TOI trend,
and slip parsing for NHL props.
"""

import re
from typing import Optional
from datetime import date, timedelta

from analysis.hit_rates import minutes_trend

# ── Prop type normalisation ────────────────────────────────────────────────────

_NHL_PROP_ALIASES: dict[str, str] = {
    # Shots on goal
    "sog":             "SOG",
    "shots":           "SOG",
    "shots on goal":   "SOG",
    "shots on net":    "SOG",
    # Goals
    "goals":           "GOALS",
    "goal":            "GOALS",
    "g":               "GOALS",
    # Assists
    "assists":         "ASSISTS",
    "assist":          "ASSISTS",
    "a":               "ASSISTS",
    # Points (goals + assists)
    "points":          "POINTS",
    "point":           "POINTS",
    "pts":             "POINTS",
    # Hits
    "hits":            "HITS",
    "hit":             "HITS",
    # Blocked shots
    "blocked":         "BLOCKS",
    "blocked shots":   "BLOCKS",
    "blocks":          "BLOCKS",
    "bs":              "BLOCKS",
    # Saves (goalies)
    "saves":           "SAVES",
    "save":            "SAVES",
    # Faceoffs
    "faceoffs":        "FOW",
    "faceoff wins":    "FOW",
    "fow":             "FOW",
    "fo":              "FOW",
    # Penalty minutes
    "pim":             "PIM",
    "penalty minutes": "PIM",
}


def normalize_nhl_prop(raw: str) -> str:
    """Map a free-text NHL prop string to our canonical key."""
    cleaned = raw.strip().lower()
    if cleaned in _NHL_PROP_ALIASES:
        return _NHL_PROP_ALIASES[cleaned]
    return raw.strip().upper()


# ── Position detection ────────────────────────────────────────────────────────

def is_goalie(player_data: dict) -> bool:
    """
    Return True if the NHL player record indicates a goalie.
    Works with both NHL API player landing and roster formats.
    """
    pos = (
        player_data.get("positionCode", "")
        or player_data.get("position", {}).get("code", "")
        or ""
    )
    return pos.upper() == "G"


def is_skater(player_data: dict) -> bool:
    return not is_goalie(player_data)


# ── TOI (Time on Ice) trend ────────────────────────────────────────────────────

def toi_trend(game_logs: list[dict]) -> dict:
    """
    Compute ice-time trend from NHL game logs.

    Returns: {avg_l5, avg_l10, trend: 'up'|'down'|'flat'}
    """
    return minutes_trend(game_logs, is_nhl=True)


def _parse_toi(toi_str: str) -> float:
    """Convert 'MM:SS' TOI string to decimal minutes."""
    if not toi_str or ":" not in toi_str:
        return 0.0
    parts = toi_str.split(":")
    try:
        return int(parts[0]) + int(parts[1]) / 60
    except (ValueError, IndexError):
        return 0.0


def usage_analysis(game_logs: list[dict]) -> dict:
    """
    For skaters: compute points-per-game and shots-per-game trends.
    For goalies: compute saves-per-game trend.

    Returns dict with relevant averages.
    """
    def _get(log: dict, key: str) -> float:
        return float(log.get(key) or 0)

    # Shots
    shots_vals = [_get(log, "shots") for log in game_logs[:10]]
    goals_vals = [_get(log, "goals") for log in game_logs[:10]]
    assists_vals = [_get(log, "assists") for log in game_logs[:10]]
    saves_vals = [_get(log, "saves") for log in game_logs[:10]]

    def avg(lst: list, n: int) -> float:
        w = lst[:n]
        return round(sum(w) / max(len(w), 1), 2)

    return {
        "avg_shots_l5":   avg(shots_vals, 5),
        "avg_shots_l10":  avg(shots_vals, 10),
        "avg_goals_l5":   avg(goals_vals, 5),
        "avg_goals_l10":  avg(goals_vals, 10),
        "avg_assists_l5": avg(assists_vals, 5),
        "avg_assists_l10": avg(assists_vals, 10),
        "avg_saves_l5":   avg(saves_vals, 5),
        "avg_saves_l10":  avg(saves_vals, 10),
        "toi_trend":      toi_trend(game_logs),
    }


# ── Back-to-back detection ────────────────────────────────────────────────────

def is_back_to_back(game_logs: list[dict]) -> bool:
    """Return True if the player played yesterday."""
    if not game_logs:
        return False
    last_str = game_logs[0].get("gameDate", "")
    if not last_str:
        return False
    try:
        last = date.fromisoformat(last_str[:10])
        return last == date.today() - timedelta(days=1)
    except ValueError:
        return False


# ── Slip parsing ──────────────────────────────────────────────────────────────

_NHL_SLIP_RE = re.compile(
    r"(?P<player>[A-Za-z][A-Za-z\.\-\' ]+?)"
    r"\s+(?P<direction>over|under)"
    r"\s+(?P<line>\d+(?:\.\d+)?)"
    r"\s+(?P<prop>[A-Za-z\s\-]+)",
    re.IGNORECASE,
)


def parse_nhl_slip_line(text: str) -> Optional[dict]:
    """Parse a single NHL slip leg. Returns dict or None."""
    text = text.strip().rstrip(",;")
    m = _NHL_SLIP_RE.search(text)
    if not m:
        return None
    return {
        "player":    m.group("player").strip(),
        "direction": m.group("direction").lower(),
        "line":      float(m.group("line")),
        "prop_type": normalize_nhl_prop(m.group("prop").strip()),
        "sport":     "NHL",
    }


# ── Alt-line suggestions ──────────────────────────────────────────────────────

def suggest_nhl_alt_lines(
    original_line: float,
    hit_rates: dict,
    prop_type: str,
    direction: str = "over",
) -> list[dict]:
    """
    Suggest safer alt lines for NHL props.
    SOG lines can safely drop to 2.5 / 1.5 from 3.5+.
    """
    suggestions = []
    step = 0.5 if prop_type in ("GOALS", "ASSISTS", "POINTS") else 1.0

    if direction.lower() == "over":
        for delta in [step, step * 2, step * 3]:
            new_line = original_line - delta
            if new_line <= 0:
                continue
            est_rate = min(hit_rates.get("l10", 0.5) + delta * 0.04, 0.92)
            suggestions.append({
                "line": new_line,
                "estimated_hit_rate": round(est_rate, 2),
                "odds_adjustment": f"-{int(delta * 15 + 115)}",
                "note": f"{delta} lower — safer line",
            })
    else:
        for delta in [step, step * 2, step * 3]:
            new_line = original_line + delta
            est_rate = min(hit_rates.get("l10", 0.5) + delta * 0.04, 0.92)
            suggestions.append({
                "line": new_line,
                "estimated_hit_rate": round(est_rate, 2),
                "odds_adjustment": f"-{int(delta * 15 + 115)}",
                "note": f"{delta} higher — safer line",
            })

    return suggestions
