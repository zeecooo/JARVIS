"""
analysis/hit_rates.py - Calculate historical hit rates from game logs.

Works for both NBA (BallDontLie game log format) and NHL (NHL API game log format).

Hit rate = fraction of games where the player exceeded the given line.
"""

from typing import Optional

# ── NBA field mapping ──────────────────────────────────────────────────────────
# Maps our internal prop_type names to the BallDontLie stat field names.
NBA_FIELD_MAP: dict[str, list[str]] = {
    "PTS": ["pts"],
    "REB": ["reb"],
    "AST": ["ast"],
    "3PM": ["fg3m"],
    "BLK": ["blk"],
    "STL": ["stl"],
    "PRA": ["pts", "reb", "ast"],          # Points + Rebounds + Assists
    "PR":  ["pts", "reb"],                  # Points + Rebounds
    "PA":  ["pts", "ast"],                  # Points + Assists
    "RA":  ["reb", "ast"],                  # Rebounds + Assists
    "BS":  ["blk", "stl"],                  # Blocks + Steals
    # Accept alternate spellings
    "POINTS": ["pts"],
    "REBOUNDS": ["reb"],
    "ASSISTS": ["ast"],
    "THREES": ["fg3m"],
    "BLOCKS": ["blk"],
    "STEALS": ["stl"],
    "PTS+REB+AST": ["pts", "reb", "ast"],
    "PTS+REB": ["pts", "reb"],
    "PTS+AST": ["pts", "ast"],
    "REB+AST": ["reb", "ast"],
}

# ── NHL field mapping ──────────────────────────────────────────────────────────
# Maps our prop names to NHL API gameLog field names.
NHL_FIELD_MAP: dict[str, list[str]] = {
    "SOG":    ["shots"],
    "SHOTS":  ["shots"],
    "GOALS":  ["goals"],
    "ASSISTS": ["assists"],
    "POINTS": ["goals", "assists"],
    "HITS":   ["hits"],
    "BLOCKS": ["blockedShots"],
    "BS":     ["blockedShots"],
    "SAVES":  ["saves"],
    "FACEOFFS": ["faceoffWinningPctg"],   # raw pct, handle separately
    "FOW":    ["faceoffWinningPctg"],
    "PIM":    ["pim"],
}


def _extract_value(log: dict, fields: list[str], is_nhl: bool = False) -> Optional[float]:
    """
    Sum the requested stat fields from a single game log entry.
    Returns None if all fields are missing/null (player didn't play).
    """
    total = 0.0
    any_found = False
    for field in fields:
        val = log.get(field)
        if val is None:
            # Try nested 'game' dict (BallDontLie format)
            if not is_nhl:
                val = log.get("game", {}).get(field)
        if val is not None:
            try:
                total += float(val)
                any_found = True
            except (TypeError, ValueError):
                pass
    return total if any_found else None


def _get_nba_value(log: dict, prop_type: str) -> Optional[float]:
    """Extract the relevant stat from a BallDontLie game log dict."""
    key = prop_type.upper().replace(" ", "").replace("+", "+")
    fields = NBA_FIELD_MAP.get(key)
    if not fields:
        return None
    return _extract_value(log, fields, is_nhl=False)


def _get_nhl_value(log: dict, prop_type: str) -> Optional[float]:
    """Extract the relevant stat from an NHL API game log dict."""
    key = prop_type.upper().replace(" ", "")
    fields = NHL_FIELD_MAP.get(key)
    if not fields:
        return None
    return _extract_value(log, fields, is_nhl=True)


def calculate_hit_rates(
    game_logs: list[dict],
    prop_type: str,
    line: float,
    is_nhl: bool = False,
) -> dict:
    """
    Calculate L5 / L10 / L20 hit rates for a player against a given line.

    Parameters
    ----------
    game_logs : list[dict]
        Game log entries sorted newest → oldest.  BallDontLie or NHL format.
    prop_type : str
        Internal prop type string e.g. "PTS", "REB", "SOG".
    line      : float
        The prop line to test against (player must exceed to count as hit).
    is_nhl    : bool
        If True, use NHL field mapping.

    Returns
    -------
    dict with keys:
        l5    : float  – fraction hit in last 5 games (0.0–1.0)
        l10   : float  – fraction hit in last 10 games
        l20   : float  – fraction hit in last 20 games
        trend : str    – 'up' | 'down' | 'flat'
        avg   : float  – average stat value over available window
        games : int    – number of valid games used
    """
    extractor = _get_nhl_value if is_nhl else _get_nba_value

    # Pull valid values (skip DNP / missing)
    values: list[float] = []
    for log in game_logs:
        val = extractor(log, prop_type)
        if val is not None:
            values.append(val)
        if len(values) >= 20:
            break

    if not values:
        return {
            "l5": 0.0,
            "l10": 0.0,
            "l20": 0.0,
            "trend": "flat",
            "avg": 0.0,
            "games": 0,
        }

    def _rate(window: list[float]) -> float:
        if not window:
            return 0.0
        hits = sum(1 for v in window if v > line)
        return round(hits / len(window), 3)

    l5_vals  = values[:5]
    l10_vals = values[:10]
    l20_vals = values[:20]

    l5_rate  = _rate(l5_vals)
    l10_rate = _rate(l10_vals)
    l20_rate = _rate(l20_vals)

    # Trend: compare L5 rate to L20 rate
    if len(values) >= 10:
        diff = l5_rate - l10_rate
        if diff >= 0.15:
            trend = "up"
        elif diff <= -0.15:
            trend = "down"
        else:
            trend = "flat"
    else:
        trend = "flat"

    avg = round(sum(values) / len(values), 2) if values else 0.0

    return {
        "l5":   l5_rate,
        "l10":  l10_rate,
        "l20":  l20_rate,
        "trend": trend,
        "avg":  avg,
        "games": len(values),
    }


def calculate_home_away_rate(
    game_logs: list[dict],
    prop_type: str,
    line: float,
    player_team_id: Optional[int] = None,
    is_home: bool = True,
    is_nhl: bool = False,
) -> float:
    """
    Hit rate filtered to home or away games.

    For NBA logs, checks game.home_team_id vs the player's team.
    For NHL logs, checks homeRoadFlag == 'H' or 'R'.
    """
    extractor = _get_nhl_value if is_nhl else _get_nba_value
    hits = 0
    total = 0

    for log in game_logs:
        if is_nhl:
            flag = log.get("homeRoadFlag", "")
            game_is_home = flag == "H"
        else:
            game = log.get("game", {})
            if player_team_id:
                game_is_home = game.get("home_team_id") == player_team_id
            else:
                game_is_home = True  # fallback: count all

        if game_is_home != is_home:
            continue

        val = extractor(log, prop_type)
        if val is not None:
            total += 1
            if val > line:
                hits += 1

    return round(hits / total, 3) if total > 0 else 0.0


def detect_back_to_back(game_logs: list[dict], is_nhl: bool = False) -> bool:
    """
    Return True if the most recent game was played the day before the
    second-most-recent game (i.e., the player just played last night).
    """
    if len(game_logs) < 2:
        return False

    date_field = "gameDate" if is_nhl else "game.date"

    def _get_date(log: dict) -> str:
        if is_nhl:
            return log.get("gameDate", "")
        return log.get("game", {}).get("date", "")

    dates = [_get_date(log) for log in game_logs[:2] if _get_date(log)]
    if len(dates) < 2:
        return False

    try:
        from datetime import date as dt_date
        d0 = dt_date.fromisoformat(dates[0][:10])
        d1 = dt_date.fromisoformat(dates[1][:10])
        return (d0 - d1).days == 1
    except (ValueError, TypeError):
        return False


def minutes_trend(game_logs: list[dict], is_nhl: bool = False) -> dict:
    """
    Return minutes/TOI trend over the last 5 and 10 games.

    Returns: {avg_l5: float, avg_l10: float, trend: 'up'|'down'|'flat'}
    """
    def _get_min(log: dict) -> Optional[float]:
        if is_nhl:
            toi = log.get("toi", "")
            # Format: "MM:SS"
            if toi and ":" in toi:
                parts = toi.split(":")
                try:
                    return int(parts[0]) + int(parts[1]) / 60
                except (ValueError, IndexError):
                    return None
            return None
        else:
            raw = log.get("min", "")
            # BallDontLie format: "35:12" or just "35"
            if raw and ":" in str(raw):
                parts = str(raw).split(":")
                try:
                    return int(parts[0]) + int(parts[1]) / 60
                except (ValueError, IndexError):
                    return None
            try:
                return float(raw) if raw else None
            except (TypeError, ValueError):
                return None

    mins = [_get_min(log) for log in game_logs[:10]]
    mins = [m for m in mins if m is not None]

    if not mins:
        return {"avg_l5": 0.0, "avg_l10": 0.0, "trend": "flat"}

    l5  = mins[:5]
    l10 = mins[:10]

    avg_l5  = round(sum(l5) / len(l5), 1) if l5 else 0.0
    avg_l10 = round(sum(l10) / len(l10), 1) if l10 else 0.0

    if avg_l5 > avg_l10 + 2:
        trend = "up"
    elif avg_l5 < avg_l10 - 2:
        trend = "down"
    else:
        trend = "flat"

    return {"avg_l5": avg_l5, "avg_l10": avg_l10, "trend": trend}
