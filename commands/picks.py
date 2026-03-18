"""
commands/picks.py - /picks and /pick slash commands.

/picks [sport] [min_confidence]
    Fetches today's games, scores top props, returns ranked embed.

/pick
    Returns the single highest-confidence pick of the day.
"""

import asyncio
import logging
from typing import Optional, Literal

import discord
from discord import app_commands
from discord.ext import commands

from analysis.engine import score_pick, PickResult
from database.db import save_pick, get_recent_picks
from utils.embeds import (
    picks_list_embed,
    pick_embed,
    potd_embed as _potd_embed,
    error_embed,
    info_embed,
    COLOR_LOCK,
    COLOR_INFO,
)
from utils.player_lookup import resolve_name
from data.sports_router import SPORT_EMOJI

log = logging.getLogger(__name__)

# All supported sports
SPORT_TYPE = Literal["NBA", "NHL", "NFL", "SOCCER", "TENNIS", "ESPORTS"]

# ── Top player-prop candidates per sport ──────────────────────────────────────
# Fallback list used when we can't enumerate all players from live games.
# In production these would be populated from today's actual game rosters.

_NBA_TEMPLATE_PROPS = [
    # (player_name, prop_type, typical_line)
    ("LeBron James",          "PTS",  25.5),
    ("Stephen Curry",         "PTS",  26.5),
    ("Kevin Durant",          "PTS",  27.5),
    ("Giannis Antetokounmpo", "PTS",  29.5),
    ("Nikola Jokic",          "PTS",  26.5),
    ("Nikola Jokic",          "REB",  12.5),
    ("Luka Doncic",           "PTS",  30.5),
    ("Anthony Davis",         "REB",  12.5),
    ("Jayson Tatum",          "PTS",  26.5),
    ("Shai Gilgeous-Alexander", "PTS", 30.5),
    ("Tyrese Haliburton",     "AST",   9.5),
    ("Joel Embiid",           "PTS",  33.5),
    ("LeBron James",          "REB",   7.5),
    ("Donovan Mitchell",      "PTS",  26.5),
    ("Bam Adebayo",           "REB",  10.5),
    ("De'Aaron Fox",          "PTS",  24.5),
]

_NHL_TEMPLATE_PROPS = [
    ("Connor McDavid",  "SOG",  5.5),
    ("Leon Draisaitl",  "SOG",  4.5),
    ("David Pastrnak",  "SOG",  4.5),
    ("Auston Matthews", "SOG",  4.5),
    ("Nathan MacKinnon","SOG",  4.5),
    ("Alex Ovechkin",   "SOG",  3.5),
    ("Brayden Point",   "SOG",  3.5),
    ("Artemi Panarin",  "SOG",  3.5),
    ("Nikita Kucherov", "POINTS", 1.5),
    ("Connor McDavid",  "POINTS", 1.5),
]

_NFL_TEMPLATE_PROPS = [
    ("Patrick Mahomes",  "PASSYDS",  275.5),
    ("Josh Allen",       "PASSYDS",  245.5),
    ("Jalen Hurts",      "RUSHYDS",   35.5),
    ("Travis Kelce",     "RECYDS",    65.5),
    ("Justin Jefferson", "RECYDS",    75.5),
    ("Tyreek Hill",      "RECYDS",    70.5),
    ("CeeDee Lamb",      "RECYDS",    72.5),
    ("Christian McCaffrey", "RUSHYDS", 80.5),
    ("Saquon Barkley",   "RUSHYDS",   65.5),
    ("Lamar Jackson",    "RUSHYDS",   45.5),
    ("Ja'Marr Chase",    "RECYDS",    65.5),
    ("A.J. Brown",       "RECYDS",    60.5),
]

_SOCCER_TEMPLATE_PROPS = [
    ("Erling Haaland",   "SHOTS",        3.5),
    ("Mohamed Salah",    "SHOTS",        2.5),
    ("Kylian Mbappe",    "SHOTS",        3.5),
    ("Harry Kane",       "SHOTS",        3.5),
    ("Marcus Rashford",  "SHOTSONTARGET", 1.5),
    ("Bukayo Saka",      "SHOTSONTARGET", 1.5),
    ("Vinicius Junior",  "SHOTS",        2.5),
    ("Jude Bellingham",  "SHOTS",        2.5),
]

_TENNIS_TEMPLATE_PROPS = [
    ("Novak Djokovic",  "ACES",  6.5),
    ("Carlos Alcaraz",  "ACES",  7.5),
    ("Jannik Sinner",   "ACES",  5.5),
    ("Iga Swiatek",     "ACES",  3.5),
    ("Coco Gauff",      "ACES",  3.5),
    ("Aryna Sabalenka", "ACES",  4.5),
    ("Taylor Fritz",    "ACES",  7.5),
    ("Alexander Zverev","ACES",  7.5),
]

_ESPORTS_TEMPLATE_PROPS = [
    ("ZywOo",    "KILLS",  26.5),
    ("NiKo",     "KILLS",  24.5),
    ("sh1ro",    "KILLS",  23.5),
    ("device",   "KILLS",  22.5),
    ("Faker",    "KILLS",  6.5),
    ("Chovy",    "KILLS",  7.5),
    ("TenZ",     "KILLS",  18.5),
    ("yay",      "KILLS",  20.5),
]

_SPORT_TEMPLATES = {
    "NBA": _NBA_TEMPLATE_PROPS,
    "NHL": _NHL_TEMPLATE_PROPS,
    "NFL": _NFL_TEMPLATE_PROPS,
    "SOCCER": _SOCCER_TEMPLATE_PROPS,
    "TENNIS": _TENNIS_TEMPLATE_PROPS,
    "ESPORTS": _ESPORTS_TEMPLATE_PROPS,
}


async def _fetch_todays_game_props(
    bot,
    sport: str,
    min_confidence: int = 0,
    limit: int = 10,
    force_return: bool = False,
) -> list[PickResult]:
    """
    Core helper: fetch today's games, get odds lines, score top props.

    Returns a sorted list of PickResult objects (highest confidence first).
    If force_return=True, ignores min_confidence and returns the best picks
    regardless (used by /pick and /potd so they never return empty).
    """
    template = _SPORT_TEMPLATES.get(sport.upper(), _NBA_TEMPLATE_PROPS)

    # Fetch shared context data once
    all_team_stats = None
    all_nhl_standings = None
    injuries = []

    if sport == "NBA":
        try:
            all_team_stats = await bot.nba_client.get_team_defensive_stats()
            injuries = await bot.nba_client.get_injuries()
        except Exception as exc:
            log.warning("Could not fetch NBA context data: %s", exc)
    elif sport == "NHL":
        try:
            all_nhl_standings = await bot.nhl_client.get_all_teams_stats()
        except Exception as exc:
            log.warning("Could not fetch NHL standings: %s", exc)

    # Try to get today's games + odds lines
    live_props = await _get_live_props(bot, sport)

    # Merge live props with template (live takes priority)
    if live_props:
        prop_list = live_props
    else:
        # Fetch today's schedule so we can fill in real opponents
        matchups: dict = {}
        if sport == "NBA":
            matchups = await _get_todays_nba_matchups(bot)

        prop_list = []
        for p in template:
            # Look up the player's team abbreviation from the static map
            from data.nba_client import _KNOWN_PLAYERS, _NBA_TEAMS
            player_entry = _KNOWN_PLAYERS.get(p[0])
            team_abbr = ""
            if player_entry:
                team_id = player_entry[1]
                team_abbr = _NBA_TEAMS.get(team_id, {}).get("abbreviation", "")

            opp, is_home = matchups.get(team_abbr, ("TBD", True))
            prop_list.append({
                "player":    p[0],
                "prop_type": p[1],
                "line":      p[2],
                "opponent":  opp,
                "is_home":   is_home,
                "odds":      "-110",
                "sport":     sport,
            })

    sem = asyncio.Semaphore(4)

    async def _score_one(prop: dict) -> Optional[PickResult]:
        async with sem:
            try:
                pick = await score_pick(
                    player_name=prop["player"],
                    prop_type=prop["prop_type"],
                    line=prop["line"],
                    opponent_team=prop.get("opponent", "TBD"),
                    is_home=prop.get("is_home", True),
                    bot=bot,
                    nba_client=bot.nba_client,
                    nhl_client=bot.nhl_client,
                    direction=prop.get("direction", "over"),
                    sport=sport,
                    injuries=injuries if sport == "NBA" else None,
                    all_team_stats=all_team_stats,
                    all_nhl_standings=all_nhl_standings,
                )
                pick.odds = prop.get("odds", "-110")
                return pick
            except Exception as exc:
                log.error("Error scoring %s: %s", prop.get("player"), exc)
                return None

    tasks = [_score_one(p) for p in prop_list]
    scored = await asyncio.gather(*tasks)

    all_results = [r for r in scored if r is not None]
    all_results.sort(key=lambda r: r.confidence, reverse=True)

    if force_return:
        # Always return something — ignore min_confidence filter
        return all_results[:limit]

    filtered = [r for r in all_results if r.confidence >= min_confidence]
    return filtered[:limit]


async def _get_todays_nba_matchups(bot) -> dict[str, tuple[str, bool]]:
    """
    Fetch today's NBA schedule from the CDN scoreboard.
    Returns {team_abbr: (opponent_abbr, is_home)} for every team playing today.
    """
    try:
        games = await bot.nba_client.get_todays_games()
        matchups: dict[str, tuple[str, bool]] = {}
        for game in games:
            home = game.get("home_team", {}).get("abbreviation", "")
            away = game.get("visitor_team", {}).get("abbreviation", "")
            if home and away:
                matchups[home] = (away, True)
                matchups[away] = (home, False)
        return matchups
    except Exception as exc:
        log.warning("Could not fetch today's NBA schedule: %s", exc)
        return {}


async def _get_live_props(bot, sport: str) -> list[dict]:
    """
    Attempt to fetch live prop lines from The Odds API for today's games.
    Returns a list of prop dicts or an empty list on failure.
    """
    try:
        if sport == "NBA":
            events = await bot.odds_client.get_todays_nba_events()
        else:
            events = await bot.odds_client.get_todays_nhl_events()

        if not events:
            return []

        props: list[dict] = []
        for event in events[:3]:  # limit to first 3 games to save API calls
            event_id = event.get("id")
            if not event_id:
                continue

            if sport == "NBA":
                odds_data = await bot.odds_client.get_nba_props(event_id)
            else:
                odds_data = await bot.odds_client.get_nhl_props(event_id)

            bookmakers = odds_data.get("bookmakers", [])
            home_team = event.get("home_team", "")
            away_team = event.get("away_team", "")

            for bm in bookmakers[:2]:  # Use first 2 books for consensus
                for market in bm.get("markets", []):
                    market_key = market.get("key", "")
                    prop_type = _market_key_to_prop(market_key)
                    if not prop_type:
                        continue

                    # Group outcomes by player
                    player_outcomes: dict = {}
                    for outcome in market.get("outcomes", []):
                        desc = outcome.get("description", "")
                        if not desc:
                            continue
                        if desc not in player_outcomes:
                            player_outcomes[desc] = []
                        player_outcomes[desc].append(outcome)

                    for player_name, outcomes in player_outcomes.items():
                        over = next((o for o in outcomes if o.get("name") == "Over"), None)
                        if not over:
                            continue
                        props.append({
                            "player":    player_name,
                            "prop_type": prop_type,
                            "line":      over.get("point", 0),
                            "direction": "over",
                            "odds":      str(over.get("price", -110)),
                            "opponent":  away_team,
                            "is_home":   True,
                            "sport":     sport,
                        })

        # Deduplicate by (player, prop_type)
        seen = set()
        unique = []
        for p in props:
            key = (p["player"], p["prop_type"])
            if key not in seen:
                seen.add(key)
                unique.append(p)

        return unique

    except Exception as exc:
        log.warning("Could not fetch live props: %s", exc)
        return []


def _market_key_to_prop(market_key: str) -> Optional[str]:
    """Map Odds API market key to our internal prop type."""
    mapping = {
        "player_points":               "PTS",
        "player_rebounds":             "REB",
        "player_assists":              "AST",
        "player_threes":               "3PM",
        "player_blocks":               "BLK",
        "player_steals":               "STL",
        "player_points_rebounds_assists": "PRA",
        "player_points_rebounds":      "PR",
        "player_points_assists":       "PA",
        "player_rebounds_assists":     "RA",
        "player_shots_on_goal":        "SOG",
        "player_goals":                "GOALS",
        "player_assists":              "ASSISTS",
        "player_hits":                 "HITS",
        "player_blocked_shots":        "BLOCKS",
        "player_saves":                "SAVES",
    }
    return mapping.get(market_key)


# ── Cog ────────────────────────────────────────────────────────────────────────

class PicksCog(commands.Cog, name="Picks"):
    """Daily top picks commands."""

    def __init__(self, bot) -> None:
        self.bot = bot

    # ── /picks ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="picks",
        description="Daily top props — scored and ranked by confidence. Supports all sports.",
    )
    @app_commands.describe(
        sport="Sport to fetch picks for (default: NBA)",
        min_confidence="Minimum confidence score 0–100 (default: 55)",
    )
    async def picks(
        self,
        interaction: discord.Interaction,
        sport: Optional[Literal["NBA", "NHL", "NFL", "SOCCER", "TENNIS", "ESPORTS"]] = "NBA",
        min_confidence: Optional[int] = 55,
    ) -> None:
        await interaction.response.defer(thinking=True)

        try:
            top_picks = await _fetch_todays_game_props(
                self.bot,
                sport=sport,
                min_confidence=min_confidence,
                limit=10,
            )
        except Exception as exc:
            log.error("/picks error: %s", exc)
            await interaction.followup.send(
                embed=error_embed("Picks unavailable", f"An error occurred: {exc}")
            )
            return

        if not top_picks:
            await interaction.followup.send(
                embed=info_embed(
                    "No picks found",
                    f"No {sport} picks with confidence ≥ {min_confidence}% right now."
                )
            )
            return

        # Save to DB
        for pick in top_picks:
            try:
                await save_pick(
                    player=pick.player_name,
                    team=pick.team,
                    opponent=pick.opponent,
                    prop_type=pick.prop_type,
                    line=pick.line,
                    confidence=pick.confidence,
                    recommendation=pick.recommendation,
                    odds=pick.odds,
                    sport=pick.sport,
                )
            except Exception as exc:
                log.warning("Could not save pick: %s", exc)

        sport_icon = SPORT_EMOJI.get(sport.upper(), "🏆")
        embed = picks_list_embed(
            picks=top_picks,
            title=f"{sport_icon} Today's Top {sport} Props",
            description=(
                f"Ranked by confidence score  |  "
                f"Min confidence: {min_confidence}%  |  "
                f"{len(top_picks)} picks found"
            ),
            color=COLOR_LOCK,
        )
        await interaction.followup.send(embed=embed)

    # ── /pick ──────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="pick",
        description="Pick of the day — single highest confidence play with full analysis.",
    )
    @app_commands.describe(
        sport="Sport to analyze (default: NBA)",
    )
    async def pick(
        self,
        interaction: discord.Interaction,
        sport: Optional[Literal["NBA", "NHL", "NFL", "SOCCER", "TENNIS", "ESPORTS"]] = "NBA",
    ) -> None:
        await interaction.response.defer(thinking=True)

        # Check DB for existing picks from today
        existing = await get_recent_picks(sport=sport)
        if existing:
            best = existing[0]  # Already sorted by confidence DESC
            # Re-create a lightweight PickResult from the DB row for the embed
            pr = PickResult(
                player_name=best["player"],
                team=best["team"],
                opponent=best["opponent"],
                prop_type=best["prop_type"],
                line=best["line"],
                direction="over",
                sport=best["sport"],
                confidence=best["confidence"],
                recommendation=best["recommendation"],
                odds=best.get("odds", "-110"),
            )
            embed = pick_embed(pr, pick_id=best["id"])
            await interaction.followup.send(embed=embed)
            return

        # Fetch fresh — force_return=True means we always get the best pick
        # even if its confidence is below the normal threshold
        try:
            top_picks = await _fetch_todays_game_props(
                self.bot, sport=sport, min_confidence=0, limit=1, force_return=True
            )
        except Exception as exc:
            log.error("/pick error: %s", exc)
            await interaction.followup.send(
                embed=error_embed("Pick unavailable", str(exc))
            )
            return

        if not top_picks:
            await interaction.followup.send(
                embed=info_embed(
                    "No pick",
                    f"Could not fetch any {sport} player data right now. "
                    "Check that BALLDONTLIE_API_KEY is valid and the API is reachable."
                )
            )
            return

        best = top_picks[0]

        # Persist
        pick_id = None
        try:
            pick_id = await save_pick(
                player=best.player_name,
                team=best.team,
                opponent=best.opponent,
                prop_type=best.prop_type,
                line=best.line,
                confidence=best.confidence,
                recommendation=best.recommendation,
                odds=best.odds,
                sport=best.sport,
            )
        except Exception as exc:
            log.warning("Could not persist pick: %s", exc)

        embed = pick_embed(best, pick_id=pick_id)
        await interaction.followup.send(embed=embed)


    # ── /potd ──────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="potd",
        description="Pick of the Day — highest confidence play with full breakdown.",
    )
    @app_commands.describe(
        sport="Sport to analyze (default: NBA)",
    )
    async def potd(
        self,
        interaction: discord.Interaction,
        sport: Optional[Literal["NBA", "NHL", "NFL", "SOCCER", "TENNIS", "ESPORTS"]] = "NBA",
    ) -> None:
        await interaction.response.defer(thinking=True)

        # Check DB for a pick already generated today
        existing = await get_recent_picks(sport=sport)
        best_db = existing[0] if existing else None

        if best_db:
            pr = PickResult(
                player_name=best_db["player"],
                team=best_db["team"],
                opponent=best_db["opponent"],
                prop_type=best_db["prop_type"],
                line=best_db["line"],
                direction="over",
                sport=best_db["sport"],
                confidence=best_db["confidence"],
                recommendation=best_db["recommendation"],
                odds=best_db.get("odds", "-110"),
            )
            embed = _potd_embed(pr, pick_id=best_db["id"])
            await interaction.followup.send(embed=embed)
            return

        # Nothing in DB — score fresh
        try:
            top_picks = await _fetch_todays_game_props(
                self.bot, sport=sport, min_confidence=0, limit=1, force_return=True
            )
        except Exception as exc:
            log.error("/potd error: %s", exc)
            await interaction.followup.send(
                embed=error_embed("POTD unavailable", str(exc))
            )
            return

        if not top_picks:
            await interaction.followup.send(
                embed=info_embed(
                    "No POTD",
                    f"Could not fetch any {sport} player data right now. "
                    "Check that BALLDONTLIE_API_KEY is valid and the API is reachable."
                )
            )
            return

        best = top_picks[0]

        pick_id = None
        try:
            pick_id = await save_pick(
                player=best.player_name,
                team=best.team,
                opponent=best.opponent,
                prop_type=best.prop_type,
                line=best.line,
                confidence=best.confidence,
                recommendation=best.recommendation,
                odds=best.odds,
                sport=best.sport,
            )
        except Exception as exc:
            log.warning("Could not persist POTD pick: %s", exc)

        embed = _potd_embed(best, pick_id=pick_id)
        await interaction.followup.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(PicksCog(bot))
