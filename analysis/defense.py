"""
analysis/defense.py - Defense rankings by position and prop type.

NBA: ranks 1–30 teams by how many points/rebounds/assists/etc. they allow
     to players at each position.
NHL: ranks 1–32 teams by shots/goals/hits allowed.

All functions are synchronous and operate on pre-fetched data dicts so
they can be called from async scoring code without additional I/O.
"""

from typing import Optional

# ── NBA position groupings ─────────────────────────────────────────────────────
# Players at "hybrid" positions are mapped to the closest primary position.
NBA_POSITION_MAP: dict[str, str] = {
    "G":   "G",
    "PG":  "G",
    "SG":  "G",
    "F":   "F",
    "SF":  "F",
    "PF":  "F",
    "C":   "C",
    "F-C": "C",
    "C-F": "C",
    "G-F": "F",
    "F-G": "F",
}

# ── Prop → defensive stat key mapping ─────────────────────────────────────────
# Maps our internal prop type to the key in the aggregated team defense dict
# produced by NBAClient.get_team_defensive_stats().
NBA_PROP_TO_DEF_KEY: dict[str, str] = {
    "PTS":   "avg_pts_allowed",
    "REB":   "avg_reb_allowed",
    "AST":   "avg_ast_allowed",
    "3PM":   "avg_threes_allowed",
    "BLK":   "avg_blk_allowed",
    "STL":   "avg_stl_allowed",
    "PRA":   "avg_pts_allowed",   # use points as primary signal
    "PR":    "avg_pts_allowed",
    "PA":    "avg_pts_allowed",
    "RA":    "avg_reb_allowed",
    # alternate spellings
    "POINTS":   "avg_pts_allowed",
    "REBOUNDS": "avg_reb_allowed",
    "ASSISTS":  "avg_ast_allowed",
    "THREES":   "avg_threes_allowed",
    "BLOCKS":   "avg_blk_allowed",
    "STEALS":   "avg_stl_allowed",
}

# NHL prop → defensive key mapping
# NHL standings provide goalsAgainstPctg / goalsAgainst / shotsAgainst
NHL_PROP_TO_DEF_KEY: dict[str, str] = {
    "SOG":    "shotsAgainstPerGame",
    "SHOTS":  "shotsAgainstPerGame",
    "GOALS":  "goalsAgainst",
    "ASSISTS": "goalsAgainst",
    "POINTS": "goalsAgainst",
    "HITS":   "goalsAgainst",    # no direct hit-allowed stat; use GA as proxy
    "BLOCKS": "goalsAgainst",
    "SAVES":  "shotsAgainstPerGame",
}

# Human-readable label for ordinal suffixes
def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


def _rank_teams(
    teams: list[dict],
    stat_key: str,
    higher_is_worse: bool = True,
) -> list[tuple[int, dict]]:
    """
    Sort teams by `stat_key` and return (rank, team_dict) tuples.
    `higher_is_worse=True` means the team that allows the most is rank 1
    (best matchup for over bets).
    """
    valid = [t for t in teams if stat_key in t and t[stat_key] is not None]
    valid.sort(key=lambda t: t[stat_key], reverse=higher_is_worse)
    return [(i + 1, t) for i, t in enumerate(valid)]


def get_defense_rank(
    team_id: int,
    position: str,
    prop_type: str,
    all_team_stats: list[dict],
) -> dict:
    """
    Return the defensive rank (1 = easiest matchup) for an NBA team against
    a given position and prop type.

    Parameters
    ----------
    team_id        : int   – The team's BallDontLie team ID
    position       : str   – Player position (PG, SG, SF, PF, C, G, F)
    prop_type      : str   – Internal prop type (PTS, REB, etc.)
    all_team_stats : list  – Output of NBAClient.get_team_defensive_stats()

    Returns
    -------
    dict:
        rank        : int   (1–30)
        total_teams : int
        label       : str   e.g. "27th easiest"
        value       : float  the raw stat allowed
        rating      : str   'elite'|'good'|'average'|'poor'|'terrible'
    """
    prop_key = prop_type.upper().replace(" ", "")
    stat_key = NBA_PROP_TO_DEF_KEY.get(prop_key, "avg_pts_allowed")

    ranked = _rank_teams(all_team_stats, stat_key, higher_is_worse=True)
    total = len(ranked)

    # Find our team
    team_rank = None
    team_value = None
    for rank, team in ranked:
        if team.get("team_id") == team_id:
            team_rank = rank
            team_value = team.get(stat_key, 0.0)
            break

    if team_rank is None:
        # Team not found in data — return neutral
        return {
            "rank": total // 2,
            "total_teams": total or 30,
            "label": "N/A",
            "value": 0.0,
            "rating": "average",
        }

    # Rating buckets (rank 1–30, lower rank = harder defense)
    pct = team_rank / max(total, 1)
    if pct >= 0.87:
        rating = "terrible"   # bottom 4 teams — bad defense, great for overs
    elif pct >= 0.67:
        rating = "poor"
    elif pct >= 0.40:
        rating = "average"
    elif pct >= 0.20:
        rating = "good"
    else:
        rating = "elite"      # top-6 defense — hard to hit overs

    return {
        "rank": team_rank,
        "total_teams": total,
        "label": f"{_ordinal(team_rank)} easiest",
        "value": round(team_value or 0.0, 2),
        "rating": rating,
    }


def get_nhl_defense_rank(
    team_abbrev: str,
    prop_type: str,
    all_standings: list[dict],
) -> dict:
    """
    Return the defensive rank for an NHL team against a given prop type.

    Parameters
    ----------
    team_abbrev   : str  – e.g. "TOR", "EDM"
    prop_type     : str  – e.g. "SOG", "GOALS"
    all_standings : list – Output of NHLClient.get_all_teams_stats()

    Returns
    -------
    Same structure as get_defense_rank.
    """
    prop_key = prop_type.upper().replace(" ", "")
    stat_key = NHL_PROP_TO_DEF_KEY.get(prop_key, "goalsAgainst")

    # NHL standings have nested teamAbbrev
    def _get_abbrev(team: dict) -> str:
        return team.get("teamAbbrev", {}).get("default", "") if isinstance(
            team.get("teamAbbrev"), dict
        ) else team.get("teamAbbrev", "")

    # Build list with consistent stat_key access
    enriched = []
    for t in all_standings:
        val = t.get(stat_key)
        if val is None:
            # Some stats need to be derived
            if stat_key == "shotsAgainstPerGame":
                val = t.get("shotsAgainstPerGame") or t.get("shotsAgainst", 0)
            else:
                val = t.get("goalsAgainst", 0)
        enriched.append({**t, stat_key: val})

    ranked = _rank_teams(enriched, stat_key, higher_is_worse=True)
    total = len(ranked)

    team_rank = None
    team_value = None
    for rank, team in ranked:
        if _get_abbrev(team).upper() == team_abbrev.upper():
            team_rank = rank
            team_value = team.get(stat_key, 0.0)
            break

    if team_rank is None:
        return {
            "rank": total // 2,
            "total_teams": total or 32,
            "label": "N/A",
            "value": 0.0,
            "rating": "average",
        }

    pct = team_rank / max(total, 1)
    if pct >= 0.875:
        rating = "terrible"
    elif pct >= 0.625:
        rating = "poor"
    elif pct >= 0.375:
        rating = "average"
    elif pct >= 0.125:
        rating = "good"
    else:
        rating = "elite"

    return {
        "rank": team_rank,
        "total_teams": total,
        "label": f"{_ordinal(team_rank)} easiest",
        "value": round(float(team_value or 0.0), 2),
        "rating": rating,
    }


def defense_rank_score_adjustment(defense_rank: dict) -> float:
    """
    Convert a defense rank dict into a confidence adjustment score (-15 to +15).

    Elite defense → penalise the pick.
    Terrible defense → boost the pick.
    """
    rating = defense_rank.get("rating", "average")
    return {
        "elite":    -15.0,
        "good":      -7.0,
        "average":    0.0,
        "poor":       7.0,
        "terrible":  15.0,
    }.get(rating, 0.0)
