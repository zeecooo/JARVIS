"""
commands/locks.py - /locks, /degen, /firstbasket, /altlines slash commands.

/locks             - High-confidence picks only (≥75)
/degen             - High-odds plays with decent data backing
/firstbasket       - First basket scorer props
/altlines [player] [prop] [original_line] - Safer alternative lines
"""

import asyncio
import logging
from typing import Optional, Literal

import discord
from discord import app_commands
from discord.ext import commands

from analysis.engine import score_pick, PickResult
from analysis.nba_analysis import normalize_prop_type, suggest_alt_lines
from analysis.nhl_analysis import normalize_nhl_prop, suggest_nhl_alt_lines
from commands.picks import _fetch_todays_game_props, _get_live_props
from utils.embeds import (
    picks_list_embed,
    pick_embed,
    altlines_embed,
    firstbasket_embed,
    error_embed,
    info_embed,
    COLOR_LOCK,
    COLOR_INFO,
)
from utils.player_lookup import resolve_name, detect_sport_for_player
import config

log = logging.getLogger(__name__)


class LocksCog(commands.Cog, name="Locks"):
    """Special prop pick commands."""

    def __init__(self, bot) -> None:
        self.bot = bot

    # ── /locks ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="locks",
        description="Safe, high-confidence picks only (confidence ≥ 75%).",
    )
    @app_commands.describe(
        sport="Sport to fetch locks for (default: NBA)",
    )
    async def locks(
        self,
        interaction: discord.Interaction,
        sport: Optional[Literal["NBA", "NHL", "NFL", "SOCCER", "TENNIS", "ESPORTS"]] = "NBA",
    ) -> None:
        await interaction.response.defer(thinking=True)

        try:
            all_picks = await _fetch_todays_game_props(
                self.bot,
                sport=sport,
                min_confidence=config.LOCK_THRESHOLD,
                limit=10,
            )
        except Exception as exc:
            log.error("/locks error: %s", exc)
            await interaction.followup.send(
                embed=error_embed("Locks unavailable", str(exc))
            )
            return

        locks = [p for p in all_picks if p.confidence >= config.LOCK_THRESHOLD]

        if not locks:
            await interaction.followup.send(
                embed=info_embed(
                    "No locks today",
                    f"No {sport} picks are currently above {config.LOCK_THRESHOLD}% confidence.\n"
                    f"Try `/picks` for lower-confidence options.",
                )
            )
            return

        embed = picks_list_embed(
            picks=locks,
            title=f"🔒 Today's {sport} Locks",
            description=(
                f"High-confidence plays with ≥{config.LOCK_THRESHOLD}% confidence  |  "
                f"{len(locks)} pick{'s' if len(locks) != 1 else ''} found"
            ),
            color=COLOR_LOCK,
        )
        await interaction.followup.send(embed=embed)

    # ── /degen ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="degen",
        description=(
            "High-odds degen plays: confidence 55–70% with +150 or better odds."
        ),
    )
    @app_commands.describe(
        sport="Sport to scan (default: NBA)",
        min_odds="Minimum American odds (e.g. 150 for +150). Default: 150.",
    )
    async def degen(
        self,
        interaction: discord.Interaction,
        sport: Optional[Literal["NBA", "NHL"]] = "NBA",
        min_odds: Optional[int] = 150,
    ) -> None:
        await interaction.response.defer(thinking=True)

        min_odds = max(100, min_odds or config.DEGEN_ODDS_MIN)

        try:
            all_picks = await _fetch_todays_game_props(
                self.bot,
                sport=sport,
                min_confidence=config.DEGEN_CONF_MIN,
                limit=20,
            )
        except Exception as exc:
            log.error("/degen error: %s", exc)
            await interaction.followup.send(
                embed=error_embed("Degen picks unavailable", str(exc))
            )
            return

        # Filter to degen range + plus-money
        degen_picks = []
        for p in all_picks:
            conf_ok = config.DEGEN_CONF_MIN <= p.confidence <= config.DEGEN_CONF_MAX
            try:
                odds_val = float(str(p.odds or "0").replace("+", ""))
                odds_ok = odds_val >= min_odds
            except ValueError:
                odds_ok = False
            if conf_ok and odds_ok:
                degen_picks.append(p)

        if not degen_picks:
            await interaction.followup.send(
                embed=info_embed(
                    "No degen picks",
                    f"No {sport} picks found in the {config.DEGEN_CONF_MIN}–{config.DEGEN_CONF_MAX}% "
                    f"confidence range with +{min_odds} or better odds right now.\n"
                    f"Try `/picks` for all confidence levels.",
                )
            )
            return

        embed = picks_list_embed(
            picks=degen_picks,
            title=f"🎲 Degen {sport} Plays",
            description=(
                f"Confidence {config.DEGEN_CONF_MIN}–{config.DEGEN_CONF_MAX}%  |  "
                f"Odds: +{min_odds} or better  |  "
                f"{len(degen_picks)} pick{'s' if len(degen_picks) != 1 else ''}"
            ),
            color=0xFF6D00,
        )
        await interaction.followup.send(embed=embed)

    # ── /firstbasket ───────────────────────────────────────────────────────────

    @app_commands.command(
        name="firstbasket",
        description="First basket scorer props for tonight's NBA games.",
    )
    async def firstbasket(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(thinking=True)

        try:
            picks = await _build_firstbasket_picks(self.bot)
        except Exception as exc:
            log.error("/firstbasket error: %s", exc)
            await interaction.followup.send(
                embed=error_embed("First basket unavailable", str(exc))
            )
            return

        embed = firstbasket_embed(picks)
        await interaction.followup.send(embed=embed)

    # ── /altlines ──────────────────────────────────────────────────────────────

    @app_commands.command(
        name="altlines",
        description=(
            "Suggest safer alternative lines for a player prop. "
            "Example: /altlines LeBron James PTS 25.5"
        ),
    )
    @app_commands.describe(
        player="Player full name (e.g. LeBron James)",
        prop="Prop type (e.g. PTS, REB, SOG)",
        original_line="The current line value (e.g. 25.5)",
        direction="Over or Under (default: over)",
    )
    async def altlines(
        self,
        interaction: discord.Interaction,
        player: str,
        prop: str,
        original_line: float,
        direction: Optional[Literal["over", "under"]] = "over",
    ) -> None:
        await interaction.response.defer(thinking=True)

        # Detect sport
        sport = detect_sport_for_player(player)
        if sport == "UNKNOWN":
            sport = "NBA"  # default fallback

        # Normalise prop
        if sport == "NHL":
            prop_type = normalize_nhl_prop(prop)
        else:
            prop_type = normalize_prop_type(prop)

        resolved_name = resolve_name(player, sport)

        try:
            # Score the original pick to get hit rates
            pick = await score_pick(
                player_name=resolved_name,
                prop_type=prop_type,
                line=original_line,
                opponent_team="TBD",
                is_home=True,
                nba_client=self.bot.nba_client,
                nhl_client=self.bot.nhl_client,
                direction=direction or "over",
                sport=sport,
            )
        except Exception as exc:
            log.error("/altlines score error: %s", exc)
            await interaction.followup.send(
                embed=error_embed("Analysis failed", str(exc))
            )
            return

        # Build alt lines
        hr = pick.hit_rates
        if sport == "NHL":
            suggestions = suggest_nhl_alt_lines(
                original_line, hr, prop_type, direction or "over"
            )
        else:
            suggestions = suggest_alt_lines(
                original_line, hr, direction or "over"
            )

        embed = altlines_embed(
            player_name=pick.player_name or player,
            prop_type=prop_type,
            original_line=original_line,
            suggestions=suggestions,
            hit_rates=hr,
        )

        # Also show the original pick's confidence at the bottom
        embed.add_field(
            name=f"Original Line Analysis ({original_line})",
            value=(
                f"Confidence: **{pick.confidence}%**  |  "
                f"Recommendation: **{pick.recommendation}**"
            ),
            inline=False,
        )

        await interaction.followup.send(embed=embed)


# ── First basket helper ────────────────────────────────────────────────────────

async def _build_firstbasket_picks(bot) -> list[dict]:
    """
    Build a list of first-basket scorer candidates from tonight's NBA games.

    Strategy:
    - Fetch today's games
    - For each game, pull rosters' top-usage players
    - Rank by: usage rate, recent scoring, team tendency to start fast
    - Return top 8 candidates with their odds if available

    Returns a list of dicts compatible with firstbasket_embed.
    """
    try:
        games = await bot.nba_client.get_todays_games()
    except Exception as exc:
        log.warning("Could not fetch today's games: %s", exc)
        games = []

    # Try to get first-basket odds from The Odds API
    fb_odds_map: dict[str, str] = {}
    try:
        events = await bot.odds_client.get_todays_nba_events()
        for event in events[:4]:
            event_id = event.get("id")
            if not event_id:
                continue
            odds_data = await bot.odds_client.get_nba_props(
                event_id, markets=["player_first_basket"]
            )
            for bm in odds_data.get("bookmakers", [])[:1]:
                for market in bm.get("markets", []):
                    if market.get("key") != "player_first_basket":
                        continue
                    for outcome in market.get("outcomes", []):
                        name = outcome.get("description", outcome.get("name", ""))
                        price = outcome.get("price")
                        if name and price:
                            fb_odds_map[name.lower()] = f"+{price}" if price > 0 else str(price)
    except Exception as exc:
        log.warning("Could not fetch first basket odds: %s", exc)

    # Build candidate list from known high-usage scorers
    # In production: pull actual rosters and filter by minutes/usage
    candidate_names = [
        ("LeBron James",          "LAL", "+600"),
        ("Stephen Curry",         "GSW", "+550"),
        ("Giannis Antetokounmpo", "MIL", "+500"),
        ("Shai Gilgeous-Alexander", "OKC", "+600"),
        ("Luka Doncic",           "DAL", "+600"),
        ("Anthony Davis",         "LAL", "+650"),
        ("Jayson Tatum",          "BOS", "+700"),
        ("Joel Embiid",           "PHI", "+750"),
    ]

    picks = []
    for name, team, default_odds in candidate_names:
        # Check if player is in tonight's games
        player_in_game = any(
            team in str(g.get("home_team", {})) or team in str(g.get("visitor_team", {}))
            for g in games
        ) if games else True  # If no games data, show all

        if not player_in_game and games:
            continue

        # Check for live odds
        odds = fb_odds_map.get(name.lower(), default_odds)

        # Get recent scoring avg
        try:
            player_data = await bot.nba_client.get_player(name)
            avg_pts = 0.0
            usage_rank = "N/A"
            if player_data:
                logs = await bot.nba_client.get_player_game_logs(
                    player_data["id"], last_n=5
                )
                if logs:
                    pts_vals = [l.get("pts", 0) or 0 for l in logs[:5]]
                    avg_pts = round(sum(pts_vals) / max(len(pts_vals), 1), 1)
        except Exception:
            avg_pts = 0.0
            usage_rank = "N/A"

        picks.append({
            "player":     name,
            "team":       team,
            "opponent":   "TBD",
            "odds":       odds,
            "avg_pts":    avg_pts,
            "usage_rank": usage_rank,
        })

    # Sort by implied probability (better odds = lower rank shown first)
    def _implied_prob(p: dict) -> float:
        try:
            v = float(str(p.get("odds", "+600")).replace("+", ""))
            if v > 0:
                return 100 / (v + 100)
            return abs(v) / (abs(v) + 100)
        except ValueError:
            return 0.0

    picks.sort(key=_implied_prob, reverse=True)
    return picks[:8]


async def setup(bot) -> None:
    await bot.add_cog(LocksCog(bot))
