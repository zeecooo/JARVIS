"""
analysis/engine.py - Central scoring engine for Jarvis.

The `score_pick` coroutine aggregates all signals (hit rates, defense rank,
H2H, home/away, minutes trend, B2B, injuries) into a single PickResult
dataclass with a 0–100 confidence score and recommendation label.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from data.nba_client import NBAClient
    from data.nhl_client import NHLClient
    from data.nfl_client import NFLClient
    from data.soccer_client import SoccerClient
    from data.tennis_client import TennisClient
    from data.esports_client import EsportsClient

import config
from analysis.hit_rates import (
    calculate_hit_rates,
    calculate_home_away_rate,
    detect_back_to_back,
    minutes_trend,
)
from analysis.defense import (
    get_defense_rank,
    get_nhl_defense_rank,
    defense_rank_score_adjustment,
)
from analysis.nba_analysis import (
    normalize_prop_type,
    is_back_to_back as nba_b2b,
    check_injury_flag,
    usage_and_minutes_analysis,
)
from analysis.nhl_analysis import (
    normalize_nhl_prop,
    is_back_to_back as nhl_b2b,
    usage_analysis as nhl_usage,
    is_goalie,
)

log = logging.getLogger(__name__)

# ── PickResult dataclass ───────────────────────────────────────────────────────

@dataclass
class PickResult:
    """
    The full output of the scoring engine for a single pick.
    """
    # Player / game context
    player_name:    str = ""
    team:           str = ""
    opponent:       str = ""
    prop_type:      str = ""
    line:           float = 0.0
    direction:      str = "over"
    sport:          str = "NBA"
    odds:           str = "-110"

    # Core signal scores (0.0–1.0 unless noted)
    confidence:     int = 50          # 0–100
    recommendation: str = "LEAN"      # LOCK / SHARP / LEAN / SKIP

    # Hit rates
    hit_rates:      dict = field(default_factory=dict)
    # {l5, l10, l20, trend, avg, games}

    # Defense rank
    defense_rank:   int = 15
    defense_label:  str = ""
    defense_rating: str = "average"

    # H2H
    h2h_rate:       float = 0.0
    h2h_games:      int = 0

    # Home / away split
    home_away_rate: float = 0.0
    is_home:        bool = True

    # Minutes / usage
    min_trend:      str = "flat"       # 'up'|'down'|'flat'
    avg_minutes:    float = 0.0

    # Flags
    back_to_back:   bool = False
    injury_flag:    bool = False
    injury_note:    str = ""

    # Human-readable reasoning bullet points
    reasoning:      list = field(default_factory=list)


# ── Confidence label mapping ───────────────────────────────────────────────────

def _label(confidence: int) -> str:
    if confidence >= config.LOCK_THRESHOLD:
        return "LOCK"
    if confidence >= config.SHARP_THRESHOLD:
        return "SHARP"
    if confidence >= config.LEAN_THRESHOLD:
        return "LEAN"
    return "SKIP"


# ── Main scoring coroutine ────────────────────────────────────────────────────

async def score_pick(
    player_name: str,
    prop_type: str,
    line: float,
    opponent_team: str,
    is_home: bool,
    nba_client: "NBAClient" = None,
    nhl_client: "NHLClient" = None,
    direction: str = "over",
    sport: str = "NBA",
    injuries: Optional[list] = None,
    all_team_stats: Optional[list] = None,
    all_nhl_standings: Optional[list] = None,
    # Multi-sport clients (pass bot or individual clients)
    bot=None,
    nfl_client: "NFLClient" = None,
    soccer_client: "SoccerClient" = None,
    tennis_client: "TennisClient" = None,
    esports_client: "EsportsClient" = None,
    # Sport-specific context
    league: str = "eng.1",
    surface: str = "hard",
    tour: str = "atp",
    game: str = "csgo",
) -> PickResult:
    """
    Score a single pick and return a populated PickResult.
    Supports NBA, NHL, NFL, SOCCER, TENNIS, ESPORTS.

    Pass bot=<JarvisBot> for all-sport support, or individual client kwargs.
    Legacy callers passing nba_client/nhl_client directly still work.
    """
    # Resolve clients from bot if provided
    _nba = nba_client or (bot.nba_client if bot and hasattr(bot, "nba_client") else None)
    _nhl = nhl_client or (bot.nhl_client if bot and hasattr(bot, "nhl_client") else None)
    _nfl = nfl_client or (bot.nfl_client if bot and hasattr(bot, "nfl_client") else None)
    _soccer = soccer_client or (bot.soccer_client if bot and hasattr(bot, "soccer_client") else None)
    _tennis = tennis_client or (bot.tennis_client if bot and hasattr(bot, "tennis_client") else None)
    _esports = esports_client or (bot.esports_client if bot and hasattr(bot, "esports_client") else None)

    result = PickResult(
        player_name=player_name,
        prop_type=prop_type,
        line=line,
        direction=direction,
        sport=sport.upper(),
        opponent=opponent_team,
        is_home=is_home,
    )

    reasoning: list[str] = []
    confidence_base = 50.0

    sport_upper = sport.upper()

    if sport_upper == "NBA":
        result = await _score_nba(
            result, reasoning, confidence_base,
            _nba, injuries, all_team_stats,
        )
    elif sport_upper == "NHL":
        result = await _score_nhl(
            result, reasoning, confidence_base,
            _nhl, all_nhl_standings,
        )
    elif sport_upper == "NFL":
        result = await _score_nfl(result, reasoning, confidence_base, _nfl)
    elif sport_upper == "SOCCER":
        result = await _score_soccer(result, reasoning, confidence_base, _soccer, league)
    elif sport_upper == "TENNIS":
        result = await _score_tennis(result, reasoning, confidence_base, _tennis, surface, tour)
    elif sport_upper == "ESPORTS":
        result = await _score_esports(result, reasoning, confidence_base, _esports, game)
    else:
        reasoning.append(f"⚠️ Sport '{sport}' not yet supported — manual review recommended.")
        result.confidence = 50

    result.reasoning = reasoning
    result.recommendation = _label(result.confidence)
    return result


# ── NBA scoring ───────────────────────────────────────────────────────────────

async def _score_nba(
    result: PickResult,
    reasoning: list,
    base: float,
    nba_client,
    injuries: Optional[list],
    all_team_stats: Optional[list],
) -> PickResult:
    """Internal NBA analysis pipeline."""
    prop = normalize_prop_type(result.prop_type)
    result.prop_type = prop

    # 1. Fetch player
    player = await nba_client.get_player(result.player_name)
    if not player:
        reasoning.append(f"⚠️ Could not find player '{result.player_name}' in database.")
        result.confidence = 40
        return result

    player_id = player.get("id")
    result.player_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
    team_info = player.get("team", {})
    result.team = team_info.get("abbreviation", "") if team_info else ""
    player_team_id = team_info.get("id") if team_info else None
    position = player.get("position", "F")

    # 2. Fetch game logs
    logs = await nba_client.get_player_game_logs(player_id, last_n=20)

    if not logs:
        reasoning.append("⚠️ No recent game logs found — limited analysis available.")
        result.confidence = 45
        return result

    # 3. Hit rates
    hr = calculate_hit_rates(logs, prop, result.line, is_nhl=False)
    result.hit_rates = hr

    # Weighted hit rate score: L5 × 0.45 + L10 × 0.35 + L20 × 0.20
    hit_score = hr["l5"] * 0.45 + hr["l10"] * 0.35 + hr["l20"] * 0.20
    confidence_adjustment = (hit_score - 0.5) * 60  # ±30 points max
    base += confidence_adjustment

    reasoning.append(
        f"📊 Hit rates: L5={hr['l5']:.0%}  L10={hr['l10']:.0%}  L20={hr['l20']:.0%}  (avg {hr['avg']})"
    )

    # Trend modifier
    if hr["trend"] == "up":
        base += 5
        reasoning.append("📈 Hit rate trending UP over L5 vs L10.")
    elif hr["trend"] == "down":
        base -= 5
        reasoning.append("📉 Hit rate trending DOWN over L5 vs L10.")

    # 4. H2H
    # Find opponent team ID
    opp_team = await _find_team_by_name(nba_client, result.opponent)
    opp_team_id = opp_team.get("id") if opp_team else None

    if opp_team_id:
        h2h_logs = await nba_client.get_h2h_games(player_id, opp_team_id, last_n=10)
        if h2h_logs:
            h2h_hr = calculate_hit_rates(h2h_logs, prop, result.line, is_nhl=False)
            result.h2h_rate = h2h_hr["l5"]  # use L5 for recency
            result.h2h_games = h2h_hr["games"]
            h2h_adj = (h2h_hr["l5"] - 0.5) * 20
            base += h2h_adj
            reasoning.append(
                f"🔁 H2H vs {result.opponent}: "
                f"{result.h2h_rate:.0%} hit rate over {result.h2h_games} games."
            )

    # 5. Home / away split
    home_rate = calculate_home_away_rate(
        logs, prop, result.line,
        player_team_id=player_team_id,
        is_home=result.is_home,
        is_nhl=False,
    )
    result.home_away_rate = home_rate
    ha_adj = (home_rate - 0.5) * 15
    base += ha_adj
    loc = "Home" if result.is_home else "Away"
    reasoning.append(f"🏠 {loc} split: {home_rate:.0%} hit rate.")

    # 6. Defense rank
    if all_team_stats and opp_team_id:
        dr = get_defense_rank(opp_team_id, position, prop, all_team_stats)
        result.defense_rank = dr["rank"]
        result.defense_label = dr["label"]
        result.defense_rating = dr["rating"]
        dr_adj = defense_rank_score_adjustment(dr)
        base += dr_adj
        reasoning.append(
            f"🛡️ {result.opponent} defense: {dr['label']} vs {position} "
            f"({dr['rating']}) — {dr['value']} avg allowed."
        )

    # 7. Minutes / usage trend
    usage = usage_and_minutes_analysis(logs)
    mt = usage["minutes_trend"]
    result.avg_minutes = mt["avg_l5"]
    result.min_trend = mt["trend"]

    if mt["trend"] == "up":
        base += 3
        reasoning.append(f"⏱️ Minutes trending UP (L5 avg {mt['avg_l5']} min).")
    elif mt["trend"] == "down":
        base -= 5
        reasoning.append(f"⏱️ Minutes trending DOWN (L5 avg {mt['avg_l5']} min).")
    else:
        reasoning.append(f"⏱️ Minutes stable (L5 avg {mt['avg_l5']} min).")

    # Usage signal
    if usage["usage_trend"] == "up":
        base += 3
        reasoning.append("📈 Usage trending up over L5 (more pts+reb+ast per game).")
    elif usage["usage_trend"] == "down":
        base -= 3

    # 8. Back-to-back
    b2b = nba_b2b(logs)
    result.back_to_back = b2b
    if b2b:
        base -= 8
        reasoning.append("⚠️ BACK-TO-BACK — player played yesterday, fatigue risk.")

    # 9. Injury check
    if injuries is None:
        injuries = await nba_client.get_injuries()
    flagged, note = check_injury_flag(player_id, injuries)
    result.injury_flag = flagged
    result.injury_note = note
    if flagged:
        base -= 20
        reasoning.append(f"🚑 INJURY FLAG: {note}")

    result.confidence = max(0, min(100, int(round(base))))
    return result


# ── NHL scoring ───────────────────────────────────────────────────────────────

async def _score_nhl(
    result: PickResult,
    reasoning: list,
    base: float,
    nhl_client,
    all_nhl_standings: Optional[list],
) -> PickResult:
    """Internal NHL analysis pipeline."""
    prop = normalize_nhl_prop(result.prop_type)
    result.prop_type = prop

    # 1. Fetch player
    player = await nhl_client.get_player(result.player_name)
    if not player:
        reasoning.append(f"⚠️ Could not find NHL player '{result.player_name}'.")
        result.confidence = 40
        return result

    player_id = player.get("playerId") or player.get("id")
    first = player.get("firstName", {})
    last  = player.get("lastName", {})
    if isinstance(first, dict):
        first = first.get("default", "")
    if isinstance(last, dict):
        last = last.get("default", "")
    result.player_name = f"{first} {last}".strip()

    # 2. Fetch game logs
    from analysis.nhl_analysis import _current_nhl_season as _cns
    season = _cns() if hasattr(_cns, "__call__") else "20242025"

    # Import the season helper from nhl_client module
    from data.nhl_client import _current_nhl_season
    season = _current_nhl_season()

    logs = await nhl_client.get_player_game_logs(player_id, season=season)

    if not logs:
        reasoning.append("⚠️ No recent NHL game logs found.")
        result.confidence = 45
        return result

    # 3. Hit rates
    hr = calculate_hit_rates(logs, prop, result.line, is_nhl=True)
    result.hit_rates = hr

    hit_score = hr["l5"] * 0.45 + hr["l10"] * 0.35 + hr["l20"] * 0.20
    confidence_adjustment = (hit_score - 0.5) * 60
    base += confidence_adjustment

    reasoning.append(
        f"📊 Hit rates: L5={hr['l5']:.0%}  L10={hr['l10']:.0%}  L20={hr['l20']:.0%}  (avg {hr['avg']})"
    )

    if hr["trend"] == "up":
        base += 5
        reasoning.append("📈 Hit rate trending UP over L5 vs L10.")
    elif hr["trend"] == "down":
        base -= 5
        reasoning.append("📉 Hit rate trending DOWN.")

    # 4. Home / away
    from analysis.hit_rates import calculate_home_away_rate
    home_rate = calculate_home_away_rate(
        logs, prop, result.line, is_home=result.is_home, is_nhl=True
    )
    result.home_away_rate = home_rate
    base += (home_rate - 0.5) * 15
    loc = "Home" if result.is_home else "Away"
    reasoning.append(f"🏠 {loc} split: {home_rate:.0%} hit rate.")

    # 5. Defense rank
    if all_nhl_standings:
        opp_abbrev = result.opponent.upper()
        dr = get_nhl_defense_rank(opp_abbrev, prop, all_nhl_standings)
        result.defense_rank = dr["rank"]
        result.defense_label = dr["label"]
        result.defense_rating = dr["rating"]
        base += defense_rank_score_adjustment(dr)
        reasoning.append(
            f"🛡️ {result.opponent} defense: {dr['label']} "
            f"({dr['rating']})."
        )

    # 6. TOI trend
    usage = nhl_usage(logs)
    toi = usage["toi_trend"]
    result.avg_minutes = toi.get("avg_l5", 0.0)
    result.min_trend = toi.get("trend", "flat")

    if toi.get("trend") == "up":
        base += 3
        reasoning.append(f"⏱️ TOI trending UP (L5 avg {toi.get('avg_l5', 0):.1f} min).")
    elif toi.get("trend") == "down":
        base -= 4
        reasoning.append(f"⏱️ TOI trending DOWN (L5 avg {toi.get('avg_l5', 0):.1f} min).")

    # 7. Back-to-back
    from analysis.nhl_analysis import is_back_to_back as nhl_b2b
    b2b = nhl_b2b(logs)
    result.back_to_back = b2b
    if b2b:
        base -= 8
        reasoning.append("⚠️ BACK-TO-BACK — fatigue risk.")

    result.confidence = max(0, min(100, int(round(base))))
    return result


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _find_team_by_name(nba_client, team_name: str) -> dict:
    """Find an NBA team by full name or abbreviation."""
    teams = await nba_client.get_teams()
    name_lower = team_name.strip().lower()
    for team in teams:
        abbr = team.get("abbreviation", "").lower()
        full = team.get("full_name", "").lower()
        city = team.get("city", "").lower()
        nick = team.get("name", "").lower()
        if name_lower in (abbr, full, city, nick):
            return team
        if name_lower in full or name_lower in abbr:
            return team
    return {}


# ── NFL scoring ────────────────────────────────────────────────────────────────

async def _score_nfl(
    result: PickResult,
    reasoning: list,
    base: float,
    nfl_client,
) -> PickResult:
    """NFL analysis pipeline — ESPN stats API."""
    if not nfl_client:
        reasoning.append("⚠️ NFL client not configured.")
        result.confidence = 45
        return result

    from data.nfl_client import NFL_PROP_TO_ESPN

    player = await nfl_client.get_player(result.player_name)
    if not player:
        reasoning.append(f"⚠️ NFL player '{result.player_name}' not found.")
        result.confidence = 45
        return result

    player_id = player.get("id")
    result.player_name = player.get("fullName", player.get("displayName", result.player_name))
    result.team = player.get("team", {}).get("abbreviation", "") if player.get("team") else ""

    logs = await nfl_client.get_player_game_logs(str(player_id))
    if not logs:
        reasoning.append("⚠️ No NFL game logs found — limited analysis.")
        result.confidence = 45
        return result

    stat_key = NFL_PROP_TO_ESPN.get(result.prop_type.upper(), result.prop_type.lower())
    values = []
    for log in logs:
        val = log.get(stat_key)
        if val is None:
            val = log.get(result.prop_type.lower())
        if val is not None:
            try:
                values.append(float(val))
            except (TypeError, ValueError):
                pass
        if len(values) >= 20:
            break

    if values:
        def _rate(w: list) -> float:
            return round(sum(1 for v in w if v > result.line) / len(w), 3) if w else 0.0
        hr = {
            "l5":  _rate(values[:5]),
            "l10": _rate(values[:10]),
            "l20": _rate(values[:20]),
            "avg": round(sum(values) / len(values), 2),
            "games": len(values),
            "trend": "flat",
        }
        if len(values) >= 10:
            diff = hr["l5"] - hr["l10"]
            hr["trend"] = "up" if diff >= 0.15 else ("down" if diff <= -0.15 else "flat")
        result.hit_rates = hr
        hit_score = hr["l5"] * 0.45 + hr["l10"] * 0.35 + hr["l20"] * 0.20
        base += (hit_score - 0.5) * 60
        reasoning.append(
            f"📊 Hit rates: L5={hr['l5']:.0%}  L10={hr['l10']:.0%}  (avg {hr['avg']})"
        )
        if hr["trend"] == "up":
            base += 5
            reasoning.append("📈 Form trending UP.")
        elif hr["trend"] == "down":
            base -= 5
            reasoning.append("📉 Form trending DOWN.")
    else:
        reasoning.append("⚠️ Insufficient NFL game log data.")

    # Home/away split
    home_hits = home_total = away_hits = away_total = 0
    for log in logs:
        val = log.get(stat_key)
        if val is not None:
            try:
                is_h = log.get("isHome", True)
                if is_h:
                    home_total += 1
                    if float(val) > result.line:
                        home_hits += 1
                else:
                    away_total += 1
                    if float(val) > result.line:
                        away_hits += 1
            except (TypeError, ValueError):
                pass

    if result.is_home and home_total > 0:
        ha_rate = round(home_hits / home_total, 3)
        result.home_away_rate = ha_rate
        base += (ha_rate - 0.5) * 15
        reasoning.append(f"🏠 Home split: {ha_rate:.0%} hit rate.")
    elif not result.is_home and away_total > 0:
        ha_rate = round(away_hits / away_total, 3)
        result.home_away_rate = ha_rate
        base += (ha_rate - 0.5) * 15
        reasoning.append(f"✈️ Away split: {ha_rate:.0%} hit rate.")

    from data.sports_router import SPORT_EMOJI
    reasoning.append(f"{SPORT_EMOJI['NFL']} NFL prop analysis complete.")

    result.confidence = max(0, min(100, int(round(base))))
    return result


# ── Soccer scoring ─────────────────────────────────────────────────────────────

async def _score_soccer(
    result: PickResult,
    reasoning: list,
    base: float,
    soccer_client,
    league: str = "eng.1",
) -> PickResult:
    """Soccer analysis pipeline — ESPN soccer API."""
    if not soccer_client:
        reasoning.append("⚠️ Soccer client not configured.")
        result.confidence = 45
        return result

    from data.soccer_client import SOCCER_PROP_TO_KEY

    player = await soccer_client.get_player(result.player_name, league)
    if not player:
        reasoning.append(f"⚠️ Soccer player '{result.player_name}' not found.")
        result.confidence = 45
        return result

    player_id = player.get("id")
    result.player_name = player.get("fullName", player.get("displayName", result.player_name))

    logs = await soccer_client.get_player_game_logs(str(player_id), league)
    if not logs:
        reasoning.append("⚠️ No soccer match logs found.")
        result.confidence = 45
        return result

    stat_key = SOCCER_PROP_TO_KEY.get(result.prop_type.upper(), result.prop_type.lower())
    values = []
    for log in logs:
        val = log.get(stat_key)
        if val is not None:
            try:
                values.append(float(val))
            except (TypeError, ValueError):
                pass
        if len(values) >= 20:
            break

    if values:
        def _rate(w: list) -> float:
            return round(sum(1 for v in w if v > result.line) / len(w), 3) if w else 0.0
        hr = {
            "l5":  _rate(values[:5]),
            "l10": _rate(values[:10]),
            "l20": _rate(values[:20]),
            "avg": round(sum(values) / len(values), 2),
            "games": len(values),
            "trend": "flat",
        }
        if len(values) >= 10:
            diff = hr["l5"] - hr["l10"]
            hr["trend"] = "up" if diff >= 0.15 else ("down" if diff <= -0.15 else "flat")
        result.hit_rates = hr
        hit_score = hr["l5"] * 0.45 + hr["l10"] * 0.35 + hr["l20"] * 0.20
        base += (hit_score - 0.5) * 60
        reasoning.append(
            f"📊 Hit rates: L5={hr['l5']:.0%}  L10={hr['l10']:.0%}  (avg {hr['avg']})"
        )
        if hr["trend"] == "up":
            base += 5
            reasoning.append("📈 Form trending UP.")
        elif hr["trend"] == "down":
            base -= 5
            reasoning.append("📉 Form trending DOWN.")
    else:
        reasoning.append("⚠️ Insufficient soccer match data.")

    # Home/away
    home_hits = home_total = away_hits = away_total = 0
    for log in logs:
        val = log.get(stat_key)
        if val is not None:
            try:
                is_h = log.get("isHome", True)
                if is_h:
                    home_total += 1
                    if float(val) > result.line:
                        home_hits += 1
                else:
                    away_total += 1
                    if float(val) > result.line:
                        away_hits += 1
            except (TypeError, ValueError):
                pass

    if result.is_home and home_total > 0:
        ha_rate = round(home_hits / home_total, 3)
        result.home_away_rate = ha_rate
        base += (ha_rate - 0.5) * 15
        reasoning.append(f"🏠 Home split: {ha_rate:.0%} hit rate.")
    elif not result.is_home and away_total > 0:
        ha_rate = round(away_hits / away_total, 3)
        result.home_away_rate = ha_rate
        base += (ha_rate - 0.5) * 15
        reasoning.append(f"✈️ Away split: {ha_rate:.0%} hit rate.")

    # League context
    league_display = {
        "eng.1": "Premier League", "esp.1": "La Liga", "ger.1": "Bundesliga",
        "ita.1": "Serie A", "fra.1": "Ligue 1", "usa.1": "MLS",
        "UEFA.CHAMPIONS": "Champions League",
    }.get(league, league)
    reasoning.append(f"⚽ League: {league_display}")

    result.confidence = max(0, min(100, int(round(base))))
    return result


# ── Tennis scoring ─────────────────────────────────────────────────────────────

async def _score_tennis(
    result: PickResult,
    reasoning: list,
    base: float,
    tennis_client,
    surface: str = "hard",
    tour: str = "atp",
) -> PickResult:
    """Tennis analysis pipeline — ESPN tennis API."""
    if not tennis_client:
        reasoning.append("⚠️ Tennis client not configured.")
        result.confidence = 45
        return result

    from data.tennis_client import TENNIS_PROP_TO_KEY

    player = await tennis_client.get_player(result.player_name, tour)
    if not player:
        # Try opposite tour
        alt_tour = "wta" if tour == "atp" else "atp"
        player = await tennis_client.get_player(result.player_name, alt_tour)
        if player:
            tour = alt_tour

    if not player:
        reasoning.append(f"⚠️ Tennis player '{result.player_name}' not found.")
        result.confidence = 45
        return result

    player_id = player.get("id")
    result.player_name = player.get("fullName", player.get("displayName", result.player_name))

    logs = await tennis_client.get_player_match_history(str(player_id), tour)
    if not logs:
        reasoning.append("⚠️ No match history found.")
        result.confidence = 45
        return result

    stat_key = TENNIS_PROP_TO_KEY.get(result.prop_type.upper(), result.prop_type.lower())
    values, surface_values = [], []
    for log in logs:
        val = log.get(stat_key)
        if val is not None:
            try:
                v = float(val)
                values.append(v)
                if surface.lower() in log.get("surface", "").lower():
                    surface_values.append(v)
            except (TypeError, ValueError):
                pass
        if len(values) >= 20:
            break

    if values:
        def _rate(w: list) -> float:
            return round(sum(1 for v in w if v > result.line) / len(w), 3) if w else 0.0
        hr = {
            "l5":  _rate(values[:5]),
            "l10": _rate(values[:10]),
            "l20": _rate(values[:20]),
            "avg": round(sum(values) / len(values), 2),
            "games": len(values),
            "trend": "flat",
        }
        if len(values) >= 10:
            diff = hr["l5"] - hr["l10"]
            hr["trend"] = "up" if diff >= 0.15 else ("down" if diff <= -0.15 else "flat")
        result.hit_rates = hr
        hit_score = hr["l5"] * 0.45 + hr["l10"] * 0.35 + hr["l20"] * 0.20
        base += (hit_score - 0.5) * 60
        reasoning.append(
            f"📊 Hit rates: L5={hr['l5']:.0%}  L10={hr['l10']:.0%}  (avg {hr['avg']})"
        )

        # Surface bonus
        if surface_values:
            surf_rate = _rate(surface_values[:10])
            if surf_rate > 0.65:
                base += 6
                reasoning.append(f"🎾 Strong on {surface.title()} courts: {surf_rate:.0%} hit rate.")
            elif surf_rate < 0.40:
                base -= 6
                reasoning.append(f"⚠️ Struggles on {surface.title()} courts: {surf_rate:.0%} hit rate.")

        if hr["trend"] == "up":
            base += 5
            reasoning.append("📈 Form trending UP.")
        elif hr["trend"] == "down":
            base -= 5
            reasoning.append("📉 Form trending DOWN.")
    else:
        reasoning.append("⚠️ Insufficient match data.")

    reasoning.append(f"🎾 Tour: {tour.upper()} | Surface: {surface.title()}")

    result.confidence = max(0, min(100, int(round(base))))
    return result


# ── Esports scoring ────────────────────────────────────────────────────────────

async def _score_esports(
    result: PickResult,
    reasoning: list,
    base: float,
    esports_client,
    game: str = "csgo",
) -> PickResult:
    """Esports analysis pipeline — Pandascore API."""
    if not esports_client:
        reasoning.append("⚠️ Esports client not configured.")
        result.confidence = 45
        return result

    from data.esports_client import ESPORTS_PROPS

    player = await esports_client.get_player(result.player_name, game)
    if not player:
        reasoning.append(f"⚠️ Esports player '{result.player_name}' not found in {game.upper()}.")
        result.confidence = 45
        return result

    player_id = player.get("id")
    handle = player.get("name", result.player_name)
    result.player_name = handle
    result.team = player.get("current_team", {}).get("acronym", "") if player.get("current_team") else ""

    stats_list = await esports_client.get_player_stats(player_id, game)
    if not stats_list:
        reasoning.append("⚠️ No esports match data found.")
        result.confidence = 45
        return result

    prop_map = ESPORTS_PROPS.get(game, {})
    stat_key = prop_map.get(result.prop_type.upper(), result.prop_type.lower())
    values = []
    for s in stats_list:
        val = s.get(stat_key)
        if val is not None:
            try:
                values.append(float(val))
            except (TypeError, ValueError):
                pass
        if len(values) >= 20:
            break

    if values:
        def _rate(w: list) -> float:
            return round(sum(1 for v in w if v > result.line) / len(w), 3) if w else 0.0
        hr = {
            "l5":  _rate(values[:5]),
            "l10": _rate(values[:10]),
            "l20": _rate(values[:20]),
            "avg": round(sum(values) / len(values), 2),
            "games": len(values),
            "trend": "flat",
        }
        if len(values) >= 10:
            diff = hr["l5"] - hr["l10"]
            hr["trend"] = "up" if diff >= 0.15 else ("down" if diff <= -0.15 else "flat")
        result.hit_rates = hr
        hit_score = hr["l5"] * 0.45 + hr["l10"] * 0.35 + hr["l20"] * 0.20
        base += (hit_score - 0.5) * 60
        reasoning.append(
            f"📊 Hit rates: L5={hr['l5']:.0%}  L10={hr['l10']:.0%}  (avg {hr['avg']})"
        )
        if hr["trend"] == "up":
            base += 5
            reasoning.append("📈 Form trending UP.")
        elif hr["trend"] == "down":
            base -= 5
            reasoning.append("📉 Form trending DOWN.")
    else:
        reasoning.append("⚠️ Insufficient esports data.")

    game_display = game.upper().replace("CSGO", "CS2")
    reasoning.append(f"🎮 Game: {game_display} | vs {result.opponent}")

    result.confidence = max(0, min(100, int(round(base))))
    return result
