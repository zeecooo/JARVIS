"""
commands/analyze.py - /analyze slash command.

/analyze [slip]
  Parses a free-text slip (one or more legs separated by commas/newlines),
  runs each through the scoring engine, and returns a full analysis embed.

  Example slip:
  "LeBron James Over 25.5 PTS, Anthony Davis Over 10.5 REB, Connor McDavid Over 3.5 SOG"
"""

import asyncio
import logging
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from analysis.engine import score_pick, PickResult
from database.db import save_analyzed_slip
from utils.embeds import slip_embed, pick_embed, error_embed, info_embed
from utils.player_lookup import resolve_name
from data.sports_router import parse_slip_line, detect_sport, SPORT_EMOJI
import config

log = logging.getLogger(__name__)


async def _extract_slip_from_image(image_url: str) -> str:
    """Download a betting slip image and extract the prop text using Claude Vision.

    Falls back to OCR.Space if ANTHROPIC_API_KEY is not set.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url) as resp:
            image_bytes = await resp.read()
            content_type = resp.content_type or "image/png"

    # ── Claude Vision (preferred) ──────────────────────────────────────────────
    if config.ANTHROPIC_API_KEY:
        import base64
        import anthropic

        b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        media_type = content_type if content_type.startswith("image/") else "image/png"

        client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "This is a sports betting slip. Extract every prop leg exactly as shown. "
                                "Output ONLY the legs, one per line, in the format: "
                                "PlayerName Over/Under Line PropType\n"
                                "Example: LeBron James Over 25.5 PTS\n"
                                "Do not add commentary, just the legs."
                            ),
                        },
                    ],
                }
            ],
        )
        text = message.content[0].text.strip()
        if not text:
            raise ValueError("Claude Vision returned no text from the image.")
        return text

    # ── OCR.Space fallback ─────────────────────────────────────────────────────
    if not config.OCR_SPACE_API_KEY:
        raise ValueError(
            "No vision API configured. Set ANTHROPIC_API_KEY (recommended) or OCR_SPACE_API_KEY."
        )

    async with aiohttp.ClientSession() as session:
        data = aiohttp.FormData()
        data.add_field("apikey", config.OCR_SPACE_API_KEY)
        data.add_field("language", "eng")
        data.add_field("isOverlayRequired", "false")
        data.add_field("file", image_bytes, filename="slip.png", content_type="image/png")

        async with session.post("https://api.ocr.space/parse/image", data=data) as resp:
            result = await resp.json()

    if result.get("IsErroredOnProcessing"):
        raise ValueError(f"OCR.Space error: {result.get('ErrorMessage', 'unknown')}")

    parsed = result.get("ParsedResults", [])
    if not parsed:
        raise ValueError("OCR.Space returned no text.")

    return parsed[0].get("ParsedText", "").strip()


def _parse_universal_slip(slip_text: str) -> list[dict]:
    """
    Parse a multi-sport slip into a list of leg dicts.

    Handles all 6 sports: NBA, NFL, NHL, SOCCER, TENNIS, ESPORTS.
    Input: comma or newline separated legs like:
      "LeBron James Over 25.5 PTS, Patrick Mahomes Over 275.5 Passing Yards, ZywOo Over 25.5 Kills"
    """
    # Split on comma or newline
    import re
    raw_legs = re.split(r"[,\n]+", slip_text.strip())
    legs = []
    for raw in raw_legs:
        raw = raw.strip()
        if not raw:
            continue
        parsed = parse_slip_line(raw)
        if parsed:
            legs.append(parsed)
        else:
            # Fallback: try the NBA parser for lines that don't match universal format
            try:
                from analysis.nba_analysis import parse_slip
                fallback = parse_slip(raw)
                for leg in fallback:
                    leg["sport"] = detect_sport(f"{leg.get('player', '')} {leg.get('prop_type', '')}")
                    legs.append(leg)
            except Exception:
                pass
    return legs


async def _score_legs(bot, legs: list[dict]) -> list[PickResult]:
    """Score all slip legs concurrently across any sport."""

    # Pre-fetch shared data for sports that appear in the slip
    sports_in_slip = {l.get("sport", "NBA").upper() for l in legs}

    nba_team_stats = None
    nba_injuries = []
    nhl_standings = None

    if "NBA" in sports_in_slip:
        try:
            nba_team_stats = await bot.nba_client.get_team_defensive_stats()
            nba_injuries   = await bot.nba_client.get_injuries()
        except Exception as exc:
            log.warning("Could not fetch NBA context: %s", exc)

    if "NHL" in sports_in_slip:
        try:
            nhl_standings = await bot.nhl_client.get_all_teams_stats()
        except Exception as exc:
            log.warning("Could not fetch NHL standings: %s", exc)

    sem = asyncio.Semaphore(3)

    async def _score_one(leg: dict) -> PickResult:
        async with sem:
            sport = leg.get("sport", "NBA").upper()
            player = resolve_name(leg.get("player", "Unknown"), sport)
            try:
                return await score_pick(
                    player_name=player,
                    prop_type=leg["prop_type"],
                    line=leg["line"],
                    opponent_team=leg.get("opponent", "TBD"),
                    is_home=leg.get("is_home", True),
                    bot=bot,
                    nba_client=bot.nba_client,
                    nhl_client=bot.nhl_client,
                    direction=leg.get("direction", "over"),
                    sport=sport,
                    injuries=nba_injuries if sport == "NBA" else None,
                    all_team_stats=nba_team_stats if sport == "NBA" else None,
                    all_nhl_standings=nhl_standings if sport == "NHL" else None,
                    league=leg.get("league", "eng.1"),
                    surface=leg.get("surface", "hard"),
                    tour=leg.get("tour", "atp"),
                    game=leg.get("game", "csgo"),
                )
            except Exception as exc:
                log.error("Error scoring leg %s: %s", leg.get("player"), exc)
                return PickResult(
                    player_name=leg.get("player", "Unknown"),
                    prop_type=leg.get("prop_type", "?"),
                    line=leg.get("line", 0),
                    direction=leg.get("direction", "over"),
                    sport=sport,
                    confidence=40,
                    recommendation="SKIP",
                    reasoning=[f"⚠️ Could not fully analyze: {exc}"],
                )

    results = await asyncio.gather(*[_score_one(leg) for leg in legs])
    return list(results)


class AnalyzeCog(commands.Cog, name="Analyze"):
    """Slip analysis command."""

    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="analyze",
        description="Analyze a prop slip — type it out or upload a photo.",
    )
    @app_commands.describe(
        slip="Your picks separated by commas. Format: Player Over/Under Line Prop. Max 6 legs.",
        image="Upload a photo of your betting slip to auto-read it.",
    )
    async def analyze(
        self,
        interaction: discord.Interaction,
        slip: Optional[str] = None,
        image: Optional[discord.Attachment] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        if not slip and not image:
            await interaction.followup.send(
                embed=error_embed(
                    "No slip provided",
                    "Either type your slip or upload a photo of it.",
                )
            )
            return

        # If image provided, extract slip text via Claude vision
        if image:
            if not image.content_type or not image.content_type.startswith("image/"):
                await interaction.followup.send(
                    embed=error_embed("Invalid file", "Please upload an image file (JPG, PNG, etc).")
                )
                return
            try:
                slip = await _extract_slip_from_image(image.url)
                log.info("Extracted slip from image: %s", slip)
            except Exception as exc:
                await interaction.followup.send(
                    embed=error_embed("Could not read image", str(exc))
                )
                return

        # Parse slip — supports all 6 sports
        legs = _parse_universal_slip(slip)

        if not legs:
            await interaction.followup.send(
                embed=error_embed(
                    "Could not parse slip",
                    "Please use the format: `Player Over/Under Line PropType`\n"
                    "Example: `LeBron James Over 25.5 PTS, Anthony Davis Over 10.5 REB`",
                )
            )
            return

        if len(legs) > 6:
            legs = legs[:6]  # Cap at 6 legs

        # Score all legs
        try:
            picks = await _score_legs(self.bot, legs)
        except Exception as exc:
            log.error("/analyze error: %s", exc)
            await interaction.followup.send(
                embed=error_embed("Analysis failed", str(exc))
            )
            return

        if not picks:
            await interaction.followup.send(
                embed=info_embed("No results", "Could not score any legs in your slip.")
            )
            return

        # Calculate overall slip score (weighted average, penalise weak links)
        confidences = [p.confidence for p in picks]
        avg_conf = sum(confidences) / len(confidences)

        # Penalise if any leg is below 50 (weak link)
        weak_links = [p for p in picks if p.confidence < 50]
        if weak_links:
            penalty = len(weak_links) * 5
            overall_score = max(0, avg_conf - penalty)
        else:
            overall_score = avg_conf

        # Persist
        slip_id = None
        try:
            slip_id = await save_analyzed_slip(
                user_id=str(interaction.user.id),
                slip_text=(slip or "")[:500],
                score=overall_score,
                legs=len(picks),
            )
        except Exception as exc:
            log.warning("Could not save slip: %s", exc)

        # Build main slip embed
        main_embed = slip_embed(
            picks=picks,
            slip_text=slip or "",
            overall_score=overall_score,
            slip_id=slip_id,
        )

        # If 1 leg, show full pick embed instead
        if len(picks) == 1:
            await interaction.followup.send(embed=pick_embed(picks[0]))
            return

        # Send the summary embed
        await interaction.followup.send(embed=main_embed)

        # For long slips, also send individual detailed embeds for each leg
        if len(picks) <= 3:
            for pick in picks:
                try:
                    await interaction.followup.send(embed=pick_embed(pick))
                except discord.HTTPException:
                    break  # Rate limit or message too long


async def setup(bot) -> None:
    await bot.add_cog(AnalyzeCog(bot))
